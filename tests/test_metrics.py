import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from bayesly import metrics
from bayesly.publish.base import load_publish_config


class FakeReader:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def ready(self, platform):
        return True

    def get(self, platform, path, **params):
        self.calls.append((platform, path))
        for key, val in self.routes.items():
            if path.endswith(key):
                return val() if callable(val) else val
        return {"data": []}


def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORMS", "threads,instagram")
    monkeypatch.setenv("THREADS_USER_ID", "tu")
    monkeypatch.setenv("INSTAGRAM_USER_ID", "iu")
    at = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(timespec="seconds")
    ledger = {"d1": {"threads": {"ok": True, "post_id": "T1", "url": "https://threads/p", "at": at},
                     "instagram": {"ok": True, "post_id": "I1", "url": "https://ig/p", "at": at}}}
    (tmp_path / "posted.json").write_text(json.dumps(ledger))
    q = tmp_path / "q" / "2026-11-21"
    q.mkdir(parents=True)
    (q / "d1-slug.json").write_text(json.dumps({"draft": {"headline": "Big chip news"}}))
    return SimpleNamespace(state_dir=tmp_path, out_dir=tmp_path / "q"), load_publish_config()


def test_comments_first_run_records_then_alerts_only_new(tmp_path, monkeypatch):
    settings, cfg = _setup(tmp_path, monkeypatch)
    replies = {"data": [{"id": "c1", "text": "nice", "username": "ann"}]}
    reader = FakeReader({"T1/replies": lambda: replies, "I1/comments": {"data": []},
                         "iu/media": {"data": [{"id": "I1", "comments_count": 0}]}})

    def next_hour():
        st = json.loads((tmp_path / "comments.json").read_text())
        st.pop("last_poll", None)
        (tmp_path / "comments.json").write_text(json.dumps(st))
    sent = []
    metrics.poll_comments(settings, cfg, lambda t, b: sent.append((t, b)), reader=reader)
    assert sent == []                                  # first run: no flood of old comments
    replies["data"] += [{"id": "c2", "text": "<b>is it open?</b>", "username": "bob"},
                        {"id": "c3", "text": "thanks", "username": "bayesly.ai"}]   # our own reply: ignored
    assert metrics.poll_comments(settings, cfg, lambda t, b: sent.append((t, b)), reader=reader) == []   # < 1 h: skipped
    next_hour()
    metrics.poll_comments(settings, cfg, lambda t, b: sent.append((t, b)), reader=reader)
    assert len(sent) == 1
    text, buttons = sent[0]
    assert "@bob" in text and "&lt;b&gt;" in text and "Big chip news" in text
    assert buttons == [[("Open post", "https://threads/p")]]
    assert not any(path.endswith("I1/comments") for _, path in reader.calls)   # IG count unchanged: no fetch
    next_hour()
    metrics.poll_comments(settings, cfg, lambda t, b: sent.append((t, b)), reader=reader)
    assert len(sent) == 1                              # never twice


def test_morning_summary_once_a_day_with_deltas(tmp_path, monkeypatch):
    settings, cfg = _setup(tmp_path, monkeypatch)
    reader = FakeReader({
        "tu/threads_insights": {"data": [{"name": "followers_count", "total_value": {"value": 12}}]},
        "T1/insights": {"data": [{"name": "views", "values": [{"value": 340}]},
                                 {"name": "likes", "values": [{"value": 9}]}]},
        "iu": {"followers_count": 5},
        "I1": {"like_count": 4, "comments_count": 1},
    })
    day = datetime.now(timezone.utc).replace(hour=7)
    (tmp_path / "metrics.json").write_text(json.dumps({"days": {(day.date() - timedelta(days=1)).isoformat(): {
        "followers": {"threads": 10}, "posts": {"threads:T1": {"views": 300}}}}}))
    sent = []
    out = metrics.morning_summary(settings, cfg, lambda t, b: sent.append((t, b)), reader=reader, now=day)
    assert out and len(sent) == 1
    text = sent[0][0]
    assert "followers 12 (+2)" in text and "views 340 (+40)" in text and "Posts in the last 24 h: <b>1</b>" in text
    assert "Big chip news" in text and sent[0][1] == [[("Open top post", "https://threads/p")]]
    assert metrics.morning_summary(settings, cfg, lambda t, b: sent.append((t, b)), reader=reader, now=day) is None
    early = day.replace(hour=3) + timedelta(days=1)
    assert metrics.morning_summary(settings, cfg, lambda t, b: sent.append((t, b)), reader=reader, now=early) is None
