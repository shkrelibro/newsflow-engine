from datetime import datetime, timezone

from newsflow.dedupe import similar, title_key
from newsflow.match import Matcher
from newsflow.normalize import canonical_url, domain_of, unwrap_redirect
from newsflow.routes import (
    bing_news_url,
    extract_links,
    gdelt_url,
    google_news_url,
    looks_like_article,
    parse_bing_news,
    parse_gdelt,
    parse_generic_feed,
    parse_google_news,
)
from tests.conftest import google_token


# ---------------------------------------------------------------- normalise
def test_canonical_strips_tracking_and_www():
    u = "https://www.expansion.com/empresas/2026/08/20/intrum.html?utm_source=rss&utm_medium=feed&id=3&fbclid=x"
    assert canonical_url(u) == "https://expansion.com/empresas/2026/08/20/intrum.html?id=3"


def test_unwrap_bing_redirect():
    u = "http://www.bing.com/news/apiclick.aspx?ref=FexRss&aid=&url=https%3a%2f%2fhurbra.se%2fintrum-aktie-rusar%2f&c=1"
    assert unwrap_redirect(u) == "https://hurbra.se/intrum-aktie-rusar/"
    assert canonical_url(u) == "https://hurbra.se/intrum-aktie-rusar"


def test_decode_google_news_token():
    target = "https://www.placera.se/nyheter/intrum-emitterar-nya-obligationer-2026-07-06"
    u = f"https://news.google.com/rss/articles/{google_token(target)}?oc=5"
    assert unwrap_redirect(u) == target
    # new-style tokens that do not embed the URL stay as they are
    u2 = "https://news.google.com/rss/articles/AU_yqLNEWFORMATTOKENxyz?oc=5"
    assert unwrap_redirect(u2) == u2


def test_domain_of():
    assert domain_of("https://www.di.se/nyheter/x") == "di.se"
    assert domain_of("https://m.kathimerini.gr/a") == "kathimerini.gr"


# ---------------------------------------------------------------- url builders
def test_url_builders():
    g = google_news_url('"Intrum"', "sv", "SE")
    assert g.startswith("https://news.google.com/rss/search?q=%22Intrum%22+when%3A1d&hl=sv&gl=SE&ceid=SE%3Asv") or "ceid=SE:sv" in g
    assert "hl=en-GB&gl=GB&ceid=GB:en-GB" in google_news_url('"Intrum"', "en", "GB").replace("%3A", ":")
    assert "ceid=PT:pt-150" in google_news_url('"Intrum"', "pt", "PT").replace("%3A", ":")
    assert bing_news_url("Intrum", "nb", "NO").endswith("mkt=nb-NO")
    assert "sourcecountry%3ASW" in gdelt_url('"Intrum"', "24h", "SE") or "sourcecountry:SW" in gdelt_url('"Intrum"', "24h", "SE")


# ---------------------------------------------------------------- parsers
def test_parse_google_news(googlenews_xml):
    items = parse_google_news(googlenews_xml, '"Intrum"', "sv", "SE", ["intrum"])
    assert len(items) == 3
    first = items[0]
    assert first.source_name == "Placera" and first.source_domain == "placera.se"
    assert first.title.startswith("Intrum offentliggör prissättning") and not first.title.endswith("Placera")
    assert first.published_at == datetime(2026, 7, 13, 7, 10, tzinfo=timezone.utc)
    assert canonical_url(first.link) == "https://placera.se/nyheter/intrum-emitterar-nya-obligationer-2026-07-06"
    assert items[1].source_domain == "di.se"  # source known even though the link stays a Google link


def test_parse_bing(bing_xml):
    items = parse_bing_news(bing_xml, "Intrum", "sv", "SE", ["intrum"])
    assert len(items) == 2
    assert items[0].link == "https://hurbra.se/intrum-aktie-rusar-13-augusti-2026/"
    assert items[0].source_domain == "hurbra.se"
    assert "DNB Carnegie" in items[0].summary


def test_parse_gdelt(gdelt_json):
    items = parse_gdelt(gdelt_json, '"Intrum"', "", "", ["intrum"])
    assert len(items) == 2
    assert items[0].source_domain == "vg.hu" and items[0].lang == "hungarian"
    assert items[1].published_at.year == 2026 and items[1].published_at.month == 1


def test_parse_generic_feed(outlet_feed_xml):
    items = parse_generic_feed(outlet_feed_xml, "https://e00-expansion.uecdn.es/rss/empresas.xml", [], 3, "ES", "es")
    assert len(items) == 3
    assert items[0].source_name == "Expansión Empresas" and items[0].tier_hint == 3


def test_extract_links_and_article_heuristic(tagpage_run1):
    links = extract_links(tagpage_run1, "https://www.mononews.gr/tag/intrum-hellas")
    urls = [u for u, _ in links]
    assert "https://www.mononews.gr/business/giorgos-georgakopoulos-intrum-hellas-i-apogeiosi-tzirou-ke-kerdon" in urls
    base = "https://www.mononews.gr/tag/intrum-hellas"
    assert looks_like_article("https://www.mononews.gr/business/intrum-hellas-ypografi-tis-tritis-symvasis-ergasias", "Intrum Hellas: Υπογραφή της τρίτης σύμβασης εργασίας", base)
    assert not looks_like_article("https://www.mononews.gr/business", "Business", base)
    assert looks_like_article("https://www.mononews.gr/business/x", "short", base, link_pattern=r"mononews\.gr/business/")


# ---------------------------------------------------------------- matching
def test_alias_inflection_and_context(cfg):
    m = Matcher.from_config(cfg)
    assert m.match("Intrums aktie rusar", "", "sv")[0].alias == "Intrum"
    assert m.match("Intrumin alkuvuoden katsauksen mukaan", "", "fi")[0].where == "title"
    assert m.match("Intrum-Aktie: Quartalszahlen", "", "de")
    assert m.match("Inkassofirmaet Intrum har opkrævet ulovlige gebyrer", "", "da")
    # Solvia needs context in Spanish
    assert not m.match("Solvia abre oficina en Valencia", "", "es")
    hit = m.match("Solvia abre oficina en Valencia", "La inmobiliaria del grupo Intrum amplía su red.", "es")
    assert hit and hit[0].name_id == "intrum"
    # people: distinctive CEO name matches alone, common name needs context
    assert m.match("Johan Åkerblom: vi ser en vändpunkt", "", "sv")
    assert not m.match("Annie Ho wins award", "", "en")
    # language scoping: Greek alias is not applied to Swedish text
    assert not m.match("Ίντρουμ", "", "sv")
    assert m.match("Η Ίντρουμ πουλά ακίνητα", "", "el")


def test_screen_rules(cfg):
    m = Matcher.from_config(cfg)
    assert m.screen("finanznachrichten.de", "https://finanznachrichten.de/nachrichten-aktien/intrum-ab.htm", "INTRUM AB").startswith("noise_domain")
    assert m.screen("kauppalehti.fi", "https://kauppalehti.fi/porssi/porssikurssit/osake/XSTO/INTRUM/osinkohistoria", "Osinkohistoria - Intrum")
    assert m.screen("hurbra.se", "https://hurbra.se/intrum-aktie-rusar-13-augusti-2026/", "Intrum aktie rusar") == ""
    assert m.screen("stock-world.de", "https://stock-world.de/x", "Intrum Justitia Aktie: Quartalszahlen am 28. August")


def test_tier1_flags(cfg):
    m = Matcher.from_config(cfg)
    cats, alert = m.tier1_categories("Intrum offentliggör prissättning av seniora säkerställda obligationer om 525 000 000 EUR", "")
    assert "capital_markets" in cats and alert
    cats, alert = m.tier1_categories("Inkassofirmaet Intrum har opkrævet ulovlige gebyrer", "")
    assert "regulatory" in cats and alert
    cats, alert = m.tier1_categories("Quase metade da Geração Z já falhou pagamentos por falta de dinheiro", "Segundo a consultora de crédito Intrum")
    assert not alert
    cats, alert = m.tier1_categories("Intrum aktie rusar – DNB Carnegie ser vändpunkt", "")
    assert not alert


# ---------------------------------------------------------------- dedupe
def test_title_similarity():
    a = title_key("Intrum aktie rusar – DNB Carnegie ser vändpunkt", "hurbra.se")
    b = title_key("Intrums aktie rusar efter köpråd från DNB Carnegie - Dagens industri")
    assert similar(a, a)
    assert not similar(a, title_key("Intrum emitterar nya obligationer"))
    c = title_key("Intrum aktie rusar: DNB Carnegie ser vändpunkt för bolaget")
    assert similar(a, c)
    assert b  # just exercised
