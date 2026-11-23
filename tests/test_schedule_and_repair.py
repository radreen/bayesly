"""When to post (15-min check) and the one-shot length repair. No network."""

import json
from datetime import datetime, timedelta, timezone

from bayesly.models import Item, Source
from bayesly.pipeline import shorten_if_needed
from bayesly.schedule import Rules, decide

NOW = datetime(2026, 11, 20, 18, 0, tzinfo=timezone.utc)
SRC = Source("tc", "TechCrunch", "u", "press", 0.75)


def item(title, score, age_h, url=None):
    i = Item(SRC, title, url or title, "text", NOW - timedelta(hours=age_h))
    i.score = score
    return i


def test_hot_fresh_story_triggers_a_post():
    go, why = decide([item("OpenAI raises", 1.05, 0.5)], set(), 0, [NOW - timedelta(hours=2)], Rules(), NOW)
    assert go and why.startswith("hot")


def test_hot_news_only_waits_for_a_short_gap_and_the_hard_ceiling():
    hot = [item("OpenAI raises", 1.2, 0.2)]
    assert not decide(hot, set(), 0, [NOW - timedelta(minutes=10)], Rules(), NOW)[0]
    assert decide(hot, set(), 0, [NOW - timedelta(minutes=20)], Rules(), NOW)[0]
    eight = [NOW - timedelta(hours=h) for h in range(9, 1, -1)]
    assert decide(hot, set(), 0, eight, Rules(), NOW)[0]                     # routine cap doesn't stop it
    go, why = decide([item("Apple builds an agent", 0.85, 1)], set(), 0, eight, Rules(), NOW)
    assert not go and "daily cap" in why


def test_already_drafted_stories_do_not_count():
    hot = [item("OpenAI raises", 1.2, 0.2, url="k1")]
    assert not decide(hot, {"k1"}, 0, [NOW - timedelta(hours=2)], Rules(), NOW)[0]


def test_approved_post_goes_out_fast_and_quiet_spell_posts_decent_story():
    assert decide([], set(), 1, [NOW - timedelta(hours=2)], Rules(), NOW)[0]
    assert decide([], set(), 1, [NOW - timedelta(minutes=20)], Rules(), NOW)[0]       # owner tapped Publish: no 60-min wait
    assert not decide([], set(), 1, [NOW - timedelta(minutes=5)], Rules(), NOW)[0]    # but never back-to-back
    decent = [item("Apple builds an agent", 0.85, 6)]
    assert not decide(decent, set(), 0, [NOW - timedelta(hours=2)], Rules(), NOW)[0]
    assert decide(decent, set(), 0, [NOW - timedelta(hours=6)], Rules(), NOW)[0]


class Gen:
    model = "fake"

    def __init__(self, answer):
        self.answer, self.prompts = answer, []

    def generate(self, prompt):
        self.prompts.append(prompt)
        return self.answer


def test_overlong_texts_are_shortened_once_and_kept_honest():
    url = "https://example.com/a"
    draft = {"threads_post": "x" * 540 + " " + url, "x_post": "y" * 290, "instagram_caption": "ok"}
    gen = Gen(json.dumps({"threads_post": "short " + url, "x_post": "short x"}))
    out = shorten_if_needed(draft, gen, url)
    assert out["threads_post"] == "short " + url and out["x_post"] == "short x"
    assert "auto-shortened" in out["editor_notes"] and len(gen.prompts) == 1
    assert "instagram_caption" not in gen.prompts[0].split("\n\n")[-1]


def test_repair_that_is_still_too_long_is_ignored():
    draft = {"threads_post": "x" * 540, "x_post": "y" * 290}
    out = shorten_if_needed(draft, Gen(json.dumps({"threads_post": "t" * 600, "x_post": "z" * 300})), "u")
    assert out["threads_post"] == draft["threads_post"] and out["x_post"] == draft["x_post"]


def test_no_call_when_everything_fits():
    gen = Gen("{}")
    d = {"threads_post": "fine", "x_post": "fine"}
    assert shorten_if_needed(d, gen, "u") is d and gen.prompts == []


def test_breaking_news_skips_the_routine_cap_and_gap_but_not_the_ceiling():
    hot = [item("OpenAI sues Microsoft for $10B", 1.2, 0.5)]
    eight = [NOW - timedelta(hours=h) for h in (9, 8, 7, 6, 5, 4, 3, 1)]          # routine cap (8) full
    assert decide(hot, set(), 0, eight[:-1] + [NOW - timedelta(minutes=25)], Rules(), NOW)[0]
    assert not decide(hot, set(), 0, eight[:-1] + [NOW - timedelta(minutes=10)], Rules(), NOW)[0]   # 20-min gap
    assert not decide([item("Apple builds an agent", 0.85, 1)], set(), 0, eight, Rules(), NOW)[0]   # routine waits
    twelve = [NOW - timedelta(minutes=30 * k + 30) for k in range(12)]
    assert not decide(hot, set(), 0, twelve, Rules(), NOW)[0]                                       # hard ceiling


def test_platform_cap_lifts_for_breaking_news():
    from bayesly.schedule import is_breaking, platform_cap
    assert platform_cap("instagram", NOW, breaking=True) == 12
    assert is_breaking(1.1, NOW - timedelta(hours=2), NOW, Rules()) and not is_breaking(0.9, NOW, NOW, Rules())
    assert not is_breaking(1.1, NOW - timedelta(hours=5), NOW, Rules())
