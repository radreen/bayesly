"""Phase 0 pipeline: collect -> dedupe -> score -> draft -> review files.

Nothing here publishes anything. There is no publish step in Phase 0.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from bayesly import collect as collect_mod
from bayesly.config import Settings, load_sources
from bayesly.dedupe import dedupe, item_key
from bayesly.gemini import GeminiError, parse_draft_json
from bayesly.prompt import build_prompt, load_prompts
from bayesly.models import Item
from bayesly.review import DraftStore
from bayesly.score import rank, select

log = logging.getLogger(__name__)

DEFAULT_MAX_PER_SOURCE = 2


class TextGenerator(Protocol):
    model: str

    def generate(self, prompt: str) -> str: ...


@dataclass
class RunResult:
    collected: int = 0
    unique: int = 0
    ranked: int = 0
    written: list[Path] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def collect_and_rank(
    settings: Settings,
    result: RunResult,
    *,
    source_ids: set[str] | None = None,
    items: list[Item] | None = None,
) -> list[Item]:
    """Collect (unless `items` is given), dedupe against earlier drafts, rank."""
    store = DraftStore(settings.out_dir, settings.state_dir)
    if items is None:
        sources = load_sources(settings.sources_path)
        if source_ids:
            sources = [s for s in sources if s.id in source_ids]
        items = collect_mod.collect(
            sources, timeout=settings.http_timeout, max_retries=settings.http_max_retries
        )
    result.collected = len(items)
    unique = dedupe(items, seen_keys=store.drafted_ids())
    result.unique = len(unique)
    ranked = rank(unique)
    result.ranked = len(ranked)
    log.info("ranked", extra={"collected": result.collected, "unique": result.unique, "ranked": result.ranked})
    return ranked


def run_draft(
    settings: Settings,
    *,
    limit: int,
    generator: TextGenerator | None,
    max_per_source: int = DEFAULT_MAX_PER_SOURCE,
    source_ids: set[str] | None = None,
    items: list[Item] | None = None,
) -> RunResult:
    """Run the pipeline. `generator=None` means --skip-llm (review files without drafts).

    `items` lets tests inject pre-collected items instead of hitting the network.
    """
    result = RunResult()
    store = DraftStore(settings.out_dir, settings.state_dir)
    ranked = collect_and_rank(settings, result, source_ids=source_ids, items=items)

    voice, template = load_prompts(settings.prompts_dir)
    for item in select(ranked, limit, max_per_source):
        item_id = item_key(item)
        if generator is None:
            path = store.write(item, item_id, None, "", model="(skipped)", mark=False)
            result.written.append(path)
            continue
        prompt = build_prompt(item, voice, template)
        try:
            raw = generator.generate(prompt)
        except GeminiError as exc:
            log.error("draft failed", extra={"item_id": item_id, "error": str(exc)})
            result.failed.append(item_id)
            continue
        draft = parse_draft_json(raw)
        if draft:
            draft = shorten_if_needed(draft, generator, item.url)
        path = store.write(item, item_id, draft, raw, model=generator.model)
        log.info("draft written", extra={"item_id": item_id, "path": str(path), "parsed": draft is not None})
        result.written.append(path)
    return result


# Platform limits the model sometimes overruns by a few characters (a held post costs us the
# "be first" race). One cheap repair call shortens just the offending texts.
TEXT_LIMITS = {"threads_post": 500, "x_post": 280, "instagram_caption": 2200}
REPAIR_TARGET = {"threads_post": 440, "x_post": 260, "instagram_caption": 2000}


def shorten_if_needed(draft: dict, generator, source_url: str) -> dict:
    over = {k: draft[k] for k, lim in TEXT_LIMITS.items() if isinstance(draft.get(k), str) and len(draft[k]) > lim}
    if not over:
        return draft
    rules = "\n".join(f"- {k}: at most {REPAIR_TARGET[k]} characters" for k in over)
    prompt = (
        "Shorten these social posts. Keep every fact, number and name exactly as written; do not add anything; "
        "keep the hook line and the question; cut filler words first.\n"
        f"Limits:\n{rules}\n"
        "- no links and no domain names anywhere\n"
        "Return ONLY a JSON object with the same keys.\n\n" + json.dumps(over, ensure_ascii=False)
    )
    try:
        fixed = parse_draft_json(generator.generate(prompt)) or {}
    except GeminiError as exc:
        log.warning("shorten failed", extra={"error": str(exc)[:200]})
        return draft
    out = dict(draft)
    for k in over:
        v = fixed.get(k)
        if isinstance(v, str) and v.strip() and len(v) <= TEXT_LIMITS[k]:
            out[k] = v.strip()
    if out != draft:
        out["editor_notes"] = ((draft.get("editor_notes") or "") + " [auto-shortened: " + ", ".join(
            k for k in over if out[k] != draft[k]) + "]").strip()
    return out
