"""Card validation and slide HTML. No browser needed: screenshots are not tested here."""

import copy
import json
from pathlib import Path

import pytest

from bayesly.cards import build_card, number_in_source
from bayesly.render import (RenderError, build_page, build_slides, find_cover_image, key_facts, plan_slides,
                            slide_ask)

FIXTURE = Path(__file__).parent / "fixtures" / "sample_draft.json"


@pytest.fixture
def record():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_full_card_gives_cover_facts_ask(record):
    card = build_card(record)
    assert card.warnings == []
    assert plan_slides(card, record) == ["cover", "facts", "ask"]
    slides = build_slides(card, record)
    assert len(slides) == 3
    assert '<span class="key">7B open-weights</span>' in slides[0]
    assert "Key facts" in slides[1]
    assert "Your call" in slides[2] and "3 / 3" in slides[2]


def test_no_source_confidence_or_take_on_slides(record):
    html = "".join(build_slides(build_card(record), record))
    assert "Source:" not in html and "confidence" not in html.lower() and "Our take" not in html


def test_model_facts_are_used_first(record):
    record["draft"]["facts"] = ["Runs on a single GPU", "Costs $0 to try", "Open weights"]
    card = build_card(record)
    assert key_facts(card, record) == ["Runs on a single GPU", "Costs $0 to try", "Open weights"]
    assert "Runs on a single GPU" in build_slides(card, record)[1]


def test_cover_uses_photo_when_available_and_credits_it(record):
    card = build_card(record)
    record["cover_image"] = "https://example.com/photo.jpg"
    cover = build_slides(card, record)[0]
    assert 'data-layout="photo"' in cover and 'src="https://example.com/photo.jpg"' in cover
    assert "Image: " in cover
    record["cover_image"] = None
    assert 'data-layout="text"' in build_slides(card, record)[0]


def test_number_must_be_in_source(record):
    assert number_in_source("2x", "matches a model that is 2× larger")
    assert not number_in_source("2x", "Llama 2 is a model")      # a bare "2" is not "2x"
    assert not number_in_source("71.2%", "scores 71.25% on the test")
    record["draft"]["card"]["number"]["value"] = "3x"
    card = build_card(record)
    assert card.number is None
    assert "3x" not in build_slides(card, record)[1]
    assert any("not found in the source" in w for w in card.warnings)


def test_bad_highlight_is_dropped(record):
    record["draft"]["card"]["highlight"] = "not in headline"
    card = build_card(record)
    assert card.highlight is None
    assert 'class="key"' not in build_slides(card, record)[0]


def test_html_is_escaped(record):
    record["draft"]["card"]["headline"] = 'A <script>alert(1)</script> & "quotes"'
    record["draft"]["card"]["highlight"] = None
    record["draft"]["question"] = "<b>bold?</b>"
    slides = build_slides(build_card(record), record)
    assert "<script>" not in slides[0] and "&lt;script&gt;" in slides[0] and "&amp;" in slides[0]
    assert "<b>bold" not in slides[-1]


def test_missing_card_still_gives_cover_and_ask(record):
    del record["draft"]["card"]
    card = build_card(record)
    assert plan_slides(card, record)[0] == "cover" and plan_slides(card, record)[-1] == "ask"
    assert "Your call" in build_slides(card, record)[-1]


def test_ask_uses_model_question_or_a_default(record):
    card = build_card(record)
    record["draft"]["question"] = "Worth switching for a 25% lower price?"
    assert "Worth switching" in slide_ask(card, record, 3, 3)
    record["draft"]["question"] = ""
    assert "?" in slide_ask(card, record, 3, 3)


def test_long_fields_are_trimmed_with_warning(record):
    record["draft"]["card"]["now"] = "word " * 60
    card = build_card(record)
    assert len(card.now) <= 110
    assert any("card.now trimmed" in w for w in card.warnings)


def test_og_image_is_found_and_only_https():
    class R:
        def __init__(self, text):
            self.status_code, self.text = 200, text

    class S:
        def __init__(self, text):
            self.text = text

        def request(self, *a, **k):
            return R(self.text)

    html = '<meta property="og:image" content="https://cdn.example.com/a.jpg?x=1&amp;y=2">'
    assert find_cover_image("https://e.com", session=S(html)) == "https://cdn.example.com/a.jpg?x=1&y=2"
    assert find_cover_image("https://e.com", session=S('<meta property="og:image" content="http://x/a.jpg">')) is None
    assert find_cover_image("https://e.com", session=S("<html></html>")) is None


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
