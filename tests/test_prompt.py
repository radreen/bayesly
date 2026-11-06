from bayesly.config import ROOT
from bayesly.prompt import EMPTY_SOURCE_MARKER, MAX_SOURCE_CHARS, build_prompt, load_prompts


def test_prompt_contains_source_text_voice_and_grounding_rules(make_item):
    voice, template = load_prompts(ROOT / "prompts")
    item = make_item(text="The model scores 71.2% on the benchmark {weird braces}.")
    prompt = build_prompt(item, voice, template)
    assert "71.2%" in prompt
    assert "{weird braces}" in prompt  # braces in source text must not break formatting
    assert item.url in prompt
    assert "Use ONLY facts that appear in the SOURCE TEXT" in prompt
    assert "No hype words" in prompt  # voice guide is embedded
    assert "{voice}" not in prompt and "{source_text}" not in prompt


def test_empty_source_text_is_explicit(make_item):
    voice, template = load_prompts(ROOT / "prompts")
    prompt = build_prompt(make_item(text=""), voice, template)
    assert EMPTY_SOURCE_MARKER in prompt


def test_long_source_text_is_truncated(make_item):
    voice, template = load_prompts(ROOT / "prompts")
    prompt = build_prompt(make_item(text="x" * (MAX_SOURCE_CHARS + 500)), voice, template)
    assert "[...truncated]" in prompt
    assert "x" * (MAX_SOURCE_CHARS + 1) not in prompt
