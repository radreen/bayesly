"""Video script, timing, audio mix and page. No browser, no ffmpeg, no network."""

import base64
import io
import json
import wave
from pathlib import Path

from bayesly.cards import build_card
from bayesly.video import RATE, Segment, build_segments, build_video_page, mix_audio, plan_timing, tts

FIXTURE = Path(__file__).parent / "fixtures" / "sample_draft.json"


def record():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_script_follows_the_carousel():
    rec = record()
    rec["draft"]["question"] = "Worth switching for a 25% lower price?"
    segs = build_segments(build_card(rec), rec)
    kinds = [s.kind for s in segs]
    assert kinds[0] == "hook" and kinds[-1] == "ask" and "fact" in kinds and "take" not in kinds
    assert segs[-1].text == "Worth switching for a 25% lower price?"
    assert segs[-1].voice.endswith("Tell us in the comments.")
    assert all(s.voice.strip() for s in segs)


def test_timing_uses_voice_length_or_reading_time():
    a = Segment("hook", "Short.", pcm=b"\x00\x00" * RATE * 2)       # 2 s of audio
    b = Segment("ask", "x" * 70)                                    # silent: 70 chars ~ 5 s
    total = plan_timing([a, b])
    assert abs(a.dur - 2.35) < 0.01 and b.start == a.dur
    assert 5.0 < b.dur < 5.5 and total > a.dur + b.dur


def test_mix_places_voice_at_segment_start():
    s = Segment("hook", "x", pcm=b"\x01\x00" * 100, start=1.0)
    wav = mix_audio([s], 2.0)
    with wave.open(io.BytesIO(wav)) as w:
        frames = w.readframes(w.getnframes())
        assert w.getframerate() == RATE and w.getnframes() == 2 * RATE
    at = RATE * 2
    assert frames[at:at + 2] == b"\x01\x00" and frames[:2] == b"\x00\x00"


def test_page_is_escaped_and_seekable():
    rec = record()
    rec["draft"]["card"]["headline"] = "A <script>x</script> launch"
    rec["draft"]["card"]["highlight"] = None
    rec["cover_image"] = "https://example.com/p.jpg"
    card = build_card(rec)
    segs = build_segments(card, rec)
    total = plan_timing(segs)
    html = build_video_page(card, rec, segs, total)
    assert "window.__seek" in html and 'class="m photo"' in html
    rec["page_shot"] = "C:/x/page.jpg"
    html2 = build_video_page(card, rec, segs, total)
    assert 'class="m shot"' in html2 and "data-scroll=" in html2
    assert "Our take" not in html2 and "Source:" not in html2
    assert "<script>x</script>" not in html and "&lt;script&gt;" in html
    assert f"window.__TOTAL = {total:.3f}" in html


def test_tts_returns_pcm_and_waits_on_rate_limit(monkeypatch):
    import bayesly.video as video
    monkeypatch.setattr(video, "sleep", lambda s: None)
    pcm = b"\x02\x00" * 10
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE); w.writeframes(pcm)
    ok = {"steps": [{"type": "model_output", "content": [{"type": "audio", "data": base64.b64encode(buf.getvalue()).decode()}]}]}

    class Resp:
        def __init__(self, code, data=None):
            self.status_code, self._d, self.text = code, data or {}, "rate limited"
        def json(self):
            return self._d

    calls = iter([Resp(429), Resp(200, ok)])

    class S:
        def request(self, *a, **k):
            return next(calls)

    class Cfg:
        gemini_api_key, gemini_api_revision, gemini_timeout, http_max_retries = "k", "r", 60, 0

    assert tts("hello", Cfg(), session=S()) == pcm


def test_one_tts_call_is_cut_at_the_long_pauses():
    from bayesly.video import split_on_pauses
    tone = (b"\x00\x10" * (RATE // 2))            # 0.5 s loud
    gap = b"\x00\x00" * RATE                       # 1.0 s silence
    short = b"\x00\x00" * (RATE // 10)             # 0.1 s: a breath, not a cut
    pcm = tone + short + tone + gap + tone + gap + tone
    parts = split_on_pauses(pcm, 3, [10, 10, 10])
    assert len(parts) == 3
    assert abs(len(parts[0]) / 2 / RATE - 1.2) < 0.15        # first line keeps its inner breath
    assert all(len(p) > 0 for p in parts)


def test_split_falls_back_to_text_length_without_pauses():
    from bayesly.video import split_on_pauses
    pcm = b"\x00\x10" * RATE * 3
    parts = split_on_pauses(pcm, 3, [1, 1, 1])
    assert len(parts) == 3 and all(abs(len(p) / 2 / RATE - 1.0) < 0.1 for p in parts)
