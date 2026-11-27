"""Instagram Stories: their OWN content, not reposts of the feed (owner, 2026-11-27).

Daily slots (UTC; a missed slot is caught up by the next run that day):
  10:00  "Number of the day"  the most striking number from our last-24h posts (always from a source)
  14:00  "Quiz" (2 stories)   a 3-option question built ONLY from our last-24h facts, then the answer
  17:00  "Today in AI"        the day's top 3 headlines
Nothing here adds facts we did not already publish.

API (docs checked 2026-11-24): POST /{ig-user-id}/media with media_type=STORIES and image_url or
video_url, then media_publish. 9:16, JPEG images. No stickers via the API (no links, polls,
locations), so the quiz answer is the next story. Stories count toward the publishing quota:
STORIES_PER_DAY (default 4) in total. STORIES=0 switches all of it off.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from html import escape as e
from pathlib import Path
from typing import Callable

SLOTS = (("number", 10, 1), ("quiz", 14, 2), ("digest", 17, 1))   # (name, from hour UTC, stories used)
MIN_POSTS_FOR_DIGEST = 2


def enabled() -> bool:
    return os.getenv("STORIES", "1").strip().lower() not in {"0", "false", "no", "off"}


def per_day() -> int:
    try:
        return int(os.getenv("STORIES_PER_DAY", "4"))
    except ValueError:
        return 4


class StoryLog:
    """state/stories.json: {"day": "YYYY-MM-DD", "count": n, "done": {slot: "YYYY-MM-DD"}}"""

    def __init__(self, state_dir: Path) -> None:
        self.path = state_dir / "stories.json"

    def load(self, now: datetime) -> dict:
        data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        today = now.date().isoformat()
        if data.get("day") != today:
            data["day"], data["count"] = today, 0
        data.setdefault("done", {})
        data.pop("posts", None)
        return data

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def note(self, now: datetime, slot: str, used: int = 1) -> None:
        data = self.load(now)
        data["count"] += used
        data["done"][slot] = data["day"]
        self.save(data)

    def skip(self, now: datetime, slot: str) -> None:
        """Nothing to show for this slot today (no number, too few facts): don't retry all day."""
        data = self.load(now)
        data["done"][slot] = data["day"]
        self.save(data)


def due_slots(log: StoryLog, now: datetime) -> list[str]:
    if not enabled():
        return []
    data = log.load(now)
    return [name for name, hour, used in SLOTS
            if now.hour >= hour and data["done"].get(name) != data["day"] and data["count"] + used <= per_day()]


# -- material: only what we already published ------------------------------------------------
def _ledger(state_dir: Path) -> dict:
    path = state_dir / "posted.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def recent_records(state_dir: Path, out_dir: Path, now: datetime, hours: int = 24) -> list[dict]:
    """Records of posts published in the last `hours`, best score first."""
    rows = []
    for draft_id, plats in _ledger(state_dir).items():
        ats = [datetime.fromisoformat(x["at"]) for x in plats.values() if x.get("ok") and x.get("at")]
        if not ats or (now - min(ats)).total_seconds() > hours * 3600:
            continue
        for path in out_dir.glob(f"*/{draft_id}-*.json"):
            rec = json.loads(path.read_text(encoding="utf-8"))
            if rec.get("draft") and (rec.get("source") or {}).get("id") != "bayesly-weekly":
                rec["_first_at"] = min(ats).isoformat()
                rows.append(rec)
            break
    return sorted(rows, key=lambda r: float(r.get("score") or 0), reverse=True)


def _headline(rec: dict) -> str:
    d = rec.get("draft") or {}
    return ((d.get("card") or {}).get("headline") or d.get("headline") or "").strip()


def today_headlines(state_dir: Path, out_dir: Path, now: datetime, n: int = 3) -> list[str]:
    today = now.date().isoformat()
    recs = [r for r in recent_records(state_dir, out_dir, now) if r["_first_at"][:10] == today]
    return [h for h in (_headline(r) for r in recs[:n]) if h]


def number_of_the_day(recs: list[dict]) -> dict | None:
    for r in recs:
        num = ((r.get("draft") or {}).get("card") or {}).get("number") or {}
        if num.get("value") and num.get("caption"):
            return {"value": str(num["value"])[:10], "caption": str(num["caption"])[:90], "headline": _headline(r)}
    return None


def quiz_prompt(facts: list[str]) -> str:
    listed = "\n".join(f"- {f}" for f in facts[:14])
    return (
        "Make ONE multiple-choice quiz question for an AI-news Instagram story, using ONLY the facts below "
        "(all already published by us). The answer must be stated in the facts. Three short options "
        "(max 32 characters each), exactly one correct, the wrong ones plausible but clearly wrong per the facts. "
        "No trick questions, no outside knowledge. The facts are listed most important story first: base the "
        "question on one of the first, best-known stories (big companies and products people have heard of)."
        "\n\nFACTS:\n" + listed +
        '\n\nReturn ONLY JSON: {"question": "max 90 characters", "options": ["...", "...", "..."], '
        '"answer": 0, "why": "one sentence from the facts, max 100 characters"}'
    )


def make_quiz(recs: list[dict], generate: Callable[[str], str]) -> dict | None:
    facts: list[str] = []
    for r in recs[:6]:
        facts += [str(f) for f in ((r.get("draft") or {}).get("facts") or [])][:3]
    if len(facts) < 3:
        return None
    from bayesly.gemini import parse_draft_json
    try:
        q = parse_draft_json(generate(quiz_prompt(facts))) or {}
    except Exception:  # noqa: BLE001 - a quiz is optional
        return None
    opts = [str(o).strip()[:40] for o in q.get("options") or [] if str(o).strip()]
    ans = q.get("answer")
    if len(opts) != 3 or not isinstance(ans, int) or not 0 <= ans < 3 or not q.get("question"):
        return None
    return {"question": str(q["question"])[:110], "options": opts, "answer": ans, "why": str(q.get("why") or "")[:120]}


# -- slides ----------------------------------------------------------------------------
def _story(inner: str, kind: str) -> str:
    return (f'<article class="post s-story s-story-{kind}" data-kind="story" style="height:1920px">'
            f'<div class="s-brand"><div class="wordmark">bayesly<i></i></div></div>{inner}</article>')


def number_slide(n: dict) -> str:
    return _story(f"""
  <p class="s-kicker">Number of the day</p>
  <div class="s-big" data-fit="420">{e(n["value"])}</div>
  <p class="s-big-cap" data-fit="330">{e(n["caption"])}</p>
  <p class="s-story-src">{e(n["headline"])}</p>
  <div class="s-story-foot"><span class="pill fill">More on @bayesly.ai</span></div>""", "number")


def quiz_q_slide(q: dict) -> str:
    opts = "".join(f'<li><span class="n">{"ABC"[i]}</span><p>{e(o)}</p></li>' for i, o in enumerate(q["options"]))
    return _story(f"""
  <p class="s-kicker">Quick quiz</p>
  <h2 class="s-quiz-q" data-fit="440">{e(q["question"])}</h2>
  <ol class="s-quiz-opts">{opts}</ol>
  <div class="s-story-foot"><span class="pill fill">Tap for the answer →</span></div>""", "quiz")


def quiz_a_slide(q: dict) -> str:
    opts = "".join(f'<li class="{"ok" if i == q["answer"] else "no"}"><span class="n">{"ABC"[i]}</span>'
                   f'<p>{e(o)}</p></li>' for i, o in enumerate(q["options"]))
    return _story(f"""
  <p class="s-kicker">Answer</p>
  <h2 class="s-quiz-q" data-fit="440">{e(q["question"])}</h2>
  <ol class="s-quiz-opts">{opts}</ol>
  <p class="s-quiz-why">{e(q["why"])}</p>
  <div class="s-story-foot"><span class="pill fill">Did you get it right?</span></div>""", "answer")


def digest_slide(headlines: list[str], now: datetime) -> str:
    items = "".join(f'<li><span class="n">{i}</span><p>{e(h)}</p></li>' for i, h in enumerate(headlines, start=1))
    day = now.strftime("%b %d").replace(" 0", " ")
    return _story(f"""
  <p class="s-kicker">{e(day)}</p>
  <h2 class="s-story-title">Today<br>in <span class="hl">AI</span></h2>
  <ol class="s-story-list" data-fit="760">{items}</ol>
  <div class="s-story-foot"><span class="pill fill">Full stories on @bayesly.ai</span></div>""", "digest")


def render_stories(slides: list[str], out_base: Path) -> list[Path]:
    """1080x1920 JPEGs, one per slide. Raises RenderError on failure."""
    from bayesly.render import build_page, screenshot
    page = out_base.with_suffix(".story.html")
    page.write_text(build_page(slides), encoding="utf-8")
    _, _, jpegs = screenshot(page, out_base)
    return jpegs
