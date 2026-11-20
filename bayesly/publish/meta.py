"""Threads and Instagram publishers (Meta Graph APIs, container -> publish flow).

Threads:   https://graph.threads.com/v1.0  (host per docs, checked 2026-11-19: developers.facebook.com/docs/threads/posts)
Instagram: https://graph.facebook.com/v27.0 (Instagram API with Unified login, system-user token; JPEG only).
           New apps can't use the older Instagram-Login setup (graph.instagram.com). Checked 2026-11-20.
Both fetch images from public URLs. See docs/platform-notes.md.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from bayesly import net
from bayesly.publish.base import PublishError, PublishPost, PublishResult, scrub

MAX_CAROUSEL = 20


class _MetaPublisher:
    name = ""
    base = ""
    container_path = ""       # e.g. "{uid}/threads" or "{uid}/media"
    publish_path = ""         # e.g. "{uid}/threads_publish" or "{uid}/media_publish"
    text_param = ""           # "text" (Threads) or "caption" (Instagram)
    status_field = ""         # "status" (Threads) or "status_code" (Instagram)
    publish_delay = 0         # seconds to wait after FINISHED before *_publish
    not_found_retries = 3     # extra publish attempts on "Media Not Found" (subcode 4279009)
    not_found_wait = 20

    def __init__(self, user_id: str, token: str, *, live: bool, timeout: float = 30, max_retries: int = 2,
                 session: Any = None, sleep: Callable[[float], None] = time.sleep,
                 poll_every: float = 5, poll_max: int = 24, base: str | None = None, use_video: bool = False) -> None:
        self.user_id, self.token, self.live, self.use_video = user_id, token, live, use_video
        self.timeout, self.max_retries, self.session = timeout, max_retries, session
        self.sleep, self.poll_every, self.poll_max = sleep, poll_every, poll_max
        if base:
            self.base = base.rstrip("/")

    # -- http ---------------------------------------------------------------
    def _req(self, method: str, path: str, **params: Any) -> dict:
        resp = net.request(method, f"{self.base}/{path}", timeout=self.timeout, max_retries=self.max_retries,
                           session=self.session, params={**params, "access_token": self.token})
        return resp.json()

    def _container(self, **params: Any) -> str:
        data = self._req("POST", self.container_path.format(uid=self.user_id), **params)
        if "id" not in data:
            raise PublishError(f"no container id in response: {data}")
        return data["id"]

    def _wait(self, container_id: str, rounds: int | None = None) -> None:
        for _ in range(rounds or self.poll_max):
            data = self._req("GET", container_id, fields=self.status_field)
            status = str(data.get(self.status_field, "")).upper()
            if status in {"FINISHED", "PUBLISHED"}:
                return
            if status in {"ERROR", "EXPIRED"}:
                raise PublishError(f"container {container_id} status {status}: {data}")
            self.sleep(self.poll_every)
        raise PublishError(f"container {container_id} not ready after {self.poll_max * self.poll_every:.0f}s")

    # -- publish -------------------------------------------------------------
    def publish(self, post: PublishPost) -> PublishResult:
        text = post.texts.get(self.name, "")
        urls = post.image_urls[:MAX_CAROUSEL]
        video = post.video_url if self.use_video else ""
        if not text and not urls and not video:
            return PublishResult(self.name, ok=False, error="nothing to publish")
        if not self.live:
            what = "1 video" if video else f"{len(urls)} image(s)"
            return PublishResult(self.name, ok=True, dry_run=True, detail=f"would post {what} + {len(text)} chars")
        if not (self.user_id and self.token):
            return PublishResult(self.name, ok=False, error=f"{self.name}: user id / token not configured")
        note = None
        if video:
            try:
                return self._finish(self._build_video(text, video), video=True)
            except (net.ExternalCallError, PublishError, ValueError, KeyError) as exc:
                # never lose the post over a video problem: publish the carousel instead
                note = f"video failed, posted images: {scrub(str(exc), self.token)[:160]}"
        try:
            result = self._finish(self._build(text, urls))
            result.detail = note
            return result
        except (net.ExternalCallError, PublishError, ValueError, KeyError) as exc:
            return PublishResult(self.name, ok=False, error=scrub(str(exc), self.token)[:500], detail=note)

    def _finish(self, parent: str, video: bool = False) -> PublishResult:
        self._wait(parent, rounds=self.poll_max * 3 if video else None)   # video takes longer to process
        if self.publish_delay:
            self.sleep(self.publish_delay)
        data = self._publish_container(parent)
        post_id = data.get("id")
        if not post_id:
            raise PublishError(f"publish returned no id: {data}")
        url = None
        try:
            url = self._req("GET", post_id, fields="permalink").get("permalink")
        except (net.ExternalCallError, ValueError):
            pass  # the post exists; a missing permalink is not a failure
        return PublishResult(self.name, ok=True, post_id=post_id, url=url, detail="video" if video else None)

    def _build_video(self, text: str, video_url: str) -> str:
        raise NotImplementedError

    def _publish_container(self, container_id: str) -> dict:
        """POST .../*_publish. A "Media Not Found" (subcode 4279009) right after the container reports
        FINISHED means Meta isn't ready yet: wait and try again a few times."""
        for attempt in range(1, self.not_found_retries + 2):
            try:
                return self._req("POST", self.publish_path.format(uid=self.user_id), creation_id=container_id)
            except net.ExternalCallError as exc:
                if "4279009" not in str(exc) or attempt > self.not_found_retries:
                    raise
                self.sleep(self.not_found_wait)
        raise PublishError("unreachable")

    def _build(self, text: str, urls: list[str]) -> str:
        raise NotImplementedError


class ThreadsPublisher(_MetaPublisher):
    name = "threads"
    base = "https://graph.threads.com/v1.0"
    container_path = "{uid}/threads"
    publish_path = "{uid}/threads_publish"
    text_param = "text"
    status_field = "status"
    # Threads docs: wait ~30 s after creating a container before publishing it. Our first live post
    # (2026-11-19) failed with "Media Not Found" when publishing right after status FINISHED.
    publish_delay = 30

    def _build_video(self, text: str, video_url: str) -> str:
        return self._container(media_type="VIDEO", video_url=video_url, text=text)

    def _build(self, text: str, urls: list[str]) -> str:
        if len(urls) >= 2:
            children = [self._container(media_type="IMAGE", image_url=u, is_carousel_item="true") for u in urls]
            for c in children:
                self._wait(c)
            return self._container(media_type="CAROUSEL", children=",".join(children), text=text)
        if urls:
            return self._container(media_type="IMAGE", image_url=urls[0], text=text)
        return self._container(media_type="TEXT", text=text)


class InstagramPublisher(_MetaPublisher):
    name = "instagram"
    base = "https://graph.facebook.com/v27.0"
    container_path = "{uid}/media"
    publish_path = "{uid}/media_publish"
    text_param = "caption"
    status_field = "status_code"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("poll_every", 15)   # docs: check status_code ~once a minute, up to 5 minutes
        kwargs.setdefault("poll_max", 20)
        super().__init__(*args, **kwargs)

    def _build_video(self, text: str, video_url: str) -> str:
        # Reels: MP4/H.264/AAC, 3 s-15 min, public URL (docs, checked 2026-11-20)
        return self._container(media_type="REELS", video_url=video_url, caption=text, share_to_feed="true")

    def _build(self, text: str, urls: list[str]) -> str:
        if not urls:
            raise PublishError("Instagram needs at least one image")
        if len(urls) >= 2:
            children = [self._container(image_url=u, is_carousel_item="true") for u in urls]
            for c in children:
                self._wait(c)
            return self._container(media_type="CAROUSEL", children=",".join(children), caption=text)
        return self._container(image_url=urls[0], caption=text)


# -- token refresh ------------------------------------------------------------------
# Threads long-lived tokens live 60 days (Instagram uses a system-user token; its expiry is set in
# the app's login configuration, so it is not refreshed here); a refresh (token >= 24h old, not expired) returns a NEW
# token valid 60 days. Docs checked 2026-11-19.
REFRESH = {
    "threads": ("https://graph.threads.com/refresh_access_token", "th_refresh_token"),
}


def refresh_token(platform: str, token: str, *, timeout: float = 30, max_retries: int = 2, session: Any = None) -> tuple[str, int]:
    """Returns (new_token, expires_in_seconds). Raises PublishError with the token scrubbed."""
    url, grant = REFRESH[platform]
    try:
        data = net.request("GET", url, timeout=timeout, max_retries=max_retries, session=session,
                           params={"grant_type": grant, "access_token": token}).json()
    except (net.ExternalCallError, ValueError) as exc:
        raise PublishError(scrub(str(exc), token)) from None
    new = data.get("access_token")
    if not new:
        raise PublishError(f"{platform}: no access_token in refresh response")
    return str(new), int(data.get("expires_in") or 0)
