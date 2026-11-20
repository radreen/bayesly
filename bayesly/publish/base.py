"""Shared types for publishing: the post, the result, the adapter interface, the ledger."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol


@dataclass
class PublishPost:
    draft_id: str
    texts: dict[str, str]                         # platform -> text
    image_urls: list[str] = field(default_factory=list)  # public JPEG URLs, slide order
    source_url: str = ""
    video_url: str = ""      # public MP4 URL (Instagram Reels, Threads)
    video_path: str = ""     # local MP4 for platforms that take an upload (X)


@dataclass
class PublishResult:
    platform: str
    ok: bool
    dry_run: bool = False
    post_id: str | None = None
    url: str | None = None
    error: str | None = None
    detail: str | None = None


class Publisher(Protocol):
    name: str

    def publish(self, post: PublishPost) -> PublishResult: ...


class PublishError(RuntimeError):
    pass


def scrub(text: str, *secrets: str) -> str:
    """Remove secrets from an error message before it reaches logs or Telegram."""
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text


@dataclass
class PublishConfig:
    platforms: list[str]
    threads_user_id: str
    threads_base: str
    threads_token: str = field(repr=False)
    instagram_user_id: str
    instagram_token: str = field(repr=False)
    instagram_base: str
    x_api_key: str = field(repr=False)
    x_api_secret: str = field(repr=False)
    x_access_token: str = field(repr=False)
    x_access_secret: str = field(repr=False)
    x_handle: str
    x_max_images: int
    media_repo: str
    media_token: str = field(repr=False)
    media_branch: str
    telegram_token: str = field(repr=False)
    telegram_chat_id: str
    posts_per_run: int
    paused_env: bool
    video_platforms: list[str] = field(default_factory=list)
    video_voice: bool = True


def load_publish_config() -> PublishConfig:
    """Read publishing settings from the environment (.env is loaded by load_settings)."""
    g = lambda k, d="": os.getenv(k, d).strip()  # noqa: E731
    return PublishConfig(
        platforms=[p.strip().lower() for p in g("PLATFORMS", "threads,instagram,x").split(",") if p.strip()],
        threads_user_id=g("THREADS_USER_ID"),
        threads_base=g("THREADS_API_BASE", "https://graph.threads.com/v1.0"),
        threads_token=g("THREADS_ACCESS_TOKEN"),
        instagram_user_id=g("INSTAGRAM_USER_ID"),
        instagram_token=g("INSTAGRAM_ACCESS_TOKEN"),
        instagram_base=g("INSTAGRAM_API_BASE", "https://graph.facebook.com/v27.0"),
        x_api_key=g("X_API_KEY"),
        x_api_secret=g("X_API_SECRET"),
        x_access_token=g("X_ACCESS_TOKEN"),
        x_access_secret=g("X_ACCESS_TOKEN_SECRET"),
        x_handle=g("X_HANDLE", "bayesly_ai"),
        x_max_images=int(g("X_MAX_IMAGES", "1") or 1),
        media_repo=g("MEDIA_GITHUB_REPO"),
        media_token=g("MEDIA_GITHUB_TOKEN"),
        media_branch=g("MEDIA_GITHUB_BRANCH", "main"),
        telegram_token=g("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=g("TELEGRAM_CHAT_ID"),
        posts_per_run=int(g("POSTS_PER_RUN", "1") or 1),
        paused_env=g("BAYESLY_PAUSED", "0").lower() in {"1", "true", "yes"},
        # Which networks get the vertical video instead of the carousel (empty = no video).
        video_platforms=[p.strip().lower() for p in g("VIDEO_PLATFORMS", "instagram,x").split(",")
                         if p.strip() and p.strip().lower() != "none"],
        video_voice=g("VIDEO_VOICE", "1").lower() not in {"0", "false", "no"},
    )


MAX_ATTEMPTS = 3


class Ledger:
    """state/posted.json: which draft went to which platform. Makes publishing idempotent.

    A platform is "done" for a draft once it succeeded, or failed MAX_ATTEMPTS times
    (so one broken post can't block the queue forever). Dry-run results are never recorded."""

    def __init__(self, state_dir: Path) -> None:
        self.path = state_dir / "posted.json"

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def entry(self, draft_id: str, platform: str) -> dict | None:
        return self._load().get(draft_id, {}).get(platform)

    def done(self, draft_id: str, platform: str) -> bool:
        e = self.entry(draft_id, platform)
        return bool(e) and (e.get("ok") or e.get("attempts", 0) >= MAX_ATTEMPTS)

    def touched(self, draft_id: str) -> bool:
        return draft_id in self._load()

    def record(self, draft_id: str, result: PublishResult) -> None:
        if result.dry_run:
            return
        data = self._load()
        prev = data.get(draft_id, {}).get(result.platform) or {}
        entry = asdict(result)
        entry["attempts"] = prev.get("attempts", 0) + 1
        entry["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        data.setdefault(draft_id, {})[result.platform] = entry
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)
