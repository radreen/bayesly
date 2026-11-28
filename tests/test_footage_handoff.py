import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bayesly import footage_handoff as fh
from bayesly.publish.base import Ledger, PublishResult
from bayesly.source_video import Clip, SourceVideo


class FakeMedia:
    def __init__(self):
        self.files = {}

    def manifest(self, draft_id):
        raw = self.files.get(f"footage/{draft_id}/manifest.json")
        return json.loads(raw) if raw else None

    def put(self, path, data, message):
        self.files[path] = data


def test_requests_wait_then_time_out(tmp_path):
    reqs = fh.FootageRequests(tmp_path)
    reqs.ask("d1", "GTA 6 PC gameplay", "GTA story")
    assert reqs.waiting("d1") and reqs.age_minutes("d1") < 1
    data = reqs.load()
    data["d1"]["at"] = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
    reqs.save(data)
    assert not reqs.waiting("d1")


def test_followups_due_when_clips_arrive_or_time_is_up(tmp_path):
    led = Ledger(tmp_path)
    led.record("d1", PublishResult("threads", ok=True, post_id="t"))        # Threads already out
    media = FakeMedia()
    fh.FootageRequests(tmp_path).ask("d1", "q", "h")
    assert fh.followups_due(tmp_path, media, ["instagram", "x"]) == []      # still waiting
    media.put("footage/d1/manifest.json", json.dumps({"clips": [{"file": "clip0.mp4"}]}).encode(), "m")
    assert fh.followups_due(tmp_path, media, ["instagram", "x"]) == ["d1"]


def test_phone_pass_uploads_clips_and_manifest_once(tmp_path, monkeypatch):
    media = FakeMedia()
    now = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(fh, "open_requests", lambda repo: {"d1": {"query": "GTA 6 PC gameplay", "subject": "x", "at": now}})
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"v")
    monkeypatch.setattr("bayesly.source_video.find_footage",
                        lambda q, t, out, judge=None: SourceVideo([Clip(clip, "Nerpah", 9.0)]))
    monkeypatch.setattr("bayesly.video.normalise_clip", lambda src, out, s, d, fps=30: Path(out).write_bytes(b"small") or out)
    done = set()
    lines = fh.serve_once("r/b", media, None, tmp_path, done)
    assert lines == ["d1: 1 clip(s) for 'GTA 6 PC gameplay'"]
    assert media.files["footage/d1/clip0.mp4"] == b"small"
    assert media.manifest("d1")["clips"] == [{"file": "clip0.mp4", "credit": "Nerpah"}]
    assert fh.serve_once("r/b", media, None, tmp_path, done) == []          # never twice
