"""Score step: drop rumours, rank by reliability, relevance and novelty.

Scores are a simple, explainable weighted sum in 0..1. Each item keeps its
breakdown so the human reviewer can see why it was picked.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from bayesly.models import Item

WEIGHTS = {"reliability": 0.40, "relevance": 0.35, "novelty": 0.25}

RUMOUR_PATTERNS = [
    r"\brumou?rs?\b",
    r"\breportedly\b",
    r"\bleak(s|ed)?\b",
    r"\bunconfirmed\b",
    r"\ballegedly\b",
    r"\bsources say\b",
    r"\bcould be\b",
]
_RUMOUR_RE = re.compile("|".join(RUMOUR_PATTERNS), re.IGNORECASE)

# Terms that signal the item is about AI/tech news we care about.
RELEVANCE_TERMS = [
    "ai", "llm", "model", "models", "gpt", "gemini", "claude", "llama", "transformer",
    "agent", "agents", "inference", "training", "benchmark", "open source", "open-source",
    "weights", "fine-tuning", "fine-tune", "release", "api", "multimodal", "reasoning",
    "gpu", "chip", "dataset", "safety", "alignment", "robotics", "diffusion",
]
_RELEVANCE_RES = [re.compile(rf"\b{re.escape(t)}\b", re.IGNORECASE) for t in RELEVANCE_TERMS]

THIN_TEXT_CHARS = 200
THIN_TEXT_PENALTY = 0.10
UNKNOWN_DATE_NOVELTY = 0.3
NOVELTY_FULL_HOURS = 24
NOVELTY_ZERO_HOURS = 24 * 7


def is_rumour(item: Item) -> bool:
    return bool(_RUMOUR_RE.search(item.title))


def relevance(item: Item) -> float:
    haystack = f"{item.title} {item.title} {item.text[:2000]}"  # title counts double
    hits = sum(1 for rx in _RELEVANCE_RES if rx.search(haystack))
    return min(hits / 5, 1.0)


def novelty(item: Item, now: datetime) -> float:
    if item.published is None:
        return UNKNOWN_DATE_NOVELTY
    age_h = (now - item.published).total_seconds() / 3600
    if age_h <= NOVELTY_FULL_HOURS:
        return 1.0
    if age_h >= NOVELTY_ZERO_HOURS:
        return 0.0
    return 1.0 - (age_h - NOVELTY_FULL_HOURS) / (NOVELTY_ZERO_HOURS - NOVELTY_FULL_HOURS)


def score_item(item: Item, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    parts = {
        "reliability": item.source.reliability,
        "relevance": relevance(item),
        "novelty": novelty(item, now),
    }
    total = sum(WEIGHTS[k] * v for k, v in parts.items())
    if len(item.text) < THIN_TEXT_CHARS:
        parts["thin_text_penalty"] = -THIN_TEXT_PENALTY
        total -= THIN_TEXT_PENALTY
    item.score = round(max(total, 0.0), 4)
    item.score_breakdown = {k: round(v, 3) for k, v in parts.items()}
    return item.score


def rank(items: list[Item], now: datetime | None = None) -> list[Item]:
    """Drop rumours, score the rest, return best first."""
    kept = [i for i in items if not is_rumour(i)]
    for item in kept:
        score_item(item, now)
    return sorted(kept, key=lambda i: i.score, reverse=True)
