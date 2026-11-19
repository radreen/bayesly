"""Card validation and slide HTML. No browser needed: screenshots are not tested here."""

import copy
import json
from pathlib import Path

import pytest

from bayesly.cards import build_card, number_in_source
from bayesly.render import (COVERS, INNER_BG, COVER_BG, RenderError, Style, build_page, build_slides,
                            choose_style, plan_slides)

FIXTURE = Path(__file__).parent / "fixtures" / "sample_draft.json"


@pytest.fixture
def record():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_full_card_gives_five_slides(record):
    card = build_card(record)
    assert card.warnings == []
    assert plan_slides(card) == ["cover", "update", "number", "confidence", "closing"]
    slides = build_slides(card, record, Style(cover="curve"))
    assert len(slides) == 5
    assert '<span class="hl">7B open-weights</span>' in slides[0]
    assert "05 · What&#x27;s next" in slides[4]


def test_number_must_be_in_source(record):
    assert number_in_source("2x", "matches a model that is 2× larger")
    assert not number_in_source("2x", "Llama 2 is a model")      # a bare "2" is not "2x"
    assert not number_in_source("71.2%", "scores 71.25% on the test")
    record["draft"]["card"]["number"]["value"] = "3x"
    card = build_card(record)
    assert card.number is None
    assert "number" not in plan_slides(card)
    assert any("not found in the source" in w for w in card.warnings)


def test_bad_highlight_is_dropped(record):
    record["draft"]["card"]["highlight"] = "not in headline"
    card = build_card(record)
    assert card.highlight is None
    assert 'class="hl"' not in build_slides(card, record)[0]


def test_html_is_escaped(record):
    record["draft"]["card"]["headline"] = 'A <script>alert(1)</script> & "quotes"'
    record["draft"]["card"]["highlight"] = None
    html = build_slides(build_card(record), record)[0]
    assert "<script>" not in html
    assert "&lt;script&gt;" in html and "&amp;" in html


def test_missing_card_falls_back_to_cover_and_confidence(record):
    del record["draft"]["card"]
    card = build_card(record)
    kinds = plan_slides(card)
    assert kinds[0] == "cover" and "confidence" in kinds and "update" not in kinds
    # Opinion still gets a slide when there is no number slide.
    assert kinds[-1] == "closing"
    assert "Our take" in build_slides(card, record)[-1]


def test_long_fields_are_trimmed_with_warning(record):
    record["draft"]["card"]["now"] = "word " * 60
    card = build_card(record)
    assert len(card.now) <= 110
    assert any("card.now trimmed" in w for w in card.warnings)


def test_high_confidence_closing_title(record):
    record["draft"]["confidence"] = "high"
    slides = build_slides(build_card(record), record)
    assert "watch." in slides[-1] and "know <span" not in slides[-1]


def test_page_links_design_assets_and_rejects_bad_palette(record):
    page = build_page(build_slides(build_card(record), record), "blue")
    assert 'data-palette="blue"' in page and "bayesly.css" in page and "bayesly.js" in page
    with pytest.raises(RenderError):
        build_page([], "pink")


def test_record_without_draft_cannot_render(tmp_path, record):
    from bayesly.render import render_record

    record = copy.deepcopy(record)
    record["draft"] = None
    path = tmp_path / "x.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(RenderError, match="no parsed draft"):
        render_record(path)


# ---------------------------------------------------------------- variation (one brand, many layouts)
def test_style_is_stable_for_a_post(record):
    card = build_card(record)
    assert choose_style(card, record) == choose_style(card, record)


def test_cover_avoids_the_two_previous_layouts(record):
    card = build_card(record)
    for i in range(30):
        rec = {**record, "id": f"post{i}"}
        st = choose_style(card, rec, ["curve", "figure"])
        assert st.cover not in {"curve", "figure"}


def test_figure_cover_needs_a_number_and_replaces_the_number_slide(record):
    card = build_card(record)
    assert plan_slides(card, "figure").count("number") == 0
    record["draft"]["card"]["number"] = None
    no_num = build_card(record)
    for i in range(30):
        assert choose_style(no_num, {**record, "id": f"p{i}"}).cover != "figure"


def test_layouts_and_backgrounds_vary_across_posts(record):
    card = build_card(record)
    covers, recent = [], []
    for i in range(12):
        st = choose_style(card, {**record, "id": f"feed{i}"}, recent)
        recent.insert(0, st.cover)
        covers.append(st.cover)
        # neighbouring slides never share a background
        kinds = plan_slides(card, st.cover)
        bgs = [COVER_BG[st.cover]] + [INNER_BG[k][st.variants[k] == "b"] for k in kinds[1:]]
        assert all(a != b for a, b in zip(bgs, bgs[1:])), (st, bgs)
    assert all(a != b for a, b in zip(covers, covers[1:]))       # no cover repeats back to back
    assert len(set(covers)) >= 4


@pytest.mark.parametrize("cover", COVERS)
def test_every_cover_layout_renders_escaped_headline(record, cover):
    record["draft"]["card"]["headline"] = 'A <b>bold</b> & "quoted" launch'
    record["draft"]["card"]["highlight"] = None
    card = build_card(record)
    html = build_slides(card, record, choose_style(card, {**record, "layout": cover}))[0]
    assert f'data-layout="{cover}"' in html and "<b>bold" not in html and "&lt;b&gt;bold" in html
