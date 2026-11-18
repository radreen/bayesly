"""Autopost: pick the best fresh draft, check it, publish it everywhere, or hold it for the owner.

One run:
  1. Kill switch: BAYESLY_PAUSED=1 or a state/PAUSED file -> do nothing.
  2. Telegram sync: apply the owner's Publish/Skip taps and /pause /resume.
  3. Approved held drafts go first, then fresh drafts by score.
  4. Quality gate: anything doubtful is held and sent to Telegram instead of published.
  5. Publish to every enabled platform; state/posted.json makes it idempotent per platform.

Dry-run (the default) prints what would happen and writes/sends nothing.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from bayesly.config import Settings
from bayesly.publish.base import Ledger, PublishConfig, PublishPost, PublishResult, Publisher
from bayesly.telegram import OffsetStore, Telegram, TelegramError

log = logging.getLogger(__name__)

MAX_AGE_HOURS = 48          # older drafts are stale news: never auto-published
MIN_RELIABILITY = 0.7       # less reliable sources always go through the owner
TEXT_KEYS = {"threads": "threads_post", "x": "x_post", "instagram": "instagram_caption"}


# -- kill switch ---------------------------------------------------------------
def pause_file(settings: Settings) -> Path:
    return settings.state_dir / "PAUSED"


def is_paused(settings: Settings, cfg: PublishConfig) -> bool:
    return cfg.paused_env or pause_file(settings).exists()


def set_paused(settings: Settings, paused: bool, reason: str = "") -> None:
    f = pause_file(settings)
    if paused:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {reason}\n", encoding="utf-8")
    elif f.exists():
        f.unlink()


# -- held queue ----------------------------------------------------------------
class HeldStore:
    """state/held.json: drafts waiting for the owner. status: pending | approved | skipped | published."""

    def __init__(self, state_dir: Path) -> None:
        self.path = state_dir / "held.json"

    def load(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def set(self, draft_id: str, **fields) -> None:
        data = self.load()
        data.setdefault(draft_id, {}).update(fields)
        self.save(data)


# -- quality gate --------------------------------------------------------------
def gate(record: dict, platforms: list[str], now: datetime | None = None) -> list[str]:
    """Reasons to hold a draft. Empty list = safe to publish without a human."""
    reasons: list[str] = []
    draft = record.get("draft")
    if not draft:
        return ["no parsed draft"]
    if draft.get("insufficient_source"):
        reasons.append("model said the source text is insufficient")
    if draft.get("confidence") not in {"medium", "high"}:
        reasons.append(f"confidence is {draft.get('confidence')!r}")
    if (record.get("source") or {}).get("reliability", 0) < MIN_RELIABILITY:
        reasons.append("source reliability below threshold")
    for w in record.get("lint_warnings") or []:
        reasons.append(f"lint: {w}")
    for w in record.get("image_warnings") or []:
        if "trimmed" in w:
            continue  # cosmetic: the card shows "…"; the post text itself is untouched
        reasons.append(f"image: {w}")
    if not record.get("images_jpg"):
        reasons.append("no rendered images")
    for p in platforms:
        if not (draft.get(TEXT_KEYS.get(p, "")) or "").strip():
            reasons.append(f"no {p} text")
    return reasons


def is_fresh(record: dict, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    try:
        created = datetime.fromisoformat(record["created_at"])
    except (KeyError, ValueError):
        return False
    return now - created <= timedelta(hours=MAX_AGE_HOURS)


# -- the run -------------------------------------------------------------------
@dataclass
class RunReport:
    paused: bool = False
    published: list[tuple[str, list[PublishResult]]] = field(default_factory=list)
    held: list[tuple[str, list[str]]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        out = ["paused: nothing done"] if self.paused else []
        for draft_id, results in self.published:
            parts = []
            for r in results:
                state = "dry-run" if r.dry_run else ("ok" if r.ok else f"FAILED ({r.error})")
                parts.append(f"{r.platform}={state}" + (f" {r.url}" if r.url else ""))
            out.append(f"publish {draft_id}: " + "; ".join(parts))
        for draft_id, reasons in self.held:
            out.append(f"held {draft_id}: " + "; ".join(reasons[:4]))
        out += [f"error: {e}" for e in self.errors]
        return out or ["nothing to do"]


class Autoposter:
    def __init__(self, settings: Settings, cfg: PublishConfig, publishers: dict[str, Publisher], *,
                 media_upload: Callable[[Path, str], str], telegram: Telegram, live: bool,
                 render: Callable[[Path], None] | None = None) -> None:
        self.settings, self.cfg, self.publishers = settings, cfg, publishers
        self.media_upload, self.telegram, self.live, self.render = media_upload, telegram, live, render
        self.ledger = Ledger(settings.state_dir)
        self.held = HeldStore(settings.state_dir)

    # Telegram ---------------------------------------------------------------
    def sync_telegram(self) -> list[str]:
        """Apply the owner's taps/commands. Returns a log of what was done."""
        if not (self.telegram.enabled and self.live):
            return []  # dry-run never consumes the owner's taps
        done: list[str] = []
        offsets = OffsetStore(self.settings.state_dir)
        offset = offsets.get()
        try:
            updates = self.telegram.updates(offset)
        except TelegramError as exc:
            return [f"telegram sync failed: {exc}"]
        for upd in updates:
            offset = max(offset, upd["update_id"] + 1)
            if not self.telegram.from_owner(upd):
                continue  # strangers can't control the bot
            try:
                done += self._handle(upd)
            except TelegramError as exc:
                done.append(f"telegram: {exc}")
        offsets.set(offset)
        return done

    def _handle(self, upd: dict) -> list[str]:
        if "callback_query" in upd:
            cq = upd["callback_query"]
            action, _, draft_id = (cq.get("data") or "").partition(":")
            held = self.held.load()
            if draft_id not in held or action not in {"pub", "skip"}:
                self.telegram.answer(cq["id"], "Unknown post")
                return []
            status = "approved" if action == "pub" else "skipped"
            if self.live:
                self.held.set(draft_id, status=status, decided_at=_now())
                msg_id = (cq.get("message") or {}).get("message_id")
                if msg_id:
                    self.telegram.clear_buttons(msg_id)
            self.telegram.answer(cq["id"], "Will publish on the next run" if status == "approved" else "Skipped")
            return [f"{draft_id}: {status}"]
        text = ((upd.get("message") or {}).get("text") or "").strip().lower()
        if text.startswith("/pause"):
            if self.live:
                set_paused(self.settings, True, "via Telegram")
            self.telegram.send("Paused. Nothing will be published until /resume.")
            return ["paused via Telegram"]
        if text.startswith("/resume"):
            if self.live:
                set_paused(self.settings, False)
            self.telegram.send("Resumed.")
            return ["resumed via Telegram"]
        if text.startswith("/status"):
            held = self.held.load()
            pending = sum(1 for h in held.values() if h.get("status") == "pending")
            paused = is_paused(self.settings, self.cfg)
            self.telegram.send(f"Paused: {'yes' if paused else 'no'}\nHeld, waiting for you: {pending}\n"
                               f"Platforms: {', '.join(self.cfg.platforms)}")
            return ["status sent"]
        return []

    # Queue ------------------------------------------------------------------
    def _records(self) -> list[tuple[Path, dict]]:
        out = []
        for path in sorted(self.settings.out_dir.rglob("*.json")):
            try:
                out.append((path, json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, ValueError):
                continue
        return out

    def _pending_platforms(self, draft_id: str) -> list[str]:
        return [p for p in self.cfg.platforms if p in self.publishers and not self.ledger.done(draft_id, p)]

    def queue(self) -> tuple[list[tuple[Path, dict]], list[tuple[Path, dict]]]:
        """(approved-by-owner or half-published, fresh unreviewed) — both best-first."""
        held = self.held.load()
        first, fresh = [], []
        for path, rec in self._records():
            rid = rec.get("id")
            if not rid or not rec.get("draft") or not self._pending_platforms(rid):
                continue
            h = held.get(rid, {}).get("status")
            if h == "approved" or (self.ledger.touched(rid) and h != "skipped"):
                first.append((path, rec))
            elif h is None and is_fresh(rec):
                fresh.append((path, rec))
        key = lambda pr: pr[1].get("score") or 0  # noqa: E731
        return sorted(first, key=key, reverse=True), sorted(fresh, key=key, reverse=True)

    # Run --------------------------------------------------------------------
    def run(self, count: int) -> RunReport:
        report = RunReport()
        for line in self.sync_telegram():
            log.info("telegram", extra={"action": line})
        if is_paused(self.settings, self.cfg):
            report.paused = True
            return report

        first, fresh = self.queue()
        published = 0
        for path, rec in first:
            if published >= count:
                break
            report.published.append((rec["id"], self.publish_record(path, rec)))
            published += 1
        for path, rec in fresh:
            if published >= count:
                break
            self._ensure_images(path, rec, report)
            rec = json.loads(path.read_text(encoding="utf-8"))
            reasons = gate(rec, self.cfg.platforms)
            if reasons:
                self.hold(path, rec, reasons, report)
                continue
            report.published.append((rec["id"], self.publish_record(path, rec)))
            published += 1
        if self.live and self.telegram.enabled and (report.published or report.errors):
            try:
                self.telegram.send("Bayesly run\n" + "\n".join(report.lines()))
            except TelegramError as exc:
                log.warning("telegram summary failed", extra={"error": str(exc)})
        return report

    def _ensure_images(self, path: Path, rec: dict, report: RunReport) -> None:
        jpgs = rec.get("images_jpg") or []
        if jpgs and all(Path(p).exists() for p in jpgs):
            return
        if self.render is None:
            return
        try:
            self.render(path)
        except Exception as exc:  # noqa: BLE001 - a render failure holds the post, never crashes the run
            report.errors.append(f"render {rec.get('id')}: {exc}")

    def hold(self, path: Path, rec: dict, reasons: list[str], report: RunReport) -> None:
        report.held.append((rec["id"], reasons))
        if not self.live:
            return
        self.held.set(rec["id"], status="pending", path=str(path), reasons=reasons, held_at=_now())
        _set_status(path, "held")
        if not self.telegram.enabled:
            return
        draft = rec.get("draft") or {}
        src = rec.get("source") or {}
        text = (f"HELD for review: {draft.get('headline') or src.get('title')}\n\n"
                f"Why held:\n- " + "\n- ".join(reasons[:8]) +
                f"\n\nThreads:\n{draft.get('threads_post', '')}\n\nX:\n{draft.get('x_post', '')}\n\n"
                f"Instagram:\n{draft.get('instagram_caption', '')}\n\nSource: {src.get('url', '')}")
        try:
            jpgs = [Path(p) for p in rec.get("images_jpg") or [] if Path(p).exists()]
            if jpgs:
                self.telegram.send_photo(jpgs[0], caption=draft.get("headline", ""))
            msg_id = self.telegram.send(text, buttons=[[("Publish", f"pub:{rec['id']}"), ("Skip", f"skip:{rec['id']}")]])
            self.held.set(rec["id"], message_id=msg_id)
        except TelegramError as exc:
            report.errors.append(f"telegram hold {rec['id']}: {exc}")

    def publish_record(self, path: Path, rec: dict) -> list[PublishResult]:
        draft_id = rec["id"]
        draft = rec.get("draft") or {}
        if self.render and not all(Path(p).exists() for p in rec.get("images_jpg") or ["-"]):
            try:
                self.render(path)  # CI keeps only JSON between runs; images are re-rendered
                rec = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                return [PublishResult("render", ok=False, error=str(exc)[:300])]
        day = (rec.get("created_at") or _now())[:10]
        try:
            urls = [self.media_upload(Path(p), f"{day}/{draft_id}/{i:02d}.jpg")
                    for i, p in enumerate(rec.get("images_jpg") or [], start=1)]
        except Exception as exc:  # noqa: BLE001 - hosting failure: nothing was posted, retry next run
            return [PublishResult("media", ok=False, error=str(exc)[:300])]
        post = PublishPost(draft_id=draft_id, image_urls=urls, source_url=(rec.get("source") or {}).get("url", ""),
                           texts={p: draft.get(k, "") or "" for p, k in TEXT_KEYS.items()})
        results = []
        for platform in self._pending_platforms(draft_id):
            result = self.publishers[platform].publish(post)
            self.ledger.record(draft_id, result)
            results.append(result)
            log.info("publish", extra={"draft": draft_id, "platform": platform, "ok": result.ok,
                                       "dry_run": result.dry_run, "error": result.error})
        if self.live:
            if self.held.load().get(draft_id):
                self.held.set(draft_id, status="published")
            if all(r.ok for r in results):
                _set_status(path, "published")
        return results


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _set_status(path: Path, status: str) -> None:
    rec = json.loads(path.read_text(encoding="utf-8"))
    rec["status"] = status
    path.write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")


def build_autoposter(settings: Settings, cfg: PublishConfig, *, live: bool, palette: str = "lime") -> Autoposter:
    from bayesly.publish.media import GitHubMediaHost
    from bayesly.publish.meta import InstagramPublisher, ThreadsPublisher
    from bayesly.publish.x import XPublisher

    kw = dict(timeout=max(settings.http_timeout, 30), max_retries=settings.http_max_retries)
    publishers: dict[str, Publisher] = {}
    if "threads" in cfg.platforms:
        publishers["threads"] = ThreadsPublisher(cfg.threads_user_id, cfg.threads_token, live=live,
                                                 base=cfg.threads_base, **kw)
    if "instagram" in cfg.platforms:
        publishers["instagram"] = InstagramPublisher(cfg.instagram_user_id, cfg.instagram_token, live=live,
                                                     base=cfg.instagram_base, **kw)
    if "x" in cfg.platforms:
        publishers["x"] = XPublisher(cfg.x_api_key, cfg.x_api_secret, cfg.x_access_token, cfg.x_access_secret,
                                     live=live, handle=cfg.x_handle, max_images=cfg.x_max_images, **kw)
    host = GitHubMediaHost(cfg.media_repo, cfg.media_token, branch=cfg.media_branch, live=live, **kw)

    def render(path: Path) -> None:
        from bayesly.render import render_record
        render_record(path, palette=palette, force=True)

    return Autoposter(settings, cfg, publishers, media_upload=host.upload,
                      telegram=Telegram(cfg.telegram_token, cfg.telegram_chat_id), live=live, render=render)
