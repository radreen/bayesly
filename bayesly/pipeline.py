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
    picked = select(ranked, limit * 2 if generator else limit, max_per_source)
    if generator is not None and picked:
        dupes = find_duplicates(picked, recent_headlines(settings.out_dir), helper_of(generator))
        for item in dupes:   # remember them, so the 15-min check doesn't fire on them again
            store.mark_skipped(item_key(item), "duplicate of a recent story")
            log.info("duplicate skipped", extra={"title": item.title[:100]})
        picked = [i for i in picked if i not in dupes][:limit]
    for item in picked:
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
            draft = shorten_if_needed(draft, helper_of(generator), item.url)
            draft["fact_check_issues"] = fact_check(draft, item.text, helper_of(generator))
        path = store.write(item, item_id, draft, raw, model=generator.model)
        log.info("draft written", extra={"item_id": item_id, "path": str(path), "parsed": draft is not None})
        result.written.append(path)
    return result


# Platform limits the model sometimes overruns by a few characters (a held post costs us the
# "be first" race). One cheap repair call shortens just the offending texts.
TEXT_LIMITS = {"threads_post": 500, "x_post": 280, "instagram_caption": 2200}
REPAIR_TARGET = {"threads_post": 440, "x_post": 260, "instagram_caption": 2000}


def helper_of(generator):
    """Small tasks (dedupe, shorten, fact-check) run on the lite models: each Gemini model has its own
    free daily quota (20 requests), so drafts keep the stronger models."""
    util = getattr(generator, "utility", None)
    return util() if callable(util) else generator


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


# -- the same story from two outlets -----------------------------------------------------------
DUPLICATE_WINDOW_H = 48


def recent_headlines(out_dir: Path, hours: float = DUPLICATE_WINDOW_H) -> list[str]:
    """Source titles + our headlines of everything drafted in the last `hours`."""
    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out: list[str] = []
    for p in out_dir.rglob("*.json"):
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
            if datetime.fromisoformat(r.get("created_at", "")) < cutoff:
                continue
        except (OSError, ValueError):
            continue
        out.append((r.get("source") or {}).get("title", ""))
        if (r.get("draft") or {}).get("headline"):
            out.append(r["draft"]["headline"])
    return [t for t in dict.fromkeys(out) if t]


def find_duplicates(candidates: list[Item], recent: list[str], generator) -> list[Item]:
    """One model call: which candidates report the same news event as a recent story or as an
    earlier candidate (different outlets word it differently, so title matching misses it)."""
    if len(candidates) < 2 and not recent:
        return []
    numbered = "\n".join(f"{i}. {c.title}" for i, c in enumerate(candidates))
    prompt = (
        "You dedupe a news feed. Two items are duplicates when they report the SAME news event "
        "(same company + same announcement/incident), even if worded differently. Follow-ups that add a "
        "genuinely new event are NOT duplicates.\n\nRECENT (already covered):\n"
        + ("\n".join(f"- {t}" for t in recent[:80]) or "- (none)")
        + f"\n\nNEW CANDIDATES:\n{numbered}\n\n"
        "Return ONLY JSON: {\"duplicates\": [indices of NEW candidates that duplicate a RECENT item "
        "or an EARLIER candidate in the list]}"
    )
    try:
        data = parse_draft_json(generator.generate(prompt)) or {}
    except GeminiError as exc:
        log.warning("duplicate check failed", extra={"error": str(exc)[:200]})
        return []
    idx = {i for i in data.get("duplicates", []) if isinstance(i, int) and 0 <= i < len(candidates)}
    return [c for i, c in enumerate(candidates) if i in idx]


# -- fact-check: every claim in the posts must be in the source ---------------------------------
def fact_check(draft: dict, source_text: str, generator) -> list[str]:
    """One model call. Returns claims in the posts that the source text does not support
    (empty list = clean). A failed check returns [] and is logged: it never blocks on its own."""
    posts = {k: draft.get(k, "") for k in ("threads_post", "x_post", "instagram_caption")}
    posts["facts"] = draft.get("facts") or []
    prompt = (
        "You are a strict fact-checker. Compare the POSTS with the SOURCE TEXT. List every factual claim in "
        "the posts (numbers, names, dates, prices, quotes, capabilities, who said what) that the source text "
        "does NOT state or clearly imply. Ignore the question at the end, hashtags and style. Attributed claims "
        "('the company says X') are fine if the source says the company says X.\n\n"
        f"<source_text>\n{source_text[:12000]}\n</source_text>\n\nPOSTS:\n{json.dumps(posts, ensure_ascii=False)}\n\n"
        "Return ONLY JSON: {\"unsupported\": [\"short description of each unsupported claim\"]}"
    )
    try:
        data = parse_draft_json(generator.generate(prompt)) or {}
    except GeminiError as exc:
        log.warning("fact check failed", extra={"error": str(exc)[:200]})
        return []
    return [str(x)[:200] for x in data.get("unsupported", []) if str(x).strip()][:5]
