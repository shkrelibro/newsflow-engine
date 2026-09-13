"""URL canonicalisation and redirect unwrapping."""
from __future__ import annotations

import base64
import re
from typing import Optional
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = {
    "fbclid", "gclid", "dclid", "msclkid", "yclid", "igshid", "mc_cid", "mc_eid", "ocid", "cmpid", "cmp",
    "ref", "refsrc", "source", "spm", "ns_campaign", "ns_mchannel", "ns_source", "ns_linkname", "ns_fee",
    "_ga", "_gl", "s_kwcid", "ito", "ESRC", "sh", "guccounter", "guce_referrer", "guce_referrer_sig",
    "xtor", "at_medium", "at_campaign", "smid", "ncid", "oc",
}


def domain_of(url: str) -> str:
    if not url:
        return ""
    try:
        host = urlsplit(url).netloc.lower()
    except ValueError:
        return ""
    host = host.split("@")[-1].split(":")[0]
    for prefix in ("www.", "m.", "amp.", "mobile."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    return host


def _decode_google_news_link(url: str) -> str:
    """Older Google News RSS article ids are base64 protobufs that contain the URL in clear."""
    m = re.search(r"news\.google\.com/(?:rss/)?articles/([A-Za-z0-9_\-]+)", url)
    if not m:
        return url
    token = m.group(1)
    try:
        padded = token + "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(padded)
    except (ValueError, TypeError):
        return url
    text = raw.decode("latin-1", errors="ignore")
    found = re.findall(r"https?://[^\x00-\x20\x7f-\xff\"'<>]+", text)
    for cand in found:
        if "google.com" not in cand:
            return cand
    return url


def unwrap_redirect(url: str) -> str:
    """Unwrap Bing / MSN / Google redirect links when the target is in the URL itself."""
    if not url:
        return url
    parts = urlsplit(url)
    host = parts.netloc.lower()
    qs = dict(parse_qsl(parts.query, keep_blank_values=True))
    if host.endswith("bing.com") and "url" in qs:
        return unquote(qs["url"])
    if host.endswith("news.google.com"):
        return _decode_google_news_link(url)
    if "url" in qs and qs["url"].startswith("http") and host.endswith(("msn.com", "duckduckgo.com")):
        return unquote(qs["url"])
    return url


def canonical_url(url: str) -> str:
    url = unwrap_redirect(url.strip())
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    scheme = (parts.scheme or "https").lower()
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = re.sub(r"/+", "/", parts.path or "/")
    if path.endswith("/index.html") or path.endswith("/index.htm"):
        path = path.rsplit("/", 1)[0] + "/"
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    # amp variants
    path = re.sub(r"/amp/?$", "", path) or "/"
    keep = []
    for k, v in parse_qsl(parts.query, keep_blank_values=True):
        kl = k.lower()
        if kl.startswith("utm_") or kl in TRACKING_PARAMS:
            continue
        keep.append((k, v))
    keep.sort()
    query = urlencode(keep, doseq=True)
    return urlunsplit((scheme, host, path, query, ""))


def is_google_news_link(url: str) -> bool:
    return "news.google.com" in urlsplit(url).netloc.lower()


_URL_YEAR = re.compile(r"(?:^|[/\-_.])((?:19|20)\d{2})(?:[/\-_.]|$)")


def url_year(url: str) -> Optional[int]:
    """The publication year encoded in a URL path, if there is one.

    Most publishers put the date in the path: /2009/03/ca-investiert/, -2009-03-12-, /archiv/2009/.
    That is written once when the article is created and does not move, which makes it the one
    date on a re-dated page that can still be trusted. Returns None when the path carries no year,
    which is common and is not evidence of anything.
    """
    try:
        path = urlsplit(url).path
    except ValueError:
        return None
    best = None
    for m in _URL_YEAR.finditer(path):
        year = int(m.group(1))
        if 1990 <= year <= 2100:
            best = year if best is None else min(best, year)
    return best
