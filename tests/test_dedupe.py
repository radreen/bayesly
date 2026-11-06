from bayesly.dedupe import dedupe, item_key, normalize_url


def test_normalize_url_strips_tracking_and_trailing_slash():
    a = normalize_url("http://www.Example.com/post/?utm_source=x&id=2#top")
    b = normalize_url("https://example.com/post?id=2")
    assert a == b


def test_same_url_is_deduped(make_item):
    items = [make_item(url="https://example.com/a?utm_medium=rss"), make_item(url="https://example.com/a")]
    assert len(dedupe(items)) == 1


def test_similar_titles_keep_more_reliable_source(make_item):
    low = make_item(title="Google releases Gemini model update", url="https://press.com/x", reliability=0.6, source_id="press")
    high = make_item(title="Google releases Gemini model update today", url="https://blog.google/y", reliability=0.95, source_id="official")
    kept = dedupe([low, high])
    assert [i.source.id for i in kept] == ["official"]


def test_different_titles_are_kept(make_item):
    a = make_item(title="New open-source speech model", url="https://a.com/1")
    b = make_item(title="GPU prices drop for inference clusters", url="https://b.com/2")
    assert len(dedupe([a, b])) == 2


def test_previously_drafted_items_are_skipped(make_item):
    item = make_item()
    assert dedupe([item], seen_keys={item_key(item)}) == []
