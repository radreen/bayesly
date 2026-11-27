"""Real footage from the news itself: videos embedded in the source article (company demo clips,
keynote snippets, product promos posted on X / YouTube / Vimeo, or plain MP4s on the page).

Owner request 2026-11-24: competitors show the actual video of the news, not stock footage or a
page screenshot. Order of preference in the Reel: this > source photo / page scroll > Pexels.

How: fetch the article HTML, collect video candidates in page order, download up to CLIPS of them
with yt-dlp (handles X, YouTube, Vimeo and direct files), keep only real video >= MIN_SECONDS.
Downloads are capped (size, duration, time). Anything failing simply yields no clip.

Rights: clips are short excerpts used for news reporting, always credited on screen
("Video: <uploader or site>"), never the whole video, and the audio is dropped (our voice-over only).
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from bayesly import net

log = logging.getLogger(__name__)

CLIPS = 3
MIN_SECONDS = 3.0
MAX_SOURCE_SECONDS = 20 * 60      # skip full-length streams/keynotes longer than this
MAX_FILESIZE = 120 * 1024 * 1024
CANDIDATES = 6

_DIRECT = re.compile(r'<(?:video|source)\b[^>]*\bsrc=["\']([^"\']+\.(?:mp4|webm|mov)(?:\?[^"\']*)?)["\']', re.I)
_OG = re.compile(r'<meta[^>]+(?:property|name)=["\'](?:og:video(?::secure_url|:url)?|twitter:player:stream)["\'][^>]+content=["\']([^"\']+)["\']', re.I)
_YT = re.compile(r'(?:youtube(?:-nocookie)?\.com/(?:embed/|watch\?v=|shorts/)|youtu\.be/)([\w-]{11})')
_VIMEO = re.compile(r'player\.vimeo\.com/video/(\d+)|vimeo\.com/(\d{6,})')
_X = re.compile(r'(?:twitter|x)\.com/(\w{1,15})/status/(\d{8,})')


@dataclass
class Clip:
    path: Path
    credit: str          # e.g. "@OpenAI" or "the-decoder.com"
    duration: float
    start: float = 0.0   # where to start inside the clip (skip intros of long videos)


@dataclass
class SourceVideo:
    clips: list[Clip] = field(default_factory=list)

    @property
    def credit(self) -> str:
        names = list(dict.fromkeys(c.credit for c in self.clips if c.credit))
        return "Video: " + ", ".join(names[:2]) if names else "Video: source"


def candidates(html: str, page_url: str) -> list[str]:
    """Video URLs in page order (the first embed is usually the one the story is about)."""
    found: list[tuple[int, str]] = []
    for m in _DIRECT.finditer(html):
        found.append((m.start(), urljoin(page_url, m.group(1))))
    for m in _OG.finditer(html):
        if re.search(r"\.(mp4|webm|mov)(\?|$)", m.group(1), re.I):
            found.append((m.start(), urljoin(page_url, m.group(1))))
    for m in _YT.finditer(html):
        found.append((m.start(), f"https://www.youtube.com/watch?v={m.group(1)}"))
    for m in _VIMEO.finditer(html):
        found.append((m.start(), f"https://vimeo.com/{m.group(1) or m.group(2)}"))
    for m in _X.finditer(html):
        if m.group(1).lower() not in {"intent", "share", "i", "home", "search"}:
            found.append((m.start(), f"https://x.com/{m.group(1)}/status/{m.group(2)}"))
    out = []
    for _, u in sorted(found):
        if u not in out:
            out.append(u)
    return out[:CANDIDATES]


def _probe_seconds(path: Path) -> float:
    from bayesly.video import _ffmpeg
    ffprobe = str(Path(_ffmpeg()).with_name("ffprobe" + Path(_ffmpeg()).suffix))
    try:
        r = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "format=duration",
                            "-of", "csv=p=0", str(path)], capture_output=True, text=True, timeout=30)
        return float(r.stdout.strip() or 0)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 0.0


def _credit(info: dict, url: str, page_url: str) -> str:
    host = urlsplit(url).netloc.lower().removeprefix("www.")
    if host in {"x.com", "twitter.com"}:
        handle = info.get("uploader_id") or url.split("/")[3]
        return f"@{handle}"
    if host in {"youtube.com", "vimeo.com", "youtu.be"}:
        return (info.get("channel") or info.get("uploader") or host)[:40]
    return urlsplit(page_url).netloc.lower().removeprefix("www.")


def download(url: str, out: Path, page_url: str, *, timeout: float = 120, min_date: str = "") -> Clip | None:
    try:
        import yt_dlp
    except ImportError:
        log.info("yt-dlp not installed: no source video")
        return None
    opts = {
        "outtmpl": f"{out}.%(ext)s",   # out has no extension: <draft>.src<i> "quiet": True, "no_warnings": True, "noplaylist": True,
        "format": "bv*[ext=mp4][height<=1080]/b[ext=mp4][height<=1080]/bv*[height<=1080]/b",
        "max_filesize": MAX_FILESIZE, "socket_timeout": 30, "retries": 1,
        "match_filter": yt_dlp.utils.match_filter_func(f"duration <? {MAX_SOURCE_SECONDS}"
                                                       + (f" & upload_date >=? {min_date}" if min_date else "")),
        "download_ranges": yt_dlp.utils.download_range_func(None, [(0, 90)]),   # never pull a whole keynote
        "http_headers": {"Referer": page_url},
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True) or {}
            path = Path(ydl.prepare_filename(info))
    except Exception as exc:  # noqa: BLE001 - one bad embed must not stop the others
        log.info("source video skipped", extra={"url": url, "error": str(exc)[:160]})
        return None
    if not path.exists():
        alt = list(out.parent.glob(out.name + ".*"))
        path = next((p for p in alt if p.suffix.lower() in {".mp4", ".webm", ".mov", ".mkv"}), path)
    secs = _probe_seconds(path) if path.exists() else 0.0
    if secs < MIN_SECONDS:
        return None
    start = min(3.0, secs * 0.1) if secs > 15 else 0.0     # skip logo intros of longer clips
    return Clip(path, _credit(info, url, page_url), secs, start)


# Official sites: when an article links to the company's own announcement, that page usually carries
# the launch video (what competitors post). One hop only.
OFFICIAL_DOMAINS = (
    "openai.com", "anthropic.com", "blog.google", "deepmind.google", "ai.google.dev", "nvidia.com",
    "apple.com", "about.fb.com", "ai.meta.com", "meta.com", "microsoft.com", "x.ai", "mistral.ai",
    "huggingface.co", "aboutamazon.com", "aws.amazon.com", "tesla.com", "figure.ai", "perplexity.ai",
    "runwayml.com", "higgsfield.ai", "elevenlabs.io", "stability.ai", "deepseek.com", "qwen.ai", "z.ai",
    "bostondynamics.com", "samsung.com", "adobe.com", "notion.com", "cursor.com", "github.blog",
)
# Official YouTube channels (exact channel names). A search result from anyone else is ignored.
OFFICIAL_CHANNELS = {
    "openai", "google", "google deepmind", "google for developers", "nvidia", "nvidia developer", "anthropic",
    "meta", "ai at meta", "microsoft", "microsoft developer", "apple", "tesla", "xai", "figure", "boston dynamics",
    "hugging face", "mistral ai", "perplexity", "runway", "elevenlabs", "samsung", "amazon", "amazon web services",
    "adobe", "github", "cursor", "higgsfield ai", "deepseek", "qwen", "alibaba cloud", "zhipu ai", "z.ai",
}
YT_MAX_AGE_DAYS = 10
_HREF = re.compile(r"""href=["'](https?://[^"'#]+)["']""", re.I)


def official_links(html: str, page_url: str, limit: int = 3) -> list[str]:
    here = urlsplit(page_url).netloc.lower().removeprefix("www.")
    out = []
    for m in _HREF.finditer(html):
        u = m.group(1)
        parts = urlsplit(u)
        host = parts.netloc.lower().removeprefix("www.")
        if host == here or len(parts.path.strip("/")) < 3:
            continue
        if any(host == d or host.endswith("." + d) for d in OFFICIAL_DOMAINS) and u not in out:
            out.append(u)
        if len(out) >= limit:
            break
    return out


def youtube_official(query: str, terms: list[str], *, limit: int = 2) -> list[str]:
    """Recent videos from official company channels matching the story (title shares a key term)."""
    try:
        import yt_dlp
    except ImportError:
        return []
    try:
        from urllib.parse import quote
        url = f"https://www.youtube.com/results?search_query={quote(query)}&sp=CAI%253D"   # sorted by upload date
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": "in_playlist",
                               "playlistend": 15, "socket_timeout": 20}) as ydl:
            res = ydl.extract_info(url, download=False) or {}
    except Exception as exc:  # noqa: BLE001 - YouTube may block CI runners: just no clip
        log.info("youtube search failed", extra={"error": str(exc)[:160]})
        return []
    keys = [t.lower() for t in terms if len(t) > 2]
    out = []
    for e in res.get("entries") or []:
        channel = (e.get("channel") or e.get("uploader") or "").strip().lower()
        title = (e.get("title") or "").lower()
        if channel in OFFICIAL_CHANNELS and any(k in title for k in keys) and e.get("id"):
            out.append(f"https://www.youtube.com/watch?v={e['id']}")
        if len(out) >= limit:
            break
    return out


def _page(url: str, session: Any = None) -> str:
    try:
        return net.request("GET", url, timeout=20, max_retries=1, session=session,
                           headers={"User-Agent": "Mozilla/5.0 (compatible; BayeslyBot/1.0)"}).text[:2_000_000]
    except Exception:  # noqa: BLE001
        return ""


def find_source_video(page_url: str, out: Path, *, html: str | None = None, session: Any = None,
                      company: str = "", terms: list[str] | None = None) -> SourceVideo | None:
    """Up to CLIPS real clips for the story, saved next to `out`. Where we look, in order:
    1. videos embedded in the article; 2. the company's own pages the article links to;
    3. recent videos on the company's official YouTube channel (needs `company` + `terms`)."""
    if not page_url:
        return None
    html = _page(page_url, session) if html is None else html
    urls = candidates(html, page_url) if html else []
    for link in official_links(html, page_url) if html else []:
        if len(urls) >= CANDIDATES:
            break
        sub = _page(link, session)
        urls += [u for u in candidates(sub, link) if u not in urls] if sub else []
    yt_new: set[str] = set()
    if company and terms and len(urls) < CLIPS:
        found = youtube_official(f"{company} {' '.join(terms[:3])}", terms)
        yt_new.update(found)
        urls += [u for u in found if u not in urls]
    got = SourceVideo()
    since = (datetime.now(timezone.utc) - timedelta(days=YT_MAX_AGE_DAYS)).strftime("%Y%m%d")
    for i, url in enumerate(urls[: CANDIDATES + 2]):
        if len(got.clips) >= CLIPS:
            break
        clip = download(url, out.parent / f"{out.name.split('.')[0]}.src{i}", page_url,
                        min_date=since if url in yt_new else "")   # searched videos must be recent
        if clip:
            got.clips.append(clip)
    if got.clips:
        log.info("source video", extra={"clips": len(got.clips), "credit": got.credit})
    else:
        log.info("source video: none", extra={"looked_at": len(urls)})
    return got if got.clips else None


STOP = {"says", "said", "with", "from", "that", "this", "will", "into", "over", "their", "after", "about", "new",
        "launches", "unveils", "announces", "releases", "adds", "gets", "report", "could", "would", "more", "than",
        "and", "are", "the", "for", "its", "has", "have", "how", "why", "what", "now", "can", "not", "but", "you",
        "employees", "company", "companies", "model", "models", "users", "tools", "people", "ceo", "inc"}


def story_terms(headline: str, company: str = "") -> list[str]:
    """Product-like words of a headline (GPT-6.2, Astra, Muse, Inkling...): capitalised or with a digit,
    not a stop word and not a company name. Generic words never trigger a YouTube search."""
    from bayesly.engage import COMPANIES
    words = [w.strip(".,:;!?'\"()") for w in headline.split()[1:]]      # skip the sentence-initial capital
    keep = []
    for w in words:
        lw = w.lower().removesuffix("'s")
        if len(w) < 3 or lw in STOP or lw in COMPANIES or lw in company.lower():
            continue
        if w[:1].isupper() or any(c.isdigit() for c in w):
            keep.append(w.removesuffix("'s"))
    return list(dict.fromkeys(keep))[:4]
