from __future__ import annotations

import argparse
import hashlib
from datetime import date
from pathlib import Path

import httpx

from src.papers import (
    SOURCE_STRUCTURED_FEEDS,
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


def source_failure_verdict(failure_count: int, source_count: int) -> tuple[bool, int]:
    """判断这次运行算不算失败，返回 (是否失败, 容忍上限)。

    单个源抓不到（改名、删库、转私有、临时 5xx）只该少几篇论文，不该让当天
    整份目录提交不上去。但如果大批源同时失败，那多半是网络或鉴权出了系统性
    问题，这时提交上去的目录是残缺的，宁可失败也不能覆盖掉好数据。

    阈值取「十分之一，且至少 3 个」：源少的时候不至于一个失败就超标，
    源多的时候也不会把半个目录的缺失当成正常。
    """
    tolerated = max(3, source_count // 10)
    return failure_count > tolerated, tolerated


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
                if source.repository in SOURCE_STRUCTURED_FEEDS:
                    for paper in parsed:
                        paper["metadata_sources"] = ["structured source"]
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

    # 失败必须看得见：打成 Actions 注解顶到运行摘要，别只躺在日志里等人翻。
    for failure in failures:
        print(f"::warning title=论文源抓取失败::{failure}")

    run_failed, tolerated = source_failure_verdict(len(failures), len(sources))
    if run_failed:
        print(
            f"::error title=论文源大面积失败::{len(failures)}/{len(sources)} 个源抓取失败，"
            f"超过容忍上限 {tolerated}，本次目录不提交。"
        )
        return 1
    if failures:
        print(
            f"{len(failures)}/{len(sources)} 个源抓取失败（容忍上限 {tolerated}），"
            "其余照常入库并提交；失败清单见 out/papers_state.json 的 failures 字段。"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
