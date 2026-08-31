from __future__ import annotations

import argparse
import hashlib
from datetime import date
from pathlib import Path

import httpx

from src.papers import (
    apply_verified_metadata_cache,
    apply_verified_metadata_overrides,
    enrich_missing_metadata,
    filter_recent,
    load_json,
    load_sources,
    merge_papers,
    parse_readme,
    paper_key,
    save_outputs,
    source_readme,
    source_updated_at,
    validate_catalog,
)


ROOT = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect newly listed AI papers from curated GitHub sources.")
    parser.add_argument("--sources", type=Path, default=ROOT / "config" / "paper_sources.csv")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "out")
    parser.add_argument("--since-year", type=int, default=date.today().year - 1)
    parser.add_argument("--max-enrich", type=int, default=25000)
    parser.add_argument("--force", action="store_true", help="Reparse sources whose README hash has not changed.")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild the catalog without carrying forward older collected rows.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    sources = load_sources(args.sources)
    cached_payload = load_json(args.out_dir / "papers.json", {"papers": []})
    cached_papers = cached_payload.get("papers", [])
    previous_papers = [] if args.rebuild else cached_papers
    previous_payload = {"papers": previous_papers}
    previous_by_key = {paper_key(paper): paper for paper in cached_papers}
    state = {"sources": {}} if args.rebuild else load_json(args.out_dir / "papers_state.json", {"sources": {}})
    state.setdefault("sources", {})
    today = date.today().isoformat()
    collected = list(previous_papers)
    failures: list[str] = []

    headers = {
        "User-Agent": "llm-price-tracker-paper-collector/1.0 (+https://github.com/Yingshu-Li/llm-price-tracker)",
        "Accept": "text/plain, application/atom+xml, application/json",
    }
    with httpx.Client(headers=headers, timeout=35, follow_redirects=True) as client:
        for index, source in enumerate(sources, start=1):
            try:
                readme, readme_url = source_readme(client, source.repository)
                digest = hashlib.sha256(readme.encode()).hexdigest()
                old = state["sources"].get(source.repository, {})
                if not args.force and old.get("readme_sha256") == digest:
                    print(f"[{index:02d}/{len(sources)}] unchanged {source.repository}")
                    continue
                updated_at = source_updated_at(client, source.repository)
                parsed = parse_readme(source, readme, updated_at, today)
                for paper in parsed:
                    old_paper = previous_by_key.get(paper_key(paper))
                    if old_paper:
                        paper["first_seen_at"] = old_paper.get("first_seen_at", today)
                collected.extend(parsed)
                state["sources"][source.repository] = {
                    "readme_sha256": digest,
                    "readme_url": readme_url,
                    "source_updated_at": updated_at,
                    "last_checked_at": today,
                    "candidate_count": len(parsed),
                }
                print(f"[{index:02d}/{len(sources)}] {source.repository}: {len(parsed)} candidates")
            except Exception as exc:
                failures.append(f"{source.repository}: {exc}")
                print(f"[{index:02d}/{len(sources)}] warning {source.repository}: {exc}")

        merged = merge_papers(collected)
        apply_verified_metadata_cache(merged, cached_papers)
        apply_verified_metadata_overrides(merged)
        enrich_missing_metadata(client, merged, max_items=args.max_enrich)
        merged = merge_papers(merged)
        apply_verified_metadata_overrides(merged)

    recent = filter_recent(merged, args.since_year)
    validate_catalog(recent)
    state["last_run_at"] = today
    state["failures"] = failures
    state["since_year"] = args.since_year
    save_outputs(args.out_dir, recent, sources, state)
    print(f"Saved {len(recent)} papers from {len(sources)} sources; failures={len(failures)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
