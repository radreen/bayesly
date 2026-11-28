"""Live footage through a home device (owner's old Android phone with Termux), 2026-11-28.

Why: YouTube answers GitHub's servers with "Sign in to confirm you're not a bot", and we do not work
around bot checks. From a home connection the same download is normal, so a phone at home does it.

Flow:
  1. CI (autopost) meets a visual story (draft has footage_query) without footage: it writes a request
     to ci-state/footage_requests.json and posts Threads at once; Instagram and X wait.
  2. The phone (`python -m bayesly footage-helper`) reads the requests from the PUBLIC repo every few
     minutes, finds the subject in action on YouTube (search of this week, cuts, Gemini vision check)
     and uploads the clips + manifest.json to the media repo: footage/<draft_id>/.
  3. The next CI run sees the manifest, builds the Reel from those clips and posts Instagram and X.
     No clips after WAIT_MINUTES: Instagram/X get the carousel, as before.

The phone needs only: python, ffmpeg, yt-dlp, a GitHub token limited to the media repo, a Gemini key.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from bayesly import net

log = logging.getLogger(__name__)

WAIT_MINUTES = 30          # how long Instagram/X wait for the phone before the carousel goes out
REQUEST_TTL_MINUTES = 45   # the phone ignores older requests (the story has moved on)
REQUESTS_FILE = "footage_requests.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# -- CI side: requests --------------------------------------------------------------------
class FootageRequests:
    def __init__(self, state_dir: Path) -> None:
        self.path = state_dir / REQUESTS_FILE

    def load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        except ValueError:
            return {}

    def save(self, data: dict) -> None:
        cutoff = _now() - timedelta(days=2)
        data = {k: v for k, v in data.items() if datetime.fromisoformat(v["at"]) > cutoff}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def ask(self, draft_id: str, query: str, subject: str) -> dict:
        data = self.load()
        if draft_id not in data:
            data[draft_id] = {"query": query, "subject": subject, "at": _now().isoformat(timespec="seconds")}
            self.save(data)
        return data[draft_id]

    def age_minutes(self, draft_id: str) -> float | None:
        r = self.load().get(draft_id)
        return (_now() - datetime.fromisoformat(r["at"])).total_seconds() / 60 if r else None

    def waiting(self, draft_id: str) -> bool:
        """True while Instagram/X should still wait for the phone."""
        age = self.age_minutes(draft_id)
        return age is not None and age < WAIT_MINUTES


# -- media repo (both sides) -------------------------------------------------------------
class MediaRepo:
    def __init__(self, repo: str, token: str, branch: str = "main", session: Any = None) -> None:
        self.repo, self.token, self.branch, self.session = repo, token, branch, session

    def _headers(self) -> dict:
        h = {"Accept": "application/vnd.github+json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def manifest(self, draft_id: str) -> dict | None:
        try:
            r = net.request("GET", f"https://api.github.com/repos/{self.repo}/contents/footage/{draft_id}/manifest.json",
                            timeout=20, max_retries=1, session=self.session, headers=self._headers(),
                            params={"ref": self.branch})
            return json.loads(base64.b64decode(r.json()["content"]).decode())
        except (net.ExternalCallError, ValueError, KeyError):
            return None

    def download(self, draft_id: str, name: str, out: Path) -> Path:
        url = f"https://raw.githubusercontent.com/{self.repo}/{self.branch}/footage/{draft_id}/{name}"
        out.write_bytes(net.request("GET", url, timeout=60, max_retries=2, session=self.session).content)
        return out

    def put(self, path: str, data: bytes, message: str) -> None:
        body = {"message": message, "content": base64.b64encode(data).decode(), "branch": self.branch}
        try:
            net.request("PUT", f"https://api.github.com/repos/{self.repo}/contents/{path}", timeout=120,
                        max_retries=2, session=self.session, json=body, headers=self._headers())
        except net.ExternalCallError as exc:
            if "HTTP 422" not in str(exc):          # 422 = already there (a retry): fine
                raise


def fetch_clips(media: MediaRepo, draft_id: str, out_dir: Path):
    """CI: the phone's clips for this draft as a SourceVideo, or None."""
    from bayesly.source_video import Clip, SourceVideo, _probe_seconds
    man = media.manifest(draft_id)
    if not man or not man.get("clips"):
        return None
    clips = []
    for i, c in enumerate(man["clips"]):
        try:
            path = media.download(draft_id, c["file"], out_dir / f"{draft_id}.phone{i}.mp4")
        except net.ExternalCallError as exc:
            log.warning("phone clip download failed", extra={"draft": draft_id, "error": str(exc)[:160]})
            continue
        secs = _probe_seconds(path)
        if secs >= 2:
            clips.append(Clip(path, c.get("credit") or man.get("credit") or "YouTube", secs, 0.0))
    return SourceVideo(clips) if clips else None


# -- phone side ------------------------------------------------------------------------------
def open_requests(public_repo: str, session: Any = None) -> dict:
    """Read ci-state/footage_requests.json from the PUBLIC main repo (no token needed)."""
    url = f"https://raw.githubusercontent.com/{public_repo}/main/ci-state/{REQUESTS_FILE}"
    try:
        data = net.request("GET", url, timeout=20, max_retries=1, session=session,
                           params={"t": int(time.time())}).json()      # skip stale CDN copies
    except (net.ExternalCallError, ValueError):
        return {}
    cutoff = _now() - timedelta(minutes=REQUEST_TTL_MINUTES)
    return {k: v for k, v in data.items() if datetime.fromisoformat(v["at"]) > cutoff}


def serve_once(public_repo: str, media: MediaRepo, judge, work: Path, done: set[str]) -> list[str]:
    """One pass on the phone: answer every open request we have not answered yet."""
    from bayesly.source_video import find_footage, footage_terms
    from bayesly.video import normalise_clip
    lines = []
    for draft_id, req in open_requests(public_repo).items():
        if draft_id in done or media.manifest(draft_id):
            done.add(draft_id)
            continue
        query = req["query"]
        found = find_footage(query, footage_terms(query), work / f"{draft_id}.src.mp4", judge=judge)
        manifest: dict[str, Any] = {"query": query, "at": _now().isoformat(timespec="seconds"), "clips": []}
        if found:
            for i, clip in enumerate(found.clips):
                small = work / f"{draft_id}.up{i}.mp4"          # 1080x800 H.264, ~9 s: a few MB to upload
                normalise_clip(clip.path, small, 0.0, min(clip.duration, 10.0))
                media.put(f"footage/{draft_id}/clip{i}.mp4", small.read_bytes(), f"footage: {draft_id} clip {i}")
                manifest["clips"].append({"file": f"clip{i}.mp4", "credit": clip.credit})
            manifest["credit"] = found.credit
        media.put(f"footage/{draft_id}/manifest.json", json.dumps(manifest, indent=2).encode(), f"footage: {draft_id}")
        done.add(draft_id)
        lines.append(f"{draft_id}: {len(manifest['clips'])} clip(s) for '{query}'")
    return lines


def helper_loop(settings, *, every: float = 300, once: bool = False) -> None:
    """Runs on the phone forever (Termux). Every `every` seconds: serve open footage requests."""
    from bayesly.cli import make_generator
    public_repo = os.getenv("BAYESLY_REPO", "radreen/bayesly")
    media = MediaRepo(os.getenv("MEDIA_GITHUB_REPO", "radreen/bayesly-media"), os.getenv("MEDIA_GITHUB_TOKEN", ""))
    judge = make_generator(settings).utility().generate if settings.gemini_api_key else None
    work = settings.out_dir / "phone"
    work.mkdir(parents=True, exist_ok=True)
    done: set[str] = set()
    while True:
        try:
            for line in serve_once(public_repo, media, judge, work, done):
                print(f"{_now():%H:%M} {line}", flush=True)
        except Exception as exc:  # noqa: BLE001 - keep the helper alive through network hiccups
            print(f"{_now():%H:%M} error: {str(exc)[:200]}", flush=True)
        for f in work.glob("*"):
            if f.is_file() and time.time() - f.stat().st_mtime > 3600:
                f.unlink(missing_ok=True)                 # phones have little storage
        if once:
            return
        time.sleep(every)


def followups_due(state_dir: Path, media: MediaRepo, video_platforms: list[str]) -> list[str]:
    """CI `check`: drafts whose Instagram/X waited for footage and can go now (clips came, or time is up)."""
    from bayesly.publish.base import Ledger
    ledger = Ledger(state_dir)
    due = []
    reqs = FootageRequests(state_dir)
    for draft_id in reqs.load():
        pending = [p for p in video_platforms if not ledger.done(draft_id, p)]
        if not pending or not ledger.touched(draft_id):
            continue
        if not reqs.waiting(draft_id) or media.manifest(draft_id):
            due.append(draft_id)
    return due
