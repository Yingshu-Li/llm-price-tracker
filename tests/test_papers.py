from datetime import date

from src.papers import PaperSource, filter_recent, merge_papers, parse_readme


SOURCE = PaperSource(
    group="Core Technology",
    category="Language",
    subcategory="Reasoning",
    repository="example/papers",
    url="https://github.com/example/papers",
)


def test_parse_markdown_paper_with_source_metadata():
    readme = "- **Reasoning Better** — ACL 2026, [Paper](https://arxiv.org/abs/2604.12345)"
    papers = parse_readme(SOURCE, readme, "2026-08-29", "2026-08-30")
    assert len(papers) == 1
    assert papers[0]["title"] == "Reasoning Better — ACL 2026"
    assert papers[0]["venue"].startswith("ACL")
    assert papers[0]["published_at"] == "2026-04-01"
    assert papers[0]["arxiv_id"] == "2604.12345"


def test_merge_uses_arxiv_id_and_preserves_all_sources():
    first = parse_readme(SOURCE, "[A Paper](https://arxiv.org/abs/2601.00001)", "2026-08-01", "2026-08-02")[0]
    second_source = PaperSource("Cross-disciplinary Applications", "Medicine & Healthcare", "Medical LLM", "other/list", "https://github.com/other/list")
    second = parse_readme(second_source, "[A Paper](https://arxiv.org/abs/2601.00001)", "2026-08-03", "2026-08-04")[0]
    merged = merge_papers([first, second])
    assert len(merged) == 1
    assert merged[0]["categories"] == ["Language", "Medicine & Healthcare"]
    assert merged[0]["source_repos"] == ["example/papers", "other/list"]
    assert merged[0]["first_seen_at"] == "2026-08-02"


def test_recent_filter_keeps_current_and_previous_year():
    year = date.today().year
    papers = [
        {"title": "New", "published_at": f"{year}-01-01", "venue": "arXiv", "source_repos": ["a"]},
        {"title": "Old", "published_at": f"{year - 2}-01-01", "venue": "arXiv", "source_repos": ["a"]},
    ]
    selected = filter_recent(papers, year - 1)
    assert [paper["title"] for paper in selected] == ["New"]


def test_tool_link_with_paper_in_name_is_not_a_publication():
    readme = "- [paper2slides](https://github.com/example/paper2slides) turns papers into decks"
    assert parse_readme(SOURCE, readme, "2026-08-29", "2026-08-30") == []


def test_undated_entries_sort_after_dated_papers():
    papers = [
        {"title": "Undated", "published_at": "", "first_seen_at": "2026-08-29", "venue": "", "source_repos": ["a"]},
        {"title": "Dated", "published_at": "2025-01-01", "first_seen_at": "2025-01-02", "venue": "arXiv", "source_repos": ["a"]},
    ]
    selected = filter_recent(papers, 2025)
    assert [paper["title"] for paper in selected] == ["Dated", "Undated"]
