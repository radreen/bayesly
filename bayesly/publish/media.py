"""Public image hosting for Threads / Instagram, which fetch images by URL.

GitHubMediaHost commits each JPEG to a PUBLIC GitHub repo (e.g. <you>/bayesly-media) through the
contents API and returns its raw.githubusercontent.com URL. Free, no extra service. The repo
holds only finished slide images, never secrets.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from bayesly import net
from bayesly.publish.base import scrub


class GitHubMediaHost:
    def __init__(self, repo: str, token: str, *, branch: str = "main", live: bool, timeout: float = 30,
                 max_retries: int = 2, session: Any = None) -> None:
        self.repo, self.token, self.branch, self.live = repo, token, branch, live
        self.timeout, self.max_retries, self.session = timeout, max_retries, session

    def url_for(self, dest: str) -> str:
        return f"https://raw.githubusercontent.com/{self.repo}/{self.branch}/{dest}"

    def cdn_url(self, sha: str, dest: str) -> str:
        # jsDelivr serves .mp4 as video/mp4 (GitHub raw says application/octet-stream); pinning the
        # commit sha makes the very first request fresh (checked with curl -I, 2026-11-20).
        return f"https://cdn.jsdelivr.net/gh/{self.repo}@{sha}/{dest}"

    def upload_video(self, local: Path, dest: str) -> str:
        """Upload an MP4 and return a jsDelivr URL pinned to the commit (Meta needs a proper video URL)."""
        if not self.live:
            return self.cdn_url("DRYRUN", dest)
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json"}
        body = {"message": f"media: {dest}", "content": base64.b64encode(local.read_bytes()).decode(), "branch": self.branch}
        try:
            data = net.request("PUT", f"https://api.github.com/repos/{self.repo}/contents/{dest}", timeout=max(self.timeout, 120),
                               max_retries=self.max_retries, session=self.session, json=body, headers=headers).json()
            sha = (data.get("commit") or {}).get("sha")
        except net.ExternalCallError as exc:
            if "HTTP 422" not in str(exc):
                raise RuntimeError(scrub(str(exc), self.token)) from exc
            sha = None                    # already uploaded: find the commit that added it
        if not sha:
            try:
                commits = net.request("GET", f"https://api.github.com/repos/{self.repo}/commits", timeout=self.timeout,
                                      max_retries=self.max_retries, session=self.session, headers=headers,
                                      params={"path": dest, "per_page": 1}).json()
                sha = commits[0]["sha"]
            except (net.ExternalCallError, ValueError, KeyError, IndexError) as exc:
                raise RuntimeError(f"cannot resolve commit for {dest}: {scrub(str(exc), self.token)[:200]}") from None
        return self.cdn_url(sha, dest)

    def upload(self, local: Path, dest: str) -> str:
        """Upload local file to dest (path inside the repo); returns its public URL.
        Dry-run returns the would-be URL without uploading."""
        url = self.url_for(dest)
        if not self.live:
            return url
        if not (self.repo and self.token):
            raise RuntimeError("MEDIA_GITHUB_REPO / MEDIA_GITHUB_TOKEN not configured")
        body = {"message": f"media: {dest}", "content": base64.b64encode(local.read_bytes()).decode(), "branch": self.branch}
        try:
            net.request("PUT", f"https://api.github.com/repos/{self.repo}/contents/{dest}", timeout=self.timeout,
                        max_retries=self.max_retries, session=self.session, json=body,
                        headers={"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json"})
        except net.ExternalCallError as exc:
            if "HTTP 422" in str(exc):   # already uploaded earlier (same path): reuse it
                return url
            raise RuntimeError(scrub(str(exc), self.token)) from exc
        return url
