"""One place for outbound HTTP: timeout + retry with exponential backoff."""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

log = logging.getLogger(__name__)

RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}
USER_AGENT = "BayeslyBot/0.0.1 (RSS reader for news drafting)"


class ExternalCallError(RuntimeError):
    """Raised when an external call fails after all retries."""


def request(
    method: str,
    url: str,
    *,
    timeout: float,
    max_retries: int,
    backoff_base: float = 1.0,
    session: Any = None,
    **kwargs: Any,
) -> requests.Response:
    sess = session or requests
    headers = {"User-Agent": USER_AGENT, **kwargs.pop("headers", {})}
    last_err = ""
    for attempt in range(1, max_retries + 2):
        try:
            resp = sess.request(method, url, timeout=timeout, headers=headers, **kwargs)
        except requests.RequestException as exc:
            last_err = f"{type(exc).__name__}: {exc}"
        else:
            if resp.status_code < 400:
                return resp
            if resp.status_code not in RETRY_STATUS:
                # Non-retryable client error: fail fast, keep a bit of body for debugging.
                raise ExternalCallError(
                    f"{method} {url} -> HTTP {resp.status_code}: {resp.text[:500]}"
                )
            last_err = f"HTTP {resp.status_code}: {resp.text[:300]}"
            if resp.status_code == 429 and "per day" in resp.text.lower():
                # a daily quota does not clear by waiting seconds: fail now, the caller switches model
                raise ExternalCallError(f"{method} {url} -> {last_err}")
        if attempt <= max_retries:
            delay = backoff_base * (2 ** (attempt - 1))
            log.warning(
                "http retry",
                extra={"url": url, "attempt": attempt, "error": last_err, "sleep_s": delay},
            )
            time.sleep(delay)
    raise ExternalCallError(f"{method} {url} failed after {max_retries + 1} attempts: {last_err}")
