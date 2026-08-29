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
            sources.append(PaperSource(
                group=current_group,
                category=(row.get("PrimaryCategory") or "Other").strip(),
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
    return value


def _link_label_is_generic(label: str) -> bool:
    normalized = _strip_markdown(label).casefold()
    return normalized in GENERIC_LABELS or len(normalized) < 5


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
        cells = [_strip_markdown(cell) for cell in line.split("|")]
        link_cell = next((index for index, cell in enumerate(cells) if label in cell), -1)
        for index in range(link_cell - 1, -1, -1):
            candidate = cells[index]
            if len(candidate) >= 8 and not re.fullmatch(r"20\d{2}(?:[./-]\d{1,2})?", candidate):
                return candidate

    before = line[:match_start]
    before = MARKDOWN_LINK_RE.sub(lambda item: item.group(1), before)
    before = _strip_markdown(before)
    before = re.sub(r"^(?:\[[^]]+\]|\([^)]*\))\s*", "", before)
    return before[-300:].strip(" |:;,-–—")


def _extract_date(text: str) -> tuple[str, str]:
    match = DATE_RE.search(text)
    if match:
        year, month, day = match.groups()
        return f"{year}-{int(month):02d}-{int(day or 1):02d}", "day" if day else "month"
    arxiv = ARXIV_RE.search(text)
    if arxiv:
        prefix = arxiv.group("id")[:2]
        month = arxiv.group("id")[2:4]
        return f"20{prefix}-{month}-01", "month"
    match = YEAR_RE.search(text)
    if match:
        return f"{match.group(1)}-01-01", "year"
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
        for label, raw_url, start in links:
            url = raw_url.rstrip(".,;:)")
            if url in seen_urls or not _looks_like_paper(label, url, raw_line):
                continue
            seen_urls.add(url)
            title = _title_from_line(raw_line, label, start)
            if len(title) < 7 or title.casefold() in GENERIC_LABELS:
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
    merged: dict[str, dict] = {}
    for paper in papers:
        key = paper_key(paper)
        if key not in merged:
            copy = dict(paper)
            copy["id"] = hashlib.sha1(key.encode()).hexdigest()[:16]
            merged[key] = copy
            continue
        current = merged[key]
        for field in ("categories", "subcategories", "source_repos", "source_urls", "metadata_sources"):
            current[field] = list(dict.fromkeys([*current.get(field, []), *paper.get(field, [])]))
        for field in ("title", "venue", "published_at", "date_precision", "arxiv_id", "doi"):
            if not current.get(field) and paper.get(field):
                current[field] = paper[field]
        current["source_updated_at"] = max(current.get("source_updated_at", ""), paper.get("source_updated_at", ""))
        current["first_seen_at"] = min(current.get("first_seen_at", "9999-12-31"), paper.get("first_seen_at", "9999-12-31"))
    return list(merged.values())


def _arxiv_metadata(client: httpx.Client, ids: list[str]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    namespace = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    for offset in range(0, len(ids), 50):
        batch = ids[offset:offset + 50]
        response = client.get("https://export.arxiv.org/api/query", params={"id_list": ",".join(batch)})
        response.raise_for_status()
        root = ET.fromstring(response.content)
        for entry in root.findall("a:entry", namespace):
            identifier = (entry.findtext("a:id", default="", namespaces=namespace).rsplit("/", 1)[-1]).split("v", 1)[0]
            result[identifier] = {
                "title": re.sub(r"\s+", " ", entry.findtext("a:title", default="", namespaces=namespace)).strip(),
                "published_at": entry.findtext("a:published", default="", namespaces=namespace)[:10],
                "venue": (entry.findtext("arxiv:journal_ref", default="", namespaces=namespace) or "arXiv preprint").strip(),
            }
        if offset + 50 < len(ids):
            time.sleep(3)
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
    if parts:
        published = f"{parts[0]:04d}-{(parts[1] if len(parts) > 1 else 1):02d}-{(parts[2] if len(parts) > 2 else 1):02d}"
    return {"title": title, "venue": venue, "published_at": published}


def enrich_missing_metadata(client: httpx.Client, papers: list[dict], max_items: int = 400) -> None:
    needs = [paper for paper in papers if not paper.get("title") or not paper.get("venue") or not paper.get("published_at")]
    arxiv_ids = list(dict.fromkeys(paper["arxiv_id"] for paper in needs if paper.get("arxiv_id")))[:max_items]
    if arxiv_ids:
        try:
            metadata = _arxiv_metadata(client, arxiv_ids)
            for paper in needs:
                item = metadata.get(paper.get("arxiv_id", ""))
                if not item:
                    continue
                for field in ("title", "venue", "published_at"):
                    if not paper.get(field) and item.get(field):
                        paper[field] = item[field]
                if "arXiv" not in paper["metadata_sources"]:
                    paper["metadata_sources"].append("arXiv")
        except Exception:
            pass

    remaining = max(0, max_items - len(arxiv_ids))
    for paper in [item for item in needs if item.get("doi")][:remaining]:
        try:
            item = _crossref_metadata(client, paper["doi"])
            for field in ("title", "venue", "published_at"):
                if not paper.get(field) and item.get(field):
                    paper[field] = item[field]
            if "Crossref" not in paper["metadata_sources"]:
                paper["metadata_sources"].append("Crossref")
        except Exception:
            continue


def filter_recent(papers: Iterable[dict], since_year: int, max_without_date: int = 50) -> list[dict]:
    selected: list[dict] = []
    undated_by_source: dict[str, int] = {}
    for paper in papers:
        published = paper.get("published_at", "")
        if published and published[:4].isdigit():
            if int(published[:4]) < since_year:
                continue
        elif not published:
            source = (paper.get("source_repos") or ["unknown"])[0]
            count = undated_by_source.get(source, 0)
            if count >= max_without_date:
                continue
            undated_by_source[source] = count + 1
        if not paper.get("title"):
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
