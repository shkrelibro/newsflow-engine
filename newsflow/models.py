"""Plain data structures shared across the pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class RawItem:
    """One mention as it came off a route, before normalisation."""

    title: str
    link: str
    route: str                     # googlenews | bingnews | gdelt | rss | page
    query: str                     # the alias/site query or feed/page URL that produced it
    summary: str = ""
    published_at: Optional[datetime] = None
    source_name: str = ""
    source_domain: str = ""
    lang: str = ""                 # language the query was run in (may be "" for feeds)
    country: str = ""              # market the query was run in
    tier_hint: Optional[int] = None  # 0 official, 1 regulator/court, 2 global, 3 national, 4 trade, 5 regional
    name_ids: list[str] = field(default_factory=list)  # names the query was run for (feeds may be shared)


@dataclass
class Item:
    """A normalised, stored mention."""

    id: Optional[int]
    canonical_url: str
    raw_link: str
    title: str
    summary: str
    source_name: str
    source_domain: str
    published_at: Optional[datetime]
    first_seen_at: datetime
    lang: str
    country: str
    route: str
    query: str
    tier_hint: Optional[int]
    status: str = "new"            # new | candidate | screened
    screen_reason: str = ""
    cluster_id: Optional[int] = None


@dataclass
class Match:
    item_id: int
    name_id: str
    alias: str
    where: str                     # title | summary
    confidence: float


@dataclass
class SourceResult:
    route: str
    source: str                    # query label or URL
    ok: bool
    items: int
    error: str = ""
    seconds: float = 0.0
