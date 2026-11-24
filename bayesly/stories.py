"""Instagram Stories: a second, lighter format next to the feed (owner request 2026-11-24).

1. Every Reel also goes to Stories (same video): followers see it in the story tray.
2. Once a day in the evening: "Today in AI", the day's top 3 headlines on one 9:16 card.

API (docs checked 2026-11-24): POST /{ig-user-id}/media with media_type=STORIES and image_url or
video_url, then media_publish. Stories: 9:16, video 3-60 s, JPEG images. No stickers via the API
(no links, polls, locations). Stories count toward the account's publishing quota, so we cap them:
STORIES_PER_DAY (default 4) in total, digest included. STORIES=0 switches all of it off.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from html import escape as e
from pathlib import Path

DIGEST_HOUR_UTC = 17      # 19:00 Kyiv in winter: evening scroll
MIN_POSTS_FOR_DIGEST = 2


def enabled() -> bool:
    return os.getenv("STORIES", "1").strip().lower() not in {"0", "false", "no", "off"}


def per_day() -> int:
    try:
        return int(os.getenv("STORIES_PER_DAY", "4"))
    except ValueError:
        return 4


class StoryLog:
    """state/stories.json: {"day": "YYYY-MM-DD", "count": n, "digest": "YYYY-MM-DD", "posts": {draft_id: story_id}}"""

    def __init__(self, state_dir: Path) -> None:
        self.path = state_dir / "stories.json"

    def load(self, now: datetime) -> dict:
        data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        today = now.date().isoformat()
        if data.get("day") != today:
            data["day"], data["count"] = today, 0
        data.setdefault("posts", {})
        return data

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data["posts"] = dict(list(data.get("posts", {}).items())[-200:])
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def room(self, now: datetime) -> bool:
        return self.load(now)["count"] < per_day()

    def note(self, now: datetime, *, draft_id: str | None = None, story_id: str = "", digest: bool = False) -> None:
        data = self.load(now)
        data["count"] += 1
        if draft_id:
            data["posts"][draft_id] = story_id
        if digest:
            data["digest"] = data["day"]
        self.save(data)


def digest_due(log: StoryLog, now: datetime, posts_today: int) -> bool:
    data = log.load(now)
    return (now.hour >= DIGEST_HOUR_UTC and data.get("digest") != data["day"]
            and posts_today >= MIN_POSTS_FOR_DIGEST and data["count"] < per_day())


def today_headlines(state_dir: Path, out_dir: Path, now: datetime, n: int = 3) -> list[str]:
    """Card headlines of today's published posts, best score first."""
    ledger_path = state_dir / "posted.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else {}
    rows = []
    for draft_id, plats in ledger.items():
        ats = [datetime.fromisoformat(x["at"]) for x in plats.values() if x.get("ok") and x.get("at")]
        if not ats or min(ats).date() != now.date():
            continue
        for path in out_dir.glob(f"*/{draft_id}-*.json"):
            rec = json.loads(path.read_text(encoding="utf-8"))
            d = rec.get("draft") or {}
            head = ((d.get("card") or {}).get("headline") or d.get("headline") or "").strip()
            if head and (rec.get("source") or {}).get("id") != "bayesly-weekly":
                rows.append((float(rec.get("score") or 0), head))
            break
    return [h for _, h in sorted(rows, reverse=True)[:n]]


def digest_slide(headlines: list[str], now: datetime) -> str:
    items = "".join(f'<li><span class="n">{i}</span><p>{e(h)}</p></li>' for i, h in enumerate(headlines, start=1))
    day = now.strftime("%b %d").replace(" 0", " ")
    return f"""
<article class="post s-story" data-kind="story" style="height:1920px">
  <div class="s-brand"><div class="wordmark">bayesly<i></i></div></div>
  <p class="s-kicker">{e(day)}</p>
  <h2 class="s-story-title">Today<br>in <span class="hl">AI</span></h2>
  <ol class="s-story-list" data-fit="760">{items}</ol>
  <div class="s-story-foot"><span class="pill fill">Full stories on @bayesly.ai</span></div>
</article>"""


def render_digest(headlines: list[str], out_base: Path, now: datetime) -> Path:
    """One 1080x1920 JPEG. Raises RenderError on failure."""
    from bayesly.render import build_page, screenshot
    page = out_base.with_suffix(".story.html")
    page.write_text(build_page([digest_slide(headlines, now)]), encoding="utf-8")
    _, _, jpegs = screenshot(page, out_base)
    return jpegs[0]
