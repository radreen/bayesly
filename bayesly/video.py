"""Vertical video (1080x1920 MP4) from a draft: the same story as the carousel, told in ~20-35 s.

Scenes: hook headline over the source photo -> key facts over the source article scrolling -> question.
Each scene lasts as long as its voice line (Gemini TTS); without a voice the timing comes from
text length and the video is silent (all text is on screen anyway: most people watch muted).

Frames are captured deterministically: design/video.html-style page, window.__seek(t) per frame,
Playwright screenshot, then ffmpeg -> H.264/AAC MP4 (fits Instagram Reels, Threads, YouTube Shorts).
"""

from __future__ import annotations

import base64
import io
import json
import logging
import shutil
import subprocess
import tempfile
import wave
from time import sleep
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from bayesly import net
from bayesly.cards import Card, build_card
from bayesly.config import ROOT, Settings
from bayesly.render import (FONTS_URL, RenderError, _headline_html, _launch, domain, e, find_cover_image,
                            key_facts, photo_allowed, question_for)

log = logging.getLogger(__name__)

FPS = 30
W, H = 1080, 1920
GAP = 0.35            # pause after each voice line, seconds
TAIL = 1.4            # hold the last scene
FADE = 0.3
TTS_MODEL = "gemini-3.8-flash-tts"
TTS_VOICE = "Charon"  # docs: "Informative"
TTS_STYLE = "calm, clear news presenter, brisk pace, no hype"
RATE = 24000          # TTS output: 24 kHz mono 16-bit PCM (docs, checked 2026-11-20)
TTS_MODELS = ("gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts")   # separate free-tier quotas
PAUSE_TAG = "<long pause>"   # docs: inline tags in angle brackets
SILENCE_LEVEL = 600          # |sample| below this counts as silence
MIN_PAUSE = 0.25             # seconds
TTS_ATTEMPTS = 3
TTS_BACKOFF = 20.0    # seconds x attempt, on 429/503


@dataclass
class Segment:
    kind: str           # hook | fact | ask
    voice: str          # what is said
    label: str = ""     # fact label
    text: str = ""      # fact text
    start: float = 0.0
    dur: float = 0.0
    pcm: bytes = b""


# -- script ---------------------------------------------------------------------------------
def build_segments(card: Card, record: dict[str, Any]) -> list[Segment]:
    """Hook headline -> 3-4 key facts -> question. No source, no confidence, no opinion (owner, 2026-11-20)."""
    segs = [Segment("hook", card.headline.rstrip(".") + ".", text=card.headline)]
    for fact in key_facts(card, record):
        segs.append(Segment("fact", fact.rstrip(".") + ".", text=fact))
    question = question_for(card, record)
    segs.append(Segment("ask", f"{question} Tell us in the comments.", text=question))
    return segs


# -- live footage: the source article itself, scrolling ----------------------------------------
PAYWALLED = ("bloomberg.com", "wsj.com", "ft.com", "nytimes.com", "theinformation.com", "economist.com",
             "barrons.com", "businessinsider.com", "washingtonpost.com", "x.com", "twitter.com")

HIDE_OVERLAYS = """
[id*="cookie" i], [class*="cookie" i], [id*="consent" i], [class*="consent" i], [class*="newsletter" i],
[class*="paywall" i], [id*="onetrust" i], [class*="modal" i], [class*="popup" i], [aria-modal="true"],
header[class*="sticky" i], [class*="sticky" i][class*="header" i] { display: none !important; }
html, body { overflow: visible !important; }
"""


def capture_article(url: str, out: Path, *, max_css_height: int = 2600, timeout_ms: int = 25000) -> Path | None:
    """Phone-sized screenshot of the source page (1080 px wide), used as scrolling footage in the video.
    Best effort: any failure returns None and the video uses the cover photo instead."""
    from playwright.sync_api import sync_playwright

    host = urlsplit(url).netloc.lower().removeprefix("www.")
    if any(host == d or host.endswith("." + d) for d in PAYWALLED):
        return None          # the page would show a subscribe wall, not the story: use the photo only
    try:
        with sync_playwright() as p:
            browser = _launch(p)
            try:
                page = browser.new_page(viewport={"width": 540, "height": 960}, device_scale_factor=2,
                                        user_agent=("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
                                                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"))
                page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                page.add_style_tag(content=HIDE_OVERLAYS)
                for y in range(0, max_css_height, 400):          # wake lazy images
                    page.evaluate(f"window.scrollTo(0, {y})")
                    page.wait_for_timeout(120)
                page.evaluate("window.scrollTo(0, 0)")
                page.wait_for_timeout(600)
                try:
                    page.wait_for_load_state("load", timeout=10000)
                except Exception:  # noqa: BLE001 - slow ads/trackers: measure what we have
                    pass
                height = 0
                for _ in range(4):                               # pages grow while they hydrate
                    height = int(page.evaluate("document.documentElement.scrollHeight"))
                    if height >= 1200:
                        break
                    page.wait_for_timeout(1000)
                height = min(height, max_css_height)
                if height < 1200:
                    log.info("article capture skipped", extra={"url": url, "error": f"page only {height}px"})
                    return None                                  # too short to be an article
                # (Playwright refuses full_page + clip together: grow the viewport instead)
                page.set_viewport_size({"width": 540, "height": height})
                page.wait_for_timeout(400)
                page.screenshot(path=str(out), type="jpeg", quality=82)
            finally:
                browser.close()
        return out
    except Exception as exc:  # noqa: BLE001 - footage is a bonus, never a blocker
        log.info("article capture skipped", extra={"url": url, "error": str(exc)[:200]})
        return None


# -- voice ----------------------------------------------------------------------------------
def _is_quota(exc: Exception) -> bool:
    return "429" in str(exc)


def voice_segments(segs: list[Segment], settings: Settings, *, session: Any = None) -> None:
    """Voice the whole script in ONE request (free tier allows ~10 TTS requests/day per model),
    then cut the audio at the long pauses we asked for between lines. Falls back to the lite
    TTS model when the main one is out of quota."""
    script = f" {PAUSE_TAG} ".join(s.voice for s in segs)
    last: Exception | None = None
    for model in TTS_MODELS:
        try:
            pcm = tts(script, settings, model=model, session=session)
            break
        except net.ExternalCallError as exc:
            last = exc
            if not _is_quota(exc):
                raise
    else:
        raise last or RenderError("no TTS model available")
    for s, part in zip(segs, split_on_pauses(pcm, len(segs), [len(s.voice) for s in segs])):
        s.pcm = part


def split_on_pauses(pcm: bytes, n: int, weights: list[int]) -> list[bytes]:
    """Cut 16-bit mono PCM into n parts at the n-1 longest silences (the <long pause> tags).
    If the audio has fewer clear pauses, cut proportionally to the text length of each line."""
    if n <= 1:
        return [pcm]
    win = RATE // 50                                   # 20 ms windows
    samples = memoryview(pcm).cast("h")
    loud = []
    for i in range(0, len(samples), win):
        chunk = samples[i:i + win]
        loud.append(max((abs(x) for x in chunk), default=0) > SILENCE_LEVEL)
    runs, start = [], None                              # silent runs as (length, start, end) in windows
    for i, is_loud in enumerate(loud + [True]):
        if not is_loud and start is None:
            start = i
        elif is_loud and start is not None:
            if start > 0 and i < len(loud) and (i - start) * 0.02 >= MIN_PAUSE:
                runs.append((i - start, start, i))
            start = None
    cuts: list[int]
    if len(runs) >= n - 1:
        best = sorted(sorted(runs, reverse=True)[: n - 1], key=lambda r: r[1])
        cuts = [((a + b) // 2) * win * 2 for _, a, b in best]
    else:
        total, acc, cuts = sum(weights) or 1, 0, []
        for w in weights[:-1]:
            acc += w
            cuts.append(int(len(pcm) * acc / total) // 2 * 2)
    edges = [0] + cuts + [len(pcm)]
    return [_trim(pcm[a:b]) for a, b in zip(edges, edges[1:])]


def _trim(part: bytes) -> bytes:
    """Drop leading/trailing silence so each line starts on time."""
    s = memoryview(part).cast("h")
    lo, hi = 0, len(s)
    while lo < hi and abs(s[lo]) <= SILENCE_LEVEL:
        lo += 1
    while hi > lo and abs(s[hi - 1]) <= SILENCE_LEVEL:
        hi -= 1
    pad = RATE // 20                                     # keep 50 ms around the speech
    return part[max(0, lo - pad) * 2: min(len(s), hi + pad) * 2]


def tts(text: str, settings: Settings, *, voice: str = TTS_VOICE, model: str = TTS_MODEL,
        session: Any = None) -> bytes:
    """Gemini TTS via the Interactions API. Returns raw 16-bit mono PCM at 24 kHz."""
    body = {
        "model": model, "store": False,
        "input": [{"type": "user_input", "content": [{"type": "text", "text": text,
                   "annotations": [{"type": "speech_metadata", "style": TTS_STYLE}]}]}],
        "response_format": {"type": "audio"},
        "generation_config": {"speech_config": [{"voice": voice}]},
    }
    for attempt in range(1, TTS_ATTEMPTS + 1):
        try:
            resp = net.request("POST", "https://generativelanguage.googleapis.com/v1beta/interactions",
                               timeout=max(settings.gemini_timeout, 60), max_retries=0, session=session,
                               headers={"x-goog-api-key": settings.gemini_api_key,
                                        "Api-Revision": settings.gemini_api_revision}, json=body)
            break
        except net.ExternalCallError as exc:
            # a daily-quota 429 won't clear by waiting: let the caller switch models
            if attempt == TTS_ATTEMPTS or "per day" in str(exc) or not any(
                    code in str(exc) for code in ("429", "503", "Timeout")):
                raise
            sleep(TTS_BACKOFF * attempt)
    data = next(c["data"] for s in resp.json().get("steps", []) if s.get("type") == "model_output"
                for c in s.get("content", []) if c.get("type") == "audio")
    raw = base64.b64decode(data)
    with wave.open(io.BytesIO(raw)) as w:      # default response is WAV; keep only the PCM frames
        if w.getframerate() != RATE or w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise RenderError(f"unexpected TTS format {w.getframerate()} Hz {w.getnchannels()} ch")
        return w.readframes(w.getnframes())


def plan_timing(segs: list[Segment]) -> float:
    """Fill start/dur from voice length (or reading time). Returns total duration."""
    t = 0.0
    for s in segs:
        if s.pcm:
            s.dur = len(s.pcm) / 2 / RATE + GAP
        else:  # silent: ~14 characters per second, at least 2.5 s
            s.dur = max(2.5, len(s.voice) / 14) + GAP
        s.start = t
        t += s.dur
    return t + TAIL


def mix_audio(segs: list[Segment], total: float) -> bytes:
    """One mono 24 kHz WAV: each voice line at its segment start, silence elsewhere."""
    buf = bytearray(int(total * RATE) * 2)
    for s in segs:
        at = int(s.start * RATE) * 2
        buf[at:at + len(s.pcm)] = s.pcm[: len(buf) - at]
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(buf))
    return out.getvalue()


# -- page -----------------------------------------------------------------------------------
def build_video_page(card: Card, record: dict[str, Any], segs: list[Segment], total: float) -> str:
    """Competitor-style Reel: big text on top, live footage in the middle, bottom kept free for the
    platform's caption. Footage: hook -> source photo (slow zoom); facts -> the source article scrolling
    (or the photo if we could not capture the page); end -> question card."""
    design = (ROOT / "design").as_uri()
    hook, ask = segs[0], segs[-1]
    facts = [s for s in segs if s.kind == "fact"]
    src = record["source"]
    photo, shot = record.get("cover_image"), record.get("page_shot")
    end = lambda s: s.start + s.dur  # noqa: E731
    credit = e(domain(src["url"]))
    facts_start = facts[0].start if facts else end(hook)
    facts_end = end(facts[-1]) if facts else end(hook)

    media = []
    if photo:
        media.append(f'<img class="m photo" src="{e(photo)}" alt="" data-in="0" data-out="{(facts_start if shot else facts_end):.2f}" '
                     f'data-zoom="0,{(facts_start if shot else facts_end):.2f}">')
    if shot:
        media.append(f'<img class="m shot" src="{Path(shot).as_uri()}" alt="" data-in="{(facts_start if photo else 0):.2f}" '
                     f'data-out="{facts_end:.2f}" data-scroll="{(facts_start if photo else 0):.2f},{facts_end:.2f}">')
    # No photo and no page: never show an empty box; the text takes the whole screen instead.
    layout = "withmedia" if media else "nomedia"

    lines = [f'<div class="line" data-in="0.05" data-out="{end(hook):.2f}" data-rise>'
             f'<h1 class="hookline" data-fit="560">{_headline_html(card)}</h1></div>']
    for i, f in enumerate(facts, start=1):
        lines.append(f'<div class="line fact" data-in="{f.start:.2f}" data-out="{end(f):.2f}" data-rise>'
                     f'<span class="n">{i}</span><p data-fit="520">{e(f.text)}</p></div>')
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Bayesly video</title>
<link rel="stylesheet" href="{FONTS_URL}"><link rel="stylesheet" href="{design}/bayesly.css"><link rel="stylesheet" href="{design}/video.css">
</head><body>
<div class="v {layout}">
  <div class="v-progress"></div>
  <div class="v-brand"><div class="wordmark">bayesly<i></i></div><span class="handle">@bayesly.ai</span></div>
  <section class="story" data-in="0" data-out="{facts_end:.2f}">
    {f'<div class="media">{"".join(media)}<div class="credit">{credit}</div></div>' if media else ''}
    {"".join(lines)}
  </section>
  <section class="ask" data-in="{ask.start:.2f}">
    <p class="kick">Your call</p>
    <h2 data-in="{ask.start + 0.1:.2f}" data-rise data-fit="700">{e(ask.text)}</h2>
    <div class="cta" data-in="{ask.start + 0.8:.2f}" data-rise><span>Tell us in the comments ↓</span><span class="pill">Follow @bayesly.ai</span></div>
  </section>
</div>
<script>
window.__TOTAL = {total:.3f};
window.__seek = function (t) {{
  var F = {FADE};
  function c(x) {{ return Math.min(1, Math.max(0, x)); }}
  document.querySelectorAll("[data-in]").forEach(function (el) {{
    var a = +el.dataset.in, b = el.dataset.out ? +el.dataset.out : 1e9;
    var i = c((t - a) / F), o = c((b - t) / F);
    el.style.opacity = i * o;
    if (el.hasAttribute("data-rise")) {{ var k = 1 - Math.pow(1 - i, 3); el.style.transform = "translateY(" + ((1 - k) * 50) + "px)"; }}
  }});
  document.querySelectorAll("[data-zoom]").forEach(function (el) {{
    var r = el.dataset.zoom.split(",").map(Number);
    el.style.transform = "scale(" + (1.02 + 0.12 * c((t - r[0]) / (r[1] - r[0]))) + ")";
  }});
  document.querySelectorAll("[data-scroll]").forEach(function (el) {{
    var r = el.dataset.scroll.split(",").map(Number), box = el.parentNode.clientHeight;
    var travel = Math.max(0, Math.min(el.naturalHeight * (1080 / el.naturalWidth) - box, 90 * (r[1] - r[0])));
    el.style.transform = "translateY(" + (-travel * c((t - r[0]) / (r[1] - r[0]))) + "px)";
  }});
  document.querySelector(".v-progress").style.width = (100 * c(t / window.__TOTAL)) + "%";
}};
</script>
<script src="{design}/logo.js"></script><script src="{design}/bayesly.js"></script>
</body></html>"""


# -- capture + encode -------------------------------------------------------------------------
def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RenderError("ffmpeg not found. Install it (winget install ffmpeg / apt-get install ffmpeg).")
    return exe


def capture(page_path: Path, frames_dir: Path, total: float, fps: int = FPS, timeout_ms: int = 30000) -> list[str]:
    from playwright.sync_api import sync_playwright

    warnings: list[str] = []
    n = int(total * fps)
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
            page.goto(page_path.as_uri(), timeout=timeout_ms)
            page.wait_for_selector('body[data-ready="1"]', timeout=timeout_ms)
            if page.locator("[data-overflow]").count():
                warnings.append("Some video text was too long to fit even after shrinking.")
            for i in range(n):
                page.evaluate(f"window.__seek({i / fps:.4f})")
                page.screenshot(path=str(frames_dir / f"{i:05d}.jpg"), type="jpeg", quality=90)
        finally:
            browser.close()
    return warnings


def encode(frames_dir: Path, wav: Path, out: Path, fps: int = FPS) -> None:
    cmd = [_ffmpeg(), "-y", "-loglevel", "error", "-framerate", str(fps), "-i", str(frames_dir / "%05d.jpg"),
           "-i", str(wav), "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p", "-crf", "20",
           "-preset", "medium", "-g", str(fps * 2), "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
           "-movflags", "+faststart", "-shortest", str(out)]
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode != 0:
        raise RenderError(f"ffmpeg failed: {done.stderr.strip()[:300]}")


def scale_for_x(src: Path, out: Path) -> None:
    """X's hard limit is 1280x1024 per side pair; it recommends 720x1280 portrait (docs, 2026-11-20)."""
    cmd = [_ffmpeg(), "-y", "-loglevel", "error", "-i", str(src), "-vf", "scale=720:1280", "-c:v", "libx264",
           "-profile:v", "high", "-pix_fmt", "yuv420p", "-b:v", "5M", "-c:a", "copy", "-movflags", "+faststart", str(out)]
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode != 0:
        raise RenderError(f"ffmpeg (X copy) failed: {done.stderr.strip()[:300]}")


def render_video(json_path: Path, settings: Settings, *, voice: bool = True, force: bool = False) -> Path:
    """Draft JSON -> <base>.mp4 next to it. Updates record["video"] and record["video_warnings"]."""
    record = json.loads(json_path.read_text(encoding="utf-8"))
    if not record.get("draft"):
        raise RenderError(f"{json_path.name}: no parsed draft")
    out = json_path.with_suffix(".mp4")
    if out.exists() and not force and record.get("video") == str(out):
        return out
    card = build_card(record)
    if "cover_image" not in record:
        src = record["source"]
        record["cover_image"] = find_cover_image(src["url"]) if photo_allowed(src.get("id", "")) else None
    segs = build_segments(card, record)
    shot_path = json_path.with_suffix(".page.jpg")
    shot = capture_article(record["source"]["url"], shot_path) or capture_article(record["source"]["url"], shot_path)
    record["page_shot"] = str(shot) if shot else None
    warnings: list[str] = []
    if voice and settings.gemini_api_key:
        try:
            voice_segments(segs, settings)
        except Exception as exc:  # noqa: BLE001 - no voice is better than no video
            warnings.append(f"voice failed, silent video: {str(exc)[:160]}")
            for x in segs:
                x.pcm = b""
    total = plan_timing(segs)
    with tempfile.TemporaryDirectory(prefix="bayesly-video-") as tmp:
        tmpd = Path(tmp)
        page = tmpd / "video.html"
        page.write_text(build_video_page(card, record, segs, total), encoding="utf-8")
        (tmpd / "frames").mkdir()
        warnings += capture(page, tmpd / "frames", total)
        wav = tmpd / "voice.wav"
        wav.write_bytes(mix_audio(segs, total))
        encode(tmpd / "frames", wav, out)
    x_out = json_path.with_suffix(".x.mp4")
    scale_for_x(out, x_out)
    record["video"] = str(out)
    record["video_x"] = str(x_out)
    record["video_seconds"] = round(total, 1)
    record["video_voice"] = bool(voice and all(s.pcm for s in segs))
    record["video_warnings"] = warnings
    json_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("video rendered", extra={"path": str(out), "seconds": record["video_seconds"]})
    return out
