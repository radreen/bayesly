"""Prompt assembly for the draft step."""

from __future__ import annotations

from pathlib import Path

from bayesly.models import Item

MAX_SOURCE_CHARS = 6000
EMPTY_SOURCE_MARKER = "(no body text in the feed; only the title is available)"


def load_prompts(prompts_dir: Path) -> tuple[str, str]:
    voice = (prompts_dir / "voice.md").read_text(encoding="utf-8")
    template = (prompts_dir / "draft.md").read_text(encoding="utf-8")
    return voice, template


def build_prompt(item: Item, voice: str, template: str) -> str:
    text = item.text.strip()
    if len(text) > MAX_SOURCE_CHARS:
        text = text[:MAX_SOURCE_CHARS] + " [...truncated]"
    return template.format(
        voice=voice.strip(),
        source_name=item.source.name,
        source_kind=item.source.kind,
        published=item.published.isoformat() if item.published else "unknown",
        title=item.title,
        url=item.url,
        url_len=len(item.url),
        source_text=text or EMPTY_SOURCE_MARKER,
    )
