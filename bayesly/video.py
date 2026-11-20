"""Vertical video (1080x1920 MP4) from a draft: the same story as the carousel, told in ~20-35 s.

Scenes: hook (source photo + headline + confidence chip) -> facts one by one -> our take -> question.
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

from bayesly import net
from bayesly.cards import Card, build_card
from bayesly.config import ROOT, Settings
from bayesly.render import (CONF_LABEL, DEFAULT_QUESTION, FONTS_URL, SOURCE_LABEL, RenderError, _facts,
                            _headline_html, _launch, _opinion, domain, e, find_cover_image, photo_allowed,
                            source_label)

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
    kind: str           # hook | fact | take | ask
    voice: str          # what is said
    label: str = ""     # fact label
    text: str = ""      # fact text
    start: float = 0.0
    dur: float = 0.0
    pcm: bytes = b""


# -- script ---------------------------------------------------------------------------------
def build_segments(card: Card, record: dict[str, Any]) -> list[Segment]:
    draft = record.get("draft") or {}
    segs = [Segment("hook", card.headline.rstrip(".") + ".")]
    spoken = {"Before": "Before", "Now": "Now", "The number": "The number", "Catch": "The catch",
              "Still unknown": "Still unknown"}
    for label, text in _facts(card):
        segs.append(Segment("fact", f"{spoken.get(label, label)}: {text.rstrip('.')}.", label=label, text=text))
    opinion = _opinion(card) or card.confidence_reason
    if opinion:
        segs.append(Segment("take", f"Our take: {opinion}"))
    question = (draft.get("question") or "").strip() or DEFAULT_QUESTION.get(card.confidence, DEFAULT_QUESTION["medium"])
    segs.append(Segment("ask", f"{question} Tell us in the comments.", text=question))
    return segs


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
    design = (ROOT / "design").as_uri()
    hook = segs[0]
    facts = [s for s in segs if s.kind == "fact"]
    take = next((s for s in segs if s.kind == "take"), None)
    ask = segs[-1]
    src = record["source"]
    photo = record.get("cover_image")
    end = lambda s: s.start + s.dur  # noqa: E731
    chip = (f'<div class="chip conf-{e(card.confidence)}" data-in="0.15" data-rise><b>●</b> '
            f'{e(CONF_LABEL.get(card.confidence, ""))} · {e(SOURCE_LABEL.get(source_label(record), "Source"))}</div>')
    pic = (f'<img class="photo" src="{e(photo)}" alt="" data-dur="{end(hook):.2f}">'
           f'<div class="credit">Image: {e(src.get("name") or domain(src["url"]))}</div>' if photo
           else '<div class="nophoto"></div>')
    scenes = [f"""
<section class="scene hook" data-in="0" data-out="{end(hook):.2f}">
  {pic}<div class="fade"></div>{chip}
  <h1 class="pad" data-in="0.1" data-rise data-fit="470">{_headline_html(card)}</h1>
  <div class="v-source">Source: {e(domain(src["url"]))}</div>
</section>"""]
    if facts:
        items = "".join(
            f'<li data-in="{f.start:.2f}" data-rise{f" data-dim={facts[i + 1].start:.2f}" if i + 1 < len(facts) else ""}>'
            f'<span class="n">{i + 1}</span><div><p class="k">{e(f.label)}</p><p class="t">{e(f.text)}</p></div></li>'
            for i, f in enumerate(facts))
        scenes.append(f"""
<section class="scene facts" data-in="{facts[0].start:.2f}" data-out="{end(facts[-1]):.2f}">
  <h2 class="pad">What changed</h2><ol class="pad" data-fit="1080">{items}</ol>
</section>""")
    if take:
        scenes.append(f"""
<section class="scene take" data-in="{take.start:.2f}" data-out="{end(take):.2f}">
  <p class="kick pad">Our take — opinion</p>
  <blockquote class="pad" data-in="{take.start + 0.1:.2f}" data-rise data-fit="1080">{e(_opinion(card) or card.confidence_reason)}</blockquote>
</section>""")
    scenes.append(f"""
<section class="scene ask" data-in="{ask.start:.2f}">
  <p class="kick pad">Your call</p>
  <h2 class="pad" data-in="{ask.start + 0.1:.2f}" data-rise data-fit="620">{e(ask.text)}</h2>
  <div class="cta pad" data-in="{ask.start + 0.8:.2f}" data-rise><span>Tell us in the comments ↓</span><span class="pill">Follow @bayesly.ai</span></div>
</section>""")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Bayesly video</title>
<link rel="stylesheet" href="{FONTS_URL}"><link rel="stylesheet" href="{design}/bayesly.css"><link rel="stylesheet" href="{design}/video.css">
</head><body>
<div class="v">
  <div class="v-progress"></div>
  <div class="v-brand"><div class="wordmark">bayesly<i></i></div><span class="handle">@bayesly.ai</span></div>
  {"".join(scenes)}
</div>
<script>
window.__TOTAL = {total:.3f};
window.__seek = function (t) {{
  var F = {FADE};
  function c(x) {{ return Math.min(1, Math.max(0, x)); }}
  document.querySelectorAll("[data-in]").forEach(function (el) {{
    var a = +el.dataset.in, b = el.dataset.out ? +el.dataset.out : 1e9;
    var i = c((t - a) / F), o = c((b - t) / F), op = i * o;
    if (el.dataset.dim && t >= +el.dataset.dim) op *= 0.4;
    el.style.opacity = op;
    if (el.hasAttribute("data-rise")) {{ var k = 1 - Math.pow(1 - i, 3); el.style.transform = "translateY(" + ((1 - k) * 60) + "px)"; }}
  }});
  var ph = document.querySelector(".photo");
  if (ph) ph.style.transform = "scale(" + (1 + 0.10 * c(t / +ph.dataset.dur)) + ")";
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
