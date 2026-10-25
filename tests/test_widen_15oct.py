"""15 October 2026: widening the net to the names a credit actually trades under.

Over the seven weeks to 14 October the engine logged no Mobilux row at all, two Maxeda rows, one HSE
row and never a Cerballiance row, because those credits are reported under their brands (But,
Praxis and Brico, Cerballiance) or under a subsidiary (CEVA, VIB Vermögen, Payone), and the
generated name files held those names as match-only aliases or not at all. Each test names the
headline or the gap it closes.
"""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from newsflow.config import Alias, NameConfig, _merge_overlay
from newsflow.export import build_coverage, build_export, compact_window, on_domains
from newsflow.http import Http
from newsflow.match import Matcher
from newsflow.models import RawItem
from newsflow.pipeline import RunSummary, build_jobs, process_items
from newsflow.store import Store

REPO = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 15, 20, 0, tzinfo=timezone.utc)


def _brief():
    spec = importlib.util.spec_from_file_location("build_brief_15oct", REPO / "scripts" / "build_brief.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ------------------------------------------------------------------ the overlay
def test_an_overlay_entry_upgrades_a_match_only_alias_without_widening_or_downgrading_it():
    names = [NameConfig(id="maxeda", name="Maxeda", aliases=[
        Alias(text="Maxeda", search=True, inflect=True),
        Alias(text="Praxis", langs=["nl"], weight=0.5, require_context=["Maxeda", "bouwmarkt"]),
        Alias(text="Brico", langs=["fr", "nl"], weight=0.5, inflect=True),
    ])]
    unknown, counts = _merge_overlay(names, {"names": {"maxeda": {"aliases": [
        {"text": "Praxis", "langs": ["nl"], "search": True, "search_every": 4, "capitalized": True,
         "require_context": ["winkel", "Bouwmarkt", "schuldeisers"]},
        {"text": "Brico", "langs": ["fr", "nl"], "search": True, "search_langs": ["fr"], "reject_context": ["Brico Dépôt"]},
        {"text": "Maxeda"},
    ]}}})
    assert unknown == []
    praxis, brico = names[0].aliases[1], names[0].aliases[2]
    assert praxis.search and praxis.search_every == 4 and praxis.capitalized
    assert praxis.langs == ["nl"] and praxis.weight == 0.5                      # neither widened nor reweighted
    assert praxis.require_context == ["Maxeda", "bouwmarkt", "winkel", "schuldeisers"]   # appended, no case duplicate
    assert brico.search and brico.search_langs == ["fr"] and brico.reject_context == ["Brico Dépôt"]
    assert brico.inflect                                                          # an entry silent on it keeps it
    assert counts["upgraded"] == 2 and counts["duplicates"] == 1 and counts["widened"] == 0
    assert len(names[0].aliases) == 3


def test_own_domains_comps_and_unknown_comp_ids():
    names = [NameConfig(id="heimstaden", name="Heimstaden", home_country="SE"),
             NameConfig(id="sector_property_credit", name="Property credit", kind="comp")]
    unknown, counts = _merge_overlay(names, {"names": {"heimstaden": {
        "own_domains": [{"domain": "Heimstaden.com", "lang": "en", "country": "SE"}, "heimstadenbostad.com"],
        "comps": ["sector_property_credit", "no_such_sector"],
    }}})
    h = names[0]
    assert h.own_domains == ["heimstaden.com", "heimstadenbostad.com"]
    assert h.site_queries == ["heimstaden.com", "heimstadenbostad.com"]
    assert h.own_domain_markets == {"heimstaden.com": ("en", "SE")}
    assert h.comps == ["sector_property_credit"]
    assert unknown == ["heimstaden.comps:no_such_sector"]                        # check-config reports it
    assert counts["own_domains"] == 2 and counts["comps"] == 1 and counts["comps_unknown"] == 1


def test_the_committed_overlay_links_the_sector_entries_to_their_credits(cfg):
    assert not cfg.overlay_unknown, cfg.overlay_unknown
    links = {"airbaltic": "sector_jet_fuel", "tuigroup": "sector_jet_fuel", "cmacgm": "sector_container_freight",
             "tereos": "sector_sugar_beet", "centrient": "sector_antibiotics", "heimstaden": "sector_property_credit",
             "adler": "sector_property_credit", "branicks": "sector_property_credit", "vivion": "sector_property_credit",
             "biogroup": "cnam", "cerba": "cnam", "synlab": "cnam"}
    for credit, sector in links.items():
        assert sector in cfg.name(credit).comps, credit
        assert cfg.name(sector).focus == "comp", sector                      # read across as a focus comp
    assert cfg.name("cnam").focus == "comp" and cfg.name("cnam").kind == "name"   # still swept as tier A


# ------------------------------------------------------------------ the matcher
@pytest.mark.parametrize("title,lang,nid,hit", [
    # Mobilux trades as But: the name, never the word
    ("Décryptage: But/Conforama, le mariage qui dérange", "fr", "mobilux", True),
    ("BUT va fermer 12 magasins et supprimer des postes", "fr", "mobilux", True),
    ("Les magasins Conforama et But fusionnent leurs centrales d'achat", "fr", "mobilux", True),
    ("Dans le but de rouvrir les magasins avant Noël, la mairie s'engage", "fr", "mobilux", False),
    ("But de Mbappé: le PSG s'impose au Parc", "fr", "mobilux", False),
    # Maxeda trades as Praxis and Brico
    ("Moederbedrijf Praxis bereikt akkoord met schuldeisers", "nl", "maxeda", True),
    ("Praxis zet winkels in de verkoop om schulden te dempen", "nl", "maxeda", True),
    ("Brico wil 10 winkels verzelfstandigen", "nl", "maxeda", True),
    ("Brico gaat 100 banen schrappen", "nl", "maxeda", True),
    ("Brico Dépôt ouvre un nouveau magasin à Lyon, une chaîne en croissance", "fr", "maxeda", False),
    ("Kingfisher (Castorama, Brico Dépôt) désigne son nouveau directeur général", "fr", "maxeda", False),
    ("Swarovski AT Balance: Spektiv mit Bildstabilisator im Praxistest", "de", "maxeda", False),
    # CMA CGM's logistics arm, as the trade press writes it
    ("Another Ceva reboot, another 10,000 staff. Can it deliver?", "en", "cmacgm", True),
    ("CEVA Logistics and Zalando deploy AI-powered robots for returns handling", "en", "cmacgm", True),
    ("Ceva Santé Animale investit dans un nouveau site de production de vaccins et de logistique", "fr", "cmacgm", False),
    # Branicks' listed subsidiary, in both spellings
    ("VIB Vermögen: Gewinn bricht im ersten Halbjahr massiv ein", "de", "branicks", True),
    ("15,3 Prozent Minus: Bei VIB Vermoegen beginnt die Suche nach dem Boden", "de", "branicks", True),
    # Heimstaden's founder, in the genitive and beside Alecta
    ("Tollefsens utleieboliger: Massiv brems i nedsalget", "nb", "heimstaden", True),
    ("Uppgifter: Alecta nära skilsmässa från Tollefsen - Di", "sv", "heimstaden", True),
    # Cerba's lab network, Worldline's German business, HSE the shopping channel
    ("Les laboratoires Cerballiance victimes d'une cyberattaque", "fr", "cerba", True),
    ("Payone: BaFin ordnet Sonderbeauftragten bei der Sparkassen-Tochter an", "de", "worldline", True),
    ("HSE: Teleshoppingsender baut in Ismaning Stellen ab", "de", "hse", True),
    ("HSE erhöht die Strompreise in Darmstadt", "de", "hse", False),
    ("HSE warns construction firms over silica dust", "en", "hse", False),
    # Groupe Bertrand under its founder's name and its grill chain
    ("Burger King, Léon, Hippopotamus… l'empire d'Olivier Bertrand bientôt en quête d'un nouvel actionnaire", "fr", "bertrand", True),
    ("Hippopotamus ferme trois restaurants en Île-de-France", "fr", "bertrand", True),
    ("Olivier Bertrand remporte le marathon de Nantes", "fr", "bertrand", False),
])
def test_the_brands_match_their_credit_and_the_common_words_do_not(cfg, title, lang, nid, hit):
    m = Matcher.from_config(cfg)
    got = {r.name_id for r in m.match(title, "", lang, only=[nid])}
    assert (nid in got) is hit, (title, got)


def test_capitalized_and_reject_need_the_name_not_the_common_word():
    from newsflow.match import _alias_regex
    pat = _alias_regex(Alias(text="BUT", capitalized=True))
    assert pat.search("But et Conforama") and pat.search("BUT ferme") and pat.search("chez But.")
    assert not pat.search("dans le but de") and not pat.search("Le butin")
    assert _alias_regex(Alias(text="Praxis", capitalized=True)).search("De Praxis-winkel")


# ------------------------------------------------------------------ the queries
def _google_labels(cfg, tmp_path, runs):
    cfg.engine["db_path"] = str(tmp_path / "q.db")
    store, http = Store(cfg.db_path), Http("test")
    labels = set()
    for r in runs:
        labels |= {j.label for j in build_jobs(cfg, http, store, run_number=r, now=NOW + timedelta(minutes=15 * r))
                   if j.route == "googlenews"}
    http.close(); store.close()
    return labels


def test_the_brands_are_searched_in_their_own_language_only(cfg, tmp_path):
    labels = _google_labels(cfg, tmp_path, range(1, 9))
    assert {'"CEVA Logistics" [fr-FR]', '"CEVA Logistics" [en-US]'} & labels
    assert '"Cerballiance" [fr-FR]' in labels and not any(l.startswith('"Cerballiance" [en') for l in labels)
    assert '"VIB Vermögen" [de-DE]' in labels and not any(l.startswith('"VIB Vermögen" [en') for l in labels)
    assert '"Praxis" [nl-NL]' in labels and '"Brico" [fr-BE]' in labels
    assert '"Payone" [de-DE]' in labels
    hippo = {l for l in labels if l.startswith('"Hippopotamus"')}
    assert hippo == {'"Hippopotamus" [fr-FR]'}                                 # never the English edition
    tolle = {l.rsplit("[", 1)[1] for l in labels if l.startswith('"Ivar Tollefsen"')}
    assert tolle == {"nb-NO]", "sv-SE]"}
    assert any(l.startswith('("enseigne But" OR "magasins But"') and l.endswith("[fr-FR]") for l in labels)
    assert not any(l.startswith('"BUT" [') for l in labels)                    # never the bare word
    assert any(l.startswith('"HSE" (Teleshopping OR') for l in labels)


def test_own_domains_are_asked_every_eight_runs_in_the_right_language(cfg, tmp_path):
    labels = _google_labels(cfg, tmp_path, range(1, 9))
    for lbl in ('"Branicks" site:vib-ag.de [de-DE]', '"Branicks" site:branicks.com [de-DE]',
                '"Heimstaden" site:heimstaden.com [en-GB]', '"Centrient" site:centrient.com [en-GB]',
                '"CMA CGM" site:cevalogistics.com [en-GB]', '"Medical Properties Trust" site:medicalpropertiestrust.com [en-US]'):
        assert lbl in labels, lbl


def test_the_focus_load_stays_inside_the_cap_and_spread_over_the_cycle(cfg, tmp_path):
    cfg.engine["db_path"] = str(tmp_path / "s.db")
    store, http = Store(cfg.db_path), Http("test")
    focus = {n.id for n in cfg.names if n.focus == "name"}
    per_run = []
    for r in range(1, 17):
        g = [j for j in build_jobs(cfg, http, store, run_number=r, now=NOW + timedelta(minutes=15 * r)) if j.route == "googlenews"]
        per_run.append(sum(1 for j in g if len(j.name_ids) == 1 and j.name_ids[0] in focus))
    http.close(); store.close()
    cap = int(cfg.routes["googlenews"]["quota"]["max_per_run"])
    assert max(per_run) < cap * 0.75, per_run          # the focus tier never fills the cap on its own
    assert max(per_run) - min(per_run) < 40, per_run   # no run carries every name's site: queries at once


# ------------------------------------------------------------------ the export and the brief
def _row(i, title, domain, nid):
    return RawItem(title=title, link=f"https://{domain}/news/{i}", route="googlenews", query=f'"{nid}" site:{domain}',
                   summary="", published_at=NOW - timedelta(hours=1), source_domain=domain, source_name=domain,
                   lang="de", country="DE", name_ids=[nid])


def test_an_own_site_row_survives_the_export_with_no_alias_in_its_headline(cfg, tmp_path):
    store = Store(cfg.db_path)
    run_id = store.start_run(NOW)
    process_items(cfg, store, Matcher.from_config(cfg), None, [
        _row(1, "Vereinbarung über eine Finanzierung von 60 Mio. Euro unterzeichnet", "vib-ag.de", "branicks"),
        _row(2, "Ad-hoc: Halbjahresfinanzbericht 2026 veröffentlicht", "www.branicks.com", "branicks"),
        _row(3, "Insolvenzbekanntmachungen Oktober", "kauppalehti.fi", "branicks"),
    ], run_id, NOW, RunSummary(run_id=run_id, started_at=NOW), lookback_hours=48)
    out = build_export(cfg, store, NOW, 24)
    b = next(n for n in out["names"] if n["id"] == "branicks")
    kept = {c["primary"]["title"]: c["primary"] for c in b["candidates"]}
    assert "Vereinbarung über eine Finanzierung von 60 Mio. Euro unterzeichnet" in kept
    assert "Ad-hoc: Halbjahresfinanzbericht 2026 veröffentlicht" in kept
    assert all(p.get("own_site") for p in kept.values())
    screened = [s["title"] for s in b["screened"]["items"]]
    assert "Insolvenzbekanntmachungen Oktober" in screened                    # an outlet's "(query)" row still goes
    w = compact_window(out)
    wb = next(n for n in w["names"] if n["id"] == "branicks")
    assert all(c["primary"].get("own_site") for c in wb["candidates"])
    cov = build_coverage(cfg, store, NOW)
    own = {o["domain"] for o in cov["names"]["branicks"]["outlets"] if o.get("own")}
    assert own == {"branicks.com", "vib-ag.de"}
    store.close()


def test_the_brief_treats_an_own_site_row_as_the_issuers_own_word():
    bb = _brief()
    assert bb.own_source({"route": "googlenews", "tier": 3, "own_site": True})
    assert not bb.own_source({"route": "googlenews", "tier": 3})
    assert bb.own_source({"route": "page", "tier": 0})
    rows = bb.collect({"names": [{"id": "branicks", "name": "Branicks", "kind": "name", "candidates": [
        {"cluster_id": 7, "primary": {"title": "t", "source": "vib-ag.de", "domain": "vib-ag.de", "country": "DE",
                                      "lang": "de", "url": "u", "first_seen_at": "2026-10-15T19:00:00+00:00",
                                      "alias_where": "none", "route": "googlenews", "tier_hint": 3, "own_site": True}}]}]})
    assert rows[0]["own_site"] is True and bb.own_source(rows[0])


def test_on_domains():
    assert on_domains("investors.worldline.com", ["worldline.com"])
    assert on_domains("www.branicks.com", ["branicks.com"])
    assert not on_domains("notworldline.com", ["worldline.com"]) and not on_domains("", ["worldline.com"])
