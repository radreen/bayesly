"""Publishing tests. Every HTTP call goes to a fake session; nothing reaches a real API."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bayesly.autopost import Autoposter, gate, is_paused, set_paused
from bayesly.config import load_settings
from bayesly.publish.base import Ledger, PublishPost, PublishResult, load_publish_config
from bayesly.publish.media import GitHubMediaHost
from bayesly.publish.meta import InstagramPublisher, ThreadsPublisher
from bayesly.publish.x import XPublisher
from bayesly.telegram import Telegram


class FakeResp:
    def __init__(self, data=None, status=200, content=b""):
        self.status_code, self._data, self.content = status, data or {}, content
        self.text = json.dumps(self._data)

    def json(self):
        return self._data


class FakeSession:
    """Answers by (method, url-substring) rules, records every call."""

    def __init__(self, rules):
        self.rules, self.calls = rules, []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        for (m, frag), resp in self.rules:
            if m == method and frag in url:
                return resp(kwargs) if callable(resp) else resp
        raise AssertionError(f"unexpected call {method} {url}")


POST = PublishPost(
    draft_id="abc",
    texts={"threads": "Fact. https://example.com/a", "instagram": "Fact. Source: Lab", "x": "Fact. Source: Lab"},
    image_urls=["https://img/1.jpg", "https://img/2.jpg"],
)


# -- adapters ----------------------------------------------------------------------
def test_dry_run_makes_no_http_calls():
    s = FakeSession([])
    for pub in (ThreadsPublisher("u", "t", live=False, session=s), InstagramPublisher("u", "t", live=False, session=s),
                XPublisher("a", "b", "c", "d", live=False, session=s)):
        r = pub.publish(POST)
        assert r.ok and r.dry_run
    assert s.calls == []


def test_threads_carousel_flow_and_token_scrubbed():
    ids = iter(["c1", "c2", "parent"])
    s = FakeSession([
        (("POST", "/threads_publish"), FakeResp({"id": "post9"})),
        (("POST", "/threads"), lambda kw: FakeResp({"id": next(ids)})),
        (("GET", "post9"), FakeResp({"permalink": "https://threads.net/p/1"})),
        (("GET", "/"), FakeResp({"status": "FINISHED"})),
    ])
    r = ThreadsPublisher("u1", "SECRET", live=True, session=s, sleep=lambda _: None).publish(POST)
    assert r.ok and r.post_id == "post9" and r.url == "https://threads.net/p/1"
    containers = [kw["params"] for m, u, kw in s.calls if m == "POST" and u.endswith("/u1/threads")]
    assert [c.get("is_carousel_item") for c in containers[:2]] == ["true", "true"]
    assert containers[2]["media_type"] == "CAROUSEL" and containers[2]["children"] == "c1,c2"
    assert containers[2]["text"] == POST.texts["threads"]


def test_meta_error_is_reported_without_token():
    s = FakeSession([(("POST", "/media"), FakeResp({"error": "bad SECRET"}, status=400))])
    r = InstagramPublisher("u1", "SECRET", live=True, session=s, sleep=lambda _: None).publish(POST)
    assert not r.ok and "SECRET" not in r.error and "***" in r.error


def test_instagram_container_error_status_fails():
    s = FakeSession([
        (("POST", "/media"), FakeResp({"id": "c1"})),
        (("GET", "/"), FakeResp({"status_code": "ERROR"})),
    ])
    one = replace(POST, image_urls=["https://img/1.jpg"])
    r = InstagramPublisher("u1", "t", live=True, session=s, sleep=lambda _: None).publish(one)
    assert not r.ok and "ERROR" in r.error


def test_x_refuses_links_even_live():
    s = FakeSession([])
    bad = replace(POST, texts={"x": "Read more at example.com"})
    r = XPublisher("a", "b", "c", "d", live=True, session=s).publish(bad)
    assert not r.ok and "link" in r.error and s.calls == []


def test_x_live_posts_with_media(monkeypatch):
    pytest.importorskip("requests_oauthlib")
    s = FakeSession([
        (("GET", "img/1.jpg"), FakeResp(content=b"jpg")),
        (("POST", "media/upload"), FakeResp({"data": {"id": "m1"}})),
        (("POST", "/2/tweets"), FakeResp({"data": {"id": "t1"}})),
    ])
    r = XPublisher("a", "b", "c", "d", live=True, session=s, handle="bayesly_ai").publish(POST)
    assert r.ok and r.url == "https://x.com/bayesly_ai/status/t1"
    body = [kw["json"] for m, u, kw in s.calls if u.endswith("/2/tweets")][0]
    assert body == {"text": POST.texts["x"], "media": {"media_ids": ["m1"]}}


def test_media_host_reuses_existing_file(tmp_path):
    f = tmp_path / "a.jpg"
    f.write_bytes(b"x")
    s = FakeSession([(("PUT", "/contents/"), FakeResp({"message": "exists"}, status=422))])
    url = GitHubMediaHost("me/media", "tok", live=True, session=s).upload(f, "2026-11-19/abc/01.jpg")
    assert url == "https://raw.githubusercontent.com/me/media/main/2026-11-19/abc/01.jpg"


# -- ledger -------------------------------------------------------------------------
def test_ledger_ignores_dry_run_and_gives_up_after_three_failures(tmp_path):
    led = Ledger(tmp_path)
    led.record("d", PublishResult("x", ok=True, dry_run=True))
    assert not led.touched("d")
    for _ in range(2):
        led.record("d", PublishResult("x", ok=False, error="boom"))
    assert not led.done("d", "x")
    led.record("d", PublishResult("x", ok=False, error="boom"))
    assert led.done("d", "x")


# -- gate ---------------------------------------------------------------------------
def good_record(**over):
    rec = {
        "id": "abc", "created_at": datetime.now(timezone.utc).isoformat(), "score": 0.8,
        "source": {"reliability": 0.9, "url": "https://example.com/a"},
        "draft": {"headline": "H", "threads_post": "T", "x_post": "X", "instagram_caption": "I",
                  "confidence": "medium", "insufficient_source": False},
        "lint_warnings": [], "image_warnings": [], "images_jpg": ["a.jpg"],
    }
    rec.update(over)
    return rec


def test_gate_passes_clean_record():
    assert gate(good_record(), ["threads", "instagram", "x"]) == []


@pytest.mark.parametrize("over, needle", [
    ({"lint_warnings": ["Threads post is 591 chars (> 500)."]}, "591"),
    ({"image_warnings": ["Brand fonts did not load"]}, "fonts"),
    ({"images_jpg": []}, "no rendered images"),
    ({"source": {"reliability": 0.5}}, "reliability"),
])
def test_gate_holds_doubtful_records(over, needle):
    assert any(needle in r for r in gate(good_record(**over), ["threads"]))


def test_gate_holds_low_confidence_and_ignores_cosmetic_trim():
    rec = good_record(image_warnings=["card.now trimmed from 95 to 90 chars."])
    rec["draft"]["confidence"] = "low"
    assert gate(rec, ["threads"]) == ["confidence is 'low'"]


# -- autoposter -----------------------------------------------------------------------
class SpyPublisher:
    def __init__(self, name, ok=True):
        self.name, self.ok, self.posts = name, ok, []

    def publish(self, post):
        self.posts.append(post)
        return PublishResult(self.name, ok=self.ok, post_id="p1" if self.ok else None, error=None if self.ok else "x")


class SpyTelegram:
    enabled = True

    def __init__(self):
        self.sent = []

    def send(self, text, buttons=None):
        self.sent.append((text, buttons))
        return 1

    def send_photo(self, path, caption=""):
        pass

    def updates(self, offset):
        return []


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("OUT_DIR", str(tmp_path / "queue"))
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("PLATFORMS", "threads,x")
    monkeypatch.delenv("BAYESLY_PAUSED", raising=False)
    settings = load_settings()
    day = settings.out_dir / "2026-11-19"
    day.mkdir(parents=True)
    jpg = day / "abc-01.jpg"
    jpg.write_bytes(b"jpg")
    return settings, day, jpg


def write(day, rec):
    path = day / f"{rec['id']}.json"
    path.write_text(json.dumps(rec), encoding="utf-8")
    return path


def make(settings, live=True, threads_ok=True):
    pubs = {"threads": SpyPublisher("threads", threads_ok), "x": SpyPublisher("x")}
    tg = SpyTelegram()
    poster = Autoposter(settings, load_publish_config(), pubs, media_upload=lambda p, d: f"https://img/{d}",
                        telegram=tg, live=live)
    return poster, pubs, tg


def test_autopost_publishes_once_then_is_idempotent(env):
    settings, day, jpg = env
    rec = write(day, good_record(images_jpg=[str(jpg)]))
    poster, pubs, _ = make(settings)
    poster.run(1)
    day_str = json.loads(rec.read_text())["created_at"][:10]
    assert len(pubs["threads"].posts) == 1 and pubs["threads"].posts[0].image_urls == [f"https://img/{day_str}/abc/01.jpg"]
    poster.run(1)
    assert len(pubs["threads"].posts) == 1 and len(pubs["x"].posts) == 1


def test_autopost_retries_only_the_failed_platform(env):
    settings, day, jpg = env
    write(day, good_record(images_jpg=[str(jpg)]))
    poster, pubs, _ = make(settings, threads_ok=False)
    poster.run(1)
    poster.run(1)
    assert len(pubs["threads"].posts) == 2 and len(pubs["x"].posts) == 1


def test_held_post_goes_to_telegram_with_buttons_not_to_platforms(env):
    settings, day, jpg = env
    rec = good_record(images_jpg=[str(jpg)])
    rec["draft"]["confidence"] = "low"
    write(day, rec)
    poster, pubs, tg = make(settings)
    report = poster.run(1)
    assert report.held and not pubs["threads"].posts
    assert tg.sent[0][1] == [[("Publish", "pub:abc"), ("Skip", "skip:abc")]]
    # owner taps Publish -> next run publishes it despite the gate
    poster.held.set("abc", status="approved")
    poster.run(1)
    assert len(pubs["threads"].posts) == 1


def test_stale_drafts_are_never_auto_published(env):
    settings, day, jpg = env
    old = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
    write(day, good_record(images_jpg=[str(jpg)], created_at=old))
    poster, pubs, _ = make(settings)
    poster.run(1)
    assert not pubs["threads"].posts


def test_kill_switch_stops_everything(env, monkeypatch):
    settings, day, jpg = env
    write(day, good_record(images_jpg=[str(jpg)]))
    set_paused(settings, True)
    poster, pubs, _ = make(settings)
    assert poster.run(1).paused and not pubs["threads"].posts
    set_paused(settings, False)
    monkeypatch.setenv("BAYESLY_PAUSED", "1")
    assert is_paused(settings, load_publish_config())


def test_dry_run_writes_no_state(env):
    settings, day, jpg = env
    rec = good_record(images_jpg=[str(jpg)])
    rec["draft"]["confidence"] = "low"
    write(day, rec)
    poster, _, tg = make(settings, live=False)
    poster.run(1)
    assert tg.sent == [] and not (settings.state_dir / "held.json").exists()


# -- telegram -----------------------------------------------------------------------------
def test_telegram_ignores_strangers_and_applies_owner_taps(env):
    settings, day, jpg = env
    rec = good_record(images_jpg=[str(jpg)])
    write(day, rec)
    poster, _, _ = make(settings)
    poster.held.set("abc", status="pending")
    updates = [
        {"update_id": 5, "callback_query": {"id": "q1", "data": "skip:abc", "message": {"chat": {"id": 999}, "message_id": 3}}},
        {"update_id": 6, "callback_query": {"id": "q2", "data": "pub:abc", "message": {"chat": {"id": 42}, "message_id": 3}}},
        {"update_id": 7, "message": {"chat": {"id": 999}, "text": "/pause"}},
    ]
    s = FakeSession([
        (("POST", "/getUpdates"), FakeResp({"ok": True, "result": updates})),
        (("POST", "/"), FakeResp({"ok": True, "result": {}})),
    ])
    poster.telegram = Telegram("TOKEN", "42", session=s)
    poster.sync_telegram()
    assert poster.held.load()["abc"]["status"] == "approved"
    assert not (settings.state_dir / "PAUSED").exists()
    assert json.loads((settings.state_dir / "telegram.json").read_text())["offset"] == 8


def test_token_refresh_returns_new_token_and_scrubs_errors():
    from bayesly.publish.base import PublishError
    from bayesly.publish.meta import refresh_token

    s = FakeSession([(("GET", "graph.threads.com/refresh_access_token"),
                      FakeResp({"access_token": "NEW", "expires_in": 5183944}))])
    assert refresh_token("threads", "OLD", session=s) == ("NEW", 5183944)
    assert s.calls[0][2]["params"]["grant_type"] == "th_refresh_token"
    bad = FakeSession([(("GET", "refresh_access_token"), FakeResp({"error": "OLD expired"}, status=400))])
    with pytest.raises(PublishError) as exc:
        refresh_token("instagram", "OLD", session=bad)
    assert "OLD" not in str(exc.value)
