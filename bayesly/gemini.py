"""Minimal Gemini client over REST (Interactions API).

Checked against https://aistudio.google.com/docs/text-generation on 2026-11-06:
POST https://generativelanguage.googleapis.com/v1beta/interactions
headers: x-goog-api-key, Api-Revision; body: model, input, generation_config, store.
Text is read from the last step's content; `output_text` is used if present.
"""

from __future__ import annotations

import json
import re
from typing import Any

from bayesly import net

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/interactions"
MAX_OUTPUT_TOKENS = 2048


class GeminiError(RuntimeError):
    pass


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
    ) -> None:
        if not api_key:
            raise GeminiError("GEMINI_API_KEY is empty. Copy .env.example to .env and set it.")
        self.api_key = api_key
        self.model = model
        self.api_revision = api_revision
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = session

    def generate(self, prompt: str) -> str:
        body = {
            "model": self.model,
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
