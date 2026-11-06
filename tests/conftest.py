from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bayesly.models import Item, Source

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def make_item():
    def _make(
        title="OpenAI releases a new reasoning model API",
        url="https://example.com/post",
        text="The company released a model with an API. " * 10,
        reliability=0.9,
        source_id="src",
        published=NOW,
    ) -> Item:
        src = Source(id=source_id, name=source_id.title(), url="https://example.com/feed",
                     kind="official_blog", reliability=reliability)
        return Item(source=src, title=title, url=url, text=text, published=published)

    return _make
