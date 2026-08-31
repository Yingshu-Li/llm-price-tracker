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
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)(?:\s+[^)]*)?\)")
RELATIVE_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\((?!https?://)([^)#?]+\.md)(?:#[^)]*)?\)", re.I)
REFERENCE_DEF_RE = re.compile(r"^\s*\[([^\]]+)\]:\s*(https?://\S+)\s*$", re.I | re.M)
REFERENCE_LINK_RE = re.compile(r"\[([^\]]+)\]\[([^\]]+)\]")
HTML_LINK_RE = re.compile(r"<a\s+[^>]*href=[\"'](https?://[^\"']+)[\"'][^>]*>(.*?)</a>", re.I)
DATE_RE = re.compile(r"(?<!\d)(20(?:1\d|2\d))[./-](0?[1-9]|1[0-2])(?:[./-](0?[1-9]|[12]\d|3[01]))?(?!\d)")
YEAR_RE = re.compile(r"(?<!\d)(20(?:1\d|2\d))(?!\d)")
DATE_ONLY_TITLE_RE = re.compile(r"^20\d{2}(?:[-/.]\d{1,2})?(?:[-/.]\d{1,2})?$")
VENUE_ONLY_TITLE_RE = re.compile(
    r"^(?:arxiv|openreview|nature|science|cvpr|iccv|eccv|acl|emnlp|naacl|eacl|iclr|icml|neurips|aaai|ijcai)"
    r"(?:\s*[:#-]?\s*20\d{2})?$",
    re.I,
)

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
    "pubmed.ncbi.nlm.nih.gov", "ojs.aaai.org", "journals.", "jmlr.org",
    "openaccess.thecvf.com", "papers.nips.cc", "aaai.org/papers",
)
GENERIC_LABELS = {
    "paper", "pdf", "arxiv", "preprint", "publication", "link", "论文",
    "article", "project", "homepage", "read", "official paper",
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


def source_readme(client: httpx.Client, repository: str) -> tuple[str, str]:
    for name in ("README.md", "readme.md", "README.MD", "Readme.md"):
        url = f"https://github.com/{repository}/raw/HEAD/{name}"
        response = client.get(url)
        if response.status_code == 200 and len(response.text) > 30:
            readme = response.text
            extra_documents: list[str] = []
            seen_paths: set[str] = set()
            for raw_path in RELATIVE_MARKDOWN_LINK_RE.findall(readme):
                path = urllib.parse.unquote(raw_path).replace("\\", "/").lstrip("./")
                if not path or path.casefold().startswith("readme") or path in seen_paths or ".." in path.split("/"):
                    continue
                seen_paths.add(path)
                extra_url = f"https://github.com/{repository}/raw/HEAD/{urllib.parse.quote(path, safe='/')}"
                extra = client.get(extra_url)
                if extra.status_code == 200 and len(extra.text) > 30:
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
    value = re.sub(r"^\d+[.)]\s*", "", value)
    value = re.sub(r"\s+", " ", value).strip(" |:;,-–—")
    if value.startswith("[") and not value.startswith("[\""):
        value = value[1:].strip()
    if value.endswith("]"):
        value = value[:-1].strip()
    return value


def _link_label_is_generic(label: str) -> bool:
    return _title_is_suspicious(_strip_markdown(label))


def _title_is_suspicious(title: str) -> bool:
    normalized = _strip_markdown(title)
    folded = normalized.casefold()
    if len(normalized) < 7 or folded in GENERIC_LABELS:
        return True
    if normalized.count(";") >= 3:
        return True
    if re.search(r"\]\(https?://", normalized, re.I) or re.match(r"^[^]]{1,30}\]\s+", normalized):
        return True
    if DATE_ONLY_TITLE_RE.fullmatch(normalized) or VENUE_ONLY_TITLE_RE.fullmatch(normalized):
        return True
    if re.fullmatch(r"(?:image|images|figure|fig\.?|thumbnail|screenshot|图片|图像?|插图)(?:\s*\d+)?", normalized, re.I):
        return True
    if re.search(r"\.(?:png|jpe?g|gif|svg|webp)$", normalized, re.I):
        return True
    if re.fullmatch(r"\[?(?:arxiv\s*)?\d{4}\.\d{4,5}(?:v\d+)?\]?", normalized, re.I):
        return True
    return False


def _looks_like_paper(label: str, url: str, line: str) -> bool:
    lowered_url = url.casefold()
    lowered_label = _strip_markdown(label).casefold()
    if any(domain in lowered_url for domain in PAPER_DOMAINS):
        return True
    if urllib.parse.urlsplit(url).path.casefold().endswith(".pdf"):
        return not any(domain in lowered_url for domain in ("github.com", "huggingface.co"))
    return False


def _title_from_line(line: str, label: str, match_start: int) -> str:
    if not _link_label_is_generic(label):
        return _strip_markdown(label)

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
    before = MARKDOWN_LINK_RE.sub(lambda item: item.group(1), before)
    before = _strip_markdown(before)
    before = re.sub(r"^(?:\[[^]]+\]|\([^)]*\))\s*", "", before)
    return before[-300:].strip(" |:;,-–—")


def _extract_date(text: str) -> tuple[str, str]:
    arxiv = ARXIV_RE.search(text)
    if arxiv:
        prefix = arxiv.group("id")[:2]
        month = arxiv.group("id")[2:4]
        return f"20{prefix}-{month}", "month"
    proceedings = re.search(r"(?:content|proceedings)/(?:CVPR|ICCV|ECCV)[_-]?(20\d{2})", text, re.I)
    if proceedings:
        return proceedings.group(1), "year"
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
    for raw_line in readme.splitlines():
        if len(raw_line) > 4000:
            continue
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
        ]
        if not paper_links:
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
            title = _title_from_line(raw_line, label, start)
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
    return papers


def merge_papers(papers: Iterable[dict]) -> list[dict]:
    merged: list[dict] = []
    alias_to_index: dict[str, int] = {}
    for paper in papers:
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


def _arxiv_metadata(client: httpx.Client, ids: list[str]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    namespace = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    # arXiv supports large id_list batches; using 400 keeps a full rebuild
    # practical while retaining the documented delay between API requests.
    batch_size = 400
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
        response = client.post(
            "https://api.semanticscholar.org/graph/v1/paper/batch",
            params={"fields": "title,publicationDate,venue,externalIds"},
            json={"ids": [f"ARXIV:{identifier}" for identifier in batch]},
        )
        response.raise_for_status()
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
    parts = ((message.get("published-online") or message.get("published-print") or message.get("issued") or {}).get("date-parts") or [[]])[0]
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


def enrich_missing_metadata(client: httpx.Client, papers: list[dict], max_items: int = 25000) -> None:
    needs = [
        paper for paper in papers
        if not paper.get("title")
        or _title_is_suspicious(paper.get("title", ""))
        or not paper.get("venue")
        or not paper.get("published_at")
        or (paper.get("arxiv_id") and paper.get("date_precision") != "day")
        or (paper.get("arxiv_id") and not {"arXiv", "Semantic Scholar"}.intersection(paper.get("metadata_sources", [])))
        or (paper.get("doi") and "Crossref title" not in paper.get("metadata_sources", []))
    ]
    arxiv_ids = list(dict.fromkeys(paper["arxiv_id"] for paper in needs if paper.get("arxiv_id")))[:max_items]
    if arxiv_ids:
        try:
            metadata = _semantic_scholar_arxiv_metadata(client, arxiv_ids)
            missing_ids = [identifier for identifier in arxiv_ids if identifier not in metadata]
            if missing_ids:
                metadata.update(_arxiv_metadata(client, missing_ids))
            for paper in needs:
                item = metadata.get(paper.get("arxiv_id", ""))
                if not item:
                    continue
                if item.get("title"):
                    paper["title"] = item["title"]
                if item.get("venue") and not paper.get("venue"):
                    paper["venue"] = item["venue"]
                if item.get("published_at") and (not paper.get("published_at") or paper.get("date_precision") != "day"):
                    paper["published_at"] = item["published_at"]
                    paper["date_precision"] = item["date_precision"]
                if "Semantic Scholar" not in paper["metadata_sources"]:
                    paper["metadata_sources"].append("Semantic Scholar")
        except Exception as exc:
            print(f"warning: scholarly metadata enrichment failed: {exc}")

    remaining = max(0, max_items - len(arxiv_ids))
    for paper in [item for item in needs if item.get("doi")][:remaining]:
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
        precision = paper.get("date_precision", "unknown")
        value = paper.get("published_at", "")
        pattern = date_patterns.get(precision)
        if pattern is None or not pattern.fullmatch(value):
            problems.append(f"row {index}: date {value!r} does not match precision {precision!r}")
        if paper.get("arxiv_id") and not {"arXiv", "Semantic Scholar", "arXiv identifier"}.intersection(paper.get("metadata_sources", [])):
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
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for paper in papers:
            row = {key: paper.get(key, "") for key in columns}
            for key in ("subcategories", "source_repos", "metadata_sources"):
                row[key] = " | ".join(row[key])
            writer.writerow(row)
