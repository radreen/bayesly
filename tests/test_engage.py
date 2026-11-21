import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from bayesly import engage, metrics
from bayesly.models import Item, Source


def _item(title, hours=1, score=0.9):
    src = Source(id="s", name="S", url="u", kind="press", reliability=0.8)
    return Item(source=src, title=title, url=f"https://ex.com/{abs(hash(title))}", text="Body text with $20 price.",
                published=datetime.now(timezone.utc) - timedelta(hours=hours), score=score)


def test_company_matching_is_word_based():
    assert engage.company_of("OpenAI ships GPT-6") == "OpenAI"
    assert engage.company_of("Metaverse fades") == ""            # not "Meta"
    assert engage.company_of("Claude gets memory") == "Anthropic"


def test_comment_ideas_pick_fresh_big_company_stories_once(tmp_path):
    settings = SimpleNamespace(state_dir=tmp_path)
    ranked = [_item("Nvidia unveils new chip"), _item("Old OpenAI story", hours=5), _item("Small startup raises"),
              _item("Anthropic adds memory", score=0.5), _item("Google launches Gemini 4")]
    prompts, sent = [], []
    gen = lambda p: (prompts.append(p), json.dumps([{"i": 0, "replies": ["<b>a</b>", "b?"]},
                                                     {"i": 1, "replies": ["c"]}]))[1]
    out = engage.comment_ideas(settings, ranked, gen, lambda t, b: sent.append((t, b)), key=lambda i: i.url)
    assert len(out) == 2 and len(sent) == 2 and len(prompts) == 1
    assert "Nvidia unveils" in prompts[0] and "Old OpenAI" not in prompts[0] and "Anthropic adds" not in prompts[0]
    assert "<code>&lt;b&gt;a&lt;/b&gt;</code>" in sent[0][0]
    assert sent[0][1][0][0][1].startswith("https://www.threads.com/search?q=Nvidia")
    engage.comment_ideas(settings, ranked, gen, lambda t, b: sent.append((t, b)), key=lambda i: i.url)
    assert len(sent) == 2                                          # never the same story twice


def test_daily_cap(tmp_path):
    settings = SimpleNamespace(state_dir=tmp_path)
    (tmp_path / "engage.json").write_text(json.dumps({"day": datetime.now(timezone.utc).date().isoformat(),
                                                      "sent_today": engage.PER_DAY}))
    called = []
    assert engage.comment_ideas(settings, [_item("OpenAI x")], lambda p: called.append(p) or "[]",
                                lambda t, b: None, key=lambda i: i.url) == [] and called == []


def test_comment_alert_carries_a_reply_draft():
    msg = metrics.comment_message("threads", {"username": "ann", "text": "is it open?"}, "H", "Not yet <soon>")
    assert "<code>Not yet &lt;soon&gt;</code>" in msg
