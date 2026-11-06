"""Dedupe step: drop repeats within a run and items already drafted earlier."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bayesly.models import Item

_TRACKING_PARAMS = {"fbclid", "gclid", "ref", "ref_src", "source", "mc_cid", "mc_eid"}
_WORD_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {"a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with", "is", "are", "new"}

TITLE_SIMILARITY_THRESHOLD = 0.8


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, urlencode(sorted(query)), ""))


def item_key(item: Item) -> str:
    """Stable id for an item, based on its normalized URL."""
    return hashlib.sha1(normalize_url(item.url).encode("utf-8")).hexdigest()[:16]


def title_tokens(title: str) -> set[str]:
    return {w for w in _WORD_RE.findall(title.lower()) if w not in _STOPWORDS}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def dedupe(items: list[Item], seen_keys: set[str] | frozenset[str] = frozenset()) -> list[Item]:
    """Return unique items, preferring the more reliable source on collisions.

    Duplicates are: same normalized URL, a key in `seen_keys` (drafted in an
    earlier run), or titles with token Jaccard similarity >= threshold.
    """
    ordered = sorted(items, key=lambda i: i.source.reliability, reverse=True)
    kept: list[Item] = []
    kept_keys: set[str] = set()
    kept_tokens: list[set[str]] = []
    for item in ordered:
        key = item_key(item)
        if key in seen_keys or key in kept_keys:
            continue
        tokens = title_tokens(item.title)
        if any(jaccard(tokens, t) >= TITLE_SIMILARITY_THRESHOLD for t in kept_tokens):
            continue
        kept.append(item)
        kept_keys.add(key)
        kept_tokens.append(tokens)
    return kept
