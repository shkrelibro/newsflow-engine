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


# ---------------------------------------------------------------- config robustness
def test_norway_is_not_false(cfg):
    n = cfg.name("intrum")
    assert any(m.country == "NO" and m.lang == "nb" for m in n.markets)
    assert not any(m.country in ("FALSE", "TRUE") for m in n.markets)
    assert any(o.country == "NO" for o in cfg.outlets)
    assert any(p.country == "NO" for p in n.pages)


def test_every_market_has_local_language_query(cfg):
    n = cfg.name("intrum")
    countries = {m.country for m in n.markets}
    for c in ["AT", "BE", "CZ", "DK", "FI", "FR", "DE", "GR", "HU", "IT", "NL", "NO", "PL", "PT", "SK", "ES", "SE", "CH", "GB", "IE"]:
        assert c in countries, c
    english_only = {m.country for m in n.markets if m.lang == "en"}
    assert english_only == {"GB", "IE"}


def test_site_queries_are_staggered(cfg, tmp_path):
    from newsflow.pipeline import build_jobs
    from newsflow.store import Store
    from newsflow.http import Http
    cfg.engine["db_path"] = str(tmp_path / "x.db")
    store = Store(cfg.db_path)
    http = Http("test")
    cfg.routes["bingnews"] = {"enabled": False}
    cfg.routes["gdelt"] = {"enabled": False}
    cfg.routes["rss"] = {"enabled": False}
    cfg.routes["pages"] = {"enabled": False}
    n = cfg.name("intrum")
    every = int(cfg.routes["googlenews"]["site_every_n_runs"])
    per_run = []
    site_total = 0
    for r in range(1, every + 1):
        jobs = build_jobs(cfg, http, store, run_number=r)
        labels = [j.label for j in jobs]
        site_total += sum(1 for l in labels if "site:" in l and "reg " not in l)
        # every run sweeps every market at least once via the grouped queries
        assert sum(1 for l in labels if l.startswith("group")) >= len(n.markets)
        per_run.append(len(jobs))
    assert site_total == len(n.site_queries)          # one full cycle covers every site query exactly once
    assert max(per_run) - min(per_run) < len(n.site_queries)  # no burst run
    forced = build_jobs(cfg, http, store, run_number=1, all_routes=True)
    assert sum(1 for j in forced if "site:" in j.label) >= len(n.site_queries) // every
    http.close(); store.close()


def test_time_budget_skips_remaining_jobs(cfg, tmp_path, fake_http_factory, googlenews_xml):
    import time
    from newsflow.pipeline import JobSpec, run_once
    from newsflow.routes import fetch_google_news
    from newsflow.store import Store
    cfg.engine["db_path"] = str(tmp_path / "b.db")
    http = fake_http_factory({"news.google.com": googlenews_xml})
    store = Store(cfg.db_path)

    def slow():
        time.sleep(0.3)
        return fetch_google_news(http, '"Intrum"', "sv", "SE", ["intrum"])

    specs = [JobSpec(f"slow-{i}", "googlenews", slow) for i in range(12)]
    s = run_once(cfg, store, http=http, backfill_days=60, jobs=specs, budget_minutes=0.0)
    assert s.skipped > 0 and s.skipped + (len(specs) - s.skipped) == len(specs)
    assert any("skipped: run time budget" in r.error for r in s.source_results)
    assert s.new_items >= 1            # whatever finished was still stored
    store.close()

def test_solo_jobs_carry_their_own_name_ids(cfg, tmp_path, monkeypatch):
    """Regression: solo Google jobs used to close over the loop variable `ids`, so every
    solo query executed with the LAST name's id (zooplus) — misattributing ~1000 items."""
    import re
    import newsflow.pipeline as pl
    from newsflow.store import Store
    from newsflow.models import SourceResult
    cfg.engine["db_path"] = str(tmp_path / "solo.db")
    store = Store(cfg.db_path)
    calls = []

    def rec(http, q, lang, country, ids, when=None):
        calls.append((q, tuple(ids)))
        return [], SourceResult("googlenews", q, True, 0, "", 0.0)

    monkeypatch.setattr(pl, "fetch_google_news", rec)
    jobs = pl.build_jobs(cfg, http=None, store=store, run_number=1, all_routes=True)
    for j in jobs:
        if j.route == "googlenews":
            j.fn()
    assert calls, "no google jobs executed"

    # ownership map: alias text -> ids of names that declare it
    owners: dict[str, set[str]] = {}
    for n in cfg.names:
        for a in n.aliases:
            owners.setdefault(a.text, set()).add(n.id)

    solo = re.compile(r'^"([^"]+)"(?: site:\S+)?$')
    checked = 0
    for q, ids in calls:
        m = solo.match(q)
        if not m or m.group(1) not in owners:
            continue
        checked += 1
        assert len(ids) == 1, (q, ids)
        assert ids[0] in owners[m.group(1)], f"query {q!r} attributed to {ids[0]!r}, owner is {owners[m.group(1)]!r}"
    assert checked > 100                                # the whole universe of solo queries was exercised
    attributed = {ids[0] for q, ids in calls if solo.match(q) and len(ids) == 1}
    assert len(attributed) > 50                         # spread across many names, not collapsed onto one
    store.close()


def test_job_order_rotates_per_run(cfg, tmp_path):
    from newsflow.pipeline import build_jobs
    from newsflow.store import Store
    from newsflow.http import Http
    cfg.engine["db_path"] = str(tmp_path / "rot.db")
    store = Store(cfg.db_path)
    http = Http("test")
    a1 = [j.label for j in build_jobs(cfg, http, store, run_number=1, all_routes=True)]
    a2 = [j.label for j in build_jobs(cfg, http, store, run_number=1, all_routes=True)]
    b = [j.label for j in build_jobs(cfg, http, store, run_number=2, all_routes=True)]
    assert a1 == a2                       # deterministic for a given run number
    assert sorted(a1) == sorted(b)        # same job set under force
    assert a1 != b                        # ...but a different order, so the budget tail rotates
    # routes are interleaved, not all-solos-first: googlenews must not fill the entire first half
    half = a1[: len(a1) // 2]
    non_google = sum(1 for l in half if l.startswith(("bing", "gdelt")) or "(" in l and l.endswith(")"))
    assert non_google > 0
    http.close(); store.close()
