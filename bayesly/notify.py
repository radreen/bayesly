"""What the owner sees in Telegram. Pure functions: record in, (text, buttons) out.

Design rules: one message per event, the decision on top, details folded away,
links as buttons (no raw URLs in the text), nothing sent when nothing happened.
"""

from __future__ import annotations

import re
from html import escape
from urllib.parse import urlsplit

CAPTION_LIMIT = 1000          # Telegram photo captions: 1024 visible characters
PLATFORM_LABEL = {"threads": "Threads", "instagram": "Instagram", "x": "X"}
CONF_ICON = {"high": "🟢", "medium": "🟡", "low": "🔴"}

# gate reasons -> one short human line
_REASONS = [
    (r"confidence is 'low'", "low confidence"),
    (r"confidence is", "confidence missing"),
    (r"insufficient", "source text too thin"),
    (r"reliability", "less reliable source"),
    (r"Threads post is (\d+) chars", r"Threads text too long (\1/500)"),
    (r"X post is (\d+) chars", r"X text too long (\1/280)"),
    (r"X post contains a link", "link in X text"),
    (r"hype word: '([^']+)'", r"hype word «\1»"),
    (r"hashtag", "hashtags"),
    (r"does not include the source link", "no source link"),
    (r"fonts did not load", "fonts failed on the image"),
    (r"too long to fit", "text overflows the image"),
    (r"no rendered images", "no images"),
    (r"no (\w+) text", r"no \1 text"),
    (r"empty", "empty text"),
]


def h(text: object) -> str:
    return escape(str(text or ""), quote=False)


def visible_len(html_text: str) -> int:
    return len(re.sub(r"<[^>]+>", "", html_text).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&"))


def short_reason(reason: str) -> str:
    text = reason.removeprefix("lint: ").removeprefix("image: ")
    for pattern, repl in _REASONS:
        m = re.search(pattern, text)
        if m:
            return m.expand(repl) if "\\" in repl else repl
    return text[:60]


def _domain(url: str) -> str:
    return urlsplit(url or "").netloc.removeprefix("www.") or "source"


def _head(rec: dict) -> tuple[str, str, str]:
    draft = rec.get("draft") or {}
    src = rec.get("source") or {}
    card = draft.get("card") or {}
    headline = draft.get("headline") or src.get("title") or "Untitled"
    conf = draft.get("confidence") or "?"
    meta = " · ".join(x for x in (card.get("category"), f"{CONF_ICON.get(conf, '⚪')} {conf}", _domain(src.get("url", ""))) if x)
    return headline, meta, src.get("url", "")


def held_message(rec: dict, reasons: list[str]) -> tuple[str, list[list[tuple[str, str]]]]:
    """Caption for the cover photo of a held post + Publish/Skip/Source buttons."""
    headline, meta, url = _head(rec)
    draft = rec.get("draft") or {}
    why = list(dict.fromkeys(short_reason(r) for r in reasons))[:3]
    top = (f"⏸ <b>Needs your OK</b>\n\n<b>{h(headline)}</b>\n{h(meta)}\n\n"
           f"⚠️ {h('; '.join(why))}")
    body = draft.get("threads_post") or draft.get("x_post") or ""
    room = CAPTION_LIMIT - visible_len(top) - 20
    if body and room > 80:
        if len(body) > room:
            body = body[: room - 1].rstrip() + "…"
        top += f"\n\n<blockquote expandable>{h(body)}</blockquote>"
    buttons = [[("✅ Publish", f"pub:{rec['id']}"), ("✖️ Skip", f"skip:{rec['id']}")]]
    if url:
        buttons.append([("🔗 Source", url)])
    return top, buttons


def decided_caption(rec: dict, approved: bool) -> str:
    headline, meta, _ = _head(rec)
    mark = "✅ <b>Approved</b> · goes out on the next run" if approved else "✖️ <b>Skipped</b>"
    return f"{mark}\n\n<b>{h(headline)}</b>\n{h(meta)}"


def published_message(rec: dict, results: list) -> tuple[str, list[list[tuple[str, str]]]] | None:
    """One message per published post: ok platforms as link buttons, failures as one line each."""
    real = [r for r in results if not getattr(r, "dry_run", False)]
    if not real:
        return None
    headline, meta, _ = _head(rec)
    ok = [r for r in real if r.ok]
    bad = [r for r in real if not r.ok]
    icon = "✅" if not bad else ("⚠️" if ok else "❌")
    title = "Published" if not bad else ("Partly published" if ok else "Publish failed")
    text = f"{icon} <b>{title}</b>\n\n<b>{h(headline)}</b>\n{h(meta)}"
    for r in bad:
        text += f"\n\n❌ {PLATFORM_LABEL.get(r.platform, r.platform)}: {h(_short_error(r.error))}\n<i>retries on the next run</i>"
    for r in ok:
        if (r.detail or "").startswith("video failed"):
            text += f"\n\nℹ️ {PLATFORM_LABEL.get(r.platform, r.platform)}: posted images, video failed ({h(_short_error(r.detail))})"
    if any(r.detail == "video" for r in ok):
        text += "\n🎬 " + ", ".join(PLATFORM_LABEL.get(r.platform, r.platform) for r in ok if r.detail == "video") + ": video"
    row = [(PLATFORM_LABEL.get(r.platform, r.platform), r.url) for r in ok if r.url]
    return text, ([row] if row else [])


def _short_error(err: str | None) -> str:
    err = err or "unknown error"
    m = re.search(r'"(?:error_user_msg|message)":"([^"]{3,140})"', err)
    if m:
        return m.group(1)
    m = re.search(r"HTTP (\d{3})", err)
    return (f"HTTP {m.group(1)}" if m else err)[:140]


def error_message(errors: list[str]) -> str:
    lines = "\n".join(f"• {h(e[:160])}" for e in errors[:5])
    return f"⚠️ <b>Bot problem</b>\n\n{lines}"


def status_message(paused: bool, pending: int, posted_today: int, platforms: list[str], next_run: str) -> str:
    state = "⏸ <b>Paused</b>" if paused else "▶️ <b>Running</b>"
    return (f"{state}\n\n"
            f"Waiting for you: <b>{pending}</b>\n"
            f"Published today: <b>{posted_today}</b>\n"
            f"Next check: {h(next_run)}\n"
            f"Networks: {h(', '.join(PLATFORM_LABEL.get(p, p) for p in platforms))}")


COMMANDS = [
    ("status", "What the bot is doing"),
    ("pause", "Stop all publishing"),
    ("resume", "Start publishing again"),
]
