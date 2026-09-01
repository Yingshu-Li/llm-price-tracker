from __future__ import annotations

import csv
import hashlib
import html
import json
import re
import time
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Iterable

import httpx


ARXIV_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/(?P<id>\d{4}\.\d{4,5})(?:v\d+)?", re.I)
DOI_RE = re.compile(r"(?:doi\.org/|doi:\s*)(?P<id>10\.\d{4,9}/[^\s\]\[<>)\"']+)", re.I)
MARKDOWN_LINK_RE = re.compile(r"\[\[?([^\]]+)\]\]?\((https?://[^)\s]+)(?:\s+[^)]*)?\)")
RELATIVE_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\((?!https?://)([^)#?]+\.md)(?:#[^)]*)?\)", re.I)
REFERENCE_DEF_RE = re.compile(r"^\s*\[([^\]]+)\]:\s*(https?://\S+)\s*$", re.I | re.M)
REFERENCE_LINK_RE = re.compile(r"\[([^\]]+)\]\[([^\]]+)\]")
HTML_LINK_RE = re.compile(r"<a\s+[^>]*href=[\"'](https?://[^\"']+)[\"'][^>]*>(.*?)</a>", re.I)
DATE_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})[./-](0?[1-9]|1[0-2])(?:[./-](0?[1-9]|[12]\d|3[01]))?(?!\d)")
YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
DATE_ONLY_TITLE_RE = re.compile(
    r"^(?:20\d{2}(?:[-/.]\d{1,2})?(?:[-/.]\d{1,2})?|"
    r"(?:0?[1-9]|1[0-2])[-/.](?:0?[1-9]|[12]\d|3[01])[-/.]20\d{2})$"
)
VENUE_ONLY_TITLE_RE = re.compile(
    r"^\[?(?:arxiv|openreview|nature|science|cvpr|iccv|eccv|wacv|bmvc|siggraph(?:\s+asia)?|"
    r"acm\s+(?:mm|mmasia)|acl|emnlp|naacl|eacl|coling|interspeech|icassp|ismir|iwslt|"
    r"iclr|icml|neurips(?:\s+db)?|aaai|ijcai|kdd|sigir|chi|colm|corl|iros|icra|rss|aamas)"
    r"(?:\s+(?:spotlight|oral|poster))?(?:\s*[:#-]?\s*(?:20\d{2}|'?\d{2}))?\]?\s*$",
    re.I,
)

VENUE_SHORTHAND_TITLE_RE = re.compile(
    r"^(?:ieee\s+)?(?:tpami|tvcg|tmi|tip|tcsvt|ral|tmm|ijcv|eswa|eaai|"
    r"neurocomputing|science\s+robotics)(?:\s*['’]?\d{2,4})?$",
    re.I,
)

VERIFIED_TITLE_SOURCES = {
    "verified source page", "Crossref title", "Semantic Scholar", "arXiv",
    "structured source", "official paper page", "official page title",
}

CORE_CATEGORIES = {
    "Language", "Vision", "Vision-Language", "Image Generation", "Video",
    "Audio & Speech", "3D & Spatial", "Unified Multimodal",
    "Robotics & Embodied AI",
}

PAPER_DOMAINS = (
    "arxiv.org", "doi.org", "openreview.net", "aclanthology.org",
    "proceedings.neurips.cc", "proceedings.mlr.press", "dl.acm.org",
    "ieeexplore.ieee.org", "link.springer.com", "nature.com/articles",
    "science.org/doi", "sciencedirect.com", "biorxiv.org", "medrxiv.org",
    "pubmed.ncbi.nlm.nih.gov", "ojs.aaai.org", "jmlr.org",
    "openaccess.thecvf.com", "papers.nips.cc", "aaai.org/papers",
)
GENERIC_LABELS = {
    "paper", "pdf", "arxiv", "preprint", "publication", "link", "论文",
    "article", "project", "project page", "homepage", "website", "webpage",
    "read", "official paper", "paper link", "technical report", "code",
}
TABLE_TITLE_HEADERS = {
    "title", "paper title", "paper", "work", "method", "benchmark", "name", "model", "model name",
}
SOURCE_DOCUMENT_OVERRIDES = {
    # The repository root is a broad security resource list; this document is
    # its actual paper feed and excludes tools, policies, and vendor reports.
    "TalEliyahu/Awesome-AI-Security": ("Research_Papers.md",),
}
SOURCE_STRUCTURED_FEEDS = {
    # This repository publishes a canonical CVF-derived JSON dataset. Its
    # Markdown pages place author/resource links close enough to paper links
    # that a generic Markdown parser can mistake an author list for a title.
    "firetix/awesome-cvpr-2026-papers": "data/cvpr2026_papers.json",
    # The README intentionally shows abbreviations for browsing. The generated
    # site dataset keeps the complete paper Title separate from Abbreviation.
    "AudioLLMs/Awesome-Audio-LLM": "docs/data.json",
}
VERIFIED_METADATA_OVERRIDES = {
    "https://doi.org/10.1007/BF00992698": {
        "title": "Q-Learning",
        "published_at": "1992",
        "date_precision": "year",
        "venue": "Machine Learning",
    },
    "https://doi.org/10.1037/h0042519": {
        "title": "The Perceptron: A Probabilistic Model for Information Storage and Organization in the Brain",
        "published_at": "1958",
        "date_precision": "year",
        "venue": "Psychological Review",
    },
    "https://arxiv.org/abs/2603.09877": {
        "title": "InternVL-U: Democratizing Unified Multimodal Models for Understanding, Reasoning, Generation and Editing",
        "published_at": "2026-03-10",
        "date_precision": "day",
        "venue": "arXiv preprint",
        "arxiv_id": "2603.09877",
    },
    "https://arxiv.org/abs/2607.15176": {
        "title": "Benchmarking Multimodal Large Language Models for Scientific Visualization Literacy",
        "published_at": "2026-07-16",
        "date_precision": "day",
        "venue": "arXiv preprint",
        "arxiv_id": "2607.15176",
    },
    "https://research.nvidia.com/labs/cosmos3/technical-report.pdf": {
        "title": "Cosmos 3: Omnimodal World Models for Physical AI",
        "published_at": "2026-06",
        "date_precision": "month",
        "venue": "arXiv preprint",
        "arxiv_id": "2606.02800",
    },
}
VERIFIED_METADATA_OVERRIDES_BY_ARXIV = {
    value["arxiv_id"]: value
    for value in VERIFIED_METADATA_OVERRIDES.values()
    if value.get("arxiv_id")
}
VENUE_PATTERNS = (
    "NeurIPS", "ICLR", "ICML", "CVPR", "ICCV", "ECCV", "ACL", "EMNLP",
    "NAACL", "EACL", "AAAI", "IJCAI", "KDD", "SIGIR", "WWW", "CHI",
    "COLM", "CoRL", "IROS", "ICRA", "RSS", "AAMAS", "TMLR", "JMLR",
    "TPAMI", "TMM", "TACL", "Nature", "Science", "Cell", "Lancet",
    "Bioinformatics", "Medical Image Analysis", "Computational Linguistics",
)


@dataclass(frozen=True)
class PaperSource:
    group: str
    category: str
    subcategory: str
    repository: str
    url: str


def load_sources(path: Path) -> list[PaperSource]:
    sources: list[PaperSource] = []
    current_group = ""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            current_group = (row.get("Group") or current_group).strip()
            repository = (row.get("Repository") or "").strip()
            if not repository:
                continue
            category = (row.get("PrimaryCategory") or "Other").strip()
            group = "Core Technology" if category in CORE_CATEGORIES else "Cross-disciplinary Applications"
            sources.append(PaperSource(
                group=group,
                category=category,
                subcategory=(row.get("Subcategory") or "General").strip(),
                repository=repository,
                url=(row.get("GitHubURL") or f"https://github.com/{repository}").strip(),
            ))
    return sources


def _repository_file(client: httpx.Client, repository: str, path: str) -> httpx.Response | None:
    encoded_path = urllib.parse.quote(path, safe="/")
    urls = (
        f"https://raw.githubusercontent.com/{repository}/HEAD/{encoded_path}",
        f"https://github.com/{repository}/raw/HEAD/{encoded_path}",
    )
    for url in urls:
        for attempt in range(2):
            try:
                response = client.get(url)
                if response.status_code == 200 and len(response.content) > 30:
                    return response
                if response.status_code not in {429, 500, 502, 503, 504}:
                    break
            except Exception:
                pass
            if attempt == 0:
                time.sleep(2)
    return None


def _structured_papers_to_markdown(payload: object, repository: str) -> str:
    if not isinstance(payload, list):
        raise RuntimeError(f"structured paper feed is not a list for {repository}")
    lines: list[str] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        title = re.sub(r"\s+", " ", str(item.get("title") or item.get("Title") or "")).strip()
        url = str(
            item.get("arxiv") or item.get("cvf_html") or item.get("cvf_pdf")
            or item.get("Paper_Link") or ""
        ).strip()
        if not title or not url.startswith(("http://", "https://")):
            continue
        title = title.replace("**", "")
        details: list[str] = []
        if repository == "firetix/awesome-cvpr-2026-papers":
            details.append("CVPR 2026")
        listed_time = str(item.get("Time") or "").strip()
        if listed_time:
            details.append(listed_time)
        suffix = f" — {'. '.join(details)}." if details else ""
        lines.append(f"- **{title}**{suffix} [Paper]({url})")
    if not lines:
        raise RuntimeError(f"structured paper feed contains no usable rows for {repository}")
    return "\n".join(lines)


def source_readme(client: httpx.Client, repository: str) -> tuple[str, str]:
    structured_path = SOURCE_STRUCTURED_FEEDS.get(repository)
    if structured_path:
        response = _repository_file(client, repository, structured_path)
        if response is None:
            raise RuntimeError(f"structured paper feed unavailable for {repository}: {structured_path}")
        return _structured_papers_to_markdown(response.json(), repository), str(response.url)
    override_paths = SOURCE_DOCUMENT_OVERRIDES.get(repository)
    if override_paths:
        documents: list[str] = []
        first_url = ""
        for path in override_paths:
            response = _repository_file(client, repository, path)
            if response is None:
                raise RuntimeError(f"paper document unavailable for {repository}: {path}")
            first_url = first_url or str(response.url)
            documents.append(response.text)
        return "\n".join(documents), first_url
    for name in ("README.md", "readme.md", "README.MD", "Readme.md"):
        response = _repository_file(client, repository, name)
        if response is not None:
            readme = response.text
            extra_documents: list[str] = []
            seen_paths: set[str] = set()
            for raw_path in RELATIVE_MARKDOWN_LINK_RE.findall(readme):
                path = urllib.parse.unquote(raw_path).replace("\\", "/").lstrip("./")
                if not path or path.casefold().startswith("readme") or path in seen_paths or ".." in path.split("/"):
                    continue
                seen_paths.add(path)
                extra = _repository_file(client, repository, path)
                if extra is not None:
                    extra_documents.append(f"\n<!-- collected from {path} -->\n{extra.text}")
                if len(extra_documents) >= 20:
                    break
            return readme + "".join(extra_documents), str(response.url)
    raise RuntimeError(f"README unavailable for {repository}")


def source_updated_at(client: httpx.Client, repository: str) -> str:
    try:
        response = client.get(f"https://github.com/{repository}/commits.atom")
        response.raise_for_status()
        root = ET.fromstring(response.content)
        value = root.findtext("{http://www.w3.org/2005/Atom}entry/{http://www.w3.org/2005/Atom}updated")
        if value:
            return value[:10]
    except Exception:
        pass
    return date.today().isoformat()


def _strip_markdown(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"[`*_~]", "", value)
    value = re.sub(r"^[\s|>*#\-+•·✅🔥✨📄📃📝📌🆕]+", "", value)
    value = re.sub(r"^[^\w\u4e00-\u9fff\[]+", "", value)
    value = re.sub(r"^\d+[.)]\s*", "", value)
    value = re.sub(r"\s+", " ", value).strip(" |:;,-–—")
    if value.startswith("[") and not value.startswith("[\""):
        value = value[1:].strip()
    if value.endswith("]"):
        value = value[:-1].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'", "“", "”"}:
        value = value[1:-1].strip()
    return value.strip('"“”')


def _clean_title_candidate(value: str) -> str:
    value = MARKDOWN_LINK_RE.sub(lambda item: item.group(1), value)
    value = HTML_LINK_RE.sub(lambda item: _strip_markdown(item.group(2)), value)
    value = _strip_markdown(value)
    if " — " in value:
        value = value.split(" — ", 1)[0].strip()
    if " - " in value:
        prefix, suffix = value.split(" - ", 1)
        if len(prefix) >= 8 and len(suffix) >= 35:
            value = prefix.strip()
    value = _strip_markdown(value)
    value = re.sub(r"\s+(?:📄|🌐|💻|⭐).*$", "", value).strip()
    value = re.sub(r"^arXiv\s+(?=[A-Z0-9])", "", value).strip()
    return value


def _link_label_is_generic(label: str) -> bool:
    return _title_is_suspicious(_strip_markdown(label))


def _title_is_suspicious(title: str) -> bool:
    if "�" in title:
        return True
    normalized = _strip_markdown(title)
    folded = normalized.casefold()
    folded_clean = folded.strip(" .,:;()[]")
    if len(normalized) < 7 or folded_clean in {*GENERIC_LABELS, "workshop", "conference"}:
        return True
    if re.fullmatch(r"arxiv(?:\s+[a-z-]+(?:\.[a-z-]+)*)?", folded_clean, re.I):
        return True
    if re.fullmatch(r"arxiv\s*:\s*(?:id|xxxx(?:\.xxxxx)?)", folded_clean, re.I):
        return True
    if re.fullmatch(r"(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)+", folded_clean, re.I):
        return True
    if "|" in normalized and re.search(r"\b20\d{2}\b", normalized):
        return True
    if normalized.count(";") >= 3:
        return True
    if re.search(r"\]\(https?://", normalized, re.I) or re.match(r"^[^]]{1,30}\]\s+", normalized):
        return True
    venue_candidate = re.sub(r"[,.:;]+", " ", normalized).strip()
    venue_candidate = re.sub(r"\s+", " ", venue_candidate)
    if (
        DATE_ONLY_TITLE_RE.fullmatch(normalized)
        or VENUE_ONLY_TITLE_RE.fullmatch(venue_candidate)
        or VENUE_SHORTHAND_TITLE_RE.fullmatch(venue_candidate)
    ):
        return True
    if re.fullmatch(r"(?:image|images|figure|fig\.?|thumbnail|screenshot|图片|图像?|插图)(?:\s*\d+)?", normalized, re.I):
        return True
    if re.search(r"\.(?:png|jpe?g|gif|svg|webp)$", normalized, re.I):
        return True
    if re.fullmatch(r"\[?(?:arxiv\s*[:#-]?\s*)?\d{4}\.\d{4,5}(?:v\d+)?\]?", normalized, re.I):
        return True
    return False


def _title_needs_metadata(title: str) -> bool:
    """Return true for titles that deserve an authoritative metadata lookup.

    Short project/model abbreviations are sometimes legitimate paper titles,
    so they are not filtered. They are instead resolved through arXiv or DOI
    metadata when available.
    """
    normalized = _strip_markdown(title)
    if _title_is_suspicious(normalized):
        return True
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9.+_-]*", normalized)
    return len(normalized) <= 32 and len(words) <= 3


def _title_has_verified_metadata(paper: dict) -> bool:
    return bool(VERIFIED_TITLE_SOURCES.intersection(paper.get("metadata_sources", [])))


def _looks_like_paper(label: str, url: str, line: str) -> bool:
    lowered_url = url.casefold()
    if "xxxx" in lowered_url or re.search(r"arxiv\.org/(?:abs|pdf)/(?:id|[a-z.-]+)$", lowered_url):
        return False
    if any(fragment in lowered_url for fragment in (
        "arxiv.org/list/", "/recentissue.jsp", "/xpl/recentissue.jsp",
    )):
        return False
    lowered_label = _strip_markdown(label).casefold()
    if any(domain in lowered_url for domain in PAPER_DOMAINS):
        return True
    if urllib.parse.urlsplit(url).path.casefold().endswith(".pdf"):
        if re.search(r"\bslides?\b", line, re.I):
            return False
        return not any(domain in lowered_url for domain in ("github.com", "huggingface.co"))
    return False


def _table_cells(line: str) -> list[str]:
    value = line.strip()
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|"):
        value = value[:-1]
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", value)]


def _is_table_separator(line: str) -> bool:
    cells = _table_cells(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells)


def _table_title(line: str, headers: list[str]) -> str:
    cells = _table_cells(line)
    if not headers or len(cells) != len(headers):
        return ""
    for index, header in enumerate(headers):
        normalized_header = _strip_markdown(header).casefold()
        if normalized_header not in TABLE_TITLE_HEADERS:
            continue
        candidate = _clean_title_candidate(cells[index])
        if not _title_is_suspicious(candidate):
            return candidate
    return ""


def _context_title(line: str) -> str:
    candidates = re.findall(r"\*\*([^*]{7,500})\*\*", line)
    if not candidates and re.match(r"^\s{0,3}#{1,6}\s+", line):
        candidates = [re.sub(r"^\s{0,3}#{1,6}\s+", "", line)]
    if not candidates and re.match(r"^\s*[-*+]\s+", line) and not MARKDOWN_LINK_RE.search(line):
        candidates = [re.sub(r"^\s*[-*+]\s+", "", line)]
    for candidate in candidates:
        candidate = _clean_title_candidate(candidate)
        if not _title_is_suspicious(candidate):
            return candidate
    return ""


def _logical_lines(readme: str) -> list[str]:
    """Join wrapped Markdown entries while a bold title is still open."""
    result: list[str] = []
    pending = ""
    for raw_line in readme.splitlines():
        if pending:
            pending = f"{pending.rstrip()} {raw_line.strip()}"
            if pending.count("**") % 2 == 0:
                result.append(pending)
                pending = ""
            continue
        if raw_line.count("**") % 2 == 1:
            pending = raw_line
        else:
            result.append(raw_line)
    if pending:
        result.append(pending)
    return result


def _title_from_line(
    line: str,
    label: str,
    match_start: int,
    *,
    table_title: str = "",
    context_title: str = "",
) -> str:
    if table_title:
        return table_title
    if not _link_label_is_generic(label):
        return _clean_title_candidate(label)
    if context_title:
        return context_title

    if "|" in line:
        same_cell_prefix = line[:match_start].rsplit("|", 1)[-1]
        same_cell_prefix = MARKDOWN_LINK_RE.sub(lambda item: item.group(1), same_cell_prefix)
        same_cell_prefix = _strip_markdown(same_cell_prefix)
        if not _title_is_suspicious(same_cell_prefix):
            return same_cell_prefix

        cells = [_strip_markdown(cell) for cell in line.split("|")]
        link_cell = next((index for index, cell in enumerate(cells) if label in cell), -1)
        for index in range(link_cell - 1, -1, -1):
            candidate = cells[index]
            if not _title_is_suspicious(candidate):
                return candidate

    before = line[:match_start]
    wrapped_venue = before.rstrip().endswith("(")
    before = MARKDOWN_LINK_RE.sub(lambda item: item.group(1), before)
    before = _strip_markdown(before)
    before = re.sub(r"^(?:\[[^]]+\]|\([^)]*\))\s*", "", before)
    before = before[-300:].strip(" |:;,-–—")
    if wrapped_venue:
        before = before.rstrip(" (")
        before = re.sub(r",\s*[A-Za-z][A-Za-z0-9-]{1,24}'?\d{2,4}$", "", before).strip()
    if not _title_is_suspicious(before):
        return before
    return ""


def _extract_date(text: str) -> tuple[str, str]:
    arxiv = ARXIV_RE.search(text)
    if arxiv:
        prefix = arxiv.group("id")[:2]
        month = arxiv.group("id")[2:4]
        return f"20{prefix}-{month}", "month"
    proceedings = re.search(r"(?:content|proceedings)/(?:CVPR|ICCV|ECCV)[_-]?(20\d{2})", text, re.I)
    if proceedings:
        return proceedings.group(1), "year"
    short_venue_year = re.search(r"\b[A-Z][A-Z0-9-]{2,12}'(\d{2})\b", text)
    if short_venue_year:
        return f"20{short_venue_year.group(1)}", "year"
    match = DATE_RE.search(text)
    if match:
        year, month, day = match.groups()
        if day:
            return f"{year}-{int(month):02d}-{int(day):02d}", "day"
        return f"{year}-{int(month):02d}", "month"
    match = YEAR_RE.search(text)
    if match:
        return match.group(1), "year"
    return "", "unknown"


def _extract_venue(text: str, url: str) -> str:
    for venue in VENUE_PATTERNS:
        if re.search(rf"(?<![A-Za-z]){re.escape(venue)}(?:\s+(?:Findings|Workshop))?(?:\s+20\d{{2}})?", text, re.I):
            match = re.search(rf"(?<![A-Za-z])({re.escape(venue)}(?:\s+(?:Findings|Workshop))?(?:\s+20\d{{2}})?)", text, re.I)
            if match:
                return match.group(1)
    if "arxiv.org" in url.casefold():
        return ""
    if "openreview.net" in url.casefold():
        return "OpenReview"
    if "biorxiv.org" in url.casefold():
        return "bioRxiv preprint"
    if "medrxiv.org" in url.casefold():
        return "medRxiv preprint"
    return ""


def normalize_title(title: str) -> str:
    title = unicodedata.normalize("NFKC", title).casefold()
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", title)


def paper_key(paper: dict) -> str:
    if paper.get("arxiv_id"):
        return f"arxiv:{paper['arxiv_id'].casefold()}"
    if paper.get("doi"):
        return f"doi:{paper['doi'].casefold().rstrip('.')}"
    normalized = normalize_title(paper.get("title", ""))
    return f"title:{normalized}" if normalized else f"url:{paper.get('paper_url', '')}"


def paper_aliases(paper: dict) -> list[str]:
    aliases: list[str] = []
    if paper.get("arxiv_id"):
        aliases.append(f"arxiv:{paper['arxiv_id'].casefold()}")
    if paper.get("doi"):
        aliases.append(f"doi:{paper['doi'].casefold().rstrip('.')}")
    normalized = normalize_title(paper.get("title", ""))
    if normalized:
        aliases.append(f"title:{normalized}")
    if not aliases and paper.get("paper_url"):
        aliases.append(f"url:{paper['paper_url']}")
    return aliases


def parse_readme(source: PaperSource, readme: str, updated_at: str, first_seen_at: str) -> list[dict]:
    papers: list[dict] = []
    seen_urls: set[str] = set()
    reference_urls = {
        key.casefold(): url.rstrip(".,;:")
        for key, url in REFERENCE_DEF_RE.findall(readme)
    }
    lines = _logical_lines(readme)
    table_headers: list[str] = []
    previous_context = ""
    context_age = 99
    for line_index, raw_line in enumerate(lines):
        if len(raw_line) > 4000:
            continue
        if "|" in raw_line and line_index + 1 < len(lines) and _is_table_separator(lines[line_index + 1]):
            table_headers = _table_cells(raw_line)
            previous_context = ""
            context_age = 99
            continue
        if _is_table_separator(raw_line):
            continue
        if "|" not in raw_line:
            table_headers = []
        line_table_title = _table_title(raw_line, table_headers) if table_headers else ""
        line_context = _context_title(raw_line)
        links: list[tuple[str, str, int]] = [
            (match.group(1), match.group(2), match.start()) for match in MARKDOWN_LINK_RE.finditer(raw_line)
        ]
        links.extend(
            (_strip_markdown(match.group(2)), match.group(1), match.start())
            for match in HTML_LINK_RE.finditer(raw_line)
        )
        links.extend(
            (match.group(1), reference_urls.get(match.group(2).casefold(), ""), match.start())
            for match in REFERENCE_LINK_RE.finditer(raw_line)
            if reference_urls.get(match.group(2).casefold())
        )
        paper_links = [
            (label, raw_url, start) for label, raw_url, start in links
            if _looks_like_paper(label, raw_url, raw_line)
            and not (
                raw_line.lstrip().startswith(">")
                and _link_label_is_generic(label)
            )
        ]
        if not paper_links:
            if line_context:
                previous_context = line_context
                context_age = 0
            elif raw_line.strip():
                context_age += 1
                if context_age > 2:
                    previous_context = ""
            continue

        def link_priority(item: tuple[str, str, int]) -> tuple[int, int]:
            url = item[1].casefold()
            if "arxiv.org/abs/" in url:
                return (0, item[2])
            if "doi.org/" in url:
                return (1, item[2])
            if "openreview.net/forum" in url:
                return (2, item[2])
            if "openaccess.thecvf.com" in url and not url.endswith(".pdf"):
                return (3, item[2])
            if not urllib.parse.urlsplit(url).path.endswith(".pdf"):
                return (4, item[2])
            return (5, item[2])

        for label, raw_url, start in [min(paper_links, key=link_priority)]:
            url = raw_url.rstrip(".,;:)")
            if url in seen_urls:
                continue
            seen_urls.add(url)
            title = _title_from_line(
                raw_line,
                label,
                start,
                table_title=line_table_title,
                context_title=line_context or (previous_context if context_age <= 2 else ""),
            )
            if _title_is_suspicious(title):
                title = ""
            arxiv_match = ARXIV_RE.search(url)
            doi_match = DOI_RE.search(url)
            published_at, precision = _extract_date(f"{raw_line} {url}")
            papers.append({
                "id": "",
                "title": title,
                "venue": _extract_venue(raw_line, url),
                "published_at": published_at,
                "date_precision": precision,
                "first_seen_at": first_seen_at,
                "source_updated_at": updated_at,
                "paper_url": url,
                "arxiv_id": arxiv_match.group("id") if arxiv_match else "",
                "doi": doi_match.group("id").rstrip(".,;)") if doi_match else "",
                "group": source.group,
                "category": source.category,
                "categories": [source.category],
                "subcategories": [source.subcategory],
                "source_repos": [source.repository],
                "source_urls": [source.url],
                "metadata_sources": ["source README"],
            })
        if line_context:
            previous_context = line_context
            context_age = 0
        elif raw_line.strip():
            context_age += 1
            if context_age > 2:
                previous_context = ""
    return papers


def merge_papers(papers: Iterable[dict]) -> list[dict]:
    merged: list[dict] = []
    alias_to_index: dict[str, int] = {}
    for paper in papers:
        paper = dict(paper)
        if paper.get("title"):
            paper["title"] = _clean_title_candidate(paper["title"])
        aliases = paper_aliases(paper)
        index = next((alias_to_index[alias] for alias in aliases if alias in alias_to_index), None)
        if index is None:
            copy = dict(paper)
            key = aliases[0] if aliases else paper_key(paper)
            copy["id"] = hashlib.sha1(key.encode()).hexdigest()[:16]
            merged.append(copy)
            index = len(merged) - 1
            for alias in aliases:
                alias_to_index[alias] = index
            continue
        current = merged[index]
        verified_title_sources = {"verified source page", "Crossref title", "Semantic Scholar", "arXiv"}
        current_title_rank = (
            3 if verified_title_sources.intersection(current.get("metadata_sources", [])) else
            2 if "structured source" in current.get("metadata_sources", []) else
            1 if current.get("title") and not _title_is_suspicious(current["title"]) else 0
        )
        paper_title_rank = (
            3 if verified_title_sources.intersection(paper.get("metadata_sources", [])) else
            2 if "structured source" in paper.get("metadata_sources", []) else
            1 if paper.get("title") and not _title_is_suspicious(paper["title"]) else 0
        )
        if paper.get("title") and paper_title_rank > current_title_rank:
            current["title"] = paper["title"]
        for field in ("categories", "subcategories", "source_repos", "source_urls", "metadata_sources"):
            current[field] = list(dict.fromkeys([*current.get(field, []), *paper.get(field, [])]))
        for field in ("title", "venue", "arxiv_id", "doi"):
            if not current.get(field) and paper.get(field):
                current[field] = paper[field]
        if paper.get("published_at"):
            precision_rank = {"unknown": 0, "year": 1, "month": 2, "day": 3}
            current_rank = precision_rank.get(current.get("date_precision", "unknown"), 0)
            paper_rank = precision_rank.get(paper.get("date_precision", "unknown"), 0)
            if not current.get("published_at") or paper_rank > current_rank:
                current["published_at"] = paper["published_at"]
                current["date_precision"] = paper.get("date_precision", "unknown")
        current["source_updated_at"] = max(current.get("source_updated_at", ""), paper.get("source_updated_at", ""))
        current["first_seen_at"] = min(current.get("first_seen_at", "9999-12-31"), paper.get("first_seen_at", "9999-12-31"))
        for alias in [*paper_aliases(current), *aliases]:
            alias_to_index[alias] = index
    return merged


def apply_verified_metadata_overrides(papers: Iterable[dict]) -> None:
    """Apply a tiny set of title fixes verified against first-party pages."""
    for paper in papers:
        override = (
            VERIFIED_METADATA_OVERRIDES.get(paper.get("paper_url", ""))
            or VERIFIED_METADATA_OVERRIDES_BY_ARXIV.get(paper.get("arxiv_id", ""))
        )
        if not override:
            continue
        paper.update(override)
        paper["metadata_sources"] = list(dict.fromkeys([
            *paper.get("metadata_sources", []), "verified source page",
        ]))


def _arxiv_metadata(client: httpx.Client, ids: list[str]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    namespace = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    # Keep the query URL below common proxy/server limits.
    batch_size = 100
    for offset in range(0, len(ids), batch_size):
        batch = ids[offset:offset + batch_size]
        root = None
        for attempt in range(3):
            try:
                response = client.get(
                    "https://export.arxiv.org/api/query",
                    params={"id_list": ",".join(batch), "max_results": len(batch)},
                )
                response.raise_for_status()
                root = ET.fromstring(response.content)
                break
            except Exception as exc:
                if attempt == 2:
                    print(f"warning: arXiv metadata batch {offset // batch_size + 1} failed: {exc}")
                else:
                    status = getattr(getattr(exc, "response", None), "status_code", None)
                    time.sleep((60 if status == 429 else 5) * (attempt + 1))
        if root is None:
            continue
        for entry in root.findall("a:entry", namespace):
            identifier = (entry.findtext("a:id", default="", namespaces=namespace).rsplit("/", 1)[-1]).split("v", 1)[0]
            result[identifier] = {
                "title": re.sub(r"\s+", " ", entry.findtext("a:title", default="", namespaces=namespace)).strip(),
                "published_at": entry.findtext("a:published", default="", namespaces=namespace)[:10],
                "date_precision": "day",
                "venue": (entry.findtext("arxiv:journal_ref", default="", namespaces=namespace) or "arXiv preprint").strip(),
            }
        if offset + batch_size < len(ids):
            time.sleep(3)
    return result


def _semantic_scholar_arxiv_metadata(client: httpx.Client, ids: list[str]) -> dict[str, dict]:
    """Resolve arXiv identifiers in batches when the arXiv API is rate limited."""
    result: dict[str, dict] = {}
    batch_size = 500
    for offset in range(0, len(ids), batch_size):
        batch = ids[offset:offset + batch_size]
        response = None
        for attempt in range(3):
            try:
                response = client.post(
                    "https://api.semanticscholar.org/graph/v1/paper/batch",
                    params={"fields": "title,publicationDate,venue,externalIds"},
                    json={"ids": [f"ARXIV:{identifier}" for identifier in batch]},
                )
                response.raise_for_status()
                break
            except Exception:
                if attempt < 2:
                    time.sleep(5 * (attempt + 1))
                else:
                    response = None
        if response is None:
            # Public unauthenticated access can be rate-limited for several
            # minutes. Stop hammering subsequent batches and let arXiv handle
            # the unresolved identifiers instead.
            break
        for requested_id, item in zip(batch, response.json()):
            if not item:
                continue
            identifier = (item.get("externalIds") or {}).get("ArXiv") or requested_id
            published = item.get("publicationDate") or ""
            precision = "day" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", published) else (
                "month" if re.fullmatch(r"\d{4}-\d{2}", published) else (
                    "year" if re.fullmatch(r"\d{4}", published) else "unknown"
                )
            )
            result[identifier] = {
                "title": re.sub(r"\s+", " ", item.get("title") or "").strip(),
                "published_at": published if precision != "unknown" else "",
                "date_precision": precision,
                "venue": (item.get("venue") or "arXiv preprint").strip(),
            }
        if offset + batch_size < len(ids):
            time.sleep(1)
    return result


def _crossref_metadata(client: httpx.Client, doi: str) -> dict:
    encoded = urllib.parse.quote(doi, safe="")
    response = client.get(f"https://api.crossref.org/works/{encoded}", params={"mailto": "llm-price-tracker@users.noreply.github.com"})
    response.raise_for_status()
    message = response.json().get("message", {})
    title = (message.get("title") or [""])[0]
    venue = (message.get("container-title") or [""])[0]
    date_parts = (message.get("published-online") or message.get("published-print") or message.get("issued") or {}).get("date-parts") or [[]]
    parts = [part for part in date_parts[0] if isinstance(part, int)] if date_parts else []
    published = ""
    precision = "unknown"
    if parts:
        if len(parts) >= 3:
            published = f"{parts[0]:04d}-{parts[1]:02d}-{parts[2]:02d}"
            precision = "day"
        elif len(parts) == 2:
            published = f"{parts[0]:04d}-{parts[1]:02d}"
            precision = "month"
        else:
            published = f"{parts[0]:04d}"
            precision = "year"
    return {"title": title, "venue": venue, "published_at": published, "date_precision": precision}


def _title_from_cvf_url(url: str) -> str:
    path = urllib.parse.unquote(urllib.parse.urlsplit(url).path)
    filename = path.rsplit("/", 1)[-1]
    match = re.match(
        r"^[^_]+_(.+?)_(?:CVPRW?|ICCVW?|ECCVW?|WACV)_20\d{2}_paper(?:\.pdf|\.html)?$",
        filename,
        re.I,
    )
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1).replace("_", " ")).strip()


def _title_from_html(document: str) -> str:
    for tag in re.findall(r"<meta\b[^>]*>", document, re.I):
        attributes = {
            key.casefold(): html.unescape(value).strip()
            for key, _, value in re.findall(r"([:\w-]+)\s*=\s*([\"'])(.*?)\2", tag, re.I | re.S)
        }
        name = (attributes.get("name") or attributes.get("property") or "").casefold()
        if name in {"citation_title", "dc.title", "dcterms.title", "og:title"}:
            candidate = _clean_title_candidate(attributes.get("content", ""))
            if candidate and not _title_is_suspicious(candidate):
                return candidate
    match = re.search(r"<title\b[^>]*>(.*?)</title>", document, re.I | re.S)
    if not match:
        return ""
    candidate = _clean_title_candidate(match.group(1))
    candidate = re.split(r"\s+(?:\||[-–—])\s+", candidate, maxsplit=1)[0].strip()
    return candidate if not _title_is_suspicious(candidate) else ""


def _webpage_title_metadata(client: httpx.Client, url: str) -> dict:
    cvf_title = _title_from_cvf_url(url) if "openaccess.thecvf.com" in url.casefold() else ""
    fetch_url = url
    if cvf_title and "/papers/" in fetch_url and fetch_url.casefold().endswith(".pdf"):
        fetch_url = fetch_url.replace("/papers/", "/html/")[:-4] + ".html"
    ojs_match = re.search(r"(ojs\.aaai\.org/index\.php/AAAI/article)/(?:download|view)/(\d+)", url, re.I)
    if ojs_match:
        fetch_url = f"https://{ojs_match.group(1)}/view/{ojs_match.group(2)}"
    response = client.get(fetch_url, headers={"Accept": "text/html,application/xhtml+xml"})
    response.raise_for_status()
    content_type = response.headers.get("content-type", "").casefold()
    if "html" not in content_type and not response.text.lstrip().startswith("<"):
        return {"title": cvf_title} if cvf_title else {}
    title = _title_from_html(response.text)
    title = title or cvf_title
    return {"title": title} if title else {}


def _is_supported_official_paper_page(url: str) -> bool:
    lowered = url.casefold()
    return any(pattern in lowered for pattern in (
        "openaccess.thecvf.com/content/",
        "ojs.aaai.org/index.php/aaai/article/",
        "ieeexplore.ieee.org/abstract/document/",
        "ieeexplore.ieee.org/document/",
        "dl.acm.org/doi/",
        "nature.com/articles/",
        "sciencedirect.com/science/article/",
        "pubmed.ncbi.nlm.nih.gov/",
        "openreview.net/forum",
        "openreview.net/pdf",
        "aclanthology.org/",
        "biorxiv.org/content/",
        "medrxiv.org/content/",
        "link.springer.com/article/",
        "link.springer.com/chapter/",
        "science.org/doi/",
        "proceedings.neurips.cc/paper",
        "proceedings.mlr.press/",
        "jmlr.org/papers/",
        "arxiv.org/abs/",
        "arxiv.org/html/",
    ))


def enrich_missing_metadata(client: httpx.Client, papers: list[dict], max_items: int = 25000) -> None:
    needs = [
        paper for paper in papers
        if not paper.get("title")
        or _title_needs_metadata(paper.get("title", ""))
        or not paper.get("published_at")
    ]
    arxiv_ids = list(dict.fromkeys(paper["arxiv_id"] for paper in needs if paper.get("arxiv_id")))[:max_items]
    if arxiv_ids:
        metadata: dict[str, dict] = {}
        semantic_ids: set[str] = set()
        try:
            semantic_metadata = _semantic_scholar_arxiv_metadata(client, arxiv_ids)
            metadata.update(semantic_metadata)
            semantic_ids.update(semantic_metadata)
        except Exception as exc:
            print(f"warning: Semantic Scholar enrichment failed: {exc}")
        missing_ids = [identifier for identifier in arxiv_ids if identifier not in metadata]
        if missing_ids:
            try:
                metadata.update(_arxiv_metadata(client, missing_ids))
            except Exception as exc:
                print(f"warning: arXiv enrichment failed: {exc}")
        for paper in needs:
            identifier = paper.get("arxiv_id", "")
            item = metadata.get(identifier)
            if not item:
                continue
            if item.get("title"):
                paper["title"] = item["title"]
            if item.get("venue") and not paper.get("venue"):
                paper["venue"] = item["venue"]
            if item.get("published_at") and (not paper.get("published_at") or paper.get("date_precision") != "day"):
                paper["published_at"] = item["published_at"]
                paper["date_precision"] = item["date_precision"]
            metadata_source = "Semantic Scholar" if identifier in semantic_ids else "arXiv"
            if metadata_source not in paper["metadata_sources"]:
                paper["metadata_sources"].append(metadata_source)

    remaining = max(0, max_items - len(arxiv_ids))
    doi_needs = [
        paper for paper in papers
        if paper.get("doi") and (
            not paper.get("title")
            or _title_needs_metadata(paper.get("title", ""))
            or not paper.get("published_at")
        )
    ]
    attempted_dois = doi_needs[:remaining]
    for paper in attempted_dois:
        try:
            item = _crossref_metadata(client, paper["doi"])
            if item.get("title"):
                paper["title"] = item["title"]
            if item.get("venue") and not paper.get("venue"):
                paper["venue"] = item["venue"]
            if item.get("published_at") and (not paper.get("published_at") or paper.get("date_precision") != "day"):
                paper["published_at"] = item["published_at"]
                paper["date_precision"] = item["date_precision"]
            if "Crossref" not in paper["metadata_sources"]:
                paper["metadata_sources"].append("Crossref")
            if "Crossref title" not in paper["metadata_sources"]:
                paper["metadata_sources"].append("Crossref title")
        except Exception as exc:
            print(f"warning: Crossref metadata failed for {paper['doi']}: {exc}")
            continue

    remaining = max(0, remaining - len(attempted_dois))
    webpage_needs = [
        paper for paper in papers
        if remaining
        and paper.get("paper_url")
        and _is_supported_official_paper_page(paper["paper_url"])
        and _title_needs_metadata(paper.get("title", ""))
        and not {"arXiv", "Semantic Scholar", "Crossref title", "verified source page", "structured source"}.intersection(
            paper.get("metadata_sources", [])
        )
    ]
    for paper in webpage_needs[:remaining]:
        try:
            item = _webpage_title_metadata(client, paper["paper_url"])
            if item.get("title"):
                paper["title"] = item["title"]
                if "official page title" not in paper["metadata_sources"]:
                    paper["metadata_sources"].append("official page title")
        except Exception as exc:
            print(f"warning: official page title failed for {paper['paper_url']}: {exc}")
            continue

    # A just-posted arXiv paper may not yet exist in either metadata index.
    # Keep the source-list title and ID-derived month, but label that lower
    # confidence explicitly instead of inventing a day value.
    for paper in papers:
        if (
            paper.get("arxiv_id")
            and not {"arXiv", "Semantic Scholar"}.intersection(paper.get("metadata_sources", []))
            and not _title_is_suspicious(paper.get("title", ""))
        ):
            if "arXiv identifier" not in paper["metadata_sources"]:
                paper["metadata_sources"].append("arXiv identifier")


def apply_verified_metadata_cache(papers: Iterable[dict], cached_papers: Iterable[dict]) -> None:
    cached_arxiv = {
        paper["arxiv_id"]: paper
        for paper in cached_papers
        if paper.get("arxiv_id") and {"arXiv", "Semantic Scholar"}.intersection(paper.get("metadata_sources", []))
    }
    cached_doi = {
        paper["doi"].casefold(): paper
        for paper in cached_papers
        if paper.get("doi") and "Crossref" in paper.get("metadata_sources", [])
    }
    for paper in papers:
        cached = None
        trusted_source = ""
        if paper.get("arxiv_id") in cached_arxiv:
            cached = cached_arxiv[paper["arxiv_id"]]
            trusted_source = next(
                (source for source in ("arXiv", "Semantic Scholar") if source in cached.get("metadata_sources", [])),
                "Semantic Scholar",
            )
        elif paper.get("doi") and paper["doi"].casefold() in cached_doi:
            cached = cached_doi[paper["doi"].casefold()]
            trusted_source = "Crossref"
        if not cached:
            continue
        if _title_is_suspicious(cached.get("title", "")):
            continue
        for field in ("title", "published_at", "date_precision"):
            if cached.get(field):
                paper[field] = cached[field]
        if not paper.get("venue") and cached.get("venue"):
            paper["venue"] = cached["venue"]
        paper["metadata_sources"] = list(dict.fromkeys([
            *paper.get("metadata_sources", []), trusted_source,
        ]))


def filter_recent(papers: Iterable[dict], since_year: int, max_without_date: int = 50) -> list[dict]:
    selected: list[dict] = []
    undated_by_source: dict[str, int] = {}
    for paper in papers:
        published = paper.get("published_at", "")
        precision = paper.get("date_precision", "unknown")
        if precision == "unknown":
            if re.fullmatch(r"20\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])", published):
                precision = "day"
            elif re.fullmatch(r"20\d{2}-(?:0[1-9]|1[0-2])", published):
                precision = "month"
            elif re.fullmatch(r"20\d{2}", published):
                precision = "year"
            paper["date_precision"] = precision
        if precision == "month" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", published):
            published = published[:7]
            paper["published_at"] = published
        elif precision == "year" and re.match(r"^\d{4}", published):
            published = published[:4]
            paper["published_at"] = published
        if published and published[:4].isdigit():
            if int(published[:4]) < since_year or int(published[:4]) > date.today().year:
                continue
        elif not published:
            source = (paper.get("source_repos") or ["unknown"])[0]
            count = undated_by_source.get(source, 0)
            if count >= max_without_date:
                continue
            undated_by_source[source] = count + 1
        if not paper.get("title") or _title_is_suspicious(paper.get("title", "")):
            continue
        # Very short labels are frequently model names, dataset names, venue
        # shorthands, or site navigation copied from an awesome-list. They are
        # displayed only after an authoritative metadata source confirms that
        # the label is the actual paper title (or replaces it with the full one).
        if _title_needs_metadata(paper.get("title", "")) and not _title_has_verified_metadata(paper):
            continue
        paper["venue"] = paper.get("venue") or "Venue not specified"
        selected.append(paper)
    selected.sort(
        key=lambda item: (
            1 if item.get("published_at") else 0,
            item.get("published_at") or "",
            item.get("source_updated_at") or "",
            item.get("title") or "",
        ),
        reverse=True,
    )
    return selected


def validate_catalog(papers: Iterable[dict]) -> None:
    problems: list[str] = []
    seen_ids: set[str] = set()
    date_patterns = {
        "day": re.compile(r"^20\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])$"),
        "month": re.compile(r"^20\d{2}-(?:0[1-9]|1[0-2])$"),
        "year": re.compile(r"^20\d{2}$"),
        "unknown": re.compile(r"^$"),
    }
    for index, paper in enumerate(papers):
        identifier = paper.get("id", "")
        if not identifier or identifier in seen_ids:
            problems.append(f"row {index}: missing or duplicate id {identifier!r}")
        seen_ids.add(identifier)
        if _title_is_suspicious(paper.get("title", "")):
            problems.append(f"row {index}: suspicious title {paper.get('title')!r}")
        if _title_needs_metadata(paper.get("title", "")) and not _title_has_verified_metadata(paper):
            problems.append(f"row {index}: unverified short title {paper.get('title')!r}")
        precision = paper.get("date_precision", "unknown")
        value = paper.get("published_at", "")
        pattern = date_patterns.get(precision)
        if pattern is None or not pattern.fullmatch(value):
            problems.append(f"row {index}: date {value!r} does not match precision {precision!r}")
        if paper.get("arxiv_id") and not {"arXiv", "Semantic Scholar", "arXiv identifier", "structured source"}.intersection(paper.get("metadata_sources", [])):
            problems.append(f"row {index}: arXiv id {paper['arxiv_id']} was not verified")
        if not paper.get("paper_url") or not paper.get("categories"):
            problems.append(f"row {index}: missing URL or category")
        if len(problems) >= 25:
            break
    if problems:
        raise RuntimeError("paper catalog quality check failed:\n" + "\n".join(problems))


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def save_outputs(out_dir: Path, papers: list[dict], sources: list[PaperSource], state: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    core_categories = sorted({source.category for source in sources if source.group == "Core Technology"})
    disciplines = sorted({source.category for source in sources if source.group == "Cross-disciplinary Applications"})
    payload = {
        "generated_at": generated_at,
        "source_count": len(sources),
        "paper_count": len(papers),
        "page_size": 200,
        "categories": {"core": core_categories, "disciplines": disciplines},
        "papers": papers,
    }
    (out_dir / "papers.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "papers_state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    columns = (
        "title", "venue", "published_at", "first_seen_at", "source_updated_at",
        "group", "category", "subcategories", "paper_url", "arxiv_id", "doi",
        "source_repos", "metadata_sources",
    )
    with (out_dir / "papers.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for paper in papers:
            row = {key: paper.get(key, "") for key in columns}
            for key in ("subcategories", "source_repos", "metadata_sources"):
                row[key] = " | ".join(row[key])
            writer.writerow(row)
