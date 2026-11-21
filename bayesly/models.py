"""Plain data types shared across pipeline steps."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    url: str
    kind: str
    reliability: float
    max_items: int = 10
    cover_image: bool = True   # use the article's og:image as the cover photo
    resolve_link: bool = False # aggregator: follow the story's first outbound link to the article


@dataclass
class Item:
    """One collected entry from a feed."""

    source: Source
    title: str
    url: str
    text: str
    published: datetime | None
    score: float = 0.0
    score_breakdown: dict[str, float] = field(default_factory=dict)
