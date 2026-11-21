import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from bayesly import weekly
from bayesly.autopost import gate
from bayesly.review import DraftStore

SUNDAY = datetime(2026, 11, 22, 15, tzinfo=timezone.utc)


def _week(tmp_path, n):
    q = tmp_path / "q" / "2026-11-20"
    q.mkdir(parents=True)
    ledger = {}
    for i in range(n):
        (q / f"d{i}-x.json").write_text(json.dumps({"source": {"id": "s", "name": "S"},
                                                     "draft": {"headline": f"Story {i}", "facts": [f"fact {i}"]}}))
        ledger[f"d{i}"] = {"threads": {"ok": True, "at": (SUNDAY - timedelta(days=2, hours=i)).isoformat()}}
    (tmp_path / "posted.json").write_text(json.dumps(ledger))
    return SimpleNamespace(state_dir=tmp_path, out_dir=tmp_path / "q")


def test_due_only_on_sunday_afternoon_once_a_week(tmp_path):
    assert weekly.due(tmp_path, SUNDAY) and not weekly.due(tmp_path, SUNDAY.replace(hour=9))
    assert not weekly.due(tmp_path, SUNDAY - timedelta(days=1))
    (tmp_path / "weekly.json").write_text(json.dumps({"done": "2026-W47"}))
    assert not weekly.due(tmp_path, SUNDAY)


def test_weekly_draft_uses_only_our_facts_and_is_always_held(tmp_path):
    settings = _week(tmp_path, 4)
    prompts = []
    draft = {"headline": "AI this week in 5 facts", "threads_post": "T", "x_post": "X", "instagram_caption": "I",
             "question": "Q?", "facts": ["a"], "confidence": "high", "insufficient_source": False}
    gen = lambda p: (prompts.append(p), json.dumps(draft))[1]
    path = weekly.make_weekly(settings, gen, DraftStore(settings.out_dir, tmp_path), now=SUNDAY)
    assert path and "fact 3" in prompts[0] and "Story 0" in prompts[0]
    rec = json.loads(path.read_text(encoding="utf-8"))
    rec["images_jpg"] = ["a.jpg"]
    assert any("weekly recap" in r for r in gate(rec, ["threads"]))
    assert not weekly.due(tmp_path, SUNDAY)


def test_too_few_stories_skips_the_week(tmp_path):
    settings = _week(tmp_path, 2)
    assert weekly.make_weekly(settings, lambda p: "{}", DraftStore(settings.out_dir, tmp_path), now=SUNDAY) is None
    assert not weekly.due(tmp_path, SUNDAY)
