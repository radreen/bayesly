from datetime import timedelta

from bayesly.score import is_rumour, novelty, rank, score_item

from conftest import NOW


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
