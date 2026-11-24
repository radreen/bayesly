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
                  "confidence": "medium", "insufficient_source": False, "facts": ["F"], "question": "Q?"},
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

    def send(self, text, buttons=None, html=False):
        self.sent.append((text, buttons))
        return 1

    def send_photo(self, path, caption="", buttons=None, html=False):
        self.sent.append((caption, buttons))
        return 2

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
    assert tg.sent[0][1][0] == [("✅ Publish", "pub:abc"), ("✖️ Skip", "skip:abc")]
    assert len(tg.sent) == 1          # one message per held post
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
        refresh_token("threads", "OLD", session=bad)
    assert "OLD" not in str(exc.value)


def test_cli_imports_and_status_runs(env, capsys):
    from bayesly.cli import main

    assert main(["status"]) == 0
    assert "paused:" in capsys.readouterr().out


def test_threads_waits_before_publish_and_retries_media_not_found():
    publish_calls = iter([
        FakeResp({"error": {"code": 24, "error_subcode": 4279009, "error_user_msg": "Media Not Found"}}, status=400),
        FakeResp({"id": "post1"}),
    ])
    s = FakeSession([
        (("POST", "/threads_publish"), lambda kw: next(publish_calls)),
        (("POST", "/threads"), FakeResp({"id": "c1"})),
        (("GET", "post1"), FakeResp({"permalink": "https://threads.com/p/1"})),
        (("GET", "/"), FakeResp({"status": "FINISHED"})),
    ])
    slept = []
    one = replace(POST, image_urls=["https://img/1.jpg"])
    r = ThreadsPublisher("u1", "t", live=True, session=s, sleep=slept.append).publish(one)
    assert r.ok and r.post_id == "post1"
    assert 30 in slept          # docs: ~30 s between container and publish
    assert len([c for c in s.calls if c[1].endswith("/threads_publish")]) == 2


# -- telegram message format ----------------------------------------------------------------
def test_held_message_is_short_with_buttons_and_folded_text():
    from bayesly.notify import held_message, visible_len
    rec = good_record()
    rec["draft"]["threads_post"] = "x" * 2000
    rec["draft"]["card"] = {"category": "Models"}
    text, buttons = held_message(rec, ["lint: Threads post is 591 chars (> 500).", "confidence is 'low'"])
    assert visible_len(text) <= 1024
    assert "Threads text too long (591/500)" in text and "low confidence" in text
    assert "<blockquote expandable>" in text
    assert buttons[0] == [("✅ Publish", "pub:abc"), ("✖️ Skip", "skip:abc")]
    assert buttons[1][0][1].startswith("https://")


def test_published_message_links_and_failures():
    from bayesly.notify import published_message
    rec = good_record()
    results = [PublishResult("threads", ok=True, url="https://threads.com/p/1"),
               PublishResult("x", ok=False, error='HTTP 400: {"error":{"message":"Bad media"}}')]
    text, buttons = published_message(rec, results)
    assert "Partly published" in text and "X: Bad media" in text
    assert buttons == [[("Threads", "https://threads.com/p/1")]]
    assert published_message(rec, [PublishResult("x", ok=True, dry_run=True)]) is None


def test_telegram_url_buttons_are_links():
    from bayesly.telegram import Telegram
    m = Telegram._markup([[("Go", "https://a.b"), ("Ok", "pub:1")]])
    assert m == {"inline_keyboard": [[{"text": "Go", "url": "https://a.b"}, {"text": "Ok", "callback_data": "pub:1"}]]}


# -- video --------------------------------------------------------------------------------------
def test_instagram_posts_reel_when_video_given():
    s = FakeSession([
        (("POST", "/media_publish"), FakeResp({"id": "reel1"})),
        (("POST", "/media"), FakeResp({"id": "c1"})),
        (("GET", "reel1"), FakeResp({"permalink": "https://instagram.com/reel/1"})),
        (("GET", "/"), FakeResp({"status_code": "FINISHED"})),
    ])
    post = replace(POST, video_url="https://cdn.jsdelivr.net/gh/a/b@sha/v.mp4")
    r = InstagramPublisher("u1", "t", live=True, session=s, sleep=lambda _: None, use_video=True).publish(post)
    assert r.ok and r.detail == "video"
    params = [kw["params"] for m, u, kw in s.calls if m == "POST" and u.endswith("/u1/media")][0]
    assert params["media_type"] == "REELS" and params["video_url"].endswith("v.mp4")


def test_video_error_falls_back_to_carousel_same_run():
    ids = iter(["vid", "c1", "c2", "parent"])
    def status(kw):
        return FakeResp({"status": "ERROR"}) if False else FakeResp({"status": "FINISHED"})
    s = FakeSession([
        (("POST", "/threads_publish"), lambda kw: FakeResp({"id": "p9"}) if kw["params"]["creation_id"] == "parent"
         else FakeResp({"error": {"message": "bad video"}}, status=400)),
        (("POST", "/threads"), lambda kw: FakeResp({"id": next(ids)})),
        (("GET", "p9"), FakeResp({"permalink": "https://threads.com/p/9"})),
        (("GET", "/"), status),
    ])
    post = replace(POST, video_url="https://cdn/v.mp4")
    r = ThreadsPublisher("u1", "t", live=True, session=s, sleep=lambda _: None, use_video=True).publish(post)
    assert r.ok and r.post_id == "p9" and r.detail.startswith("video failed, posted images")


def test_x_uploads_video_in_chunks(tmp_path):
    pytest.importorskip("requests_oauthlib")
    mp4 = tmp_path / "v.x.mp4"
    mp4.write_bytes(b"0" * (5 * 1024 * 1024))           # 5 MB -> 2 chunks of <= 4 MB
    s = FakeSession([
        (("POST", "/media/upload/initialize"), FakeResp({"data": {"id": "m7"}})),
        (("POST", "/media/upload/m7/append"), FakeResp({})),
        (("POST", "/media/upload/m7/finalize"), FakeResp({"data": {"processing_info": {"state": "pending", "check_after_secs": 1}}})),
        (("GET", "/media/upload"), FakeResp({"data": {"processing_info": {"state": "succeeded"}}})),
        (("POST", "/2/tweets"), FakeResp({"data": {"id": "t9"}})),
    ])
    post = replace(POST, video_path=str(mp4))
    r = XPublisher("a", "b", "c", "d", live=True, session=s, use_video=True, sleep=lambda _: None).publish(post)
    assert r.ok and r.detail == "video"
    appends = [kw for m, u, kw in s.calls if u.endswith("/append")]
    assert [a["data"]["segment_index"] for a in appends] == ["0", "1"]
    body = [kw["json"] for m, u, kw in s.calls if u.endswith("/2/tweets")][0]
    assert body["media"] == {"media_ids": ["m7"]}


def test_autopost_hands_video_to_video_platforms(env, monkeypatch):
    settings, day, jpg = env
    monkeypatch.setenv("VIDEO_PLATFORMS", "x")
    mp4 = day / "abc.mp4"; mp4.write_bytes(b"v")
    xmp4 = day / "abc.x.mp4"; xmp4.write_bytes(b"v")
    write(day, good_record(images_jpg=[str(jpg)], video=str(mp4), video_x=str(xmp4)))
    pubs = {"threads": SpyPublisher("threads"), "x": SpyPublisher("x")}
    poster = Autoposter(settings, load_publish_config(), pubs, media_upload=lambda p, d: f"https://img/{d}",
                        telegram=SpyTelegram(), live=True, render_video=lambda p: None,
                        video_upload=lambda p, d: "https://cdn/" + d)
    poster.run(1)
    assert pubs["x"].posts[0].video_path == str(xmp4)
    assert pubs["x"].posts[0].video_url == ""            # X uploads the file; no hosting needed for it alone


def test_threads_topic_tag_on_the_post_container_only():
    from bayesly.publish.meta import clean_topic
    assert clean_topic("A.I. & robots") == "A I and robots" and len(clean_topic("x" * 80)) == 50
    ids = iter(["c1", "c2", "parent"])
    s = FakeSession([
        (("POST", "/threads_publish"), FakeResp({"id": "p1"})),
        (("POST", "/threads"), lambda kw: FakeResp({"id": next(ids)})),
        (("GET", "p1"), FakeResp({"permalink": "u"})),
        (("GET", "/"), FakeResp({"status": "FINISHED"})),
    ])
    ThreadsPublisher("u1", "t", live=True, session=s, sleep=lambda _: None).publish(replace(POST, topic="OpenAI"))
    containers = [kw["params"] for m, u, kw in s.calls if m == "POST" and u.endswith("/u1/threads")]
    assert "topic_tag" not in containers[0] and "topic_tag" not in containers[1]   # carousel items
    assert containers[2]["topic_tag"] == "OpenAI"


def test_old_drafts_are_relinted_and_stale_ones_skipped():
    from bayesly.autopost import gate, is_fresh, is_outdated_format
    rec = {"source": {"reliability": 0.9, "url": "u"}, "images_jpg": ["a.jpg"], "lint_warnings": [],
           "draft": {"threads_post": "T", "x_post": "X", "confidence": "medium", "facts": ["F"], "question": "Q?",
                     "instagram_caption": "Hook.\n\nBefore: old\nConfidence: Medium — vendor claim\nOur take: wait."}}
    reasons = gate(rec, ["instagram"])                       # stored lint was empty; today's rules catch it
    assert any("Before/Now" in r for r in reasons)
    assert is_outdated_format({"draft": {"threads_post": "T"}}) and not is_outdated_format(rec)
    old = (datetime.now(timezone.utc) - timedelta(hours=9)).isoformat()
    assert not is_fresh({"created_at": old}) and is_fresh({"created_at": datetime.now(timezone.utc).isoformat()})


def test_meta_daily_cap_ramps_and_x_keeps_posting(env, monkeypatch):
    from bayesly.schedule import platform_cap
    monkeypatch.setenv("META_RAMP_FROM", "2026-11-23")
    day0 = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)
    assert platform_cap("instagram", day0) == 4 and platform_cap("instagram", day0 + timedelta(days=4)) == 5
    assert platform_cap("threads", day0 + timedelta(days=60)) == 8 and platform_cap("x", day0) == 8
    monkeypatch.setenv("MAX_POSTS_PER_DAY_INSTAGRAM", "2")
    assert platform_cap("instagram", day0) == 2

    settings, day, jpg = env
    monkeypatch.setenv("MAX_POSTS_PER_DAY_THREADS", "0")       # Threads full for today
    write(day, good_record(images_jpg=[str(jpg)]))
    poster, pubs, _ = make(settings)
    poster.run(1)
    assert len(pubs["threads"].posts) == 0 and len(pubs["x"].posts) == 1
    assert poster.ledger.entry("abc", "threads")["skipped"] == "daily cap 0"
    monkeypatch.setenv("MAX_POSTS_PER_DAY_THREADS", "5")
    poster.run(1)
    assert len(pubs["threads"].posts) == 0                         # stale tomorrow: never posted late


def test_meta_never_posts_the_same_text_twice():
    s = FakeSession([
        (("GET", "/u1/threads"), FakeResp({"data": [{"id": "p9", "text": POST.texts["threads"] + " ",
                                                      "permalink": "https://threads/p9"}]})),
    ])
    r = ThreadsPublisher("u1", "t", live=True, session=s, sleep=lambda _: None).publish(POST)
    assert r.ok and r.post_id == "p9" and "already published" in r.detail
    assert not [c for c in s.calls if c[0] == "POST"]


def test_instagram_story_container_and_publish():
    s = FakeSession([
        (("POST", "/media_publish"), FakeResp({"id": "st1"})),
        (("POST", "/u1/media"), FakeResp({"id": "c1"})),
        (("GET", "/"), FakeResp({"status_code": "FINISHED"})),
    ])
    r = InstagramPublisher("u1", "t", live=True, session=s, sleep=lambda _: None).publish_story(video_url="https://v/x.mp4")
    assert r.ok and r.post_id == "st1" and r.platform == "instagram_story"
    create = [kw["params"] for m, u, kw in s.calls if m == "POST" and u.endswith("/u1/media")][0]
    assert create["media_type"] == "STORIES" and create["video_url"] == "https://v/x.mp4"


def test_story_log_caps_and_digest_once(tmp_path, monkeypatch):
    from bayesly import stories
    monkeypatch.setenv("STORIES_PER_DAY", "2")
    now = datetime(2026, 11, 24, 18, tzinfo=timezone.utc)
    log = stories.StoryLog(tmp_path)
    assert stories.digest_due(log, now, posts_today=3) and not stories.digest_due(log, now.replace(hour=12), 3)
    assert not stories.digest_due(log, now, posts_today=1)
    log.note(now, draft_id="a")
    log.note(now, digest=True)
    assert not log.room(now) and not stories.digest_due(log, now, 3)
    assert log.room(now + timedelta(days=1))                                  # new day, new budget
