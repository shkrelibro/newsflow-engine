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
from html import unescape as html_unescape
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
    ("lv", "LV"): ("lv", "LV", "LV:lv"),
    ("et", "EE"): ("et-EE", "EE", "EE:et-EE"),
    ("he", "IL"): ("he", "IL", "IL:he"),
    ("zh", "CN"): ("zh-CN", "CN", "CN:zh-Hans"),
    ("zh", "HK"): ("zh-HK", "HK", "HK:zh-Hant"),
    ("zh", "TW"): ("zh-TW", "TW", "TW:zh-Hant"),
    ("en", "HK"): ("en-US", "US", "US:en"),
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
    "US": "US", "CN": "CH", "HK": "HK", "IN": "IN", "TW": "TW",
}
GDELT_LANGS: dict[str, str] = {
    "sv": "swedish", "el": "greek", "es": "spanish", "de": "german", "it": "italian", "nl": "dutch",
    "fr": "french", "en": "english", "hu": "hungarian", "pl": "polish", "cs": "czech", "sk": "slovak",
    "fi": "finnish", "nb": "norwegian", "no": "norwegian", "da": "danish", "pt": "portuguese",
    "lt": "lithuanian", "lv": "latvian", "et": "estonian", "ro": "romanian", "zh": "chinese",
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


COMMON_FEED_PATHS = [
    "/rss", "/feed", "/rss.xml", "/feed.xml", "/feeds", "/rss/", "/feed/", "/index.xml", "/atom.xml",
    # paths European publishers actually use, learned from the outlets that discovery kept missing
    "/rss/", "/rss/index.xml", "/rss/home.xml", "/rss/nyheter", "/rss/ekonomi",
    "/feeds/all.rss", "/feeds/rss", "/feed/rss", "/rssfeed", "/rssfeeds", "/rss-feeds",
    "/arc/outboundfeeds/rss/", "/api/rss", "/rss/wirtschaft", "/rss/economia", "/rss/economie",
    "/laatste-nieuws/rss", "/rss/nieuws", "/xml/rss", "/rss/tuoreimmat", "/rss/full.xml",
]

# Pages that list a publisher's feeds rather than being one. Discovery follows these one level.
FEED_INDEX_PATHS = ["/rss", "/rss/", "/feeds", "/feeds/", "/rss-feeds", "/rssfeeds", "/hjelp/rss", "/service/rss"]

_FEEDISH = re.compile(r"(rss|atom|feed)", re.I)


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


def _feedish_anchors(html: str, base_url: str) -> list[str]:
    """Anchors whose href or text mentions rss/atom/feed.

    Most European publishers never declare <link rel=alternate>; they link an "RSS" page from the
    footer instead, so the declared-link-only search finds nothing on exactly the titles that matter.
    """
    out: list[str] = []
    for href, text in extract_links(html, base_url):
        if _FEEDISH.search(href) or _FEEDISH.search(text or ""):
            out.append(href)
    return out


def discover_feed(http: Http, homepage: str) -> tuple[str, str]:
    """Find an RSS/Atom feed for an outlet. Returns (feed_url, reason_if_not_found).

    Four passes, cheapest first: declared <link rel=alternate>; feed-ish anchors on the homepage;
    common paths under both the configured URL and the site root; and any feed linked from a feed
    index page. The reason is stored so a persistent failure is diagnosable instead of silent.
    """
    ACCEPT = "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8"
    tried: set[str] = set()
    home_fetched = False
    index_pages: list[str] = []

    def try_url(url: str) -> tuple[str, bool]:
        """Returns (feed_url_if_valid, looked_like_an_index_page)."""
        if url in tried:
            return "", False
        tried.add(url)
        try:
            text = http.get_text(url, accept=ACCEPT)
        except FetchError:
            return "", False
        if parse_feed_text(text).entries:
            return url, False
        # not a feed; if it is HTML mentioning feeds, it may be a feed index worth following
        return "", bool(_FEEDISH.search(text[:20000]))

    candidates: list[str] = []
    try:
        html = http.get_text(homepage, is_page=True)
        home_fetched = True
        candidates.extend(extract_feed_links(html, homepage))
        candidates.extend(_feedish_anchors(html, homepage))
    except FetchError:
        pass

    # Both the configured URL and the site root. An outlet configured as a section page
    # (news.sky.com/business, is.fi/taloussanomat/) otherwise never gets its root tried at all.
    parts = urlsplit(homepage)
    bases = [homepage.rstrip("/")]
    root = f"{parts.scheme}://{parts.netloc}"
    if root.rstrip("/") not in bases:
        bases.append(root)
    for b in bases:
        candidates.extend(b + p for p in COMMON_FEED_PATHS)

    for c in candidates:
        found, is_index = try_url(c)
        if found:
            return found, ""
        if is_index:
            index_pages.append(c)

    # Follow feed index pages one level: "/rss" is very often a list of feeds, not a feed.
    for b in bases:
        index_pages.extend(b + p for p in FEED_INDEX_PATHS)
    for page in index_pages[:6]:
        try:
            html = http.get_text(page, is_page=True)
        except FetchError:
            continue
        for c in extract_feed_links(html, page) + _feedish_anchors(html, page):
            found, _ = try_url(c)
            if found:
                return found, ""

    if not home_fetched:
        return "", f"homepage unreachable; {len(tried)} candidates tried"
    return "", f"no feed found; {len(tried)} candidates tried"


def fetch_page_links(http: Http, page_url: str) -> tuple[list[tuple[str, str]], SourceResult]:
    t0 = time.monotonic()
    try:
        html = http.get_text(page_url, is_page=True)
        links = extract_links(html, page_url)
        return links, SourceResult("page", page_url, True, len(links), "", time.monotonic() - t0)
    except Exception as exc:  # noqa: BLE001
        return [], SourceResult("page", page_url, False, 0, str(exc)[:300], time.monotonic() - t0)


# ----------------------------------------------------------------------
# Page watchers take the link text as the headline, and on some newsrooms the link text is a
# button. Cheplapharm's 5 October release, the largest refinancing on the page that day, was
# stored as "Read more chevron_right"; British Sugar's beet-contract notice as "Read more". A
# headline like that cannot be matched by the golden set, cannot be judged without opening the
# page, and reads as a broken row in the brief. So a new link whose text is generic is fetched
# once and its og:title, h1 or <title> used instead, a handful of fetches per watcher per run.
# ----------------------------------------------------------------------
GENERIC_LINK_TEXT = re.compile(
    r"^\s*(?:read\s+more|more|mehr(?:\s+erfahren|\s+lesen)?|weiterlesen|zum\s+artikel|zur\s+meldung|details?"
    r"|lire\s+la\s+suite|en\s+savoir\s+plus|lire|leer\s+m[áa]s|leggi(?:\s+tutto|\s+di\s+pi[uù])?"
    r"|l[äa]s\s+mer|les\s+mer|l[æa]s\s+mere|lue\s+lis[äa][äa]|czytaj\s+wi[ęe]cej|tov[áa]bb|meer\s+lezen|lees\s+meer"
    r"|download|pdf|herunterladen|open|view|link|here|click\s+here|learn\s+more|find\s+out\s+more|full\s+(?:story|article|release)"
    r"|chevron_right|arrow_forward|→|»|>)\s*(?:chevron_right|arrow_forward|→|»|>)?\s*$",
    re.IGNORECASE)
_OG_TITLE = re.compile(r"<meta[^>]+property=[\"']og:title[\"'][^>]+content=[\"']([^\"']+)[\"']", re.IGNORECASE)
_OG_TITLE_REV = re.compile(r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+property=[\"']og:title[\"']", re.IGNORECASE)
_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_TITLE_TAG = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def generic_link_text(text: str) -> bool:
    t = (text or "").strip()
    return len(t) < 12 or GENERIC_LINK_TEXT.match(t) is not None or "chevron_right" in t.lower()


def title_from_article(html: str) -> str:
    """The article's own headline: og:title, then the first h1, then <title> without the site suffix."""
    for pat in (_OG_TITLE, _OG_TITLE_REV, _H1, _TITLE_TAG):
        m = pat.search(html or "")
        if not m:
            continue
        t = html_unescape(re.sub(r"<[^>]+>", " ", m.group(1)))
        t = re.sub(r"\s+", " ", t).strip()
        if pat is _TITLE_TAG:
            head = re.split(r"\s+[|\u2013\u2014-]\s+", t)[0].strip()
            if len(head) >= 12:
                t = head
        if len(t) >= 12 and not generic_link_text(t):
            return t[:300]
    return ""


def enrich_generic_titles(http: Http, items: list[RawItem], limit: int = 5) -> int:
    """Replace button-text headlines with the article's own, fetching at most `limit` pages."""
    fetched = 0
    for it in items:
        if fetched >= limit:
            break
        if not generic_link_text(it.title) or it.link.lower().split("?")[0].endswith(".pdf"):
            continue
        try:
            html = http.get_text(it.link, is_page=True)
        except Exception:  # noqa: BLE001 - a failed enrichment keeps the link text
            fetched += 1
            continue
        fetched += 1
        title = title_from_article(html)
        if title:
            it.title = title
    return fetched


# ----------------------------------------------------------------------
# Page watchers have no date of their own: a link that is new to the watcher is not necessarily a
# new article. On 12 October Adler's newsroom listed six releases from April (news ids 1558 to
# 1573, below the ids 1574 to 1601 the watcher had stored in September) and all six went out as
# today's news. So every new link is fetched once and dated from the article itself; an article
# older than the freshness limit is dropped, and one with no readable date keeps published_at
# None, which the brief treats as undated and will not publish without a date check.
# ----------------------------------------------------------------------
_DATE_META = [
    re.compile(r"<meta[^>]+(?:property|name|itemprop)=[\"'](?:article:published_time|og:published_time|datePublished|date|dc\.date|DC\.date\.issued|pubdate|publish-date|parsely-pub-date|sailthru\.date)[\"'][^>]+content=[\"']([^\"']+)[\"']", re.IGNORECASE),
    re.compile(r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+(?:property|name|itemprop)=[\"'](?:article:published_time|og:published_time|datePublished|date|pubdate|publish-date)[\"']", re.IGNORECASE),
    re.compile(r"\"datePublished\"\s*:\s*\"([^\"]+)\"", re.IGNORECASE),
    re.compile(r"<time[^>]+datetime=[\"']([^\"']+)[\"']", re.IGNORECASE),
]
_MONTHS = {m: i for i, m in enumerate(
    "january february march april may june july august september october november december".split(), 1)}
_MONTHS.update({m: i for i, m in enumerate(
    "janvier février mars avril mai juin juillet août septembre octobre novembre décembre".split(), 1)})
_MONTHS.update({m: i for i, m in enumerate(
    "januar februar märz april mai juni juli august september oktober november dezember".split(), 1)})
_TEXT_DATE_NUM = re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](20\d{2})\b")
_TEXT_DATE_WORD = re.compile(r"\b(\d{1,2})\.?\s+([A-Za-zÀ-ÿ]+)\s+(20\d{2})\b")
_URL_DATE = re.compile(r"/(20\d{2})[/-](\d{1,2})[/-](\d{1,2})(?:/|-|$)")


def _parse_any_date(v: str) -> Optional[datetime]:
    v = (v or "").strip()
    if not v:
        return None
    try:
        t = datetime.fromisoformat(v.replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    m = re.match(r"(20\d{2})-(\d{2})-(\d{2})", v)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def date_from_article(html: str, url: str = "", text_fallback: bool = False) -> Optional[datetime]:
    """The article's own publication date: meta tags and JSON-LD first, then <time>, then a date in
    the URL. None when nothing is readable.

    text_fallback also takes the first plain-text date in the body. It is off for the page watchers:
    a regulatory piece cites older dates in its first paragraph ("the protocol signed on 23 July
    2023"), and a sidebar lists newer ones, so a body date can neither prove an article old nor
    prove it new. Such an article stays undated and the writer checks it."""
    for pat in _DATE_META:
        m = pat.search(html or "")
        if m:
            t = _parse_any_date(m.group(1))
            if t:
                return t
    m = _URL_DATE.search(url or "")
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
        except ValueError:
            pass
    if not text_fallback:
        return None
    body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html or "", flags=re.DOTALL | re.IGNORECASE)
    body = re.sub(r"<[^>]+>", " ", body)
    m = _TEXT_DATE_NUM.search(body)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), tzinfo=timezone.utc)
        except ValueError:
            pass
    for m in _TEXT_DATE_WORD.finditer(body):
        mon = _MONTHS.get(m.group(2).lower())
        if mon:
            try:
                return datetime(int(m.group(3)), mon, int(m.group(1)), tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


def date_page_items(http, items: list[RawItem], max_age_hours: float = 72.0, limit: int = 8,
                    now: Optional[datetime] = None) -> tuple[list[RawItem], int, int]:
    """Date each new page-watcher item from its article; drop the ones older than max_age_hours.
    Returns (kept, dropped_old, undated). Items beyond `limit` fetches stay undated."""
    now = now or datetime.now(timezone.utc)
    kept: list[RawItem] = []
    dropped = undated = fetched = 0
    for it in items:
        t = None
        m = _URL_DATE.search(it.link or "")
        if m:
            try:
                t = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
            except ValueError:
                t = None
        if t is None and fetched < limit and http is not None and not it.link.lower().split("?")[0].endswith(".pdf"):
            fetched += 1
            try:
                t = date_from_article(http.get_text(it.link, is_page=True), it.link)
            except Exception:  # noqa: BLE001 - an unreachable article stays undated
                t = None
        if t is not None:
            if (now - t).total_seconds() > max_age_hours * 3600:
                dropped += 1
                continue
            it.published_at = t
        else:
            undated += 1
        kept.append(it)
    return kept, dropped, undated


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
