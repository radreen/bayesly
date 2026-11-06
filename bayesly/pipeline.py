"""Phase 0 pipeline: collect -> dedupe -> score -> draft -> review files.

Nothing here publishes anything. There is no publish step in Phase 0.
"""

from __future__ import annotations

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
        path = store.write(item, item_id, draft, raw, model=generator.model)
        log.info("draft written", extra={"item_id": item_id, "path": str(path), "parsed": draft is not None})
        result.written.append(path)
    return result
