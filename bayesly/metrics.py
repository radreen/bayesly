"""Read-only stats and comments from Threads / Instagram, reported to the owner's Telegram.

- poll_comments(): new replies (Threads) and comments (Instagram) on our recent posts -> one Telegram
  message each, with a button that opens the post. Seen ids live in state/comments.json.
- morning_summary(): once a day, views / likes / replies / followers with the change since yesterday.
  Snapshots live in state/metrics.json.

Endpoints (official docs, checked 2026-11-21):
  Threads  GET /{media-id}/insights?metric=views,likes,replies,reposts,quotes,shares
           GET /{user-id}/threads_insights?metric=followers_count       (scope threads_manage_insights)
           GET /{media-id}/replies?fields=id,text,username,timestamp     (scope threads_read_replies)
  Instagram GET /{ig-media-id}?fields=like_count,comments_count
           GET /{ig-user-id}?fields=followers_count
           GET /{ig-media-id}/comments?fields=id,text,username,timestamp
  Instagram reach/views need instagram_manage_insights (not granted yet); X metrics need a paid tier.
Nothing here posts anything; a failing call is logged and skipped so `check` never breaks.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any, Callable

from bayesly import net
from bayesly.publish.base import PublishConfig, scrub

log = logging.getLogger(__name__)

THREADS_METRICS = ["views", "likes", "replies", "reposts", "quotes", "shares"]
COMMENT_DAYS = 4         # watch comments on posts from the last few days
POLL_EVERY_MIN = 55      # comment polling at most hourly (Meta app rate limits)
STATS_DAYS = 14
OWN_HANDLES = {"bayesly.ai", "bayesly_ai"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except ValueError:
        return {}


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def recent_posts(state_dir: Path, platform: str, days: int) -> list[dict]:
    """Successful live posts on `platform` from the ledger, newest first."""
    ledger = _load(state_dir / "posted.json")
    cutoff = _now() - timedelta(days=days)
    out = []
    for draft_id, plats in ledger.items():
        e = plats.get(platform) or {}
        if not (e.get("ok") and e.get("post_id")):
            continue
        try:
            at = datetime.fromisoformat(e["at"])
        except (KeyError, ValueError):
            continue
        if at >= cutoff:
            out.append({"draft_id": draft_id, "post_id": str(e["post_id"]), "url": e.get("url") or "", "at": at})
    return sorted(out, key=lambda p: p["at"], reverse=True)


def draft_for(out_dir: Path, draft_id: str) -> dict:
    for path in sorted(out_dir.glob(f"*/{draft_id}-*.json")):
        try:
            return json.loads(path.read_text(encoding="utf-8")).get("draft") or {}
        except ValueError:
            continue
    return {}


def headline_for(out_dir: Path, draft_id: str) -> str:
    return draft_for(out_dir, draft_id).get("headline") or ""


class MetaReader:
    """Tiny read-only client for the two Graph APIs."""

    def __init__(self, cfg: PublishConfig, *, timeout: float = 20, max_retries: int = 1, session: Any = None) -> None:
        self.cfg, self.timeout, self.max_retries, self.session = cfg, timeout, max_retries, session

    def get(self, platform: str, path: str, **params: Any) -> dict:
        base, token = ((self.cfg.threads_base, self.cfg.threads_token) if platform == "threads"
                       else (self.cfg.instagram_base, self.cfg.instagram_token))
        try:
            resp = net.request("GET", f"{base.rstrip('/')}/{path}", timeout=self.timeout, max_retries=self.max_retries,
                               session=self.session, params={**params, "access_token": token})
            return resp.json()
        except (net.ExternalCallError, ValueError) as exc:
            raise RuntimeError(scrub(str(exc), token)[:300]) from None

    def ready(self, platform: str) -> bool:
        c = self.cfg
        return bool(c.threads_user_id and c.threads_token) if platform == "threads" \
            else bool(c.instagram_user_id and c.instagram_token)


def _insight_value(entry: dict) -> int:
    """Threads insights come as values[0].value (media) or total_value.value (user)."""
    if isinstance(entry.get("total_value"), dict):
        return int(entry["total_value"].get("value") or 0)
    values = entry.get("values") or [{}]
    return int(values[0].get("value") or 0)


# -- comments -----------------------------------------------------------------------
def fetch_comments(reader: MetaReader, platform: str, post_id: str) -> list[dict]:
    if platform == "threads":
        data = reader.get("threads", f"{post_id}/replies", fields="id,text,username,timestamp,permalink")
    else:
        data = reader.get("instagram", f"{post_id}/comments", fields="id,text,username,timestamp")
    return [c for c in data.get("data") or [] if c.get("id")]


def comment_message(platform: str, comment: dict, headline: str, reply: str = "") -> str:
    where = {"threads": "Threads", "instagram": "Instagram"}[platform]
    text = escape((comment.get("text") or "").strip()[:600]) or "<i>(no text)</i>"
    lines = [f"💬 <b>New comment on {where}</b>", f"<b>@{escape(comment.get('username') or '?')}</b>: {text}"]
    if headline:
        lines.append(f"\n<i>on: {escape(headline[:120])}</i>")
    if reply:
        lines.append(f"\n✍️ Suggested reply (tap to copy):\n<code>{escape(reply)}</code>")
    return "\n".join(lines)


def poll_comments(settings: Any, cfg: PublishConfig, send: Callable[[str, list], Any], *,
                  reader: MetaReader | None = None, drafter: Callable[[str, str, str], str] | None = None) -> list[str]:
    """Send each new comment once. The first run only records what is there (no flood of old ones).
    drafter(headline, post_text, comment) -> a suggested reply (optional, Gemini)."""
    reader = reader or MetaReader(cfg)
    path = settings.state_dir / "comments.json"
    state = _load(path)
    first_run = not state
    now = datetime.now(timezone.utc)
    last = state.get("last_poll")
    # Meta's app rate limit for a small account is low: an Instagram publish failed with "Application
    # request limit reached" (2026-11-28) while we polled every 15 min. Poll hourly, and on Instagram
    # ask once for all comment counts, then fetch comments only where the count went up.
    if last and now - datetime.fromisoformat(last) < timedelta(minutes=POLL_EVERY_MIN):
        return []
    state["last_poll"] = now.isoformat(timespec="seconds")
    seen: dict[str, list[str]] = state.get("seen") or {}
    counts: dict[str, int] = state.get("counts") or {}
    report = []
    for platform in ("threads", "instagram"):
        if platform not in cfg.platforms or not reader.ready(platform):
            continue
        posts = recent_posts(settings.state_dir, platform, COMMENT_DAYS)
        if platform == "instagram" and posts:
            try:
                media = reader.get("instagram", f"{cfg.instagram_user_id}/media", fields="id,comments_count", limit=30)
                live = {str(m["id"]): int(m.get("comments_count") or 0) for m in media.get("data") or []}
            except RuntimeError as exc:
                log.warning("instagram comment counts: %s", exc)
                continue
            changed = [p for p in posts if live.get(p["post_id"], 0) > counts.get(p["post_id"], 0)]
            for p in posts:
                counts[p["post_id"]] = live.get(p["post_id"], counts.get(p["post_id"], 0))
            posts = changed
        for post in posts:
            try:
                comments = fetch_comments(reader, platform, post["post_id"])
            except RuntimeError as exc:
                log.warning("comments %s %s: %s", platform, post["post_id"], exc)
                continue
            known = set(seen.get(post["post_id"]) or [])
            new = [c for c in comments if c["id"] not in known
                   and (c.get("username") or "").lower() not in OWN_HANDLES]
            for c in new:
                if not first_run:
                    url = c.get("permalink") or post["url"]
                    buttons = [[("Open post", url)]] if url.startswith("http") else []
                    d = draft_for(settings.out_dir, post["draft_id"])
                    post_text = d.get("threads_post" if platform == "threads" else "instagram_caption") or ""
                    reply = drafter(d.get("headline") or "", post_text, c.get("text") or "") \
                        if drafter and c.get("text") else ""
                    try:
                        send(comment_message(platform, c, d.get("headline") or "", reply), buttons)
                    except Exception as exc:  # noqa: BLE001 - a Telegram hiccup must not lose the seen list
                        log.warning("telegram comment alert failed: %s", exc)
                        continue
                known.add(c["id"])
                report.append(f"{platform}: comment {c['id']}" + (" (recorded)" if first_run else ""))
            seen[post["post_id"]] = sorted(known)
    state["seen"] = seen
    state["counts"] = counts
    _save(path, state)
    return report


# -- stats + morning summary ---------------------------------------------------------
def collect_stats(settings: Any, cfg: PublishConfig, reader: MetaReader | None = None) -> dict:
    reader = reader or MetaReader(cfg)
    snap: dict[str, Any] = {"at": _now().isoformat(timespec="seconds"), "followers": {}, "posts": {}}
    if "threads" in cfg.platforms and reader.ready("threads"):
        try:
            data = reader.get("threads", f"{cfg.threads_user_id}/threads_insights", metric="followers_count")
            for e in data.get("data") or []:
                if e.get("name") == "followers_count":
                    snap["followers"]["threads"] = _insight_value(e)
        except RuntimeError as exc:
            log.warning("threads followers: %s", exc)
        for post in recent_posts(settings.state_dir, "threads", STATS_DAYS):
            try:
                data = reader.get("threads", f"{post['post_id']}/insights", metric=",".join(THREADS_METRICS))
                snap["posts"][f"threads:{post['post_id']}"] = {
                    e["name"]: _insight_value(e) for e in data.get("data") or [] if e.get("name")}
            except RuntimeError as exc:
                log.warning("threads insights %s: %s", post["post_id"], exc)
    if "instagram" in cfg.platforms and reader.ready("instagram"):
        try:
            data = reader.get("instagram", cfg.instagram_user_id, fields="followers_count")
            if "followers_count" in data:
                snap["followers"]["instagram"] = int(data["followers_count"])
        except RuntimeError as exc:
            log.warning("instagram followers: %s", exc)
        for post in recent_posts(settings.state_dir, "instagram", STATS_DAYS):
            try:
                data = reader.get("instagram", post["post_id"], fields="like_count,comments_count")
                snap["posts"][f"instagram:{post['post_id']}"] = {
                    "likes": int(data.get("like_count") or 0), "replies": int(data.get("comments_count") or 0)}
            except RuntimeError as exc:
                log.warning("instagram media %s: %s", post["post_id"], exc)
    return snap


def _totals(snap: dict, platform: str) -> dict[str, int]:
    tot: dict[str, int] = {}
    for key, vals in (snap.get("posts") or {}).items():
        if key.startswith(platform + ":"):
            for k, v in vals.items():
                tot[k] = tot.get(k, 0) + int(v)
    return tot


def _delta(now: int, before: int | None) -> str:
    if before is None:
        return f"{now}"
    d = now - before
    return f"{now} ({'+' if d >= 0 else ''}{d})"


def summary_message(snap: dict, prev: dict | None, posted_24h: int, best: tuple[str, int, str] | None) -> str:
    prev = prev or {}
    lines = ["☀️ <b>Bayesly — morning summary</b>", f"Posts in the last 24 h: <b>{posted_24h}</b>", ""]
    th, th_prev = _totals(snap, "threads"), _totals(prev, "threads") if prev else {}
    f, fp = snap.get("followers") or {}, prev.get("followers") or {}
    if th or "threads" in f:
        lines.append("<b>Threads</b> (posts of the last 14 days)")
        lines.append(f"  followers {_delta(f.get('threads', 0), fp.get('threads'))}")
        for k in ("views", "likes", "replies", "reposts", "shares"):
            lines.append(f"  {k} {_delta(th.get(k, 0), th_prev.get(k) if prev else None)}")
    ig, ig_prev = _totals(snap, "instagram"), _totals(prev, "instagram") if prev else {}
    if ig or "instagram" in f:
        lines.append("<b>Instagram</b>")
        lines.append(f"  followers {_delta(f.get('instagram', 0), fp.get('instagram'))}")
        for k in ("likes", "replies"):
            lines.append(f"  {'comments' if k == 'replies' else k} {_delta(ig.get(k, 0), ig_prev.get(k) if prev else None)}")
        lines.append("  <i>views/reach: needs the instagram_manage_insights permission</i>")
    if best:
        lines += ["", f"Top Threads post: <b>{best[1]}</b> views — {escape(best[2][:100])}"]
    lines.append("<i>X: stats need a paid API tier, skipped.</i>")
    return "\n".join(lines)


def summary_due(state: dict, now: datetime | None = None) -> bool:
    now = now or _now()
    hour = int(os.getenv("SUMMARY_HOUR_UTC", "6") or 6)   # 06 UTC = 08:00 Kyiv in winter
    return now.hour >= hour and state.get("summary_sent") != now.date().isoformat()


def morning_summary(settings: Any, cfg: PublishConfig, send: Callable[[str, list], Any], *,
                    reader: MetaReader | None = None, now: datetime | None = None, force: bool = False) -> str | None:
    path = settings.state_dir / "metrics.json"
    state = _load(path)
    now = now or _now()
    if not force and not summary_due(state, now):
        return None
    snap = collect_stats(settings, cfg, reader)
    days = state.setdefault("days", {})
    yesterday = days.get((now.date() - timedelta(days=1)).isoformat())
    ledger = _load(settings.state_dir / "posted.json")
    posted_24h = 0
    for plats in ledger.values():
        ats = [e.get("at") for e in plats.values() if e.get("ok") and e.get("at")]
        if ats and datetime.fromisoformat(min(ats)) >= now - timedelta(hours=24):
            posted_24h += 1
    best = None
    for post in recent_posts(settings.state_dir, "threads", STATS_DAYS):
        v = (snap["posts"].get(f"threads:{post['post_id']}") or {}).get("views", 0)
        if v and (best is None or v > best[1]):
            best = (post["url"], v, headline_for(settings.out_dir, post["draft_id"]) or "(no headline)")
    buttons = [[("Open top post", best[0])]] if best and best[0].startswith("http") else []
    send(summary_message(snap, yesterday, posted_24h, best), buttons)
    days[now.date().isoformat()] = snap
    for old in sorted(days)[:-30]:       # keep a month of daily snapshots
        days.pop(old)
    state["summary_sent"] = now.date().isoformat()
    _save(path, state)
    return f"summary sent ({len(snap['posts'])} posts, followers {snap['followers']})"
