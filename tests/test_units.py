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
    total_site_queries = sum(len(x.site_queries) for x in cfg.names)
    assert site_total == total_site_queries           # one full cycle covers every site query exactly once (all names)
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

# ---------------------------------------------------------------- reliability fixes (23 Aug)
def test_regulatory_titles_are_never_screened(cfg):
    from newsflow.match import Matcher
    m = Matcher.from_config(cfg)
    # the Adler bug: an EQS release republished by a noise domain was swallowed
    assert m.screen("boerse.de", "https://boerse.de/x", "EQS-AFR: Adler Group S.A.: Vorabbekanntmachung") == ""
    assert m.screen("boerse.de", "https://boerse.de/x", "DGAP-News: irgendwas Wichtiges") == ""
    assert m.screen("boerse.de", "https://boerse.de/x", "Ad hoc: Anleihe gekündigt") == ""


def test_fallback_suppressed_for_cause(cfg, tmp_path):
    """The Evoca bug: a context-guarded alias rejected for cause must not come back
    as a 0.4 (query) candidate, and comps never get the fallback at all."""
    from datetime import datetime, timezone
    from newsflow.match import Matcher
    from newsflow.models import RawItem
    from newsflow.pipeline import RunSummary, process_items
    from newsflow.store import Store
    cfg.engine["db_path"] = str(tmp_path / "fb.db")
    store = Store(cfg.db_path)
    matcher = Matcher.from_config(cfg)
    now = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
    mk = lambda i, title, nids, summary="": RawItem(
        title=title, link=f"https://example.org/{i}", route="googlenews", query='"Evoca"',
        summary=summary, published_at=now, source_domain="example.org", lang="it", country="IT", name_ids=nids)
    s = RunSummary(run_id=1, started_at=now)
    items = [
        mk(1, "Il suono dei gong evoca lo spirito del patrimonio", ["evoca"]),       # verb, ctx absent -> rejected
        mk(2, "Evoca Group rifinanzia il debito", ["evoca"], "macchine da caffè"),   # real: ctx present
        mk(3, "Genoa-Napoli 0-2, decidono De Bruyne e Vergara", ["evoca"]),          # alias absent, ctx absent -> suppressed
        mk(4, "Mieterverein kritisiert Nebenkosten", ["vonovia"]),                   # comp -> never fallback
        mk(5, "Inkassobolaget pressas av nya regler", ["intrum"]),                   # tier-A, no ctx guard -> fallback stays
    ]
    run_id = store.start_run(now)
    process_items(cfg, store, matcher, None, items, run_id, now, s, lookback_hours=48)
    got = {r["name_id"]: (r["alias"], r["confidence"]) for r in store.conn.execute(
        "SELECT m.name_id, m.alias, m.confidence FROM matches m")}
    assert "evoca" in got and got["evoca"][0] == "Evoca"          # only the real item matched
    n_evoca = store.conn.execute("SELECT COUNT(*) FROM matches WHERE name_id='evoca'").fetchone()[0]
    assert n_evoca == 1
    assert "vonovia" not in got                                    # comp fallback suppressed
    assert got.get("intrum") == ("(query)", 0.4)                   # legitimate fallback preserved
    store.close()


def test_coverage_ledger(cfg, tmp_path):
    from datetime import datetime, timezone
    from newsflow.export import build_coverage
    from newsflow.models import SourceResult
    from newsflow.store import Store
    cfg.engine["db_path"] = str(tmp_path / "cov.db")
    store = Store(cfg.db_path)
    now = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
    run_id = store.start_run(now)
    store.add_source_results(run_id, [
        SourceResult("googlenews", '"Intrum" [sv-SE] home', True, 3, "", 1.0, ["intrum"]),
        SourceResult("googlenews", "group0 2 names", False, 0, "skipped: run time budget reached", 0.0, ["hse", "evoca"]),
        SourceResult("googlenews", "group1 1 names", False, 0, "HTTP 429", 0.0, ["evoca"]),
    ])
    cov = build_coverage(cfg, store, now)
    ns = cov["names"]
    assert ns["intrum"]["jobs_24h"] == 1 and ns["intrum"]["state"] in ("ok", "quiet")
    assert ns["hse"]["state"] == "STARVED"                          # only skipped jobs
    assert ns["evoca"]["state"] == "STARVED"                        # skipped + failed, none ok
    assert ns["adler"]["state"] == "NO_QUERIES"                     # nothing swept it in 24h
    assert "adler" in cov["flags"]["no_queries"] and "hse" in cov["flags"]["starved"]
    assert cov["summary"]["total"] == len(cfg.names)
    store.close()


# --- 6 Sep 2026: boels/lowell/paragon coverage-gap fix (issuer solo searches + official routes) ---

def _load_name(nid):
    from newsflow.config import load_config
    cfg = load_config()
    return next(n for n in cfg.names if n.id == nid)


def test_issuer_solo_searches_present():
    """The holdco/issuer legal names must be searched, not just matched (coverage gap of 5 Sep 2026)."""
    expectations = {
        "boels": "Boels Topholding",
        "lowell": "Garfunkelux Holdco",
        "paragon": "PCC Global",
    }
    for nid, issuer in expectations.items():
        n = _load_name(nid)
        searched = {a.text for a in n.aliases if getattr(a, "search", False)}
        assert issuer in searched, f"{nid}: '{issuer}' must carry search: true (was match-only)"


def test_official_site_routes_present():
    routes = {
        "boels": {"group.boels.com", "globenewswire.com"},
        "lowell": {"lowell.com", "tisegroup.com", "globenewswire.com"},
        "paragon": {"global.paragon.world", "tisegroup.com"},
    }
    for nid, doms in routes.items():
        n = _load_name(nid)
        assert doms.issubset(set(n.site_queries)), f"{nid}: site_queries missing {doms - set(n.site_queries)}"


def test_lowell_us_noise_excluded():
    """Hurricane Lowell / Lowell MA class must never reach the pile as Lowell (Garfunkelux) mentions."""
    n = _load_name("lowell")
    excl = " ".join(n.exclude_terms)
    for term in ("Hurricane Lowell", "UMass Lowell", "Lowell Observatory"):
        assert term in excl, f"lowell: exclude_terms missing '{term}'"


# ---------------------------------------------------------------------------
# Feed discovery. The outlets that discovery kept missing are the ones that matter:
# Boersen-Zeitung, WirtschaftsWoche, NZZ, Milano Finanza, Kauppalehti, Puls Biznesu,
# De Tijd, Kathimerini. They do not declare <link rel=alternate>; they link an RSS
# page from the footer, and their homepage is sometimes a section URL.
# ---------------------------------------------------------------------------

FEED_XML = """<?xml version="1.0"?><rss version="2.0"><channel><title>Wirtschaft</title>
<item><title>Cheplapharm platziert neue Anleihe</title><link>https://ex.invalid/a1</link>
<pubDate>Mon, 08 Sep 2026 08:00:00 GMT</pubDate></item></channel></rss>"""


def test_discover_follows_a_footer_link_to_a_feed_index(fake_http_factory):
    from newsflow.routes import discover_feed

    homepage = "https://www.boersen-zeitung.de"
    http = fake_http_factory({
        "boersen-zeitung.de/rss/wirtschaft.xml": FEED_XML,
        "boersen-zeitung.de/rss": '<html><body><a href="/rss/wirtschaft.xml">Wirtschaft RSS</a></body></html>',
        "boersen-zeitung.de": '<html><body><footer><a href="/rss">RSS-Feeds</a></footer></body></html>',
    })
    url, reason = discover_feed(http, homepage)
    assert url == "https://www.boersen-zeitung.de/rss/wirtschaft.xml"
    assert reason == ""


def test_discover_tries_the_site_root_for_a_section_homepage(fake_http_factory):
    """news.sky.com/business + '/rss' is not a feed; the root has to be tried too."""
    from newsflow.routes import discover_feed

    http = fake_http_factory({
        "news.sky.com/feeds/rss": FEED_XML,
        "news.sky.com": "<html><body>no feed declared here</body></html>",
    })
    url, _ = discover_feed(http, "https://news.sky.com/business")
    assert url == "https://news.sky.com/feeds/rss"


def test_discover_reports_why_it_failed(fake_http_factory):
    from newsflow.routes import discover_feed

    http = fake_http_factory({"example.invalid": "<html><body>nothing here at all</body></html>"})
    url, reason = discover_feed(http, "https://example.invalid")
    assert url == ""
    assert "candidates tried" in reason          # diagnosable, not a silent blank


def test_discover_prefers_a_declared_feed_link(fake_http_factory):
    from newsflow.routes import discover_feed

    http = fake_http_factory({
        "site.invalid/declared.xml": FEED_XML,
        "site.invalid": '<html><head><link rel="alternate" type="application/rss+xml" href="/declared.xml"></head></html>',
    })
    url, _ = discover_feed(http, "https://site.invalid")
    assert url == "https://site.invalid/declared.xml"


# ------------------------------------------------------------ database choice
def test_db_generation_ranks_copies_and_survives_a_corrupt_file(tmp_path):
    """pick-db has to be able to tell a good database from a stale one and from rubbish."""
    from newsflow.store import Store, db_generation

    fresh = tmp_path / "fresh.db"
    stale = tmp_path / "stale.db"
    for path, runs in ((fresh, 5), (stale, 2)):
        s = Store(str(path))
        for _ in range(runs):
            s.start_run(datetime(2026, 9, 1, tzinfo=timezone.utc))
        s.close()

    assert db_generation(fresh)[0] == 5
    assert db_generation(stale)[0] == 2
    assert db_generation(fresh) > db_generation(stale)

    missing = tmp_path / "nope.db"
    assert db_generation(missing) == (-1, -1)

    empty = tmp_path / "empty.db"
    empty.write_bytes(b"")
    assert db_generation(empty) == (-1, -1)

    junk = tmp_path / "junk.db"
    junk.write_bytes(b"this is not a database" * 100)
    assert db_generation(junk) == (-1, -1)              # rubbish always loses


def test_pick_db_keeps_the_committed_backup_when_the_cache_is_older(tmp_path):
    """The failure this exists to prevent: a stale cache restore overwriting a good backup."""
    from newsflow.cli import main
    from newsflow.store import Store, db_generation

    target = tmp_path / "data" / "newsflow.db"
    cache = tmp_path / "data" / "cache" / "newsflow.db"
    for path, runs in ((target, 900), (cache, 400)):
        path.parent.mkdir(parents=True, exist_ok=True)
        s = Store(str(path))
        for _ in range(runs):
            s.start_run(datetime(2026, 9, 1, tzinfo=timezone.utc))
        s.close()

    assert main(["pick-db", "--candidate", str(cache), "--target", str(target)]) == 0
    assert db_generation(target)[0] == 900             # the backup was kept

    # and the other way round: a current cache replaces an old backup
    s = Store(str(cache))
    for _ in range(1000):
        s.start_run(datetime(2026, 9, 2, tzinfo=timezone.utc))
    s.close()
    assert main(["pick-db", "--candidate", str(cache), "--target", str(target)]) == 0
    assert db_generation(target)[0] == 1400


def test_pick_db_reads_a_gzipped_backup(tmp_path):
    """The committed backup is gzipped, because the plain file passed GitHub's 100MB limit."""
    import gzip
    import shutil as _sh

    from newsflow.cli import main
    from newsflow.store import Store, db_generation

    plain = tmp_path / "backup.db"
    s = Store(str(plain))
    for _ in range(900):
        s.start_run(datetime(2026, 9, 1, tzinfo=timezone.utc))
    s.close()

    packed = tmp_path / "backup.db.gz"
    with open(plain, "rb") as src, gzip.open(packed, "wb") as dst:
        _sh.copyfileobj(src, dst)
    plain.unlink()

    assert db_generation(packed) == (900, 0)           # counted without unpacking by hand

    target = tmp_path / "data" / "newsflow.db"         # nothing came back from the cache
    assert main(["pick-db", "--candidate", str(packed), "--target", str(target)]) == 0
    assert db_generation(target)[0] == 900             # and it is a usable plain database
    assert target.read_bytes()[:15] == b"SQLite format 3"

    # a corrupt archive must lose rather than crash
    bad = tmp_path / "bad.db.gz"
    bad.write_bytes(b"not gzip at all")
    assert db_generation(bad) == (-1, -1)


# ------------------------------------------------------ require_context boundaries
def test_require_context_matches_words_not_substrings(cfg):
    """The guard is only a guard if its terms have to appear as words.

    Quick's context list names its owner, HIG. Substring matching made "hig" satisfy it inside
    "Michigan", so a French burger chain collected American college football for months.
    """
    from newsflow.match import Matcher
    m = Matcher.from_config(cfg)

    def hit(title, lang="en"):
        return bool(m.match(title, "", lang, only=["quick"]))

    assert not hit("Three Quick Takeaways From No. 11 Oklahoma's Loss to Michigan")
    assert not hit("Couch: 3 quick takes on Michigan State football's 35-7 win")
    assert not hit("High school volleyball: Quick adjustments help Houston top Riverside")
    assert hit("Quick ouvre 20 nouveaux restaurants en France", "fr")      # plural of a term
    assert hit("Quick, l'enseigne de restauration rapide, cède 30 franchises", "fr")


def test_require_context_allows_a_short_inflection(cfg):
    """"store" must satisfy "stores"; the allowance is on the right only."""
    from newsflow.match import Matcher
    m = Matcher.from_config(cfg)
    assert m.match("Boots to close 300 stores, says the retailer", "", "en", only=["boots"])
    # Nottingham must not be satisfied by Nottinghamshire: five characters, past the allowance
    assert not m.match("Nottinghamshire's champions honoured at Boots and Beret 2026 Awards",
                       "", "en", only=["boots"])


def test_common_noun_aliases_are_guarded(cfg):
    """Enterprise and Carnival between them produced 85 clusters in a single day."""
    from newsflow.match import Matcher
    m = Matcher.from_config(cfg)
    assert not m.match("Auburn @ Enterprise | 2026 Week 3", "", "en", only=["enterprise"])
    assert not m.match("RF destroyed an enterprise in Rivne region", "", "en", only=["enterprise"])
    assert m.match("Enterprise Holdings expands its rental car fleet", "", "en", only=["enterprise"])
    assert not m.match("Colyton carnival procession set to light up town streets", "", "en", only=["carnival"])
    assert m.match("Carnival Corporation lifts cruise bookings guidance", "", "en", only=["carnival"])


def test_tui_does_not_inflect_into_the_dutch_word_for_garden(cfg):
    """TUI is three letters; inflection turned "tuin" into a coverage name."""
    from newsflow.match import Matcher
    m = Matcher.from_config(cfg)
    assert not m.match("Ligt je tuin er treurig bij? Zo laat je gras en planten herleven",
                       "", "nl", only=["tuigroup"])
    assert m.match("TUI verkauft 80 Prozent Eigenprodukte", "", "de", only=["tuigroup"])


# ---------------------------------------------------------------- adaptive backoff
def test_rate_limiter_widens_after_a_refusal_and_earns_it_back():
    """A run fires ~100 GDELT jobs; without memory each one rediscovers the rate limit."""
    from newsflow.http import RateLimiter
    rl = RateLimiter(default_seconds=1.0, per_host={"api.gdeltproject.org": 6.0},
                     max_penalty_seconds=60.0)
    h = "api.gdeltproject.org"
    assert rl.penalty(h) == 0.0

    assert rl.penalise(h) == 6.0            # starts at the host's own base spacing
    assert rl.penalise(h) == 12.0           # then doubles
    assert rl.penalise(h) == 24.0
    assert rl.penalise(h) == 48.0
    assert rl.penalise(h) == 60.0           # capped: one bad upstream cannot stall the run
    assert rl.penalise(h) == 60.0

    rl.relax(h); assert rl.penalty(h) == 30.0
    rl.relax(h); assert rl.penalty(h) == 15.0
    for _ in range(8):
        rl.relax(h)
    assert rl.penalty(h) == 0.0             # fully recovered, back to base spacing

    other = RateLimiter(default_seconds=1.0)
    other.penalise("a.example")
    assert other.penalty("b.example") == 0.0   # the penalty is per host, not global


def test_rate_limiter_honours_retry_after():
    from newsflow.http import RateLimiter
    rl = RateLimiter(default_seconds=1.0, max_penalty_seconds=60.0)
    assert rl.penalise("h", 30.0) == 30.0      # the server's number wins over doubling
    assert rl.penalise("h", 5.0) == 30.0       # and a smaller hint never narrows an open penalty
    assert rl.penalise("h", 90.0) == 60.0      # still capped


def test_retry_after_header_parsing():
    """Retry-After is either seconds or an HTTP date; both appear in the wild."""
    from email.utils import format_datetime
    from datetime import datetime, timedelta, timezone

    import httpx

    from newsflow.http import retry_after_seconds

    def resp(value=None):
        headers = {"Retry-After": value} if value is not None else {}
        return httpx.Response(429, headers=headers)

    assert retry_after_seconds(resp()) is None
    assert retry_after_seconds(resp("12")) == 12.0
    assert retry_after_seconds(resp("not a number")) is None
    future = datetime.now(timezone.utc) + timedelta(seconds=40)
    got = retry_after_seconds(resp(format_datetime(future)))
    assert got is not None and 30 <= got <= 45
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    assert retry_after_seconds(resp(format_datetime(past))) == 0.0


def test_brief_reads_the_domain_screen_from_config(tmp_path):
    """One source of truth. The hardcoded copy drifted within a day of being written."""
    import sys
    sys.path.insert(0, "scripts")
    import build_brief as bb

    f = tmp_path / "noise.yaml"
    f.write_text(
        "# comment\n"
        "domains:\n"
        "  - ad-hoc-news.de\n"
        "  - boerse-express.com     # note .com, not .de\n"
        "  - kauppalehti.fi/porssi\n"
        "\n"
        "title_patterns:\n"
        "  - \"Stock Price\"\n",
        encoding="utf-8")
    got = bb.load_junk(f)
    assert "boerse-express.com" in got          # the one-letter gap that let Branicks through
    assert "kauppalehti.fi/porssi" in got       # path rules survive the parse
    assert "Stock Price" not in got             # title patterns are not domains
    assert set(bb.FALLBACK_JUNK) <= got         # the fallback is a floor, never a ceiling

    missing = bb.load_junk(tmp_path / "absent.yaml")
    assert missing == set(bb.FALLBACK_JUNK)     # a missing file must not empty the screen
    assert bb.load_junk(None) == set(bb.FALLBACK_JUNK)
