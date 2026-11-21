"""Weekly series: "AI this week in 5 facts" — one recap post on Sunday built from the week's own posts.

A series gives people a reason to follow (they know what comes every Sunday). It reuses only facts
we already published (each from its source), so nothing new is claimed. It is a new format, so it is
ALWAYS held for the owner's Publish/Skip in Telegram, never auto-published.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from bayesly.models import Item, Source

log = logging.getLogger(__name__)

SOURCE_ID = "bayesly-weekly"
WEEKDAY = 6                # Sunday
HOUR_UTC = 14              # 16:00 Kyiv in winter: weekend afternoon scroll
MIN_STORIES = 3


def _week_key(now: datetime) -> str:
    y, w, _ = now.isocalendar()
    return f"{y}-W{w:02d}"


def due(state_dir: Path, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    if now.weekday() != WEEKDAY or now.hour < HOUR_UTC:
        return False
    path = state_dir / "weekly.json"
    done = json.loads(path.read_text(encoding="utf-8")).get("done") if path.exists() else None
    return done != _week_key(now)


def week_stories(state_dir: Path, out_dir: Path, now: datetime) -> list[dict]:
    """Drafts published in the last 7 days (any platform ok), oldest first."""
    ledger = json.loads((state_dir / "posted.json").read_text(encoding="utf-8")) if (state_dir / "posted.json").exists() else {}
    cutoff = now - timedelta(days=7)
    stories = []
    for draft_id, plats in ledger.items():
        ats = [datetime.fromisoformat(e["at"]) for e in plats.values() if e.get("ok") and e.get("at")]
        if not ats or min(ats) < cutoff:
            continue
        for path in out_dir.glob(f"*/{draft_id}-*.json"):
            rec = json.loads(path.read_text(encoding="utf-8"))
            d = rec.get("draft") or {}
            if (rec.get("source") or {}).get("id") == SOURCE_ID or not d.get("headline"):
                continue
            stories.append({"at": min(ats), "headline": d["headline"], "facts": d.get("facts") or d.get("facts_used") or [],
                            "source": (rec.get("source") or {}).get("name", "")})
            break
    return sorted(stories, key=lambda s: s["at"])


def weekly_prompt(stories: list[dict]) -> str:
    blocks = "\n\n".join(f"[{i}] {s['headline']}\n" + "\n".join(f"- {f}" for f in s["facts"][:4])
                         for i, s in enumerate(stories))
    return (
        "You are the editor of Bayesly (AI/tech news). Write the Sunday recap post \"AI this week in 5 facts\".\n"
        "Pick the 5 most important or surprising facts of the week from the STORIES below (one per story when "
        "possible). Use ONLY facts written below; do not add numbers, names or claims. Voice: calm, precise, "
        "short sentences, no hype words, no emojis, no Source/Confidence lines, no opinion.\n\nSTORIES:\n" + blocks +
        "\n\nReturn ONLY a JSON object with exactly these keys:\n"
        '{"headline": "AI this week in 5 facts", '
        '"threads_post": "max 450 chars: one hook line, then the 5 facts as short numbered lines, then the question; no link, no hashtags", '
        '"x_post": "max 260 chars, no link, no domain, max 1 hashtag", '
        '"instagram_caption": "max 900 chars: hook, 5 numbered facts, the question, then 3-5 lowercase hashtags", '
        '"question": "max 90 chars: which of these matters most to you / what did we miss", '
        '"facts": ["5 items, each max 90 chars, standalone"], '
        '"topic_tag": "AI", "broll_query": "", '
        '"confidence": "high", "confidence_reason": "recap of already published, sourced posts", '
        '"facts_used": ["the facts you used, copied from the stories"], "insufficient_source": false, '
        '"editor_notes": "", '
        '"card": {"category": "Other", "headline": "AI this week in 5 facts", "highlight": "5 facts", "number": null}}'
    )


def make_weekly(settings: Any, generate: Callable[[str], str], store: Any, *, model: str = "",
                now: datetime | None = None) -> Path | None:
    """Write the recap draft into the queue (normal record, so render/video/held flow all apply)."""
    from bayesly.gemini import parse_draft_json

    now = now or datetime.now(timezone.utc)
    stories = week_stories(settings.state_dir, settings.out_dir, now)
    state_path = settings.state_dir / "weekly.json"
    if len(stories) < MIN_STORIES:
        log.info("weekly: only %d stories this week, skipped", len(stories))
        state_path.write_text(json.dumps({"done": _week_key(now), "skipped": "too few stories"}), encoding="utf-8")
        return None
    text = "\n".join(f"{s['headline']} " + " ".join(s["facts"][:4]) for s in stories)
    raw = generate(weekly_prompt(stories))
    draft = parse_draft_json(raw)
    if not draft:
        raise ValueError("weekly: model returned no JSON")
    item = Item(source=Source(id=SOURCE_ID, name="Bayesly weekly", url="", kind="digest", reliability=1.0),
                title=f"AI this week in 5 facts ({_week_key(now)})", url="", text=text, published=now, score=1.0)
    path = store.write(item, f"weekly-{_week_key(now).lower()}", draft, raw, model=model)
    state_path.write_text(json.dumps({"done": _week_key(now), "draft": str(path)}), encoding="utf-8")
    return path
