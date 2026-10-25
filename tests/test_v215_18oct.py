"""v2.15 (18 October 2026): old stories under new dates, the Galician town of Tui, Synnovis, and the
two evening cuts.

On the weekend of 17 and 18 October three airBaltic lead items were old articles that Google News
had stamped with the day it found them: Latvian Public Media's English desk on 1 April (the EUR 30m
loan) and 6 July (the prime minister weighing investors), and a liepajniekiem.lv copy of the
25 August LETA wire on the EUR 18m loan extension. Orange Actualites re-dated a June clip of Olivier
Bertrand. Of 55 TUI clusters, 37 were the town of Tui, the TUI Palma Marathon or video spam.
"""
from __future__ import annotations

import importlib.util
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from newsflow.match import Matcher
from newsflow.models import Item
from newsflow.normalize import title_trailing_date, url_date
from newsflow.pipeline import _TitleHistory, _redated
from newsflow.store import Store

REPO = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc)


def _brief():
    spec = importlib.util.spec_from_file_location("build_brief_18oct", REPO / "scripts" / "build_brief.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ------------------------------------------------------------------ dates in URLs and headlines
@pytest.mark.parametrize("url,expected", [
    ("https://eng.lsm.lv/article/economy/transport/06.07.2026-latvian-pm-kulbergs-ponders-potential-airbaltic-investors-again.a653977/",
     date(2026, 7, 6)),
    ("https://www.lsm.lv/raksts/zinas/ekonomika/25.08.2026-valdiba-atbalsta-airbaltic.a660301/", date(2026, 8, 25)),
    ("https://upinthesky.nl/2026/10/11/datalek-bij-tui-volledige-namen", date(2026, 10, 11)),
    ("https://elespanol.com/invertia/empresas/turismo/20261018/lufthansa-copia/1003744422402_0.html", date(2026, 10, 18)),
    ("https://example.com/news/2026-10-16-story", date(2026, 10, 16)),
    ("https://example.com/a/1003744422402", None),          # an article id is not a date
    ("https://example.com/a/20261399/x", None),            # month 13
    ("https://www.placera.se/nyheter/intrum-emitterar-nya-obligationer", None),
])
def test_url_date_reads_the_full_date_in_the_path(url, expected):
    assert url_date(url) == expected


def test_title_trailing_date_reads_the_broadcast_date_orange_appends():
    pub = datetime(2026, 10, 16, 23, 48, tzinfo=timezone.utc)
    assert title_trailing_date('Olivier Bertrand (Groupe Bertrand) : "Nous allons terminer l\'annee dans le rouge" - 03/06', pub) == date(2026, 6, 3)
    assert title_trailing_date("Le regard de Challenges: Conforama a jete l'eponge dans la bataille pour Darty - 27/04", pub) == date(2026, 4, 27)
    # a later day and month than the claimed date belongs to the previous year
    assert title_trailing_date("Bilan - 20/12", pub) == date(2025, 12, 20)
    assert title_trailing_date("Lille 2 - 1", pub) is None
    assert title_trailing_date("Score - 2/1", pub) is None


def test_redated_compares_the_path_and_the_headline_with_the_claimed_date():
    cutoff = NOW - timedelta(days=3)
    claimed = datetime(2026, 10, 18, 7, 4, tzinfo=timezone.utc)
    lsm = "https://eng.lsm.lv/article/economy/transport/01.04.2026-silina-airbaltic-30-million-euro-loan-is-short-term-solution.a641351/"
    assert _redated(lsm, "Silina: airBaltic 30 million euro loan is 'short term solution' / Article", claimed, cutoff)
    assert _redated("https://news.google.com/rss/articles/CBMi", "Olivier Bertrand : dans le rouge - 03/06", claimed, cutoff)
    # same day, or within the gap: news
    assert not _redated("https://example.com/2026/10/17/story", "A story", claimed, cutoff)
    assert not _redated("https://example.com/2026/10/08/story", "A story", claimed, cutoff)
    assert not _redated("https://news.google.com/rss/articles/CBMi", "Branicks: Glaeubiger stimmen ab", claimed, cutoff)
    # undated item: compared with the lookback cutoff
    assert _redated("https://example.com/2026/09/01/story", "A story", None, cutoff)
    assert not _redated("https://example.com/2026/10/17/story", "A story", None, cutoff)


# ------------------------------------------------------------------ republished headlines
def _item(title: str, seen: datetime, url: str) -> Item:
    return Item(id=None, canonical_url=url, raw_link=url, title=title, summary="", source_name="LSM",
                source_domain="lsm.lv", published_at=seen, first_seen_at=seen, lang="lv", country="LV",
                route="rss", query="", tier_hint=3, status="candidate")


def test_a_headline_the_store_saw_weeks_ago_is_a_republication(tmp_path):
    from newsflow.dedupe import title_key
    store = Store(tmp_path / "h.db")
    run = store.start_run(NOW - timedelta(days=60))
    old = "Valdība atbalsta airBaltic atlikušā valsts aizdevuma 18 miljonu eiro apmērā maksājuma termiņa pagarināšanu līdz gada beigām"
    store.insert_item(_item(old, NOW - timedelta(days=54), "https://www.lsm.lv/a"), title_key(old), run)
    recent = "Recordati, le adesioni salgono al 50,52% per OPA Respighi BidCo oggi"
    store.insert_item(_item(recent, NOW - timedelta(days=5), "https://x/b"), title_key(recent), run)
    store.commit()
    h = _TitleHistory(store, NOW)
    # the liepajniekiem.lv copy of 16 October, with its curly quotes
    copy = "Valdība atbalsta “airBaltic” atlikušā valsts aizdevuma 18 miljonu eiro apmērā maksājuma termiņa pagarināšanu līdz gada beigām"
    assert h.first_seen(title_key(copy)) == (NOW - timedelta(days=54)).isoformat()[:10]
    # a new count is news even when every word matches: the numbers must agree
    assert h.first_seen(title_key("Recordati, le adesioni salgono al 50,63% per OPA Respighi BidCo oggi")) == ""
    # a different story about the same loan is not a republication
    assert h.first_seen(title_key("airBaltic valsts aizdevums: Saeima lems par 18 miljonu eiro atmaksu nākamnedēļ")) == ""
    store.close()


def test_the_history_ignores_what_the_cluster_window_already_handles(tmp_path):
    from newsflow.dedupe import title_key
    store = Store(tmp_path / "w.db")
    run = store.start_run(NOW - timedelta(days=1))
    t = "Branicks Group Aktie: Gläubiger stimmen über Zukunft der 400-Millionen-Anleihe ab"
    store.insert_item(_item(t, NOW - timedelta(hours=20), "https://x/c"), title_key(t), run)
    store.commit()
    assert _TitleHistory(store, NOW).first_seen(title_key(t)) == ""   # under 72 hours old: a duplicate, not a re-date
    store.close()


# ------------------------------------------------------------------ the renderer
def test_rows_from_verify_date_outlets_need_a_verified_date_to_publish():
    bb = _brief()
    row = {"id": 85991, "nid": "airbaltic", "cats": [], "name": "airBaltic", "domain": "eng.lsm.lv", "source": "Latvian Public Media",
           "route": "googlenews", "url": "https://news.google.com/rss/articles/CBMixwFB",
           "title": "Latvian PM Kulbergs ponders potential airBaltic investors again / Article",
           "published": "2026-10-18T10:29:13+00:00", "seen": "2026-10-18T12:42:00+00:00"}
    assert bb.needs_date_check(row)
    assert not bb.needs_date_check({**row, "url": "https://eng.lsm.lv/article/x", "route": "rss"})
    assert not bb.needs_date_check({**row, "domain": "lsm.lv.example.com"})
    P = {"A_rows": [dict(row)], "C_rows": []}
    bb.grade_rows(P)
    r = P["A_rows"][0]
    assert r["verify_date"] and r["redater"]
    j = {"lead": ["85991"], "so": {"85991": "The prime minister is weighing airBaltic investors, which bears on the EUR 257m bridge (single source)."},
         "tags": {"85991": ["single source"]}}
    errors, _ = bb.lint(j, [], {"85991": "airbaltic"}, rows_by_id={"85991": r}, now=NOW)
    assert any("date verified" in e for e in errors)
    j["tags"]["85991"].append("date verified")
    errors, _ = bb.lint(j, [], {"85991": "airbaltic"}, rows_by_id={"85991": r}, now=NOW)
    assert not any("date verified" in e for e in errors)


def test_the_noise_file_lists_the_verify_date_outlets():
    bb = _brief()
    doms = bb.load_verify_date(REPO / "config" / "noise.yaml")
    assert {"eng.lsm.lv", "liepajniekiem.lv", "actu.orange.fr"} <= doms


def test_a_re_dated_clip_is_disqualified_before_judgement():
    bb = _brief()
    clip = {"title": 'Olivier Bertrand (Groupe Bertrand) : "Nous allons terminer l\'annee dans le rouge" - 03/06',
            "published": "2026-10-16T23:48:00+00:00"}
    assert bb.redated_headline(clip).startswith("re-dated clip")
    assert bb.redated_headline({"title": "Qui se cache derriere le Groupe Bertrand ?", "published": "2026-10-18T16:27:00+00:00"}) == ""
    assert bb.redated_headline({"title": "Bilan de la semaine - 16/10", "published": "2026-10-16T18:00:00+00:00"}) == ""


def test_the_brief_reads_the_full_url_date_when_it_resolves_links():
    bb = _brief()
    assert bb.url_ymd("https://eng.lsm.lv/article/economy/transport/06.07.2026-x.a653977/").date() == date(2026, 7, 6)
    assert bb.url_ymd("https://example.com/a/1003744422402") is None


def test_the_evening_cuts_are_slots():
    bb = _brief()
    assert (17, 45) in bb.SLOTS_UTC and (19, 45) in bb.SLOTS_UTC
    # the 20:45 London cut (19:45Z in summer) reaches back to the 18:45 one
    assert bb.previous_cut(datetime(2026, 10, 18, 19, 50, tzinfo=timezone.utc)) == datetime(2026, 10, 18, 17, 45, tzinfo=timezone.utc)
    # the first cut of the next morning reaches back to the last evening cut
    assert bb.previous_cut(datetime(2026, 10, 19, 5, 50, tzinfo=timezone.utc)) == datetime(2026, 10, 19, 4, 45, tzinfo=timezone.utc)
    assert bb.previous_cut(datetime(2026, 10, 19, 4, 50, tzinfo=timezone.utc)) == datetime(2026, 10, 16, 19, 45, tzinfo=timezone.utc)


# ------------------------------------------------------------------ TUI and Synnovis
@pytest.mark.parametrize("title,hit", [
    ("La literatura fantástica toma la Praza da Inmaculada de Tui con la TuidCon HispaCon", False),
    ("Tui completa el Paseo da Corredoira con 550.000 euros de la Deputación", False),
    ("La alemana Lütters y el español Severino López, ganadores del TUI Palma Marathon Mallorca 2026", False),
    ("Tui licita la creación de un nuevo parque infantil en el centro por 166.000 euros", False),
    ("TUI-Österreich-Chef Gottfried Math sieht Vorteile bei früher Buchung", True),
    ("Tui urges families to book early for summer 2028 as 1m free child places released", True),
    ("Datalek bij TUI: volledige namen en reisdata van klanten gelekt", True),
    ("CCOO denuncia presiones laborales y riesgos para la salud mental de los conductores de TUI en Mallorca", True),
])
def test_tui_keeps_the_group_and_drops_the_town_and_the_marathon(cfg, title, hit):
    m = Matcher.from_config(cfg) if hasattr(Matcher, "from_config") else Matcher.build(cfg)
    assert bool(m.match(title, "", only=["tuigroup"])) is hit


def test_synnovis_reaches_synlab(cfg):
    m = Matcher.from_config(cfg) if hasattr(Matcher, "from_config") else Matcher.build(cfg)
    res = m.match("Germany probes Vladimir K arrested in Japan over Synnovis hack", "", only=["synlab"])
    assert res and res[0].name_id == "synlab"
    syn = [a for a in {n.id: n for n in cfg.names}["synlab"].aliases if a.text == "Synnovis"]
    assert syn and syn[0].search
