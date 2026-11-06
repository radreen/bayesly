from bayesly.collect import clean_text, parse_feed
from bayesly.models import Source

RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>T</title>
<item><title>Model &amp; API released</title><link>https://example.com/a</link>
<description>&lt;p&gt;It is <b>fast</b>.&lt;/p&gt;</description>
<pubDate>Thu, 05 Nov 2026 10:00:00 GMT</pubDate></item>
<item><title></title><link>https://example.com/no-title</link></item>
</channel></rss>"""


def test_parse_feed_extracts_clean_items():
    src = Source(id="s", name="S", url="u", kind="official_blog", reliability=0.9)
    items = parse_feed(src, RSS)
    assert len(items) == 1  # entry without a title is dropped
    assert items[0].title == "Model & API released"
    assert items[0].text == "It is fast ."
    assert items[0].published.year == 2026


def test_clean_text_collapses_whitespace():
    assert clean_text("<p>a\n\n  b</p>") == "a b"
