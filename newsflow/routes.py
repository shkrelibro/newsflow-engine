"""Fetch routes: Google News RSS, Bing News RSS, GDELT, outlet feeds, page watchers.

Every route returns a list of RawItem plus a SourceResult describing what
happened, so that source health is visible in the export.
"""
from __future__ import annotations

import calendar
import json
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Iterable, Optional
from urllib.parse import quote_plus, urljoin, urlsplit

import feedparser

from .http import FetchError, Http
from .models import RawItem, SourceResult
from .normalize import domain_of, unwrap_redirect

# ----------------------------------------------------------------------
# Locale tables
# ----------------------------------------------------------------------

# Google News wants hl / gl / ceid triples that are not always the obvious ones.
GOOGLE_LOCALES: dict[tuple[str, str], tuple[str, str, str]] = {
    ("en", "GB"): ("en-GB", "GB", "GB:en-GB"),
    ("en", "IE"): ("en-IE", "IE", "IE:en"),
    ("en", "US"): ("en-US", "US", "US:en"),
    ("en", "SE"): ("en-GB", "GB", "GB:en-GB"),
    ("pt", "PT"): ("pt-PT", "PT", "PT:pt-150"),
    ("pt", "BR"): ("pt-BR", "BR", "BR:pt-419"),
    ("nb", "NO"): ("no", "NO", "NO:no"),
    ("no", "NO"): ("no", "NO", "NO:no"),
    ("de", "AT"): ("de", "AT", "AT:de"),
    ("de", "CH"): ("de", "CH", "CH:de"),
    ("fr", "BE"): ("fr", "BE", "BE:fr"),
    ("nl", "BE"): ("nl", "BE", "BE:nl"),
    ("fr", "CH"): ("fr", "CH", "CH:fr"),
    ("it", "CH"): ("it", "CH", "CH:it"),
}

# Bing market codes.
BING_MARKETS: dict[tuple[str, str], str] = {
    ("nb", "NO"): "nb-NO", ("no", "NO"): "nb-NO",
}

# GDELT uses FIPS 10-4 country codes, not ISO.
GDELT_FIPS: dict[str, str] = {
    "SE": "SW", "GR": "GR", "ES": "SP", "DE": "GM", "IT": "IT", "NL": "NL", "FR": "FR", "GB": "UK",
    "HU": "HU", "PL": "PL", "CZ": "EZ", "SK": "LO", "FI": "FI", "NO": "NO", "DK": "DA", "PT": "PO",
    "CH": "SZ", "AT": "AU", "BE": "BE", "IE": "EI", "LT": "LH", "LV": "LG", "EE": "EN", "RO": "RO",
    "US": "US",
}
GDELT_LANGS: dict[str, str] = {
    "sv": "swedish", "el": "greek", "es": "spanish", "de": "german", "it": "italian", "nl": "dutch",
    "fr": "french", "en": "english", "hu": "hungarian", "pl": "polish", "cs": "czech", "sk": "slovak",
    "fi": "finnish", "nb": "norwegian", "no": "norwegian", "da": "danish", "pt": "portuguese",
    "lt": "lithuanian", "lv": "latvian", "et": "estonian", "ro": "romanian",
}


def google_locale(lang: str, country: str) -> tuple[str, str, str]:
    key = (lang.lower(), country.upper())
    if key in GOOGLE_LOCALES:
        return GOOGLE_LOCALES[key]
    return (lang.lower(), country.upper(), f"{country.upper()}:{lang.lower()}")


def bing_market(lang: str, country: str) -> str:
    return BING_MARKETS.get((lang.lower(), country.upper()), f"{lang.lower()}-{country.upper()}")


# ----------------------------------------------------------------------
# URL builders (pure functions, easy to test)
# ----------------------------------------------------------------------

def google_news_url(query: str, lang: str, country: str, when: str = "1d") -> str:
    hl, gl, ceid = google_locale(lang, country)
    q = f'{query} when:{when}' if when else query
    return f"https://news.google.com/rss/search?q={quote_plus(q)}&hl={hl}&gl={gl}&ceid={ceid}"


def bing_news_url(query: str, lang: str, country: str) -> str:
    return f"https://www.bing.com/news/search?q={quote_plus(query)}&format=rss&mkt={bing_market(lang, country)}"


def gdelt_url(query: str, timespan: str = "24h", country: str = "", lang: str = "", maxrecords: int = 250) -> str:
    q = query
    if country:
        q += f" sourcecountry:{GDELT_FIPS.get(country.upper(), country.upper())}"
    if lang:
        q += f" sourcelang:{GDELT_LANGS.get(lang.lower(), lang.lower())}"
    return (
        "https://api.gdeltproject.org/api/v2/doc/doc?query=" + quote_plus(q)
        + f"&mode=artlist&maxrecords={maxrecords}&format=json&timespan={timespan}&sort=datedesc"
    )


# ----------------------------------------------------------------------
# Parsing helpers
# ----------------------------------------------------------------------

def _dt_from_struct(st) -> Optional[datetime]:
    if not st:
        return None
    try:
        return datetime.fromtimestamp(calendar.timegm(st), tz=timezone.utc)
    except (OverflowError, ValueError, TypeError):
        return None


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"&#39;|&apos;", "'", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_feed_text(text: str) -> feedparser.FeedParserDict:
    return feedparser.parse(text)


def _entry_source(entry) -> tuple[str, str]:
    """Return (source_name, source_url) from an RSS entry if present."""
    src = entry.get("source")
    if isinstance(src, dict):
        return (src.get("title", "") or "", src.get("href", "") or src.get("url", "") or "")
    # Bing uses a News namespace; feedparser lowercases and prefixes it
    for key in ("news_source", "newssource"):
        if entry.get(key):
            return (str(entry.get(key)), "")
    return ("", "")


def parse_google_news(text: str, query: str, lang: str, country: str, name_ids: list[str]) -> list[RawItem]:
    feed = parse_feed_text(text)
    items: list[RawItem] = []
    for e in feed.entries:
        title = _strip_html(e.get("title", ""))
        src_name, src_url = _entry_source(e)
        if src_name and title.endswith(" - " + src_name):
            title = title[: -(len(src_name) + 3)].strip()
        link = e.get("link", "")
        items.append(
            RawItem(
                title=title,
                link=link,
                route="googlenews",
                query=query,
                summary=_strip_html(e.get("summary", ""))[:600],
                published_at=_dt_from_struct(e.get("published_parsed")),
                source_name=src_name,
                source_domain=domain_of(src_url) if src_url else "",
                lang=lang,
                country=country,
                name_ids=list(name_ids),
            )
        )
    return items


def parse_bing_news(text: str, query: str, lang: str, country: str, name_ids: list[str]) -> list[RawItem]:
    feed = parse_feed_text(text)
    items: list[RawItem] = []
    for e in feed.entries:
        raw_link = e.get("link", "")
        link = unwrap_redirect(raw_link)
        src_name, _ = _entry_source(e)
        items.append(
            RawItem(
                title=_strip_html(e.get("title", "")),
                link=link,
                route="bingnews",
                query=query,
                summary=_strip_html(e.get("summary", e.get("description", "")))[:600],
                published_at=_dt_from_struct(e.get("published_parsed")),
                source_name=src_name,
                source_domain=domain_of(link),
                lang=lang,
                country=country,
                name_ids=list(name_ids),
            )
        )
    return items


def parse_gdelt(text: str, query: str, lang: str, country: str, name_ids: list[str]) -> list[RawItem]:
    text = text.strip()
    if not text.startswith("{"):
        # GDELT answers errors and rate limits with plain text
        raise FetchError(f"GDELT non-JSON response: {text[:120]}")
    data = json.loads(text)
    items: list[RawItem] = []
    for a in data.get("articles", []) or []:
        seen = a.get("seendate", "")
        published = None
        if seen:
            try:
                published = datetime.strptime(seen, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            except ValueError:
                published = None
        items.append(
            RawItem(
                title=_strip_html(a.get("title", "")),
                link=a.get("url", ""),
                route="gdelt",
                query=query,
                summary="",
                published_at=published,
                source_name=a.get("domain", ""),
                source_domain=domain_of(a.get("url", "")),
                lang=(a.get("language", "") or lang or "").lower()[:12],
                country=a.get("sourcecountry", "") or country,
                name_ids=list(name_ids),
            )
        )
    return items


def parse_generic_feed(text: str, feed_url: str, name_ids: list[str], tier: int, country: str, lang: str) -> list[RawItem]:
    feed = parse_feed_text(text)
    feed_title = (feed.feed.get("title", "") if feed.feed else "") or domain_of(feed_url)
    items: list[RawItem] = []
    for e in feed.entries:
        link = e.get("link", "") or ""
        items.append(
            RawItem(
                title=_strip_html(e.get("title", "")),
                link=link,
                route="rss",
                query=feed_url,
                summary=_strip_html(e.get("summary", e.get("description", "")))[:600],
                published_at=_dt_from_struct(e.get("published_parsed") or e.get("updated_parsed")),
                source_name=feed_title,
                source_domain=domain_of(link) or domain_of(feed_url),
                lang=lang,
                country=country,
                tier_hint=tier,
                name_ids=list(name_ids),
            )
        )
    return items


# ----------------------------------------------------------------------
# HTML helpers for page watchers and feed discovery
# ----------------------------------------------------------------------

class _LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []   # (href, text)
        self.feeds: list[str] = []
        self._href: Optional[str] = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "link":
            typ = (a.get("type") or "").lower()
            rel = (a.get("rel") or "").lower()
            if "alternate" in rel and ("rss" in typ or "atom" in typ) and a.get("href"):
                self.feeds.append(a["href"])
        elif tag == "a" and a.get("href"):
            self._href = a["href"]
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            text = re.sub(r"\s+", " ", "".join(self._text)).strip()
            self.links.append((self._href, text))
            self._href = None
            self._text = []


def extract_links(html: str, base_url: str) -> list[tuple[str, str]]:
    p = _LinkCollector()
    try:
        p.feed(html)
    except Exception:  # noqa: BLE001 - tolerate broken HTML
        pass
    out: list[tuple[str, str]] = []
    for href, text in p.links:
        if href.startswith(("javascript:", "mailto:", "#", "tel:")):
            continue
        out.append((urljoin(base_url, href).split("#")[0], text))
    return out


def extract_feed_links(html: str, base_url: str) -> list[str]:
    p = _LinkCollector()
    try:
        p.feed(html)
    except Exception:  # noqa: BLE001
        pass
    return [urljoin(base_url, h) for h in p.feeds]


COMMON_FEED_PATHS = ["/rss", "/feed", "/rss.xml", "/feed.xml", "/feeds", "/rss/", "/feed/", "/index.xml", "/atom.xml"]


def looks_like_article(url: str, text: str, base_url: str, link_pattern: str = "") -> bool:
    """Heuristic: article links have a path with some depth and a real title."""
    if link_pattern:
        return re.search(link_pattern, url) is not None
    parts = urlsplit(url)
    base = urlsplit(base_url)
    if parts.netloc != base.netloc:
        return False
    path = parts.path.rstrip("/")
    if path.count("/") < 1 or len(path) < 12:
        return False
    if len(text) < 20:
        return False
    return True


# ----------------------------------------------------------------------
# Live fetchers (take an Http; easy to stub in tests)
# ----------------------------------------------------------------------

def _timed(fn):
    t0 = time.monotonic()
    try:
        items = fn()
        return items, None, time.monotonic() - t0
    except Exception as exc:  # noqa: BLE001 - any failure becomes a source result
        return [], str(exc)[:300], time.monotonic() - t0


def fetch_google_news(http: Http, query: str, lang: str, country: str, name_ids: list[str], when: str = "1d") -> tuple[list[RawItem], SourceResult]:
    url = google_news_url(query, lang, country, when)
    items, err, secs = _timed(lambda: parse_google_news(http.get_text(url, accept="application/rss+xml, application/xml;q=0.9, */*;q=0.8"), query, lang, country, name_ids))
    label = f"{query} [{lang}-{country}]"
    return items, SourceResult("googlenews", label, err is None, len(items), err or "", secs)


def fetch_bing_news(http: Http, query: str, lang: str, country: str, name_ids: list[str]) -> tuple[list[RawItem], SourceResult]:
    url = bing_news_url(query, lang, country)
    items, err, secs = _timed(lambda: parse_bing_news(http.get_text(url, accept="application/rss+xml, application/xml;q=0.9, */*;q=0.8"), query, lang, country, name_ids))
    label = f"{query} [{bing_market(lang, country)}]"
    return items, SourceResult("bingnews", label, err is None, len(items), err or "", secs)


def fetch_gdelt(http: Http, query: str, name_ids: list[str], timespan: str = "24h", country: str = "", lang: str = "") -> tuple[list[RawItem], SourceResult]:
    url = gdelt_url(query, timespan, country, lang)
    items, err, secs = _timed(lambda: parse_gdelt(http.get_text(url, accept="application/json"), query, lang, country, name_ids))
    label = f"{query} [{country or 'all'}/{lang or 'all'}]"
    return items, SourceResult("gdelt", label, err is None, len(items), err or "", secs)


def fetch_feed(http: Http, feed_url: str, name_ids: list[str], tier: int, country: str, lang: str, label: str = "") -> tuple[list[RawItem], SourceResult]:
    items, err, secs = _timed(lambda: parse_generic_feed(http.get_text(feed_url, accept="application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8"), feed_url, name_ids, tier, country, lang))
    return items, SourceResult("rss", label or feed_url, err is None, len(items), err or "", secs)


def discover_feed(http: Http, homepage: str) -> str:
    """Find an RSS/Atom feed for an outlet: <link rel=alternate> first, then common paths."""
    candidates: list[str] = []
    try:
        html = http.get_text(homepage, is_page=True)
        candidates.extend(extract_feed_links(html, homepage))
    except FetchError:
        pass
    base = homepage.rstrip("/")
    candidates.extend(base + p for p in COMMON_FEED_PATHS)
    seen: set[str] = set()
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        try:
            text = http.get_text(c, accept="application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8")
        except FetchError:
            continue
        feed = parse_feed_text(text)
        if feed.entries:
            return c
    return ""


def fetch_page_links(http: Http, page_url: str) -> tuple[list[tuple[str, str]], SourceResult]:
    t0 = time.monotonic()
    try:
        html = http.get_text(page_url, is_page=True)
        links = extract_links(html, page_url)
        return links, SourceResult("page", page_url, True, len(links), "", time.monotonic() - t0)
    except Exception as exc:  # noqa: BLE001
        return [], SourceResult("page", page_url, False, 0, str(exc)[:300], time.monotonic() - t0)


def page_items_from_links(
    links: Iterable[tuple[str, str]],
    page_url: str,
    name_ids: list[str],
    tier: int,
    country: str,
    lang: str,
    source_name: str,
    link_pattern: str = "",
) -> list[RawItem]:
    items: list[RawItem] = []
    for url, text in links:
        if not looks_like_article(url, text, page_url, link_pattern):
            continue
        items.append(
            RawItem(
                title=text,
                link=url,
                route="page",
                query=page_url,
                summary="",
                published_at=None,
                source_name=source_name,
                source_domain=domain_of(url),
                lang=lang,
                country=country,
                tier_hint=tier,
                name_ids=list(name_ids),
            )
        )
    return items
