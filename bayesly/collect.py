"""Collect step: fetch RSS/Atom feeds into Items."""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timezone
from time import struct_time
from urllib.parse import urlsplit

import feedparser

from bayesly import net
from bayesly.models import Item, Source

log = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def clean_text(raw: str) -> str:
    """Strip HTML tags and collapse whitespace."""
    # Unescape first so entity-escaped markup (&lt;p&gt;) is stripped too.
    text = _TAG_RE.sub(" ", html.unescape(raw or ""))
    return _WS_RE.sub(" ", text).strip()


def _to_dt(value: struct_time | None) -> datetime | None:
    if not value:
        return None
    return datetime(*value[:6], tzinfo=timezone.utc)


_HREF_RE = re.compile(r'href="([^"]+)"')


def original_link(summary_html: str, fallback: str) -> str:
    """Aggregators (Techmeme) link to their own page; the story's first outbound link is the article,
    which has the real photo and page. Skip the aggregator itself and bare homepages."""
    own = urlsplit(fallback).netloc
    for href in _HREF_RE.findall(summary_html or ""):
        href = html.unescape(href)
        parts = urlsplit(href)
        if parts.scheme not in ("http", "https") or not parts.netloc or parts.netloc == own or not parts.path.strip("/"):
            continue
        if parts.netloc.removeprefix("www.") in ("x.com", "twitter.com") and "/status/" not in parts.path:
            continue                       # an account page, not the post itself
        return href
    return fallback


def parse_feed(source: Source, content: bytes) -> list[Item]:
    parsed = feedparser.parse(content)
    items: list[Item] = []
    for entry in parsed.entries[: source.max_items]:
        url = entry.get("link") or ""
        title = clean_text(entry.get("title", ""))
        if not url or not title:
            continue
        body = ""
        if entry.get("content"):
            body = " ".join(c.get("value", "") for c in entry["content"])
        if not body:
            body = entry.get("summary", "")
        if source.resolve_link:
            url = original_link(body, url)
        items.append(
            Item(
                source=source,
                title=title,
                url=url,
                text=clean_text(body),
                published=_to_dt(entry.get("published_parsed") or entry.get("updated_parsed")),
            )
        )
    return items


def collect(sources: list[Source], *, timeout: float, max_retries: int) -> list[Item]:
    """Fetch all sources. A broken feed is logged and skipped, never fatal."""
    all_items: list[Item] = []
    for src in sources:
        try:
            resp = net.request("GET", src.url, timeout=timeout, max_retries=max_retries)
            items = parse_feed(src, resp.content)
            log.info("collected", extra={"source": src.id, "count": len(items)})
            all_items.extend(items)
        except net.ExternalCallError as exc:
            log.error("source failed", extra={"source": src.id, "error": str(exc)})
    return all_items
