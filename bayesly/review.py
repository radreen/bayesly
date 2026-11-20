"""Review queue: write draft files for a human and remember what was drafted.

Each draft produces two files in out/drafts/YYYY-MM-DD/:
  <id>-<slug>.md    human-readable, for copy/paste into Threads / X
  <id>-<slug>.json  machine-readable, status = "pending_review"
state/drafted.json records drafted item ids so re-runs don't redo them.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bayesly.models import Item

THREADS_MAX_CHARS = 500
X_MAX_CHARS = 280
INSTAGRAM_MAX_CHARS = 2200
# X charges 13x more for a post with a URL, so X text must carry no link and no bare domain.
_URL_OR_DOMAIN_RE = re.compile(r"https?://|www\.|\b[a-z0-9-]+\.(com|ai|io|org|net|dev|co|app|gov|edu)\b", re.IGNORECASE)
HYPE_WORDS = [
    "revolutionary", "game-changer", "game changer", "insane", "mind-blowing",
    "groundbreaking", "unprecedented", "crazy", "this changes everything",
]
_HASHTAG_RE = re.compile(r"(?<!\w)#\w+")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str, max_len: int = 50) -> str:
    return _SLUG_RE.sub("-", text.lower()).strip("-")[:max_len].rstrip("-") or "item"


def lint_draft(draft: dict[str, Any], source_url: str) -> list[str]:
    """Cheap checks against the voice rules. Warnings go to the reviewer, not a gate."""
    warnings: list[str] = []
    threads = draft.get("threads_post", "") or ""
    x_post = draft.get("x_post", "") or ""
    insta = draft.get("instagram_caption", "") or ""
    if len(threads) > THREADS_MAX_CHARS:
        warnings.append(f"Threads post is {len(threads)} chars (> {THREADS_MAX_CHARS}).")
    if len(x_post) > X_MAX_CHARS:
        warnings.append(f"X post is {len(x_post)} chars (> {X_MAX_CHARS}).")
    if len(insta) > INSTAGRAM_MAX_CHARS:
        warnings.append(f"Instagram caption is {len(insta)} chars (> {INSTAGRAM_MAX_CHARS}).")
    if _HASHTAG_RE.search(threads):
        warnings.append("Threads post contains a hashtag (rule: none on Threads).")
    if len(_HASHTAG_RE.findall(x_post)) > 2:
        warnings.append("X post has more than 2 hashtags.")
    if len(_HASHTAG_RE.findall(insta)) > 5:
        warnings.append("Instagram caption has more than 5 hashtags.")
    for name, text in (("Threads", threads), ("X", x_post), ("Instagram", insta)):
        lowered = text.lower()
        for word in HYPE_WORDS:
            if word in lowered:
                warnings.append(f"{name} post uses hype word: '{word}'.")
    if source_url and source_url not in threads:
        warnings.append("Threads post does not include the source link.")
    if _URL_OR_DOMAIN_RE.search(x_post):
        warnings.append("X post contains a link or domain (costs $0.20 instead of $0.015).")
    if not x_post.strip():
        warnings.append("X post is empty.")
    if not insta.strip():
        warnings.append("Instagram caption is empty.")
    if draft.get("confidence") not in {"low", "medium", "high"}:
        warnings.append("Confidence is missing or not one of low/medium/high.")
    if draft.get("insufficient_source"):
        warnings.append("Model flagged the source text as insufficient. Check the source before posting.")
    return warnings


class DraftStore:
    def __init__(self, out_dir: Path, state_dir: Path) -> None:
        self.out_dir = out_dir
        self.state_path = state_dir / "drafted.json"

    def drafted_ids(self) -> set[str]:
        if not self.state_path.exists():
            return set()
        return set(json.loads(self.state_path.read_text(encoding="utf-8")).keys())

    def _mark_drafted(self, item_id: str, path: Path) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        state = {}
        if self.state_path.exists():
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        state[item_id] = {
            "path": str(path),
            "drafted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.state_path)  # atomic on the same filesystem

    def write(
        self,
        item: Item,
        item_id: str,
        draft: dict[str, Any] | None,
        raw_text: str,
        model: str,
        *,
        mark: bool = True,
    ) -> Path:
        now = datetime.now(timezone.utc)
        day_dir = self.out_dir / now.strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        base = day_dir / f"{item_id}-{slugify(item.title)}"

        warnings = lint_draft(draft, item.url) if draft else ["No parsed draft (see raw_model_output)."]
        record = {
            "id": item_id,
            "status": "pending_review",
            "created_at": now.isoformat(timespec="seconds"),
            "model": model,
            "source": {
                "id": item.source.id,
                "name": item.source.name,
                "kind": item.source.kind,
                "reliability": item.source.reliability,
                "url": item.url,
                "title": item.title,
                "published": item.published.isoformat() if item.published else None,
                "text": item.text,
            },
            "score": item.score,
            "score_breakdown": item.score_breakdown,
            "draft": draft,
            "raw_model_output": raw_text if draft is None else None,
            "lint_warnings": warnings,
        }
        json_path = base.with_suffix(".json")
        json_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        base.with_suffix(".md").write_text(render_markdown(record), encoding="utf-8")
        if mark:
            self._mark_drafted(item_id, json_path)
        return json_path


def render_markdown(record: dict[str, Any]) -> str:
    src = record["source"]
    draft = record.get("draft") or {}
    lines = [
        f"# REVIEW: {src['title']}",
        "",
        f"- Status: **{record['status']}** (nothing is published automatically)",
        f"- Source: {src['name']} ({src['kind']}, reliability {src['reliability']})",
        f"- Link: {src['url']}",
        f"- Published: {src['published'] or 'unknown'}",
        f"- Score: {record['score']} {record['score_breakdown']}",
        f"- Model: {record['model']}",
        "",
    ]
    if record["lint_warnings"]:
        lines += ["## Warnings", *[f"- {w}" for w in record["lint_warnings"]], ""]
    if record.get("images"):
        lines += ["## Images (carousel, in order)"]
        lines += [f"![slide {i}]({Path(p).name})" for i, p in enumerate(record["images"], start=1)]
        lines += [*[f"- Image warning: {w}" for w in record.get("image_warnings", [])], ""]
    if draft:
        lines += [
            f"## Confidence: {draft.get('confidence', '?')} — {draft.get('confidence_reason', '')}",
            "",
            "## Threads",
            "```",
            draft.get("threads_post", ""),
            "```",
            "",
            "## X",
            "```",
            draft.get("x_post", ""),
            "```",
            "",
            "## Instagram",
            "```",
            draft.get("instagram_caption", ""),
            "```",
            "",
            f"**Opinion line:** {draft.get('opinion_line', '')}",
            "",
            "## Facts used (check each against the source)",
            *[f"- {f}" for f in draft.get("facts_used", [])],
            "",
            f"**Editor notes:** {draft.get('editor_notes', '')}",
            "",
        ]
    elif record.get("raw_model_output"):
        lines += ["## Raw model output (not valid JSON)", "```", record["raw_model_output"], "```", ""]
    lines += ["## Source text (as collected from the feed)", "", src["text"] or "_(empty)_", ""]
    return "\n".join(lines)
