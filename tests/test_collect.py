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


def test_aggregator_items_point_to_the_original_article():
    from bayesly.collect import original_link
    summary = ('<a href="https://www.techmeme.com/261120/p43#a261120p43">x</a> '
               '<a href="https://www.cnbc.com/2026/11/20/cursor-models.html?a=1&amp;b=2">CNBC</a> '
               '<a href="http://www.cnbc.com/">CNBC</a>')
    assert original_link(summary, "https://www.techmeme.com/261120/p43") == "https://www.cnbc.com/2026/11/20/cursor-models.html?a=1&b=2"
    assert original_link("<p>no links</p>", "https://www.techmeme.com/x") == "https://www.techmeme.com/x"


def test_x_account_pages_are_skipped_for_the_post_link():
    from bayesly.collect import original_link
    s = '<a href="https://x.com/oracle">Oracle</a> <a href="https://x.com/oracle/status/2123884437045760420">post</a>'
    assert original_link(s, "https://www.techmeme.com/a") == "https://x.com/oracle/status/2123884437045760420"
