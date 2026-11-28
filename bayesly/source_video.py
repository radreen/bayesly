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
        "outtmpl": f"{out}.%(ext)s",   # out has no extension: <draft>.src<i>
        "quiet": True, "no_warnings": True, "noplaylist": True, "noprogress": True,
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


# -- live footage: the subject itself in action (gameplay, robot, product demo) ------------------
# Owner, 2026-11-28: competitors show GTA 6 gameplay for a GTA 6 story, not a photo or an article
# scroll. The draft's `footage_query` says what the viewer should SEE; we search YouTube uploads of
# THIS WEEK, keep videos whose title names the subject, pick the most watched, and cut 3 short
# moments from its middle (no intros, no talking heads at the start). Credited on screen.
FOOTAGE_CUTS = (0.22, 0.36, 0.5, 0.64, 0.78)   # 5 candidate moments; the 3 liveliest are kept
CUT_SECONDS = 9
_TALKY = re.compile(r"\b(podcast|reaction|reacts|explained|interview|news|livestream|live stream|tier list|"
                    r"q&a|shorts?|#shorts|top \d+|ranking|rumou?rs?)\b", re.I)


MIN_MOTION = 14.0     # mean luma difference between frames at 2 fps (gameplay ~18-21, a web page ~13)
MAX_BRIGHT = 175.0    # mean luma; documents and web pages sit near white


def liveliness(path: Path) -> tuple[float, float]:
    """(mean frame-to-frame luma change, mean brightness) via ffmpeg signalstats at 2 fps."""
    from bayesly.video import _ffmpeg
    try:
        r = subprocess.run([_ffmpeg(), "-nostdin", "-v", "error", "-i", str(path), "-vf",
                            "fps=2,signalstats,metadata=print:file=-", "-an", "-f", "null", "-"],
                           capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return 0.0, 255.0
    ydif = [float(x) for x in re.findall(r"lavfi\.signalstats\.YDIF=([\d.]+)", r.stdout)]
    yavg = [float(x) for x in re.findall(r"lavfi\.signalstats\.YAVG=([\d.]+)", r.stdout)]
    mean = lambda v: sum(v) / len(v) if v else 0.0   # noqa: E731
    return mean(ydif[1:]), mean(yavg) if yavg else 255.0


def footage_search(query: str, terms: list[str], *, limit: int = 12) -> list[dict]:
    """YouTube uploads of this week for `query`, best first. Each: {url, title, channel, duration, views}."""
    try:
        import yt_dlp
        from urllib.parse import quote
    except ImportError:
        return []
    url = f"https://www.youtube.com/results?search_query={quote(query)}&sp=EgIIAw%253D%253D"   # this week
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": "in_playlist",
                               "playlistend": limit, "socket_timeout": 20}) as ydl:
            res = ydl.extract_info(url, download=False) or {}
    except Exception as exc:  # noqa: BLE001 - YouTube can block CI runners: no clip, carousel instead
        log.info("footage search failed", extra={"error": str(exc)[:160]})
        return []
    keys = [t.lower() for t in terms if len(t) > 1]
    hits = []
    for e in res.get("entries") or []:
        title, dur = (e.get("title") or ""), e.get("duration") or 0
        if not e.get("id") or not 45 <= dur <= 4 * 3600 or _TALKY.search(title):
            continue
        low = title.lower()
        if keys and (keys[0] not in low or (len(keys) > 2 and not any(k in low for k in keys[1:]))):
            continue                     # the subject's name must be in the title
        hits.append({"url": f"https://www.youtube.com/watch?v={e['id']}", "title": title, "duration": dur,
                     "channel": (e.get("channel") or e.get("uploader") or "YouTube")[:40], "views": e.get("view_count") or 0})
    return sorted(hits, key=lambda h: h["views"], reverse=True)


def cut_footage(hit: dict, out: Path) -> list[Clip]:
    """Download only CUT_SECONDS windows from the middle of the video (not the whole file)."""
    try:
        import yt_dlp
    except ImportError:
        return []
    dur = float(hit["duration"])
    windows = [(max(30.0, dur * f), max(30.0, dur * f) + CUT_SECONDS) for f in FOOTAGE_CUTS]
    windows = [(a, min(b, dur)) for a, b in windows if a + 3 < dur]
    opts = {"outtmpl": f"{out}.%(section_start)d.%(ext)s", "quiet": True, "no_warnings": True, "noprogress": True,
            "noplaylist": True, "format": "bv*[ext=mp4][height<=1080]/bv*[height<=1080]/b[height<=1080]",
            "socket_timeout": 30, "retries": 1, "download_ranges": yt_dlp.utils.download_range_func(None, windows),
            "force_keyframes_at_cuts": False}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(hit["url"], download=True)
    except Exception as exc:  # noqa: BLE001
        log.info("footage download failed", extra={"url": hit["url"], "error": str(exc)[:160]})
        return []
    scored = []
    for path in sorted(out.parent.glob(out.name + ".*")):
        if path.suffix.lower() in {".mp4", ".webm", ".mkv", ".mov"}:
            secs = _probe_seconds(path)
            motion, bright = liveliness(path)
            # a static page / slide (little change, mostly white) is not "the thing in action"
            if secs >= MIN_SECONDS and motion >= MIN_MOTION and bright <= MAX_BRIGHT:
                scored.append((motion, Clip(path, hit["channel"], secs, 0.0)))
    keep = [c for _, c in sorted(scored, key=lambda x: -x[0])[:CLIPS]]
    return sorted(keep, key=lambda c: int(c.path.name.split('.')[-2]))   # play in time order


def find_footage(query: str, terms: list[str], out: Path) -> SourceVideo | None:
    if not query.strip():
        return None
    for hit in footage_search(query, terms)[:2]:
        clips = cut_footage(hit, out.parent / f"{out.name.split('.')[0]}.live")
        if clips:
            log.info("live footage", extra={"title": hit["title"][:80], "channel": hit["channel"], "cuts": len(clips)})
            return SourceVideo(clips)
    log.info("live footage: none", extra={"query": query})
    return None


_GENERIC = {"gameplay", "demo", "footage", "live", "video", "official", "trailer", "pc", "launch", "test",
            "hands-on", "hands", "on", "first", "look", "new", "the", "of", "in", "and", "with", "robot", "4k"}


def footage_terms(query: str) -> list[str]:
    """Words of a footage query that must appear in a video title (the subject's name, not 'gameplay')."""
    return [w for w in query.replace(",", " ").split() if w.lower() not in _GENERIC and (len(w) > 2 or w.isdigit())][:3]         or query.split()[:1]
