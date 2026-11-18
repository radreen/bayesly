"""X publisher: OAuth 1.0a user context, POST /2/tweets (+ media upload).

Pricing (docs.x.com, checked 2026-11-19): $0.015 per post without a URL, $0.20 with one.
This adapter REFUSES text that contains a link or a domain, so a prompt slip can't cost 13x.
Media upload endpoint/shape: confirm on the first live call (see docs/platform-notes.md).
"""

from __future__ import annotations

from typing import Any

from bayesly import net
from bayesly.publish.base import PublishError, PublishPost, PublishResult, scrub
from bayesly.review import _URL_OR_DOMAIN_RE

TWEETS = "https://api.x.com/2/tweets"
UPLOAD = "https://api.x.com/2/media/upload"
X_MAX_CHARS = 280


class XPublisher:
    name = "x"

    def __init__(self, api_key: str, api_secret: str, access_token: str, access_secret: str, *, live: bool,
                 handle: str = "bayesly_ai", max_images: int = 1, timeout: float = 30, max_retries: int = 2,
                 session: Any = None) -> None:
        self.keys = (api_key, api_secret, access_token, access_secret)
        self.live, self.handle, self.max_images = live, handle, max(0, min(max_images, 4))
        self.timeout, self.max_retries, self.session = timeout, max_retries, session

    def _auth(self):
        from requests_oauthlib import OAuth1  # imported lazily: only live posting needs it
        return OAuth1(*self.keys)

    def publish(self, post: PublishPost) -> PublishResult:
        text = (post.texts.get("x") or "").strip()
        if not text:
            return PublishResult(self.name, ok=False, error="empty X text")
        if _URL_OR_DOMAIN_RE.search(text):
            return PublishResult(self.name, ok=False, error="X text contains a link/domain (would cost $0.20); not posted")
        if len(text) > X_MAX_CHARS:
            return PublishResult(self.name, ok=False, error=f"X text is {len(text)} chars (> {X_MAX_CHARS})")
        images = post.image_urls[: self.max_images]
        if not self.live:
            return PublishResult(self.name, ok=True, dry_run=True, detail=f"would post {len(images)} image(s) + {len(text)} chars")
        if not all(self.keys):
            return PublishResult(self.name, ok=False, error="x: API keys / access tokens not configured")
        try:
            auth = self._auth()
            media_ids = [self._upload(url, auth) for url in images]
            body: dict = {"text": text}
            if media_ids:
                body["media"] = {"media_ids": media_ids}
            resp = net.request("POST", TWEETS, timeout=self.timeout, max_retries=self.max_retries,
                               session=self.session, auth=auth, json=body)
            tweet_id = (resp.json().get("data") or {}).get("id")
            if not tweet_id:
                raise PublishError(f"no tweet id in response: {resp.text[:300]}")
            return PublishResult(self.name, ok=True, post_id=tweet_id, url=f"https://x.com/{self.handle}/status/{tweet_id}")
        except (net.ExternalCallError, PublishError, ValueError, ImportError) as exc:
            return PublishResult(self.name, ok=False, error=scrub(str(exc), *self.keys)[:500])

    def _upload(self, image_url: str, auth) -> str:
        img = net.request("GET", image_url, timeout=self.timeout, max_retries=self.max_retries, session=self.session)
        resp = net.request("POST", UPLOAD, timeout=self.timeout, max_retries=self.max_retries, session=self.session,
                           auth=auth, files={"media": ("slide.jpg", img.content, "image/jpeg")},
                           data={"media_category": "tweet_image"})
        data = resp.json()
        media_id = (data.get("data") or {}).get("id") or data.get("media_id_string")
        if not media_id:
            raise PublishError(f"no media id in upload response: {str(data)[:300]}")
        return str(media_id)
