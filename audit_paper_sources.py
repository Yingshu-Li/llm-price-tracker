from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date
from pathlib import Path

import httpx

from src.papers import (
    _title_is_suspicious,
    filter_recent,
    load_sources,
    load_json,
    parse_readme,
    source_readme,
    source_updated_at,
)


ROOT = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit every configured paper source and its README layout.")
    parser.add_argument("--sources", type=Path, default=ROOT / "config" / "paper_sources.csv")
    parser.add_argument("--since-year", type=int, default=date.today().year - 1)
    parser.add_argument("--json", type=Path, help="Optional JSON report path.")
    parser.add_argument("--offline", action="store_true", help="Build the report from papers_state.json and papers.json.")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "out")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    sources = load_sources(args.sources)
    if args.offline:
        payload = load_json(args.out_dir / "papers.json", {"papers": []})
        state = load_json(args.out_dir / "papers_state.json", {"sources": {}})
        output_counts = Counter(
            repository
            for paper in payload.get("papers", [])
            for repository in paper.get("source_repos", [])
        )
        reports = []
        for source in sources:
            source_state = state.get("sources", {}).get(source.repository, {})
            candidate_count = source_state.get("candidate_count", 0)
            output_count = output_counts[source.repository]
            status = "ok"
            if candidate_count == 0:
                status = "no_extractable_papers"
            elif output_count == 0:
                status = "no_recent_output"
            reports.append({
                "repository": source.repository,
                "category": source.category,
                "subcategory": source.subcategory,
                "source_updated_at": source_state.get("source_updated_at", ""),
                "last_checked_at": source_state.get("last_checked_at", ""),
                "candidate_count": candidate_count,
                "output_count": output_count,
                "status": status,
            })
        if args.json:
            args.json.parent.mkdir(parents=True, exist_ok=True)
            args.json.write_text(json.dumps(reports, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for report in reports:
            print(
                f"{report['status']:<24} {report['category']:<24} {report['repository']:<52} "
                f"candidates={report['candidate_count']:5d} output={report['output_count']:5d}"
            )
        return 0
    reports: list[dict] = []
    headers = {
        "User-Agent": "llm-price-tracker-paper-source-audit/1.0",
        "Accept": "text/plain, application/atom+xml",
    }
    with httpx.Client(headers=headers, timeout=35, follow_redirects=True) as client:
        for index, source in enumerate(sources, start=1):
            try:
                readme, readme_url = source_readme(client, source.repository)
                updated_at = source_updated_at(client, source.repository)
                parsed = parse_readme(source, readme, updated_at, date.today().isoformat())
                blank_titles = sum(not paper.get("title") for paper in parsed)
                suspicious_titles = [
                    paper.get("title", "") for paper in parsed
                    if paper.get("title") and _title_is_suspicious(paper.get("title", ""))
                ]
                recent = filter_recent([dict(paper) for paper in parsed], args.since_year)
                report = {
                    "repository": source.repository,
                    "category": source.category,
                    "subcategory": source.subcategory,
                    "readme_url": readme_url,
                    "source_updated_at": updated_at,
                    "candidate_count": len(parsed),
                    "recent_before_enrichment": len(recent),
                    "blank_title_count": blank_titles,
                    "suspicious_title_count": len(suspicious_titles),
                    "recent_years": dict(Counter(
                        paper.get("published_at", "")[:4] or "undated" for paper in recent
                    )),
                    "status": "ok",
                }
            except Exception as exc:
                report = {
                    "repository": source.repository,
                    "category": source.category,
                    "subcategory": source.subcategory,
                    "status": "error",
                    "error": str(exc),
                }
            reports.append(report)
            print(
                f"[{index:02d}/{len(sources)}] {report['status']:<5} "
                f"{source.category:<24} {source.repository:<52} "
                f"candidates={report.get('candidate_count', 0):5d} "
                f"recent={report.get('recent_before_enrichment', 0):4d} "
                f"blank={report.get('blank_title_count', 0):4d}"
            )
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(reports, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 1 if any(report["status"] == "error" for report in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
