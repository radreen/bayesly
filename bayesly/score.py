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

# Release notes: pre-releases are dropped, patch releases (x.y.Z, Z > 0) pushed down.
# Major/minor releases (x.Y.0) are real news and keep their score.
_VERSION_RE = re.compile(
    r"\bv?(\d+)\.(\d+)(?:\.(\d+))?(?:[-.]?(rc|alpha|beta|dev|nightly|pre|preview)\.?\d*)?\b",
    re.IGNORECASE,
)
PATCH_RELEASE_PENALTY = 0.35

# How-to / vendor tutorial posts: useful, but rarely news.
_HOWTO_RE = re.compile(
    r"^(how to|getting started|get started|a guide|guide to|introduction to|best practices|"
    r"build(ing)?|deploy(ing)?|use|using|tutorial)\b"
    r"|^how [\w.&-]+( [\w.&-]+)? uses\b"  # customer / internal case studies
    r"|\b(on|with|using) (amazon|aws|azure|google cloud|vertex ai)\b"
    r"|\b(in review|recap|roundup|part \d+)\b",  # retrospectives and series
    re.IGNORECASE,
)
HOWTO_PENALTY = 0.15

# Title wording that signals an actual announcement.
_NEWS_RE = re.compile(
    r"\b(introduc(es|ing)|announc(es|ed|ing)|launch(es|ed)?|releas(es|ed)|unveil(s|ed)?|"
    r"now available|now supports|open[- ]sources?d?|general availability)\b",
    re.IGNORECASE,
)
NEWS_SIGNAL_BONUS = 0.10

# Audience interest (from the competitor analysis, docs/competitor-analysis.md): the stories that
# travel name a company/person people know, or involve money and conflict. Fresh beats thorough.
_NAMES_RE = re.compile(
    r"\b(openai|chatgpt|gpt-?\d|sora|anthropic|claude|google|gemini|deepmind|nvidia|meta|llama|xai|grok|"
    r"microsoft|copilot|apple|tesla|musk|altman|amodei|hassabis|huang|zuckerberg|mistral|deepseek|qwen|"
    r"perplexity|midjourney|hugging ?face|tiktok|youtube|samsung)\b",
    re.IGNORECASE,
)
NAMES_BONUS = 0.12
_STAKES_RE = re.compile(
    r"\b(billion|million|ipo|valuation|rais(es|ed)|funding|acquir(es|ed)|acquisition|buys|lawsuit|sues|sued|"
    r"ban(s|ned)?|layoffs?|fired|record|beats|outperforms|tops|price|cheaper|free)\b",
    re.IGNORECASE,
)
STAKES_BONUS = 0.08
FRESH_BONUS = ((1, 0.18), (3, 0.10))   # (published within N hours, bonus)
NICHE_RESEARCH_PENALTY = 0.15          # arXiv papers without a known name rarely reach a wide audience
# Our own numbers (Threads views, 2026-11-19..26): enterprise cloud plumbing got 0-7 views while
# surprising, human stories got 75-130. Dry B2B infrastructure news drops down the queue.
ENTERPRISE_DRY_PENALTY = 0.15
_ENTERPRISE_DRY_RE = re.compile(
    r"\b(sagemaker|bedrock|agentcore|strands|cross-region|inference routing|kubernetes|azure (?:ai )?foundry|"
    r"vertex ai|data ?lake|observability|enterprise networks?|managed (?:cluster|service)|harness)\b", re.I)


def release_kind(item: Item) -> str | None:
    """For release_notes sources: 'prerelease' | 'patch' | 'minor' | None (no version found)."""
    if item.source.kind != "release_notes":
        return None
    match = _VERSION_RE.search(item.title)
    if not match:
        return None
    if match.group(4):
        return "prerelease"
    patch = match.group(3)
    return "patch" if patch is not None and int(patch) > 0 else "minor"
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
    adjustments: dict[str, float] = {}
    if len(item.text) < THIN_TEXT_CHARS:
        adjustments["thin_text_penalty"] = -THIN_TEXT_PENALTY
    if release_kind(item) == "patch":
        adjustments["patch_release_penalty"] = -PATCH_RELEASE_PENALTY
    if _HOWTO_RE.search(item.title):
        adjustments["howto_penalty"] = -HOWTO_PENALTY
    if _NEWS_RE.search(item.title):
        adjustments["news_signal_bonus"] = NEWS_SIGNAL_BONUS
    named = bool(_NAMES_RE.search(item.title))
    if named and item.source.kind in {"press", "research"}:   # a vendor naming itself in its own blog is no signal
        adjustments["names_bonus"] = NAMES_BONUS
    if _STAKES_RE.search(item.title):
        adjustments["stakes_bonus"] = STAKES_BONUS
    if item.published is not None:
        age_h = (now - item.published).total_seconds() / 3600
        for hours, bonus in FRESH_BONUS:
            if 0 <= age_h <= hours:
                adjustments["fresh_bonus"] = bonus
                break
    if item.source.id.startswith("arxiv") and not named:
        adjustments["niche_research_penalty"] = -NICHE_RESEARCH_PENALTY
    if _ENTERPRISE_DRY_RE.search(item.title):
        adjustments["enterprise_dry_penalty"] = -ENTERPRISE_DRY_PENALTY
    total += sum(adjustments.values())
    parts.update(adjustments)
    item.score = round(max(total, 0.0), 4)
    item.score_breakdown = {k: round(v, 3) for k, v in parts.items()}
    return item.score


def rank(items: list[Item], now: datetime | None = None) -> list[Item]:
    """Drop rumours and pre-releases, score the rest, return best first."""
    kept = [i for i in items if not is_rumour(i) and release_kind(i) != "prerelease"]
    for item in kept:
        score_item(item, now)
    return sorted(kept, key=lambda i: i.score, reverse=True)


def select(ranked: list[Item], limit: int, max_per_source: int) -> list[Item]:
    """Take the best `limit` items, at most `max_per_source` from any one source."""
    picked: list[Item] = []
    per_source: dict[str, int] = {}
    for item in ranked:
        if len(picked) >= limit:
            break
        if per_source.get(item.source.id, 0) >= max_per_source:
            continue
        picked.append(item)
        per_source[item.source.id] = per_source.get(item.source.id, 0) + 1
    return picked
