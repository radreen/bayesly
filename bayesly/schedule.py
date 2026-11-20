"""When to post. A cheap check runs every 15 minutes; the full autopost (drafting, carousel, video)
only starts when it is worth it. Being early matters (docs/competitor-analysis.md), flooding does not.

go = budget allows it  AND  (an approved held post waits
                             OR a hot story appeared: fresh + high score
                             OR nothing was posted for a while and a decent fresh story exists)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from bayesly.config import Settings
from bayesly.models import Item
from bayesly.publish.base import Ledger
from bayesly.review import DraftStore


def _env(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Rules:
    max_per_day: int = 8            # MAX_POSTS_PER_DAY
    min_gap_min: float = 60         # MIN_GAP_MINUTES between two posts
    hot_score: float = 1.0          # HOT_SCORE: names + money/conflict + very fresh lands here
    hot_age_h: float = 2.0          # HOT_AGE_HOURS
    quiet_h: float = 5.0            # QUIET_HOURS: post something decent if silent this long
    decent_score: float = 0.8
    decent_age_h: float = 12.0

    @classmethod
    def from_env(cls) -> "Rules":
        return cls(max_per_day=int(_env("MAX_POSTS_PER_DAY", 8)), min_gap_min=_env("MIN_GAP_MINUTES", 60),
                   hot_score=_env("HOT_SCORE", 1.0), hot_age_h=_env("HOT_AGE_HOURS", 2.0),
                   quiet_h=_env("QUIET_HOURS", 5.0))


def post_times(ledger: Ledger) -> list[datetime]:
    """When each draft first went out successfully (one time per post, not per platform)."""
    out = []
    for platforms in ledger._load().values():
        times = [v.get("at") for v in platforms.values() if v.get("ok") and v.get("at")]
        if times:
            out.append(datetime.fromisoformat(min(times)))
    return sorted(out)


def decide(ranked: list[Item], drafted: set[str], approved_waiting: int, posted: list[datetime],
           rules: Rules, now: datetime, key=lambda i: i.url) -> tuple[bool, str]:
    today = [t for t in posted if t.date() == now.date()]
    if len(today) >= rules.max_per_day:
        return False, f"daily cap reached ({len(today)}/{rules.max_per_day})"
    last = posted[-1] if posted else None
    if last and now - last < timedelta(minutes=rules.min_gap_min):
        return False, f"last post {int((now - last).total_seconds() // 60)} min ago (< {rules.min_gap_min:.0f})"
    if approved_waiting:
        return True, f"{approved_waiting} approved post(s) waiting"

    def age_h(item: Item) -> float:
        return (now - item.published).total_seconds() / 3600 if item.published else 1e9

    fresh = [i for i in ranked if key(i) not in drafted]
    hot = [i for i in fresh if i.score >= rules.hot_score and age_h(i) <= rules.hot_age_h]
    if hot:
        return True, f"hot: {hot[0].title[:70]} ({hot[0].score:.2f}, {age_h(hot[0]):.1f} h old)"
    quiet = last is None or now - last >= timedelta(hours=rules.quiet_h)
    decent = [i for i in fresh if i.score >= rules.decent_score and age_h(i) <= rules.decent_age_h]
    if quiet and decent:
        return True, f"quiet {'since start' if last is None else f'{(now - last).total_seconds() / 3600:.1f} h'}: {decent[0].title[:60]}"
    return False, "nothing hot"


def check(settings: Settings) -> tuple[bool, str]:
    """Network: RSS only (no LLM, no browser). Used by `python -m bayesly check`."""
    from bayesly.autopost import HeldStore
    from bayesly.dedupe import item_key
    from bayesly.pipeline import RunResult, collect_and_rank

    ranked = collect_and_rank(settings, RunResult())
    drafted = DraftStore(settings.out_dir, settings.state_dir).drafted_ids()
    approved = sum(1 for h in HeldStore(settings.state_dir).load().values() if h.get("status") == "approved")
    return decide(ranked, drafted, approved, post_times(Ledger(settings.state_dir)), Rules.from_env(),
                  datetime.now(timezone.utc), key=item_key)
