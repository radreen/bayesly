"""Minimal Gemini client over REST (Interactions API).

Checked against https://aistudio.google.com/docs/text-generation on 2026-11-06:
POST https://generativelanguage.googleapis.com/v1beta/interactions
headers: x-goog-api-key, Api-Revision; body: model, input, generation_config, store.
Text is read from the last step's content; `output_text` is used if present.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from bayesly import net

log = logging.getLogger(__name__)

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/interactions"
MAX_OUTPUT_TOKENS = 8192  # thinking tokens share this budget


class GeminiError(RuntimeError):
    pass


# Free tier (checked 2026-11-27 from the API's own 429 message): 20 requests per DAY per model.
# Each model has its own budget, so we spread work: drafts on the stronger models, everything else
# (dedupe, fact-check, shortening, comment ideas, reply drafts) on the lite ones.
DRAFT_MODELS = ["gemini-3.5-flash", "gemini-3.8-flash", "gemini-3-flash-preview", "gemini-3.7-flash"]
UTILITY_MODELS = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-flash-lite-latest"]


class QuotaStore:
    """state/gemini_quota.json: {model: ISO time its daily quota resets}. Exhausted models are skipped
    until then, so a used-up model costs zero calls instead of failing every 15 minutes."""

    def __init__(self, path: Path | None) -> None:
        self.path = path

    def _load(self) -> dict:
        if not self.path or not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except ValueError:
            return {}

    def exhausted(self, model: str, now: datetime | None = None) -> bool:
        until = self._load().get(model)
        return bool(until) and datetime.fromisoformat(until) > (now or datetime.now(timezone.utc))

    def mark(self, model: str, error: str, now: datetime | None = None) -> None:
        if not self.path:
            return
        now = now or datetime.now(timezone.utc)
        m = re.search(r"retry in (?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", error)
        wait = (timedelta(hours=int(m.group(1) or 0), minutes=int(m.group(2) or 0), seconds=int(m.group(3) or 0))
                if m and any(m.groups()) else timedelta(hours=8))
        data = {k: v for k, v in self._load().items() if datetime.fromisoformat(v) > now}
        data[model] = (now + wait + timedelta(minutes=2)).isoformat(timespec="seconds")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        log.warning("gemini daily quota used up", extra={"model": model, "until": data[model]})

    def available(self, models: list[str]) -> list[str]:
        return [m for m in models if not self.exhausted(m)]


class GeminiClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        api_revision: str,
        *,
        timeout: float,
        max_retries: int,
        session: Any = None,
        fallback_models: list[str] | None = None,
        quota_path: Path | None = None,
        utility_models: list[str] | None = None,
    ) -> None:
        if not api_key:
            raise GeminiError("GEMINI_API_KEY is empty. Copy .env.example to .env and set it.")
        self.api_key = api_key
        self.model = model                      # the model that produced the last answer
        self.models = [model] + [m for m in (fallback_models or []) if m and m != model]
        self.api_revision = api_revision
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = session
        self.quota = QuotaStore(quota_path)
        self.utility_models = utility_models if utility_models is not None else UTILITY_MODELS

    def _call(self, model: str, prompt: str) -> str:
        body = {
            "model": model,
            "input": prompt,
            "generation_config": {"max_output_tokens": MAX_OUTPUT_TOKENS},
            "store": False,  # stateless: we don't need server-side history
        }
        try:
            resp = net.request(
                "POST",
                ENDPOINT,
                timeout=self.timeout,
                max_retries=self.max_retries,
                session=self.session,
                headers={
                    "x-goog-api-key": self.api_key,
                    "Api-Revision": self.api_revision,
                    "Content-Type": "application/json",
                },
                json=body,
            )
        except net.ExternalCallError as exc:
            # Never leak the key into logs: it's only in headers, but be explicit.
            raise GeminiError(str(exc).replace(self.api_key, "***")) from exc
        return extract_text(resp.json())

    def available(self) -> bool:
        """Is any drafting model left today? (check uses this to stop GO-ing into a wall)"""
        return bool(self.quota.available(self.models))

    def utility(self) -> "GeminiClient":
        """A sibling client on the lite models (same key, same quota file) for small tasks.
        Falls back to the drafting models when every lite model is used up."""
        twin = GeminiClient(self.api_key, (self.utility_models or self.models)[0], self.api_revision,
                            timeout=self.timeout, max_retries=self.max_retries, session=self.session,
                            fallback_models=(self.utility_models or [])[1:] + self.models, quota_path=self.quota.path,
                            utility_models=[])
        return twin

    def generate(self, prompt: str) -> str:
        """Try each model not known to be out of quota today; busy models (503/timeouts) are skipped too."""
        errors = []
        models = self.quota.available(self.models)
        if not models:
            raise GeminiError("all Gemini models are out of today's free quota")
        for model in models:
            try:
                text = self._call(model, prompt)
                self.model = model
                return text
            except GeminiError as exc:
                if "429" in str(exc) and "per day" in str(exc).lower():
                    self.quota.mark(model, str(exc))
                errors.append(f"{model}: {str(exc)[:160]}")
                log.warning("gemini model failed, trying next", extra={"model": model, "error": str(exc)[:200]})
        raise GeminiError(" | ".join(errors))


def extract_text(data: dict[str, Any]) -> str:
    """Pull the model's final text out of an Interactions API response."""
    if isinstance(data.get("output_text"), str) and data["output_text"].strip():
        return data["output_text"]
    for step in reversed(data.get("steps") or []):
        if step.get("type") in {"thought", "function_call", "user_input"}:
            continue
        content = step.get("content")
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            texts = [c.get("text", "") for c in content if isinstance(c, dict)]
            joined = "".join(texts).strip()
            if joined:
                return joined
    raise GeminiError(f"no text in Gemini response: {json.dumps(data)[:500]}")


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_draft_json(text: str) -> dict[str, Any] | None:
    """Parse the model's JSON answer; return None if it isn't valid JSON."""
    cleaned = _FENCE_RE.sub("", text.strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        parsed = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
