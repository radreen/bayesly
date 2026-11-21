"""Gemini client and end-to-end pipeline with mocked HTTP. No real API calls."""

import json
from dataclasses import replace

import pytest

from bayesly.config import ROOT, load_settings
from bayesly.gemini import GeminiClient, GeminiError, extract_text, parse_draft_json
from bayesly.pipeline import run_draft
from bayesly.review import lint_draft

GOOD_DRAFT = {
    "headline": "A model API was released.",
    "threads_post": "A model API was released.\n\nIt costs 30% less than the last one.\n\nWould you switch?",
    "x_post": "A model API was released, 30% cheaper than the last one. Would you switch?",
    "instagram_caption": "A model API was released.\n\nWould you switch?\n\n#ai #llm #api",
    "confidence": "medium",
    "confidence_reason": "vendor claim",
    "opinion_line": "Our take: wait for independent tests.",
    "facts_used": ["released a model with an API"],
    "insufficient_source": False,
    "editor_notes": "",
}


class FakeResponse:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


def client(session, retries=2):
    return GeminiClient("test-key", "gemini-test", "2026-05-20", timeout=5, max_retries=retries, session=session)


def test_extract_text_from_steps_skips_thoughts():
    data = {"steps": [{"type": "thought", "content": [{"text": "hmm"}]},
                      {"type": "model_output", "content": [{"text": "hello"}]}]}
    assert extract_text(data) == "hello"
    assert extract_text({"output_text": "direct"}) == "direct"
    with pytest.raises(GeminiError):
        extract_text({"steps": []})


def test_parse_draft_json_handles_fences_and_garbage():
    assert parse_draft_json("```json\n" + json.dumps(GOOD_DRAFT) + "\n```")["confidence"] == "medium"
    assert parse_draft_json("not json") is None


def test_client_retries_on_503_then_succeeds(monkeypatch):
    monkeypatch.setattr("bayesly.net.time.sleep", lambda s: None)
    session = FakeSession([FakeResponse(503, {}), FakeResponse(200, {"output_text": "ok"})])
    assert client(session).generate("p") == "ok"
    assert len(session.calls) == 2
    method, url, kwargs = session.calls[0]
    assert kwargs["headers"]["x-goog-api-key"] == "test-key"
    assert kwargs["json"]["model"] == "gemini-test"
    assert kwargs["timeout"] == 5


def test_client_fails_fast_on_400_without_leaking_key():
    session = FakeSession([FakeResponse(400, {"error": "bad test-key"})])
    with pytest.raises(GeminiError) as err:
        client(session).generate("p")
    assert "test-key" not in str(err.value)
    assert len(session.calls) == 1


def test_missing_key_is_a_clear_error():
    with pytest.raises(GeminiError, match="GEMINI_API_KEY"):
        GeminiClient("", "m", "r", timeout=1, max_retries=0)


def test_lint_flags_hashtags_hype_and_length():
    bad = dict(GOOD_DRAFT, threads_post="Revolutionary! #ai " + "x" * 600)
    warnings = " ".join(lint_draft(bad, "https://example.com/post"))
    assert "hashtag" in warnings and "hype" in warnings and "chars" in warnings
    assert lint_draft(GOOD_DRAFT, "https://example.com/post") == []


def test_lint_flags_links_in_x_post():
    for bad_x in ("Read more https://a.com/x", "Details on openai.com today", "see www.example.org"):
        warnings = " ".join(lint_draft(dict(GOOD_DRAFT, x_post=bad_x), "https://example.com/post"))
        assert "X post contains a link" in warnings, bad_x


class FakeGenerator:
    model = "fake"

    def __init__(self):
        self.prompts = []

    def generate(self, prompt):
        self.prompts.append(prompt)
        return json.dumps(GOOD_DRAFT)


def test_pipeline_writes_review_files_and_is_idempotent(tmp_path, make_item):
    settings = replace(load_settings(), out_dir=tmp_path / "out", state_dir=tmp_path / "state",
                       prompts_dir=ROOT / "prompts")
    items = [make_item(url=f"https://example.com/{i}", title=f"Model release number {i} with API {'x' * i}")
             for i in range(3)]
    gen = FakeGenerator()

    first = run_draft(settings, limit=2, generator=gen, items=items)
    assert len(first.written) == 2
    record = json.loads(first.written[0].read_text(encoding="utf-8"))
    assert record["status"] == "pending_review"
    assert record["draft"]["confidence"] == "medium"
    assert first.written[0].with_suffix(".md").exists()

    # Second run with the same items must not redraft the first two.
    second = run_draft(settings, limit=5, generator=gen, items=items)
    assert len(second.written) == 1
    assert len([x for x in gen.prompts if "SOURCE ITEM" in x]) == 3   # draft calls only


def test_client_falls_back_to_next_model_when_busy(monkeypatch):
    monkeypatch.setattr("bayesly.net.time.sleep", lambda s: None)
    session = FakeSession([FakeResponse(503, {}), FakeResponse(503, {}), FakeResponse(200, {"output_text": "ok"})])
    c = GeminiClient("k", "busy-model", "r", timeout=5, max_retries=1, session=session, fallback_models=["spare-model"])
    assert c.generate("p") == "ok"
    assert c.model == "spare-model"
    assert [call[2]["json"]["model"] for call in session.calls] == ["busy-model", "busy-model", "spare-model"]


def test_lint_flags_source_confidence_and_take_lines():
    for line in ("Source: Example Lab", "Confidence: medium — vendor claim", "Our take: wait"):
        warnings = " ".join(lint_draft(dict(GOOD_DRAFT, threads_post="A fact.\n\n" + line), "u"))
        assert "not wanted" in warnings, line


class ScriptedGenerator:
    model = "fake"

    def __init__(self, dedupe=None, unsupported=None):
        self.dedupe, self.unsupported, self.prompts = dedupe or [], unsupported or [], []

    def generate(self, prompt):
        self.prompts.append(prompt)
        if prompt.startswith("You dedupe"):
            return json.dumps({"duplicates": self.dedupe})
        if prompt.startswith("You are a strict fact-checker"):
            return json.dumps({"unsupported": self.unsupported})
        return json.dumps(GOOD_DRAFT)


def test_duplicate_story_is_skipped_and_remembered(tmp_path, make_item):
    from bayesly.review import DraftStore
    settings = replace(load_settings(), out_dir=tmp_path / "out", state_dir=tmp_path / "state",
                       prompts_dir=load_settings().prompts_dir)
    items = [make_item(title="Anthropic claims Claude proved the Riemann hypothesis", url="https://a/1"),
             make_item(title="Hours after Anthropic's Riemann claim, mathematicians doubt it", url="https://b/2")]
    gen = ScriptedGenerator(dedupe=[1])
    res = run_draft(settings, limit=2, generator=gen, items=items)
    assert len(res.written) == 1
    assert len(DraftStore(settings.out_dir, settings.state_dir).drafted_ids()) == 2   # the duplicate is remembered


def test_fact_check_issue_becomes_a_lint_warning(tmp_path, make_item):
    settings = replace(load_settings(), out_dir=tmp_path / "out", state_dir=tmp_path / "state",
                       prompts_dir=load_settings().prompts_dir)
    gen = ScriptedGenerator(unsupported=["claims a 40% speedup; source gives no number"])
    res = run_draft(settings, limit=1, generator=gen, items=[make_item()])
    record = json.loads(res.written[0].read_text(encoding="utf-8"))
    assert any(w.startswith("Fact-check:") for w in record["lint_warnings"])
