"""Turn a draft record into a validated Card for image slides.

The card comes from the model (draft["card"]). We never trust it blindly:
- the big number must literally appear in the source text, or it is dropped;
- the highlight must be part of the headline, or it is dropped;
- long fields are trimmed, empty lists stay empty.
Every change is reported as a warning for the human reviewer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

CATEGORIES = {"Models", "Research", "Tools", "Infra", "Chips", "Policy", "Safety", "Business", "Other"}
LIMITS = {"headline": 90, "before": 110, "now": 110, "caption": 80, "footnote": 80, "list_item": 55, "value": 7}


@dataclass
class Number:
    value: str
    caption: str
    footnote: str


@dataclass
class Card:
    category: str
    headline: str
    highlight: str | None
    before: str | None
    now: str | None
    number: Number | None
    evidence_for: list[str]
    evidence_against: list[str]
    open_questions: list[str]
    confidence: str
    confidence_reason: str
    opinion: str
    warnings: list[str] = field(default_factory=list)


def _clean(value: Any, limit: int, name: str, warnings: list[str]) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text or text.lower() in {"null", "none", "n/a", "unclear"}:
        return None
    if len(text) > limit:
        warnings.append(f"card.{name} trimmed from {len(text)} to {limit} chars.")
        text = text[: limit - 1].rstrip() + "…"
    return text


def _clean_list(values: Any, name: str, warnings: list[str], max_items: int = 2) -> list[str]:
    if not isinstance(values, list):
        return []
    out = [v for v in (_clean(x, LIMITS["list_item"], name, warnings) for x in values) if v]
    if len(out) > max_items:
        warnings.append(f"card.{name}: kept {max_items} of {len(out)} items.")
    return out[:max_items]


def _norm_number(text: str) -> str:
    return text.lower().replace("×", "x").replace(",", "").replace(" ", "")


def number_in_source(value: str, source_text: str) -> bool:
    """True if the number appears in the source text as written (ignoring spaces, commas, ×/x).

    Strict on purpose: "2x" must be in the source as "2x"/"2×", not just any "2".
    """
    if not re.search(r"\d", value):
        return False
    needle = _norm_number(value)
    haystack = _norm_number(source_text)
    return re.search(rf"(?<![\d.]){re.escape(needle)}(?![\d])", haystack) is not None


def build_card(record: dict[str, Any]) -> Card:
    draft = record.get("draft") or {}
    raw = draft.get("card") if isinstance(draft.get("card"), dict) else {}
    source_text = (record.get("source") or {}).get("text", "") or ""
    warnings: list[str] = []
    if not raw:
        warnings.append("Draft has no card: using headline only (cover + confidence slides).")

    headline = _clean(raw.get("headline") or draft.get("headline") or record["source"]["title"],
                      LIMITS["headline"], "headline", warnings) or record["source"]["title"]

    highlight = _clean(raw.get("highlight"), 40, "highlight", warnings)
    if highlight and highlight not in headline:
        warnings.append(f"card.highlight '{highlight}' is not in the headline; dropped.")
        highlight = None

    category = str(raw.get("category") or "Other").strip().title()
    if category not in CATEGORIES:
        category = "Other"

    number = None
    raw_num = raw.get("number")
    if isinstance(raw_num, dict) and raw_num.get("value"):
        value = _clean(raw_num.get("value"), LIMITS["value"], "number.value", warnings)
        if value and number_in_source(value, source_text):
            number = Number(
                value=value,
                caption=_clean(raw_num.get("caption"), LIMITS["caption"], "number.caption", warnings) or "",
                footnote=_clean(raw_num.get("footnote"), LIMITS["footnote"], "number.footnote", warnings)
                or "Source-reported. Not independently verified.",
            )
        elif value:
            warnings.append(f"card.number '{value}' not found in the source text; number slide dropped.")

    confidence = draft.get("confidence") if draft.get("confidence") in {"low", "medium", "high"} else "low"

    return Card(
        category=category,
        headline=headline,
        highlight=highlight,
        before=_clean(raw.get("before"), LIMITS["before"], "before", warnings),
        now=_clean(raw.get("now"), LIMITS["now"], "now", warnings),
        number=number,
        evidence_for=_clean_list(raw.get("evidence_for"), "evidence_for", warnings),
        evidence_against=_clean_list(raw.get("evidence_against"), "evidence_against", warnings),
        open_questions=_clean_list(raw.get("open_questions"), "open_questions", warnings),
        confidence=confidence,
        confidence_reason=_clean(draft.get("confidence_reason"), 70, "confidence_reason", warnings) or "",
        opinion=_clean(draft.get("opinion_line"), 140, "opinion_line", warnings) or "",
        warnings=warnings,
    )
