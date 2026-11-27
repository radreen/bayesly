"""Tell the owner when the bot goes quiet. On 2026-11-26 it published nothing for 21 hours (Gemini's
free daily quota was used up) and every run still looked "successful". Now: no post for
QUIET_ALERT_HOURS during daytime -> one Telegram message with the reason, at most every 6 hours."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any, Callable

QUIET_ALERT_HOURS = 6
REPEAT_HOURS = 6


def last_post_at(state_dir: Path) -> datetime | None:
    path = state_dir / "posted.json"
    ledger = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    times = [datetime.fromisoformat(e["at"]) for p in ledger.values() for e in p.values() if e.get("ok") and e.get("at")]
    return max(times) if times else None


def quota_note(state_dir: Path, now: datetime) -> str:
    path = state_dir / "gemini_quota.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    out = [f"{m} until {datetime.fromisoformat(t):%H:%M} UTC" for m, t in data.items() if datetime.fromisoformat(t) > now]
    return ("Gemini out of free quota: " + "; ".join(out)) if out else ""


def check_quiet(state_dir: Path, why: str, send: Callable[[str, list], Any], *, paused: bool = False,
                now: datetime | None = None) -> str | None:
    now = now or datetime.now(timezone.utc)
    if paused or not 6 <= now.hour <= 21:        # nights are allowed to be quiet; a pause is on purpose
        return None
    last = last_post_at(state_dir)
    if last and now - last < timedelta(hours=QUIET_ALERT_HOURS):
        return None
    flag = state_dir / "watchdog.json"
    seen = json.loads(flag.read_text(encoding="utf-8")) if flag.exists() else {}
    if seen.get("alerted") and now - datetime.fromisoformat(seen["alerted"]) < timedelta(hours=REPEAT_HOURS):
        return None
    hours = f"{(now - last).total_seconds() / 3600:.0f} h" if last else "ever"
    lines = [f"⚠️ <b>No post for {hours}</b>", f"Last check: {escape(why[:200])}"]
    quota = quota_note(state_dir, now)
    if quota:
        lines += [escape(quota), "Fix: a paid Gemini tier (cents per day) or wait for the reset."]
    send("\n".join(lines), [])
    flag.write_text(json.dumps({"alerted": now.isoformat(timespec="seconds")}), encoding="utf-8")
    return f"quiet alert sent ({hours})"
