from datetime import timedelta

from dataclasses import replace

from bayesly.score import is_rumour, novelty, rank, release_kind, score_item, select

from conftest import NOW


def _as_release(item):
    item.source = replace(item.source, kind="release_notes", reliability=1.0)
    return item


def test_release_kind_classifies_versions(make_item):
    assert release_kind(_as_release(make_item(title="v0.40.15"))) == "patch"
    assert release_kind(_as_release(make_item(title="v5.22.0: Mistral Large 4"))) == "minor"
    assert release_kind(_as_release(make_item(title="Release 6.0"))) == "minor"
    assert release_kind(_as_release(make_item(title="v0.41.0-rc1"))) == "prerelease"
    assert release_kind(make_item(title="v0.40.15")) is None  # not a release_notes source


def test_patch_release_ranks_below_minor_release_and_prerelease_is_dropped(make_item):
    patch = _as_release(make_item(title="v0.40.15", url="https://gh.com/p"))
    minor = _as_release(make_item(title="v0.41.0", url="https://gh.com/m"))
    rc = _as_release(make_item(title="v0.42.0-rc1", url="https://gh.com/rc"))
    assert [i.url for i in rank([patch, minor, rc], now=NOW)] == ["https://gh.com/m", "https://gh.com/p"]
    assert "patch_release_penalty" in patch.score_breakdown


def test_howto_posts_rank_below_announcements(make_item):
    howto = make_item(title="Getting started with agents on Amazon Bedrock", url="https://a.com/h")
    news = make_item(title="Company releases open-weights reasoning model", url="https://a.com/n")
    ranked = rank([howto, news], now=NOW)
    assert [i.url for i in ranked] == ["https://a.com/n", "https://a.com/h"]
    assert "howto_penalty" in howto.score_breakdown
    assert "news_signal_bonus" in news.score_breakdown
    case_study = make_item(title="How NVIDIA Uses AI Agents to Streamline Operations")
    score_item(case_study, NOW)
    assert "howto_penalty" in case_study.score_breakdown


def test_select_caps_items_per_source(make_item):
    items = [make_item(url=f"https://a.com/{n}", source_id="aws") for n in range(4)]
    items.append(make_item(url="https://b.com/1", source_id="openai"))
    picked = select(items, limit=3, max_per_source=2)
    assert [i.source.id for i in picked] == ["aws", "aws", "openai"]


def test_rumours_are_dropped(make_item):
    rumour = make_item(title="GPT-6 reportedly coming next month", url="https://a.com/r")
    fact = make_item(title="Model API released", url="https://a.com/f")
    assert is_rumour(rumour)
    assert [i.url for i in rank([rumour, fact], now=NOW)] == ["https://a.com/f"]


def test_novelty_decays_with_age(make_item):
    assert novelty(make_item(published=NOW), NOW) == 1.0
    assert 0 < novelty(make_item(published=NOW - timedelta(days=3)), NOW) < 1
    assert novelty(make_item(published=NOW - timedelta(days=10)), NOW) == 0.0
    assert novelty(make_item(published=None), NOW) == 0.3


def test_reliable_relevant_fresh_item_ranks_first(make_item):
    good = make_item(url="https://a.com/good", reliability=1.0)
    weak = make_item(
        title="Company picnic photos",
        text="We had a nice picnic with the team and some cake. " * 10,
        url="https://a.com/weak",
        reliability=0.6,
        published=NOW - timedelta(days=6),
    )
    assert [i.url for i in rank([weak, good], now=NOW)] == ["https://a.com/good", "https://a.com/weak"]


def test_thin_text_is_penalised(make_item):
    full = make_item(url="https://a.com/1")
    thin = make_item(url="https://a.com/2", text="short")
    assert score_item(thin, NOW) < score_item(full, NOW)
    assert "thin_text_penalty" in thin.score_breakdown
