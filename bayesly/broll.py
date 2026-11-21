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
CATEGORY_QUERY = {
    "Models": "artificial intelligence abstract", "Research": "scientist laboratory", "Tools": "person typing laptop",
    "Infra": "data center servers", "Chips": "computer chip macro", "Policy": "government building",
    "Safety": "cyber security code", "Business": "city office skyline", "Other": "technology abstract",
}


@dataclass
class Broll:
    path: Path
    credit: str        # "Video: Pexels · <author>"
    page: str          # pexels.com page of the clip


def _pick_file(video: dict) -> dict | None:
    """An mp4 between 1280 and 1920 px wide: sharp enough for 1080 px, light enough to download."""
    files = [f for f in video.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("width")]
    good = sorted((f for f in files if 1280 <= f["width"] <= 1920), key=lambda f: -f["width"])
    return good[0] if good else (sorted(files, key=lambda f: -f["width"])[0] if files else None)


def find_broll(queries: list[str], api_key: str, out: Path, *, timeout: float = 30, session: Any = None) -> Broll | None:
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
        for video in data.get("videos", []):
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
            out.write_bytes(blob)
            author = ((video.get("user") or {}).get("name") or "").strip()
            return Broll(out, "Video: Pexels" + (f" · {author}" if author else ""), video.get("url", ""))
    return None


def queries_for(draft: dict, category: str) -> list[str]:
    return [str(draft.get("broll_query") or ""), CATEGORY_QUERY.get(category, ""), CATEGORY_QUERY["Other"]]
