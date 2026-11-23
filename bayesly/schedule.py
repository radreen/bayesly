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


APPROVED_GAP_MIN = 15


# -- per-platform daily caps ---------------------------------------------------------------
# Meta flagged the business portfolio for "automation" on 2026-11-22 (cleared 2026-11-23 after an ID
# check). So Instagram/Threads ramp up slowly: META_CAP_START posts/day from META_RAMP_FROM, +1 every
# META_RAMP_EVERY_DAYS, up to META_CAP_MAX. X is unaffected. MAX_POSTS_PER_DAY_<PLATFORM> overrides.
META = ("threads", "instagram")


def platform_cap(platform: str, now: datetime, breaking: bool = False) -> int:
    """Routine posts per day on `platform`. Breaking news and owner-approved posts get the hard ceiling."""
    if breaking:
        return hard_max()
    override = os.getenv(f"MAX_POSTS_PER_DAY_{platform.upper()}", "").strip()
    if override:
        try:
            return int(override)
        except ValueError:
            pass
    if platform not in META:
        return int(_env("MAX_POSTS_PER_DAY", 8))
    start, top, every = int(_env("META_CAP_START", 4)), int(_env("META_CAP_MAX", 8)), _env("META_RAMP_EVERY_DAYS", 4)
    try:
        since = datetime.fromisoformat(os.getenv("META_RAMP_FROM", "2026-11-23")).replace(tzinfo=timezone.utc)
    except ValueError:
        return start
    days = max(0, (now - since).days)
    return min(top, start + int(days // max(every, 1)))


def posted_today(ledger: Ledger, platform: str, now: datetime) -> int:
    day = now.date()
    return sum(1 for plats in ledger._load().values()
               for p, e in plats.items()
               if p == platform and e.get("ok") and e.get("at") and datetime.fromisoformat(e["at"]).date() == day)


HOT_GAP_MIN = 20        # a breaking story waits at most this long after our previous post
HARD_MAX_PER_DAY = 12   # absolute ceiling per network, breaking news included (anti-spam)


def hard_max() -> int:
    return int(_env("HARD_MAX_PER_DAY", HARD_MAX_PER_DAY))


def is_breaking(score: float, published: datetime | None, now: datetime, rules: "Rules | None" = None) -> bool:
    """A big, fresh story: daily caps and the normal 60-min gap don't apply (only the hard ceiling).
    A news account that posts the story of the day 3 hours late loses the moment."""
    rules = rules or Rules.from_env()
    if published is None:
        return False
    return score >= rules.hot_score and (now - published) <= timedelta(hours=rules.hot_age_h + 1)


def decide(ranked: list[Item], drafted: set[str], approved_waiting: int, posted: list[datetime],
           rules: Rules, now: datetime, key=lambda i: i.url) -> tuple[bool, str]:
    today = [t for t in posted if t.date() == now.date()]
    if len(today) >= hard_max():
        return False, f"hard daily ceiling reached ({len(today)}/{hard_max()})"
    last = posted[-1] if posted else None
    since = (now - last) if last else None
    # The owner tapped Publish: he expects it out now, not an hour later. Only a short gap applies.
    if approved_waiting and (since is None or since >= timedelta(minutes=APPROVED_GAP_MIN)):
        return True, f"{approved_waiting} approved post(s) waiting"

    def age_h(item: Item) -> float:
        return (now - item.published).total_seconds() / 3600 if item.published else 1e9

    fresh = [i for i in ranked if key(i) not in drafted]
    hot = [i for i in fresh if i.score >= rules.hot_score and age_h(i) <= rules.hot_age_h]
    if hot and (since is None or since >= timedelta(minutes=HOT_GAP_MIN)):
        # breaking news: ignores the routine cap and the 60-min gap
        return True, f"hot: {hot[0].title[:70]} ({hot[0].score:.2f}, {age_h(hot[0]):.1f} h old)"
    if len(today) >= rules.max_per_day:
        return False, f"daily cap reached ({len(today)}/{rules.max_per_day}); only breaking news now"
    if since is not None and since < timedelta(minutes=rules.min_gap_min):
        return False, f"last post {int(since.total_seconds() // 60)} min ago (< {rules.min_gap_min:.0f})"
    quiet = last is None or now - last >= timedelta(hours=rules.quiet_h)
    decent = [i for i in fresh if i.score >= rules.decent_score and age_h(i) <= rules.decent_age_h]
    if quiet and decent:
        return True, f"quiet {'since start' if last is None else f'{(now - last).total_seconds() / 3600:.1f} h'}: {decent[0].title[:60]}"
    return False, "nothing hot"


def check(settings: Settings, keep: list | None = None) -> tuple[bool, str]:
    """Network: RSS only (no LLM, no browser). Used by `python -m bayesly check`."""
    from bayesly.autopost import HeldStore
    from bayesly.dedupe import item_key
    from bayesly.pipeline import RunResult, collect_and_rank

    ranked = collect_and_rank(settings, RunResult())
    if keep is not None:      # hand the ranking to the owner reports: no second RSS fetch
        keep.extend(ranked)
    drafted = DraftStore(settings.out_dir, settings.state_dir).drafted_ids()
    approved = sum(1 for h in HeldStore(settings.state_dir).load().values() if h.get("status") == "approved")
    return decide(ranked, drafted, approved, post_times(Ledger(settings.state_dir)), Rules.from_env(),
                  datetime.now(timezone.utc), key=item_key)
