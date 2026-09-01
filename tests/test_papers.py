from datetime import date

import httpx

import src.papers as papers_module

from src.papers import (
    PaperSource,
    _structured_papers_to_markdown,
    _title_from_cvf_url,
    _title_from_html,
    _title_is_suspicious,
    apply_verified_metadata_cache,
    apply_verified_metadata_overrides,
    filter_recent,
    merge_papers,
    parse_readme,
    validate_catalog,
    _crossref_metadata,
    _arxiv_metadata,
    enrich_missing_metadata,
)


SOURCE = PaperSource(
    group="Core Technology",
    category="Language",
    subcategory="Reasoning",
    repository="example/papers",
    url="https://github.com/example/papers",
)


def test_structured_feed_uses_title_field_instead_of_authors():
    markdown = _structured_papers_to_markdown([{
        "title": "CrossVL: Complexity-Aware Feature Routing",
        "authors": "Zhipeng Liu; Chunbo Luo",
        "arxiv": "https://arxiv.org/abs/2605.09802",
    }], "firetix/awesome-cvpr-2026-papers")
    papers = parse_readme(SOURCE, markdown, "2026-06-08", "2026-08-31")
    assert papers[0]["title"] == "CrossVL: Complexity-Aware Feature Routing"
    assert "Zhipeng Liu" not in papers[0]["title"]
    assert papers[0]["venue"] == "arXiv"


def test_audio_hub_structured_feed_uses_complete_title_not_abbreviation():
    markdown = _structured_papers_to_markdown([{
        "Abbreviation": "ACA-SER",
        "Title": "Acoustic Cue Alignment in Audio Language Models for Speech Emotion Recognition",
        "Time": "2026-06",
        "Paper_Link": "https://arxiv.org/abs/2606.07309",
    }], "AudioLLMs/Awesome-Audio-LLM")
    papers = parse_readme(SOURCE, markdown, "2026-08-24", "2026-08-31")
    assert papers[0]["title"] == "Acoustic Cue Alignment in Audio Language Models for Speech Emotion Recognition"
    assert papers[0]["published_at"] == "2026-06"


def test_structured_title_replaces_an_earlier_unverified_readme_title():
    common = {
        "paper_url": "https://arxiv.org/abs/2605.09802",
        "arxiv_id": "2605.09802",
        "doi": "",
        "venue": "CVPR 2026",
        "published_at": "2026-05",
        "date_precision": "month",
        "categories": ["Vision"],
        "subcategories": ["General"],
        "source_urls": ["https://github.com/example/source"],
    }
    merged = merge_papers([
        {**common, "title": "Zhipeng Liu; Chunbo Luo", "source_repos": ["example/readme"], "metadata_sources": ["source README"]},
        {**common, "title": "CrossVL: Complexity-Aware Feature Routing", "source_repos": ["example/structured"], "metadata_sources": ["structured source"]},
    ])
    assert merged[0]["title"] == "CrossVL: Complexity-Aware Feature Routing"


def test_resource_badges_are_removed_from_a_paper_title():
    readme = "- **VideoWeaver: Evaluating Skills 📄 arXiv 💻 Code ⭐ 7** [Paper](https://arxiv.org/abs/2606.08091)"
    papers = parse_readme(SOURCE, readme, "2026-06-08", "2026-08-31")
    assert papers[0]["title"] == "VideoWeaver: Evaluating Skills"


def test_merge_normalizes_cached_resource_badges():
    paper = {
        "title": "PhyCo: Learning Controllable Physical Priors 📄 arXiv 🌐 Homepage",
        "paper_url": "https://arxiv.org/abs/2606.00001",
        "arxiv_id": "2606.00001",
        "categories": ["Video"],
        "subcategories": ["General"],
        "source_repos": ["example/video"],
        "source_urls": ["https://github.com/example/video"],
        "metadata_sources": ["source README"],
    }
    assert merge_papers([paper])[0]["title"] == "PhyCo: Learning Controllable Physical Priors"


def test_conference_link_uses_preceding_bold_paper_title():
    readme = """**ConsistEdit: Highly Consistent and Precise Training-free Visual Editing** \\
[[SIGGRAPH Asia 2025](https://arxiv.org/abs/2510.17803)]
[[Project](https://example.org/consistedit)]
**OverLayBench: A Benchmark for Layout-to-Image Generation with Dense Overlaps** \\
[[NeurIPS DB 2025](https://arxiv.org/abs/2509.19282)]
"""
    papers = parse_readme(SOURCE, readme, "2026-06-08", "2026-08-31")
    assert [paper["title"] for paper in papers] == [
        "ConsistEdit: Highly Consistent and Precise Training-free Visual Editing",
        "OverLayBench: A Benchmark for Layout-to-Image Generation with Dense Overlaps",
    ]


def test_official_html_citation_title_is_preferred_over_page_chrome():
    document = '<html><head><meta name="citation_title" content="A Complete Paper Title for Testing"><title>Short | Publisher</title></head></html>'
    assert _title_from_html(document) == "A Complete Paper Title for Testing"


def test_cvf_filename_recovers_full_title_from_conference_link():
    url = "https://openaccess.thecvf.com/content/CVPR2026W/DG-EBF/papers/Ullah_Teresa_Uncertainty-Aware_Generalizable_Chest_X-ray_Report_Generation_and_Disease_Classification_CVPRW_2026_paper.pdf"
    assert _title_from_cvf_url(url) == "Teresa Uncertainty-Aware Generalizable Chest X-ray Report Generation and Disease Classification"


def test_domain_and_method_year_venue_labels_are_suspicious():
    assert _title_is_suspicious("nature.com")
    assert _title_is_suspicious("SiMGR | 2026 | AAAI")
    assert _title_is_suspicious("WACV'26")
    assert _title_is_suspicious("Workshop")
    assert _title_is_suspicious("TPAMI'26")
    assert _title_is_suspicious("IEEE TVCG")
    assert _title_is_suspicious("arXiv cs.CV")
    assert _title_is_suspicious("arXiv:ID")
    assert _title_is_suspicious("���� corrupted title")


def test_unverified_short_label_is_not_displayed_as_a_paper_title():
    paper = {
        "title": "VoxCPM2",
        "published_at": "2026-06",
        "date_precision": "month",
        "venue": "arXiv preprint",
        "source_repos": ["example/list"],
        "metadata_sources": ["source README"],
    }
    assert filter_recent([paper], 2025) == []


def test_parse_markdown_paper_with_source_metadata():
    readme = "- **Reasoning Better** — ACL 2026, [Paper](https://arxiv.org/abs/2604.12345)"
    papers = parse_readme(SOURCE, readme, "2026-08-29", "2026-08-30")
    assert len(papers) == 1
    assert papers[0]["title"] == "Reasoning Better"
    assert papers[0]["venue"] == "arXiv"
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
        {"title": "New Paper", "published_at": f"{year}-01-01", "venue": "arXiv", "source_repos": ["a"], "metadata_sources": ["arXiv"]},
        {"title": "Old Paper", "published_at": f"{year - 2}-01-01", "venue": "arXiv", "source_repos": ["a"], "metadata_sources": ["arXiv"]},
    ]
    selected = filter_recent(papers, year - 1)
    assert [paper["title"] for paper in selected] == ["New Paper"]


def test_tool_link_with_paper_in_name_is_not_a_publication():
    readme = "- [paper2slides](https://github.com/example/paper2slides) turns papers into decks"
    assert parse_readme(SOURCE, readme, "2026-08-29", "2026-08-30") == []


def test_undated_entries_sort_after_dated_papers():
    papers = [
        {"title": "Undated Paper", "published_at": "", "first_seen_at": "2026-08-29", "venue": "", "source_repos": ["a"], "metadata_sources": ["Crossref title"]},
        {"title": "Dated Paper", "published_at": "2025-01-01", "first_seen_at": "2025-01-02", "venue": "arXiv", "source_repos": ["a"], "metadata_sources": ["arXiv"]},
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
        "venue": "arXiv",
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


def test_arxiv_link_always_uses_one_canonical_venue_badge():
    variants = ["", "arxiv", "arXiv preprint", "CVPR 2026", "Venue not specified"]
    papers = [{
        "title": "A Complete and Reliably Sourced Paper Title",
        "published_at": "2026-04",
        "date_precision": "month",
        "venue": venue,
        "paper_url": f"https://arxiv.org/abs/2604.{index:05d}",
        "arxiv_id": f"2604.{index:05d}",
        "source_repos": ["example/list"],
        "metadata_sources": ["arXiv"],
    } for index, venue in enumerate(variants, start=1)]

    selected = filter_recent(papers, 2025)
    assert {paper["venue"] for paper in selected} == {"arXiv"}

    html_paper = parse_readme(
        SOURCE,
        "[A Complete arXiv HTML Paper Title](https://arxiv.org/html/2604.99999v1)",
        "2026-04-20",
        "2026-04-20",
    )[0]
    assert html_paper["arxiv_id"] == "2604.99999"
    assert html_paper["venue"] == "arXiv"

    doubled_slash = parse_readme(
        SOURCE,
        "[A Complete Paper behind a Malformed Source Link](https://arxiv.org/abs//2604.88888)",
        "2026-04-20",
        "2026-04-20",
    )[0]
    assert doubled_slash["arxiv_id"] == "2604.88888"
    assert doubled_slash["venue"] == "arXiv"


def test_arxiv_search_and_home_pages_are_not_papers():
    readme = "\n".join((
        "[arXiv home](https://arxiv.org/)",
        "[Search results](https://arxiv.org/search/?query=multimodal)",
        "[Missing identifier](https://arxiv.org/abs/)",
    ))
    assert parse_readme(SOURCE, readme, "2026-04-20", "2026-04-20") == []


def test_non_arxiv_official_venue_is_preserved():
    paper = {
        "title": "A Complete Conference Paper Title for Testing",
        "published_at": "2026",
        "date_precision": "year",
        "venue": "CVPR 2026",
        "paper_url": "https://openaccess.thecvf.com/content/CVPR2026/html/example.html",
        "source_repos": ["example/list"],
        "metadata_sources": ["official page title"],
    }
    assert filter_recent([paper], 2025)[0]["venue"] == "CVPR 2026"


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


def test_arxiv_rate_limit_stops_immediately_without_long_sleep(monkeypatch):
    class Response:
        status_code = 429
        request = httpx.Request("GET", "https://export.arxiv.org/api/query")

        def raise_for_status(self):
            raise httpx.HTTPStatusError("rate limited", request=self.request, response=self)

    class Client:
        calls = 0

        def get(self, *args, **kwargs):
            self.calls += 1
            return Response()

    monkeypatch.setattr(
        papers_module.time,
        "sleep",
        lambda *_: (_ for _ in ()).throw(AssertionError("429 must not trigger a long sleep")),
    )
    client = Client()
    assert _arxiv_metadata(client, [f"2604.{index:05d}" for index in range(101)]) == {}
    assert client.calls == 1


def test_complete_title_without_exact_date_does_not_trigger_network_enrichment():
    class Client:
        def get(self, *args, **kwargs):
            raise AssertionError("complete titles should not be fetched merely to add a date")

        def post(self, *args, **kwargs):
            raise AssertionError("complete titles should not be fetched merely to add a date")

    paper = {
        "title": "A Complete Paper Title Already Supplied by Its Curated Source",
        "paper_url": "https://arxiv.org/abs/2604.12345",
        "arxiv_id": "2604.12345",
        "doi": "",
        "published_at": "",
        "metadata_sources": ["source README"],
    }
    enrich_missing_metadata(Client(), [paper], max_items=500)
    assert paper["title"].startswith("A Complete Paper Title")


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
