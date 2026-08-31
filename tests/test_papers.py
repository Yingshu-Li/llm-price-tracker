from datetime import date

from src.papers import (
    PaperSource,
    _title_is_suspicious,
    apply_verified_metadata_cache,
    apply_verified_metadata_overrides,
    filter_recent,
    merge_papers,
    parse_readme,
    validate_catalog,
    _crossref_metadata,
)


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
    assert papers[0]["title"] == "Reasoning Better"
    assert papers[0]["venue"].startswith("ACL")
    assert papers[0]["published_at"] == "2026-04"
    assert papers[0]["date_precision"] == "month"
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
        {"title": "New Paper", "published_at": f"{year}-01-01", "venue": "arXiv", "source_repos": ["a"]},
        {"title": "Old Paper", "published_at": f"{year - 2}-01-01", "venue": "arXiv", "source_repos": ["a"]},
    ]
    selected = filter_recent(papers, year - 1)
    assert [paper["title"] for paper in selected] == ["New Paper"]


def test_tool_link_with_paper_in_name_is_not_a_publication():
    readme = "- [paper2slides](https://github.com/example/paper2slides) turns papers into decks"
    assert parse_readme(SOURCE, readme, "2026-08-29", "2026-08-30") == []


def test_undated_entries_sort_after_dated_papers():
    papers = [
        {"title": "Undated Paper", "published_at": "", "first_seen_at": "2026-08-29", "venue": "", "source_repos": ["a"]},
        {"title": "Dated Paper", "published_at": "2025-01-01", "first_seen_at": "2025-01-02", "venue": "arXiv", "source_repos": ["a"]},
    ]
    selected = filter_recent(papers, 2025)
    assert [paper["title"] for paper in selected] == ["Dated Paper", "Undated Paper"]


def test_table_uses_paper_title_not_date_or_arxiv_label():
    readme = "2026-06-17 | SierpinskiCam: Camera-Controlled Video Retaking | [📄 arXiv](https://arxiv.org/abs/2606.17310)"
    papers = parse_readme(SOURCE, readme, "2026-06-18", "2026-06-18")
    assert len(papers) == 1
    assert papers[0]["title"] == "SierpinskiCam: Camera-Controlled Video Retaking"
    assert papers[0]["published_at"] == "2026-06"
    assert papers[0]["date_precision"] == "month"


def test_generic_visual_and_venue_labels_are_not_titles():
    readme = "- [image](https://arxiv.org/abs/2604.00001)\n- [arXiv 2026](https://arxiv.org/abs/2604.00002)\n- [2604.00003](https://arxiv.org/abs/2604.00003)"
    papers = parse_readme(SOURCE, readme, "2026-06-18", "2026-06-18")
    assert [paper["title"] for paper in papers] == ["", "", ""]


def test_merge_deduplicates_arxiv_and_publisher_links_by_title():
    arxiv = parse_readme(SOURCE, "[Same Reliable Paper](https://arxiv.org/abs/2604.12345)", "2026-04-20", "2026-04-20")[0]
    publisher = parse_readme(SOURCE, "[Same Reliable Paper](https://openaccess.thecvf.com/content/CVPR2026/html/example)", "2026-04-20", "2026-04-20")[0]
    merged = merge_papers([arxiv, publisher])
    assert len(merged) == 1
    assert merged[0]["arxiv_id"] == "2604.12345"


def test_openreview_button_is_not_a_title():
    papers = parse_readme(SOURCE, "[OpenReview](https://openreview.net/forum?id=abc123)", "2026-01-01", "2026-01-01")
    assert papers[0]["title"] == ""


def test_quality_gate_rejects_unverified_arxiv_and_invented_precision():
    bad = [{
        "id": "one",
        "title": "A Valid Looking Paper Title",
        "published_at": "2026-04-01",
        "date_precision": "month",
        "paper_url": "https://arxiv.org/abs/2604.12345",
        "arxiv_id": "2604.12345",
        "metadata_sources": ["source README"],
        "categories": ["Language"],
    }]
    try:
        validate_catalog(bad)
    except RuntimeError as exc:
        assert "does not match precision" in str(exc)
        assert "was not verified" in str(exc)
    else:
        raise AssertionError("quality gate should reject invalid catalog rows")


def test_one_table_row_emits_one_preferred_paper_link():
    readme = "[Reliable Vision Paper](https://openaccess.thecvf.com/content/CVPR2026/html/example.html) | Authors | [PDF](https://openaccess.thecvf.com/content/CVPR2026/papers/example.pdf) | [arXiv](https://arxiv.org/abs/2604.12345)"
    papers = parse_readme(SOURCE, readme, "2026-04-20", "2026-04-20")
    assert len(papers) == 1
    assert papers[0]["paper_url"] == "https://arxiv.org/abs/2604.12345"


def test_future_year_is_not_displayed():
    papers = [{
        "title": "A Plausible but Future-Dated Paper",
        "published_at": str(date.today().year + 1),
        "date_precision": "year",
        "venue": "CVPR",
        "source_repos": ["a"],
    }]
    assert filter_recent(papers, date.today().year - 1) == []


def test_verified_metadata_cache_replaces_source_placeholders():
    fresh = parse_readme(SOURCE, "[Authors Only](https://arxiv.org/abs/2604.12345)", "2026-04-20", "2026-04-20")
    cached = [{
        "arxiv_id": "2604.12345",
        "title": "Official Paper Title",
        "published_at": "2026-04-18",
        "date_precision": "day",
        "venue": "arXiv preprint",
        "metadata_sources": ["source README", "arXiv"],
    }]
    apply_verified_metadata_cache(fresh, cached)
    assert fresh[0]["title"] == "Official Paper Title"
    assert fresh[0]["published_at"] == "2026-04-18"
    assert "arXiv" in fresh[0]["metadata_sources"]


def test_semantic_scholar_is_accepted_as_arxiv_id_verification():
    paper = {
        "id": "verified",
        "title": "Official Paper Title from an arXiv Identifier",
        "published_at": "2026-04-18",
        "date_precision": "day",
        "paper_url": "https://arxiv.org/abs/2604.12345",
        "arxiv_id": "2604.12345",
        "metadata_sources": ["source README", "Semantic Scholar"],
        "categories": ["Language"],
    }
    validate_catalog([paper])


def test_month_precision_never_displays_an_invented_first_day():
    paper = {
        "title": "A Paper Known Only to the Month",
        "published_at": "2026-04-01",
        "date_precision": "month",
        "venue": "arXiv preprint",
        "source_repos": ["a"],
    }
    selected = filter_recent([paper], 2025)
    assert selected[0]["published_at"] == "2026-04"


def test_author_lists_and_embedded_resource_links_are_not_titles():
    assert _title_is_suspicious("Alice; Bob; Carol; Dave")
    assert _title_is_suspicious("[GitHub](https://github.com/example/code)")


def test_table_header_selects_title_instead_of_date_or_arxiv_id():
    readme = """| Icon | Title | Authors | Venue | Paper |
| --- | --- | --- | --- | --- |
| X | From Positionwise Confidence to Prefix Scheduling | A. Author | arXiv | [arXiv:2608.14787](https://arxiv.org/abs/2608.14787) |
"""
    papers = parse_readme(SOURCE, readme, "2026-08-22", "2026-08-22")
    assert papers[0]["title"] == "From Positionwise Confidence to Prefix Scheduling"


def test_model_table_uses_model_column_instead_of_lab_link():
    readme = """| Model | Date | Parameters | Organization | Links |
| --- | --- | --- | --- | --- |
| Cosmos 3 | 2026-05-31 | 64 B | NVIDIA | [NVIDIA Lab](https://research.nvidia.com/report.pdf) |
"""
    papers = parse_readme(SOURCE, readme, "2026-08-22", "2026-08-22")
    assert papers[0]["title"] == "Cosmos 3"


def test_multiline_website_link_inherits_previous_bold_title():
    readme = """**DreaMontage: Arbitrary Frame-Guided One-Shot Video Generation** \\
[[Website](https://arxiv.org/abs/2512.21252)]
[[Project](https://example.org/project)]
"""
    papers = parse_readme(SOURCE, readme, "2026-08-22", "2026-08-22")
    assert papers[0]["title"] == "DreaMontage: Arbitrary Frame-Guided One-Shot Video Generation"


def test_blockquote_project_link_is_not_a_second_paper():
    readme = """- **[PAWBench: A Physical AI Benchmark](https://arxiv.org/abs/2608.00001)** — Authors
  > arXiv 2026 · [🌐 project](https://arxiv.org/abs/2608.00002)
"""
    papers = parse_readme(SOURCE, readme, "2026-08-22", "2026-08-22")
    assert len(papers) == 1
    assert papers[0]["arxiv_id"] == "2608.00001"


def test_us_style_date_and_colon_arxiv_id_are_suspicious_titles():
    assert _title_is_suspicious("05/25/2026")
    assert _title_is_suspicious("arXiv:2608.14787")
    assert _title_is_suspicious("🌐 project")


def test_double_bracket_paper_link_inherits_plain_bullet_title():
    readme = """- Solving an Open Problem in Theoretical Physics using AI-Assisted Discovery
  Brenner, Cohen-Addad, and Woodruff
  arXiv, 2026. [[Paper]](http://arxiv.org/abs/2603.04735)
"""
    papers = parse_readme(SOURCE, readme, "2026-08-22", "2026-08-22")
    assert len(papers) == 1
    assert papers[0]["title"] == "Solving an Open Problem in Theoretical Physics using AI-Assisted Discovery"


def test_crossref_metadata_tolerates_null_date_parts():
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"title": ["Reliable Title"], "date-parts": [[None]]}}

    class Client:
        def get(self, *args, **kwargs):
            return Response()

    item = _crossref_metadata(Client(), "10.0000/example")
    assert item["title"] == "Reliable Title"
    assert item["published_at"] == ""


def test_filter_recovers_precision_from_a_complete_cached_date():
    paper = {
        "title": "A Reliably Titled Paper",
        "published_at": "2026-07-14",
        "date_precision": "unknown",
        "venue": "arXiv preprint",
        "source_repos": ["a"],
    }
    selected = filter_recent([paper], 2025)
    assert selected[0]["date_precision"] == "day"


def test_verified_source_override_replaces_model_or_lab_label():
    papers = [{
        "title": "InternVL-U (Shanghai AI Lab)",
        "paper_url": "https://arxiv.org/abs/2603.09877",
        "metadata_sources": ["source README"],
    }]
    apply_verified_metadata_overrides(papers)
    assert papers[0]["title"].startswith("InternVL-U: Democratizing Unified Multimodal Models")
    assert papers[0]["date_precision"] == "day"
    assert "verified source page" in papers[0]["metadata_sources"]


def test_verified_doi_override_dates_foundational_papers_before_recent_filter():
    papers = [{
        "title": "Perceptron",
        "paper_url": "https://doi.org/10.1037/h0042519",
        "published_at": "",
        "date_precision": "unknown",
        "metadata_sources": ["source README"],
    }]
    apply_verified_metadata_overrides(papers)
    assert papers[0]["published_at"] == "1958"
    assert papers[0]["title"].startswith("The Perceptron:")
    assert filter_recent(papers, 2025) == []


def test_bold_title_wins_over_summary_and_author_text_before_generic_link():
    readme = (
        "- **TraceCAD: Trace-Guided Repair for Agentic CAD Generation** — Preserves requirements and repair outcomes. "
        "*Authors, arXiv 2026*. [[2608.03062](https://arxiv.org/abs/2608.03062)]"
    )
    papers = parse_readme(SOURCE, readme, "2026-08-31", "2026-08-31")
    assert papers[0]["title"] == "TraceCAD: Trace-Guided Repair for Agentic CAD Generation"


def test_link_wrapped_title_discards_long_table_summary():
    readme = """| Paper | Link |
| --- | --- |
| **[PerceptUI: LLM Agents as Human-Aligned Synthetic Users](https://arxiv.org/pdf/2606.05697)** - A long explanatory summary that is not part of the title and should be removed. | [arXiv](https://arxiv.org/abs/2606.05697) |
"""
    papers = parse_readme(SOURCE, readme, "2026-08-31", "2026-08-31")
    assert papers[0]["title"] == "PerceptUI: LLM Agents as Human-Aligned Synthetic Users"


def test_external_slide_deck_is_not_collected_as_a_paper():
    readme = '- **"Practical LLM Security"** — Speaker. Slides: [PDF](https://example.org/talk.pdf)'
    assert parse_readme(SOURCE, readme, "2026-08-31", "2026-08-31") == []


def test_wrapping_quotes_are_removed_from_bold_paper_title():
    readme = '- **"SoK: AI Auditing and Accountability"** — Authors. [OpenReview](https://openreview.net/forum?id=abc)'
    papers = parse_readme(SOURCE, readme, "2026-08-31", "2026-08-31")
    assert papers[0]["title"] == "SoK: AI Auditing and Accountability"


def test_wrapped_bold_title_is_joined_before_parsing_link():
    readme = """- **EchoX: Towards Mitigating Acoustic-Semantic Gap via Echo Training for
  Speech-to-Speech LLMs**, `arXiv, 2509.09174`, [arxiv](http://arxiv.org/abs/2509.09174v1)
"""
    papers = parse_readme(SOURCE, readme, "2026-08-31", "2026-08-31")
    assert papers[0]["title"] == "EchoX: Towards Mitigating Acoustic-Semantic Gap via Echo Training for Speech-to-Speech LLMs"


def test_parenthesized_generic_link_does_not_leave_venue_fragment():
    readme = "- Trend-Heuristic Reinforcement Learning for Portfolio Management, *ICASSP'24* ([Paper](https://ieeexplore.ieee.org/stamp/stamp.jsp?arnumber=1))"
    papers = parse_readme(SOURCE, readme, "2026-08-31", "2026-08-31")
    assert papers[0]["title"] == "Trend-Heuristic Reinforcement Learning for Portfolio Management"
    assert papers[0]["published_at"] == "2024"
    assert filter_recent(papers, 2025) == []


def test_historical_year_is_extracted_so_old_doi_is_not_treated_as_undated():
    readme = "| Foundational work | [Perceptron](https://doi.org/10.1037/h0042519) | 1958 |"
    papers = parse_readme(SOURCE, readme, "2026-08-31", "2026-08-31")
    assert papers[0]["published_at"] == "1958"
    assert filter_recent(papers, 2025) == []
