"""Last-resort footage: free stock video from Pexels, used ONLY when the source gives us nothing
(no photo and no capturable page). Owner rule 2026-11-21: source material always comes first.

Pexels API (checked 2026-11-21, https://www.pexels.com/api/documentation/):
  GET https://api.pexels.com/v1/videos/search?query=..&orientation=landscape&size=medium
  header "Authorization: <key>"; 200 requests/hour, 20,000/month, free.
  Guidelines: show a prominent link/credit to Pexels -> we put "Video: Pexels · <author>" on screen.
License: free for social media and commercial use, may be modified; don't imply endorsement by people
or brands in the footage -> queries are generic, never brand or person names.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bayesly import net

log = logging.getLogger(__name__)

SEARCH = "https://api.pexels.com/v1/videos/search"
MIN_SECONDS = 6


CLIPS = 3          # 2-3 different shots with a cut every few seconds, like competitors' Reels


@dataclass
class Broll:
    paths: list[Path]  # 1-3 clips, played one after another
    credit: str        # "Video: Pexels · <author>, <author>"
    page: str          # pexels.com page of the first clip

    @property
    def path(self) -> Path:
        return self.paths[0]


def _pick_file(video: dict) -> dict | None:
    """The smallest mp4 at least 1080 px wide: sharp enough for our 1080 px zone, light to download."""
    files = [f for f in video.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("width")]
    sharp = sorted((f for f in files if f["width"] >= 1080), key=lambda f: f["width"])   # smallest that is sharp
    return sharp[0] if sharp else (max(files, key=lambda f: f["width"]) if files else None)


def find_broll(queries: list[str], api_key: str, out: Path, *, timeout: float = 30, session: Any = None,
               clips: int = CLIPS) -> Broll | None:
    """Up to `clips` different videos for the FIRST query that has results (queries are story-specific)."""
    if not api_key:
        return None
    for q in [q for q in dict.fromkeys(x.strip() for x in queries) if q]:
        try:
            data = net.request("GET", SEARCH, timeout=timeout, max_retries=1, session=session,
                               headers={"Authorization": api_key},
                               params={"query": q, "orientation": "landscape", "size": "medium", "per_page": 10}).json()
        except (net.ExternalCallError, ValueError) as exc:
            log.info("pexels search failed", extra={"query": q, "error": str(exc)[:200]})
            continue
        paths: list[Path] = []
        authors: list[str] = []
        page = ""
        for video in data.get("videos", []):
            if len(paths) >= clips:
                break
            if (video.get("duration") or 0) < MIN_SECONDS:
                continue
            f = _pick_file(video)
            if not f:
                continue
            try:
                blob = net.request("GET", f["link"], timeout=max(timeout, 60), max_retries=1, session=session).content
            except net.ExternalCallError as exc:
                log.info("pexels download failed", extra={"error": str(exc)[:200]})
                continue
            target = out if not paths else out.with_name(f"{out.stem}{len(paths)}{out.suffix}")
            target.write_bytes(blob)
            paths.append(target)
            page = page or video.get("url", "")
            author = ((video.get("user") or {}).get("name") or "").strip()
            if author and author not in authors:
                authors.append(author)
        if paths:
            return Broll(paths, "Video: Pexels" + (f" · {', '.join(authors)}" if authors else ""), page)
    return None


def queries_for(draft: dict, category: str) -> list[str]:
    """Only the story-specific query Gemini wrote. No category fallback: a generic "scientist laboratory"
    clip put medical gloves under a maths story (2026-11-21). Off-topic footage is worse than none, so
    without a query the video uses the clean text layout."""
    return [str(draft.get("broll_query") or "").strip()]
