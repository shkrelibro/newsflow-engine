"""5 October 2026: the mechanisms added after the question "is there nothing new on any of the names".

Each test names the headline that was invisible that day and the mechanism that now shows it:
the engine-side keep from the domain screen, the watch flag, the overlay, the standfirst rule in
the brief, the shared lexicon, the sector entries, and the page watcher's headline fetch.
"""
from __future__ import annotations

import importlib.util
import inspect
from datetime import datetime, timezone
from pathlib import Path

import pytest

from newsflow import signal as S
from newsflow.config import Alias, NameConfig, _merge_overlay, load_config
from newsflow.match import Matcher
from newsflow.models import RawItem
from newsflow.pipeline import RunSummary, process_items
from newsflow.routes import enrich_generic_titles, generic_link_text, title_from_article
from newsflow.store import Store

REPO = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 5, 20, 30, tzinfo=timezone.utc)


def _brief():
    """scripts/build_brief.py by path: the repository root carries a stray older copy of the file."""
    spec = importlib.util.spec_from_file_location("build_brief_5oct", REPO / "scripts" / "build_brief.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ------------------------------------------------------------------ the shared lexicon
def test_the_brief_carries_an_identical_copy_of_the_signal_lexicon():
    """build_brief.py cannot import the package in the scheduled session, so it carries a copy.
    A copy that drifts is the bug the noise-domain list had; this is the guard."""
    bb = _brief()
    for name in ("CREDIT_SIGNAL", "STOCK_CHATTER", "BIG_MOVE", "HARD_EVENT", "_BARE_AMOUNT"):
        assert getattr(bb, name).pattern == getattr(S, name).pattern, name
        assert getattr(bb, name).flags == getattr(S, name).flags, name
    for fn in ("credit_signal", "hard_signal"):
        assert inspect.getsource(getattr(bb, fn)) == inspect.getsource(getattr(S, fn)), fn


@pytest.mark.parametrize("title,kept", [
    ("Branicks Group: Aktie stürzt um 20 Prozent", True),                       # the 5 Oct headline
    ("Branicks Group Aktie: Hauptversammlung berät Sanierungskonzept", True),    # event word beats the Aktie prefix
    ("Branicks Group Aktie: Aktionäre stimmen über Sanierungsplan ab", True),
    ("Branicks Group Shareholders Brace for October 9 Restructuring Vote as Losses Mount", True),
    ("Branicks Group Aktie: 12-Prozent-Sprung auf 0,6220 Euro", True),           # double digit, either way
    ("Branicks Group Aktie: S&P senkt Rating auf SD", True),
    ("Branicks: 6 Millionen Euro Liquidität", True),
    ("Südzucker stock fell 3.42 percent as earnings guidance rose", False),      # 3.42 is not 42
    ("Südzucker-Aktie: Lohnt sich der Einstieg jetzt?", False),
    ("Die TUI-Aktie reagiert nach starken Quartalszahlen und frischer Analystenbewertung", False),
    ("Lufthansa Aktie: Analysten sehen Kursziel bei 8 Euro", False),
    ("Skechers’ Sparkly $80 Sneakers with Memory Foam Are on Sale for $30", False),   # a price, not money
    ("Nike shares rise 2 percent on upgrade", False),
])
def test_hard_signal_policy(title, kept):
    assert bool(S.hard_signal(title)) is kept, (title, S.hard_signal(title))


def test_stems_match_the_forms_headlines_use():
    """The old list anchored "restructur" to a word boundary and never matched "restructuring"."""
    assert S.credit_signal("Branicks restructuring vote set for 17 October")
    assert S.credit_signal("Bondholders extend Branicks notes to end-2026")
    assert S.credit_signal("airBaltic announces collective redundancy procedure")


# ------------------------------------------------------------------ tier-1 categories
@pytest.fixture(scope="module")
def cfg_module():
    return load_config(REPO / "config")


@pytest.fixture(scope="module")
def cats(cfg_module):
    m = Matcher.from_config(cfg_module)
    return lambda t: m.tier1_categories(t, "")[0]


@pytest.mark.parametrize("title,category", [
    ("Branicks Group Aktie: Hauptversammlung berät Sanierungskonzept", "restructuring"),
    ("Branicks Group Aktie: Aktionäre stimmen über Sanierungsplan ab", "restructuring"),
    ("Bondholders extend Branicks notes to end-2026", "capital_markets"),
    ("Branicks Group: Aktie stürzt um 20 Prozent", "price_move"),
    ("Branicks Group Aktie: 12-Prozent-Sprung auf 0,6220 Euro", "price_move"),
    ("airBaltic announces collective redundancy procedure, hundreds of jobs to go", "workforce"),
    ("Lufthansa: Streik der Flugbegleiter am Montag", "workforce"),
    ("Air Baltic to slash hundreds of jobs under restructuring", "workforce"),
])
def test_new_terms_flag(cats, title, category):
    assert category in cats(title), f"missed {category}: {title} -> {cats(title)}"


@pytest.mark.parametrize("title", [
    "Südzucker stock fell 3.42 percent as earnings guidance rose",     # a decimal is not a double-digit move
    "adidas ( ETR : ADS ) Trading Down 0 . 9 % – Should You Sell ?",
])
def test_small_moves_are_not_price_moves(cats, title):
    assert "price_move" not in cats(title), cats(title)


# ------------------------------------------------------------------ the engine keeps tier-A churn rows
def _raw(i, title, nid, domain, lang="de", country="DE"):
    return RawItem(title=title, link=f"https://{domain}/{i}", route="googlenews", query=f'"{nid}"',
                   summary="", published_at=NOW, source_domain=domain, source_name=domain,
                   lang=lang, country=country, name_ids=[nid])


def test_domain_screen_keeps_tier_a_rows_with_a_signal_and_comps_never(cfg, tmp_path):
    """Branicks, 5 October: three churn-domain headlines were the only trace of a 20% fall and the
    Sanierungskonzept meeting, and the screen removed them before the export could see them."""
    store = Store(cfg.db_path)
    matcher = Matcher.from_config(cfg)
    assert cfg.name("branicks").watch and not cfg.name("sudzucker").watch
    items = [
        _raw(1, "Branicks Group: Aktie stürzt um 20 Prozent", "branicks", "boerse-express.com"),
        _raw(2, "Branicks Group Aktie: Hauptversammlung berät Sanierungskonzept", "branicks", "boerse-global.de"),
        _raw(3, "Branicks Group Aktie: Lohnt sich der Einstieg jetzt?", "branicks", "finanztrends.de"),   # watch: kept anyway
        _raw(4, "Branicks Group Aktie: Kursziel 0,50 Euro", "branicks", "finanztrends.de"),               # title screen: never
        _raw(5, "Südzucker-Aktie: Lohnt sich der Einstieg jetzt?", "sudzucker", "boerse.de"),             # chatter, no watch
        _raw(6, "Südzucker stock fell 3.42 percent as earnings guidance rose", "sudzucker", "ad-hoc-news.de", "en", "GB"),
        _raw(7, "Südzucker: Anleihe über 400 Millionen Euro platziert", "sudzucker", "ad-hoc-news.de"),   # signal: kept
        _raw(8, "Vonovia Aktie: Anleihe platziert", "vonovia", "finanztrends.de"),                        # comp: never
        _raw(9, "Branicks Group AG: Pflichtmitteilung", "branicks", "handelsblatt.com"),                  # clean domain
    ]
    s = RunSummary(run_id=1, started_at=NOW)
    run_id = store.start_run(NOW)
    process_items(cfg, store, matcher, None, items, run_id, NOW, s, lookback_hours=48)
    rows = {r["raw_link"].rsplit("/", 1)[1]: r for r in store.conn.execute(
        "SELECT raw_link, status, screen_reason, alert_candidate FROM items")}
    assert rows["1"]["status"] == "candidate" and rows["1"]["screen_reason"].startswith("kept:churn:boerse-express.com:")   # v2: churn sites carry their own reason
    assert rows["2"]["status"] == "candidate" and "restructuring" in rows["2"]["screen_reason"]
    assert rows["3"]["status"] == "candidate" and rows["3"]["screen_reason"].endswith(":watch")
    assert rows["4"]["status"] == "screened" and rows["4"]["screen_reason"].startswith("noise_title:")   # Kursziel: a title pattern, never kept, never an echo
    assert rows["5"]["status"] == "screened"
    assert rows["6"]["status"] == "screened"
    assert rows["7"]["status"] == "candidate" and rows["7"]["screen_reason"].startswith("kept:")
    assert rows["8"]["status"] == "screened"
    assert rows["9"]["status"] == "candidate" and rows["9"]["screen_reason"] == ""
    assert not any(rows[k]["alert_candidate"] for k in ("1", "2", "3", "7"))     # churn never alerts
    assert s.kept_from_screen == 4 and s.screened == 4
    store.close()


def test_export_carries_the_watch_flag_and_the_kept_count(cfg, tmp_path):
    from newsflow.export import build_export
    store = Store(cfg.db_path)
    matcher = Matcher.from_config(cfg)
    s = RunSummary(run_id=1, started_at=NOW)
    run_id = store.start_run(NOW)
    process_items(cfg, store, matcher, None, [
        _raw(1, "Branicks Group: Aktie stürzt um 20 Prozent", "branicks", "boerse-express.com"),
        _raw(2, "Branicks Group AG: Pflichtmitteilung", "branicks", "handelsblatt.com"),
    ], run_id, NOW, s, lookback_hours=48)
    out = build_export(cfg, store, NOW, 24)
    by_id = {n["id"]: n for n in out["names"]}
    b = by_id["branicks"]
    assert b["watch"] is True and by_id["sudzucker"]["watch"] is False
    assert b["candidate_count"] == 2 and b["kept_from_screen"] == 1
    kept = [c for c in b["candidates"] if c["primary"]["screen_reason"].startswith("kept:")]
    assert len(kept) == 1 and "20 Prozent" in kept[0]["primary"]["title"]
    store.close()


# ------------------------------------------------------------------ the overlay
def test_overlay_sets_watch_widens_and_adds_aliases_and_reports_unknown_ids():
    names = [
        NameConfig(id="sbb", name="SBB", aliases=[Alias(text="Sveafastigheter", langs=["sv"], weight=0.6)]),
        NameConfig(id="rossini", name="Rossini", aliases=[Alias(text="Recordati", search=True)]),
    ]
    unknown, counts = _merge_overlay(names, {"names": {
        "sbb": {"aliases": [{"text": "Sveafastigheter", "langs": ["en", "nb"]}]},
        "rossini": {"watch": True, "aliases": [{"text": "Respighi BidCo", "weight": 1.0}, {"text": "Recordati"}],
                    "exclude_terms": ["Gioachino"], "site_queries": ["recordati.com"]},
        "nosuchname": {"watch": True},
    }})
    assert unknown == ["nosuchname"]
    old_keys = ("watch", "aliases", "exclude_terms", "duplicates", "widened", "site_queries", "key_outlets")
    assert {k: counts[k] for k in old_keys} == {"watch": 1, "aliases": 1, "exclude_terms": 1, "duplicates": 1, "widened": 1, "site_queries": 1, "key_outlets": 0}
    assert counts["upgraded"] == counts["own_domains"] == counts["comps"] == 0   # 15 Oct keys, unused here
    sbb, ros = names
    assert ros.site_queries == ["recordati.com"]
    assert sbb.aliases[0].langs == ["sv", "en", "nb"] and len(sbb.aliases) == 1
    assert ros.watch and [a.text for a in ros.aliases] == ["Recordati", "Respighi BidCo"]
    assert ros.exclude_terms == ["Gioachino"]


def test_the_committed_overlay_names_real_ids_and_reaches_the_matcher(cfg_module):
    assert not cfg_module.overlay_unknown, cfg_module.overlay_unknown
    assert {n.id for n in cfg_module.names if n.watch} >= {"branicks", "airbaltic", "cheplapharm"}
    m = Matcher.from_config(cfg_module)
    got = {r.name_id: r for r in m.match("Respighi BidCo raises its stake ahead of the offer close", "", "en")}
    assert got["rossini"].alias == "Respighi BidCo"
    got = {r.name_id: r for r in m.match("Sveafastigheter and KlaraBo agree merger", "", "en")}
    assert "sbb" in got                                           # Swedish-only alias, widened


# ------------------------------------------------------------------ the brief
def _row(i, name, title, domain, cats=(), comp=False, where="title", screen_reason="", watch=False):
    return {"id": i, "nid": name.lower(), "name": name, "comp": comp, "title": title,
            "source": domain, "domain": domain, "country": "SE", "lang": "sv",
            "url": "https://x/" + str(i), "seen": "2026-10-05T11:41:00+00:00", "published": None,
            "where": where, "confidence": 0.7, "cats": list(cats), "sources": 1,
            "screen_reason": screen_reason, "watch": watch}


def test_brief_offers_tier_a_standfirst_rows_flagged_and_holds_comps():
    """Realtid 5 Oct: "KlaraBo är historia" named Sveafastigheter only in the standfirst; DI's
    "Pokerspel med 80 miljarder i potten" named Heimstaden the same way. Both were held."""
    bb = _brief()
    bb.JUNK_DOMAINS = {"ad-hoc-news.de"}
    rows = [
        _row(1, "SBB", "KlaraBo är historia – så ser börsens nya bostadsjätte ut", "realtid.se", where="summary"),
        _row(2, "Heimstaden", "Pokerspel med 80 miljarder i potten", "di.se", where="summary"),
        _row(3, "Heimstaden", "Berlinförslag kan drabba Heimstaden", "di.se"),
        _row(4, "Heimstaden", "Något annat helt", "di.se", where="none"),
        _row(5, "Balder", "Banken tar över Areims miljardfastighet i Malmö", "di.se", where="summary", comp=True),
        _row(6, "Balder", "Balder emitterar obligationer", "di.se", comp=True),
    ]
    P = bb.partition(rows)
    a = {r["id"]: r for r in P["A_rows"]}
    assert 1 in a and a[1]["standfirst"] is True
    assert 2 in a and a[2]["standfirst"] is True
    assert 3 in a and not a[3].get("standfirst")
    assert 4 not in a                                             # alias nowhere: still held
    assert P["A_standfirst"] == 2 and P["A_inherited"] == 1 and P["A_bearing"] == 3
    assert P["A_bearing"] + P["A_inherited"] == P["A"]
    assert P["A_clean"] + P["A_junk"] + P["A_disqualified"] == P["A_bearing"]
    c = {r["id"] for r in P["C_rows"]}
    assert c == {6} and P["C_standfirst"] == 1                    # comps stay on the headline test
    html = bb.item_html(a[1], {}, False)
    assert "name in standfirst only" in html


def test_brief_keeps_engine_kept_rows_big_moves_and_watched_names():
    bb = _brief()
    bb.JUNK_DOMAINS = {"boerse-express.com", "finanztrends.de"}
    rows = [
        _row(1, "Branicks", "Branicks Group: Aktie stürzt um 20 Prozent", "boerse-express.com",
             screen_reason="kept:noise_domain:boerse-express.com:price_move"),
        _row(2, "Branicks", "Branicks Group Aktie: Lohnt sich der Einstieg jetzt?", "finanztrends.de", watch=True),
        _row(3, "Südzucker", "Südzucker-Aktie: Lohnt sich der Einstieg jetzt?", "finanztrends.de"),
        _row(4, "Vonovia", "Vonovia Aktie: Anleihe platziert", "finanztrends.de", ["capital_markets"], comp=True),
    ]
    P = bb.partition(rows)
    a = {r["id"]: r for r in P["A_rows"]}
    assert 1 in a and a[1]["redater"] is True
    assert 2 in a and a[2]["redater"] is True                     # on watch: offered, flagged
    assert 3 not in a                                             # chatter on a name not on watch
    assert not P["C_rows"] and P["C_junk"] == 1
    assert P["A_redater_kept"] == 2 and P["A_junk"] == 1
    html = bb.item_html(a[2], {}, False)
    assert "on watch" in html and "re-dater domain" in html


def test_brief_categories_cover_the_new_tier1_categories():
    bb = _brief()
    for c in ("workforce", "price_move"):
        assert c in bb.CATEGORY_ORDER and c in bb.CATEGORY_LABEL


# ------------------------------------------------------------------ sector entries
@pytest.mark.parametrize("title,lang,sector", [
    ("Face au rhizopus, la course contre la montre", "fr", "sector_sugar_beet"),
    ("Zuckerrübenernte: Rübenkampagne startet mit schwachen Erträgen", "de", "sector_sugar_beet"),
    ("Sugar beet growers warned of yellows virus losses", "en", "sector_sugar_beet"),
    ("Banken tar över Areims miljardfastighet i Malmö", "sv", "sector_property_credit"),
    ("Volksentscheid zur Enteignung: Berliner Wohnungskonzerne unter Druck", "de", "sector_property_credit"),
    ("Drewry World Container Index falls 4% as transpacific rates slide", "en", "sector_container_freight"),
    ("Hormuz tanker transits fall to one a day, war risk cover withdrawn", "en", "sector_container_freight"),
    ("Jet fuel prices surge in Europe as refiners cut distillate output", "en", "sector_jet_fuel"),
    ("Lufthansa erhöht Kerosinzuschlag zum dritten Mal", "de", "sector_jet_fuel"),
    ("Antibiotic shortage: UK pharmacies report amoxicillin supply gaps", "en", "sector_antibiotics"),
    ("Antibiotika-Engpass: Hersteller warnen vor Lieferengpässen bei Penicillin", "de", "sector_antibiotics"),
])
def test_sector_entries_carry_the_sector_vocabulary(cfg_module, title, lang, sector):
    m = Matcher.from_config(cfg_module)
    assert sector in {r.name_id for r in m.match(title, "", lang)}, title


@pytest.mark.parametrize("title,lang", [
    ("Blood sugar diet recipe", "en"),
    ("Red Sea diving holidays booming", "en"),
    ("Enteignung von Bauernland für Autobahn", "de"),
    ("Heizöl wird teurer", "de"),
    ("Fastighetsmäklare ser fler visningar", "sv"),
])
def test_sector_entries_are_guarded(cfg_module, title, lang):
    m = Matcher.from_config(cfg_module)
    assert not {r.name_id for r in m.match(title, "", lang) if r.name_id.startswith("sector_")}, title


def test_sector_entries_are_comps_and_survive_regeneration(cfg_module):
    spec = importlib.util.spec_from_file_location("build_universe_5oct", REPO / "scripts" / "build_universe.py")
    bu = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bu)  # type: ignore[union-attr]
    for sid in ("sector_sugar_beet", "sector_antibiotics", "sector_container_freight",
                "sector_jet_fuel", "sector_property_credit"):
        assert cfg_module.name(sid).kind == "comp"
        assert sid in bu.HAND_MAINTAINED, sid


# ------------------------------------------------------------------ page watcher headlines
class _Http:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get_text(self, url, *, is_page=False, accept=""):
        self.calls.append(url)
        if url not in self.pages:
            raise RuntimeError("no such page")
        return self.pages[url]


def _page_item(title, link):
    return RawItem(title=title, link=link, route="page", query="https://newsroom", summary="",
                   published_at=None, source_name="watcher", source_domain="x", lang="en",
                   country="DE", name_ids=["cheplapharm"])


def test_button_text_headlines_are_replaced_by_the_article_headline():
    """Cheplapharm 5 Oct: the largest refinancing on the page, stored as "Read more chevron_right"."""
    assert generic_link_text("Read more chevron_right") and generic_link_text("Read more")
    assert generic_link_text("Mehr erfahren") and generic_link_text("Lire la suite") and generic_link_text("→")
    assert not generic_link_text("CHEPLAPHARM launches refinancing of its term loan")
    html = ('<html><head><title>News | Cheplapharm</title>'
            '<meta property="og:title" content="CHEPLAPHARM announces lender meetings for a new term loan facility"/>'
            '</head><body><h1>Other</h1></body></html>')
    assert title_from_article(html) == "CHEPLAPHARM announces lender meetings for a new term loan facility"
    assert title_from_article("<title>Beet contract agreed for 2027/28 crop - British Sugar</title>") == \
        "Beet contract agreed for 2027/28 crop"
    assert title_from_article("<title>Read more - Site</title><h1> NFU and British Sugar settle </h1>") == \
        "NFU and British Sugar settle"
    http = _Http({"https://x/a": html})
    items = [_page_item("Read more chevron_right", "https://x/a"),
             _page_item("A real headline about a refinancing", "https://x/b"),
             _page_item("Read more", "https://x/c.pdf"),
             _page_item("Read more", "https://x/d")]
    fetched = enrich_generic_titles(http, items, limit=5)
    assert fetched == 2 and http.calls == ["https://x/a", "https://x/d"]      # the PDF is left alone
    assert [i.title for i in items] == [
        "CHEPLAPHARM announces lender meetings for a new term loan facility",
        "A real headline about a refinancing", "Read more", "Read more"]        # a failed fetch keeps the text


def test_page_job_enriches_new_links(cfg, tmp_path):
    from newsflow.config import PageSource
    from newsflow.pipeline import _page_job
    listing = ('<html><body><a href="/en/press-news/news-detail/launch-press-release">Read more chevron_right</a>'
               '<a href="/en/press-news/news-detail/older-release">Read more chevron_right</a></body></html>')
    article = '<html><head><meta property="og:title" content="CHEPLAPHARM launches EUR 1.28bn term loan refinancing"/></head></html>'
    http = _Http({"https://www.cheplapharm.com/en/press-news/": listing,
                  "https://www.cheplapharm.com/en/press-news/news-detail/launch-press-release": article,
                  "https://www.cheplapharm.com/en/press-news/news-detail/older-release": "<html></html>"})
    page = PageSource(url="https://www.cheplapharm.com/en/press-news/", name="Cheplapharm press & news", tier=0,
                      country="DE", lang="en", name_ids=["cheplapharm"], link_pattern="press-news/news-detail/.+",
                      require_alias=False)
    store = Store(cfg.db_path)
    items, result = _page_job(http, store, page, backfill=True, seed_items=5)
    titles = sorted(i.title for i in items)
    assert titles == ["CHEPLAPHARM launches EUR 1.28bn term loan refinancing", "Read more chevron_right"]
    assert result.ok and result.items == 2
    store.close()


# ------------------------------------------------------------------ 6 Oct: German real estate sources
def test_a_watcher_with_no_names_is_matched_against_every_name(cfg, tmp_path):
    """Thomas Daily's morning-news archive lists the day's German property stories. Declared with
    names: [], its links are matched against every name's aliases like an outlet feed, so an Adler
    asset sale files under Adler and a Vonovia one under Vonovia, and a story naming nobody is dropped."""
    from newsflow.pipeline import _page_job
    page = next(p for n in cfg.names for p in n.pages if p.name == "Thomas Daily morning news")
    assert page.name_ids == [] and page.require_alias and page.tier == 4
    listing = ('<html><body>'
               '<a href="/td-morning-news/1719800-adler-group-verkauft-wohnportfolio-in-berlin">Adler Group verkauft Wohnportfolio in Berlin</a>'
               '<a href="/td-morning-news/1719801-vonovia-platziert-anleihe">Vonovia platziert Anleihe über 500 Mio. Euro</a>'
               '<a href="/td-morning-news/1719802-geodis-mietet-buero">Geodis mietet 2.700 m² Bürofläche in Hamburg</a>'
               '<a href="/service/td-morning-news/abonnieren/">Abonnieren</a>'
               '</body></html>')
    http = _Http({"https://www.thomas-daily.de/service/td-morning-news/": listing})
    store = Store(cfg.db_path)
    items, result = _page_job(http, store, page, backfill=True, seed_items=5)
    assert result.ok and len(items) == 3 and all(it.name_ids == [] for it in items)
    matcher = Matcher.from_config(cfg)
    s = RunSummary(run_id=1, started_at=NOW)
    run_id = store.start_run(NOW)
    process_items(cfg, store, matcher, http, items, run_id, NOW, s, lookback_hours=48)
    got = {r["name_id"]: r["title"] for r in store.conn.execute(
        "SELECT m.name_id, i.title FROM matches m JOIN items i ON i.id = m.item_id")}
    assert got.get("adler", "").startswith("Adler Group verkauft")
    assert got.get("vonovia", "").startswith("Vonovia platziert")
    assert s.unrelated == 1                                         # Geodis names nobody: dropped
    store.close()


def test_value_marktdaten_posts_file_under_the_sector_entry(cfg_module):
    page = next(p for n in cfg_module.names for p in n.pages if p.name == "Value Marktdaten news")
    assert page.name_ids == ["sector_property_credit"] and not page.require_alias and page.tier == 2
    import re
    assert re.search(page.link_pattern, "https://www.value-marktdaten.de/2026/07/13/immobilienmarktdaten-update-q2-2026/")
    assert not re.search(page.link_pattern, "https://www.value-marktdaten.de/aktuelles/")


def test_overlay_site_queries_and_notes_reach_the_german_real_estate_names(cfg_module):
    adler = cfg_module.name("adler")
    assert {"iz.de", "thomas-daily.de", "greenstreetnews.com", "tagesspiegel.de"} <= set(adler.site_queries)
    assert "wallstreet-online.de/diskussion/1220823-1" in adler.notes
    for nid in ("branicks", "demire", "vivion", "aroundtown"):
        assert "thomas-daily.de" in cfg_module.name(nid).site_queries, nid
    names = [n.name for n in cfg_module.outlets]
    assert "Tagesspiegel Wirtschaft" in names and "Tagesspiegel Berlin" in names


def test_config_loader_refuses_a_repeated_key(tmp_path):
    """PyYAML keeps the last of two identical keys and says nothing; on 6 October a second
    branicks block in overlay.yaml silently dropped the watch flag the first one set."""
    from newsflow.config import _load_yaml
    f = tmp_path / "overlay.yaml"
    f.write_text("names:\n  branicks:\n    watch: true\n  branicks:\n    site_queries: [iz.de]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate key 'branicks'"):
        _load_yaml(f)
    f.write_text("names:\n  branicks:\n    watch: true\n", encoding="utf-8")
    assert _load_yaml(f)["names"]["branicks"]["watch"] is True
