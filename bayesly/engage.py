"""Growth helpers for the owner: comment ideas under big accounts, reply drafts for our own comments.

Why not automatic: replying to other people's posts through the Threads API needs App Review
(threads_keyword_search or threads_manage_mentions), and reading big accounts' posts needs
threads_profile_discovery (standard access only reaches @meta, @threads, @instagram, @facebook).
Docs checked 2026-11-21. Automated replies from a brand account also risk spam flags. So the bot
prepares the text and the owner posts it by hand: one tap to copy, one to open Threads.

Source of ideas: the same fresh, high-scoring stories `check` already collects. When OpenAI,
Anthropic, Nvidia... announce something, they post it on Threads too; early replies there get seen.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote_plus

from bayesly.models import Item

log = logging.getLogger(__name__)

COMPANIES = {   # title keyword -> who is likely posting about it on Threads
    "openai": "OpenAI", "chatgpt": "OpenAI", "sora": "OpenAI", "anthropic": "Anthropic", "claude": "Anthropic",
    "deepmind": "Google DeepMind", "gemini": "Google", "google": "Google", "nvidia": "Nvidia", "meta": "Meta",
    "llama": "Meta", "microsoft": "Microsoft", "copilot": "Microsoft", "apple": "Apple", "mistral": "Mistral",
    "xai": "xAI", "grok": "xAI", "hugging face": "Hugging Face", "amazon": "Amazon", "perplexity": "Perplexity",
}
MAX_AGE_HOURS = 3        # early replies get the views; an old thread is not worth it
MIN_SCORE = 0.8
PER_CHECK = 2
PER_DAY = 6


def company_of(title: str) -> str:
    low = title.lower()
    for key, name in COMPANIES.items():
        if re.search(rf"(?<![a-z]){re.escape(key)}(?![a-z])", low):
            return name
    return ""


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


def _json(text: str) -> Any:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except ValueError:
        m = re.search(r"[\[{].*[\]}]", text, re.S)
        return json.loads(m.group(0)) if m else None


VOICE = ("Voice: calm, precise, curious; short sentences; English. No links, no hashtags, no emojis, "
         "no hype words, no 'follow us' or self-promotion, no flattery ('great post!'). "
         "Never state a fact that is not in the text given to you.")


def comment_prompt(items: list[Item]) -> str:
    blocks = "\n\n".join(f"[{i}] {it.title}\n{(it.text or '')[:1200]}" for i, it in enumerate(items))
    return (
        "You write reply ideas for the Threads account @bayesly.ai (AI/tech news). The owner will post them by "
        "hand under the official announcement or the first big posts about each story. Goal: a reply people "
        "like and tap through to our profile, because it adds something.\n"
        "For each story write 2 different replies, each max 200 characters: one adds the most surprising "
        "concrete detail from the text (a number, a limit, a price, who gets it), one asks a sharp genuine "
        "question a practitioner would ask.\n" + VOICE + "\n\nSTORIES:\n" + blocks +
        "\n\nReturn ONLY JSON: [{\"i\": 0, \"replies\": [\"...\", \"...\"]}, ...]"
    )


STOP = {"after", "about", "says", "said", "claims", "hours", "new", "with", "from", "that", "this", "will", "into",
         "over", "their", "shares", "first", "releases", "launches", "announces", "unveils", "report", "reports"}


def search_url(company: str, title: str) -> str:
    """Company + the 3 most specific words (names, numbers, capitalised terms first)."""
    words = [w.strip(".,:;!?'\"") for w in re.findall(r"[A-Za-z0-9][\w.-]*", title)]
    words = [w for w in words if len(w) > 3 and w.lower() not in STOP and w.lower() not in COMPANIES
             and w.lower() not in company.lower()]
    words.sort(key=lambda w: not (w[0].isupper() or any(ch.isdigit() for ch in w)))   # stable: keeps order
    return "https://www.threads.com/search?q=" + quote_plus(" ".join([company, *list(dict.fromkeys(words))[:3]]))


def idea_message(item: Item, company: str, replies: list[str]) -> str:
    lines = [f"💡 <b>Comment idea — {escape(company)}</b>", escape(item.title[:200]), ""]
    for n, r in enumerate(replies, 1):
        lines.append(f"{n}) <code>{escape(r)}</code>")
    lines.append("\n<i>Tap a reply to copy it, open Threads, reply under the first big post about this.</i>")
    return "\n".join(lines)


def comment_ideas(settings: Any, ranked: list[Item], generate: Callable[[str], str],
                  send: Callable[[str, list], Any], *, key: Callable[[Item], str],
                  now: datetime | None = None) -> list[str]:
    now = now or datetime.now(timezone.utc)
    path = settings.state_dir / "engage.json"
    state = _load(path)
    today = now.date().isoformat()
    if state.get("day") != today:
        state["day"], state["sent_today"] = today, 0
    seen = set(state.get("seen") or [])
    room = min(PER_CHECK, PER_DAY - int(state.get("sent_today", 0)))
    picks: list[tuple[Item, str]] = []
    for it in ranked:
        if room <= 0 or len(picks) >= room:
            break
        if key(it) in seen or it.score < MIN_SCORE or not it.published:
            continue
        if now - it.published > timedelta(hours=MAX_AGE_HOURS):
            continue
        company = company_of(it.title)
        if company:
            picks.append((it, company))
    if not picks:
        return []
    try:
        data = _json(generate(comment_prompt([p[0] for p in picks]))) or []
    except Exception as exc:  # noqa: BLE001 - ideas are optional; never break check
        log.warning("comment ideas failed: %s", str(exc)[:200])
        return []
    out = []
    for row in data if isinstance(data, list) else []:
        i = row.get("i") if isinstance(row, dict) else None
        replies = [str(r).strip()[:220] for r in (row.get("replies") or []) if str(r).strip()][:2] if isinstance(i, int) else []
        if not replies or not 0 <= i < len(picks):
            continue
        item, company = picks[i]
        try:
            send(idea_message(item, company, replies),
                 [[("Find on Threads", search_url(company, item.title)), ("Source", item.url)]])
        except Exception as exc:  # noqa: BLE001
            log.warning("telegram idea failed: %s", exc)
            continue
        seen.add(key(item))
        state["sent_today"] = int(state.get("sent_today", 0)) + 1
        out.append(f"idea: {company}: {item.title[:60]}")
    state["seen"] = sorted(seen)[-500:]
    _save(path, state)
    return out


def reply_draft(generate: Callable[[str], str], headline: str, post_text: str, comment: str) -> str:
    """One short reply for a comment under OUR post. Empty string when the model fails."""
    prompt = (
        "You reply as @bayesly.ai to a comment under our own AI-news post. Max 200 characters. Answer the "
        "comment directly; if it asks something the post doesn't cover, say we don't know yet. Friendly, "
        "no corporate tone, invite a follow-up only if natural. " + VOICE +
        f"\n\nOUR POST (headline): {headline}\nOUR POST (text): {post_text[:700]}\nCOMMENT: {comment[:500]}\n\n"
        "Return ONLY the reply text."
    )
    try:
        text = generate(prompt).strip().strip('"')
    except Exception as exc:  # noqa: BLE001
        log.warning("reply draft failed: %s", str(exc)[:200])
        return ""
    return text[:240]
