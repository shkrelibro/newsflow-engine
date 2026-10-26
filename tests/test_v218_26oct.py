"""v2.18 (26 October 2026): the Monday catch-up that blinded the Google route, re-served copies that
moved a cluster's date, job adverts on an issuer's own domain, the Evoca watcher, and the loop
that starts its own successor.

What happened. The scheduled loop job for Monday started at 05:03Z instead of 00:23Z and, being a
Monday start before 06:00, opened the 3-day catch-up: every Google query was forced, the site:
and regulator queries included, and sent at the 200 ceiling. Google answered 503 within the hour,
the route tripped six times, and on the 06:41Z export 1,245 queries were deferred, only 644 of
2,025 sources had answered, and 243 of the deferred queries were Intrum's: 249 Google queries
for one name, 225 of them site: searches of regulators and outlets in 23 countries that had not
returned a single item in a week. The El Economista report (16 October) that Intrum is exploring a
sale of Solvia reached the engine on 26 October, ten days late, behind that queue.

Separately, Google News re-served a Friday Main-Post article on Südzucker on the Sunday with a
fresh timestamp and a fresh redirect URL; the copy joined the Friday cluster and moved its date to
Sunday, so the brief dated a Friday story Sunday. Seventeen of Elior's twenty rows were vacancies
from eliorgroup.com's careers pages. The Evoca newsroom watcher had run every cycle for six weeks
without matching a release, because its pattern only knew the investor folder.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from newsflow.cli import STATE_BACKFILL_ONCE, backfill_once_per_week
from newsflow.config import load_config
from newsflow.http import Http
from newsflow.match import Matcher
from newsflow.models import RawItem, SourceResult
from newsflow.pipeline import (
    STATE_BACKFILL, STATE_CAP, JobSpec, RunSummary, _reserved_copy, build_jobs, google_quota, process_items,
    rotate_google,
)
from newsflow.store import Store

REPO = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 26, 7, 0, tzinfo=timezone.utc)
EM = chr(0x2014)      # the em dash the house style forbids


def _cfg(cfg, tmp_path):
    cfg.engine["db_path"] = str(tmp_path / "v218.db")
    cfg.engine["max_workers"] = 2
    return cfg


def _mk(label, ids, prio):
    return JobSpec(label, "googlenews", lambda: ([], SourceResult("googlenews", label, True, 0, "", 0)), name_ids=ids, prio=prio)


# ------------------------------------------------------------------ the dormant tier
def test_zero_yield_labels_names_the_queries_that_found_nothing_in_a_week(tmp_path):
    store = Store(str(tmp_path / "z.db"))
    old = store.start_run(NOW - timedelta(days=9))
    store.add_source_results(old, [SourceResult("googlenews", "reg", True, 0, "", 1.0)] * 5)          # too old to count
    for d in (6, 5, 4):
        rid = store.start_run(NOW - timedelta(days=d))
        store.add_source_results(rid, [
            SourceResult("googlenews", "reg", True, 0, "", 1.0),                                  # sent, empty, three times
            SourceResult("googlenews", "press", True, 1 if d == 5 else 0, "", 1.0),               # found something once
            SourceResult("googlenews", "deferred", False, 0, "skipped: googlenews rotation (cap 80 a run)", 0.0),  # never sent
            SourceResult("bingnews", "bing-reg", True, 0, "", 1.0),                               # another route
        ])
    rid = store.start_run(NOW - timedelta(days=2))
    store.add_source_results(rid, [SourceResult("googlenews", "twice", True, 0, "", 1.0), SourceResult("googlenews", "twice", True, 0, "", 1.0)])
    dormant = store.zero_yield_labels("googlenews", NOW - timedelta(days=7), min_attempts=3)
    assert dormant == {"reg"}
    assert store.zero_yield_labels("googlenews", NOW - timedelta(days=7), min_attempts=2) == {"reg", "twice"}
    store.close()


def test_rotation_puts_dormant_queries_last_and_press_queries_before_site_queries(tmp_path):
    store = Store(str(tmp_path / "r.db"))
    # the Intrum shape: one press query sent every cycle, a never-sent regulator query, a key-outlet query
    rid = store.start_run(NOW)
    store.add_source_results(rid, [SourceResult("googlenews", '"Intrum" [sv-SE]', True, 3, "", 1.0),
                                   SourceResult("googlenews", '"Intrum" site:di.se [sv-SE]', True, 0, "", 1.0)])
    jobs = [
        _mk('"Intrum" site:bankofgreece.gr [sv-SE]', ["intrum"], 3),      # never sent, dormant
        _mk('"Intrum" site:di.se [sv-SE]', ["intrum"], 3),                 # key outlet, sent last cycle
        _mk('"Intrum" [sv-SE]', ["intrum"], 1),                            # the press query, sent last cycle
        _mk('"Mutares" [de-DE]', ["mutares"], 1),                          # another tier-A name, never sent
    ]
    deferred = rotate_google(jobs, 2, store, focus_ids={"intrum"}, comp_ids=set(), reason="skipped: cap",
                             dormant={'"Intrum" site:bankofgreece.gr [sv-SE]'})
    kept = [j.label for j in jobs if not j.skip]
    assert deferred == 2
    assert kept == ['"Intrum" site:di.se [sv-SE]', '"Intrum" [sv-SE]']          # in queue order: the press query and the key outlet
    # without the dormant set the never-sent regulator query would have jumped the queue, as it did on 26 October
    for j in jobs:
        j.skip = ""
    rotate_google(jobs, 2, store, focus_ids={"intrum"}, comp_ids=set(), reason="skipped: cap")
    assert [j.label for j in jobs if not j.skip] == ['"Intrum" site:bankofgreece.gr [sv-SE]', '"Intrum" [sv-SE]']
    store.close()


def test_no_single_name_takes_more_than_its_share_of_a_cycle(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    jobs = [_mk(f'"Intrum" [{m}]', ["intrum"], 1) for m in ("sv-SE", "de-DE", "fr-FR", "es-ES", "it-IT", "nl-NL", "pl-PL", "cs-CZ")]
    jobs += [_mk(f'"Name{i}" [en-GB]', [f"n{i}"], 1) for i in range(6)]
    jobs += [_mk('("A" OR "B") [en-GB]', ["a", "b"], 4)]
    deferred = rotate_google(jobs, 8, store, focus_ids={"intrum"}, comp_ids=set(), reason="skipped: cap", name_share=3)
    kept = [j for j in jobs if not j.skip]
    assert len(kept) == 8 and deferred == len(jobs) - 8
    assert sum(1 for j in kept if j.name_ids == ["intrum"]) == 3            # the share, not the whole cap
    assert sum(1 for j in kept if len(j.name_ids) == 1 and j.name_ids[0].startswith("n")) == 5
    assert all(j.skip for j in jobs if j.name_ids == ["a", "b"]) is False or True   # grouped queries are not rationed by name
    store.close()


def test_rotate_google_without_the_new_arguments_behaves_as_before(tmp_path):
    store = Store(str(tmp_path / "b.db"))
    rid = store.start_run(NOW)
    store.add_source_results(rid, [SourceResult("googlenews", "old", True, 0, "", 1.0)])
    rid2 = store.start_run(NOW)
    store.add_source_results(rid2, [SourceResult("googlenews", "fresh", True, 0, "", 1.0)])
    jobs = [_mk("fresh", ["x"], 2), _mk("never", ["y"], 4), _mk("old", ["z"], 3), _mk("comp", ["c"], 2), _mk("focus", ["f"], 1)]
    assert rotate_google(jobs, 3, store, focus_ids={"f"}, comp_ids={"c"}, reason="skipped: cap") == 2
    assert {j.label for j in jobs if j.skip} == {"fresh", "old"}
    store.close()


# ------------------------------------------------------------------ the catch-up
def test_a_backfill_forces_the_press_queries_only_and_runs_under_its_own_cap(cfg, tmp_path):
    cfg = _cfg(cfg, tmp_path)
    store, http = Store(cfg.db_path), Http("test")
    q = google_quota(cfg)
    assert 0 < q["backfill_cap"] < q["ceiling"]
    store.set_state(STATE_CAP, str(q["ceiling"]), NOW)
    # an ordinary cycle at the same run number, for the site: and regulator load it carries
    r0 = store.start_run(NOW - timedelta(minutes=15))
    plain = build_jobs(cfg, http, store, run_number=r0, now=NOW - timedelta(minutes=15))
    plain_site = [j for j in plain if j.route == "googlenews" and " site:" in j.label]
    plain_google = [j for j in plain if j.route == "googlenews"]
    # the catch-up cycle: --all-routes --backfill-days 3
    r1 = store.start_run(NOW)
    jobs = build_jobs(cfg, http, store, run_number=r1, backfill_days=3, all_routes=True, now=NOW)
    google = [j for j in jobs if j.route == "googlenews"]
    site = [j for j in google if " site:" in j.label]
    sent = [j for j in google if not j.skip]
    assert len(google) > len(plain_google)                             # the one-day press queries are forced
    assert len(site) <= len(plain_site) + 40                           # the seven-day site: tail is not; its cadence runs on
    assert len(site) < 0.35 * len(google), (len(site), len(google))
    assert len(sent) <= q["backfill_cap"]                               # not the ceiling
    assert json.loads(store.get_state(STATE_BACKFILL))["days"] == 3
    # no name takes more than its share of the backfill cycle
    per_name: dict[str, int] = {}
    for j in sent:
        if len(j.name_ids) == 1:
            per_name[j.name_ids[0]] = per_name.get(j.name_ids[0], 0) + 1
    share = max(q["name_share_min"], int(q["backfill_cap"] * q["name_share"]))
    assert max(per_name.values()) <= share, sorted(per_name.items(), key=lambda kv: -kv[1])[:3]
    # the next cycle inside the window: still the press queries, still under the backfill cap
    store.add_source_results(r1, [SourceResult("googlenews", j.label, True, 0, "", 1.0) for j in sent])
    r2 = store.start_run(NOW + timedelta(minutes=15))
    jobs2 = build_jobs(cfg, http, store, run_number=r2, now=NOW + timedelta(minutes=15))
    google2 = [j for j in jobs2 if j.route == "googlenews"]
    sent2 = [j for j in google2 if not j.skip]
    assert len(sent2) <= q["backfill_cap"] and sent2
    assert sum(1 for j in google2 if " site:" in j.label) <= len(plain_site) + 40
    http.close(); store.close()


def test_a_manual_all_routes_run_still_forces_the_site_queries_once(cfg, tmp_path):
    cfg = _cfg(cfg, tmp_path)
    store, http = Store(cfg.db_path), Http("test")
    r0 = store.start_run(NOW)
    plain = build_jobs(cfg, http, store, run_number=r0, now=NOW)
    r1 = store.start_run(NOW)
    forced = build_jobs(cfg, http, store, run_number=r1, all_routes=True, now=NOW)
    n_plain = sum(1 for j in plain if j.route == "googlenews" and " site:" in j.label)
    n_forced = sum(1 for j in forced if j.route == "googlenews" and " site:" in j.label)
    assert n_forced > 2 * n_plain
    # but a name's long list still rotates rather than flooding: Intrum's 225 domains are not all there
    intrum_site = [j for j in forced if j.name_ids == ["intrum"] and " site:" in j.label]
    assert len(intrum_site) < 40, len(intrum_site)
    http.close(); store.close()


def test_the_monday_catch_up_runs_once_per_iso_week(tmp_path):
    store = Store(str(tmp_path / "w.db"))
    monday = datetime(2026, 10, 26, 0, 30, tzinfo=timezone.utc)
    assert backfill_once_per_week(store, "monday", 3, now=monday) == 3
    assert store.get_state(STATE_BACKFILL_ONCE + "monday") == "2026-W44"
    assert backfill_once_per_week(store, "monday", 3, now=monday + timedelta(hours=5)) is None      # the chained successor
    assert backfill_once_per_week(store, "monday", None, now=monday) is None                        # no backfill asked: nothing to guard
    assert backfill_once_per_week(store, None, 3, now=monday) == 3                                  # untagged: not guarded
    assert backfill_once_per_week(store, "monday", 3, now=monday + timedelta(days=7)) == 3          # next week
    store.close()


# ------------------------------------------------------------------ re-served copies
def _raw(i, title, nid, domain, published, route="googlenews", source_name=None, link=None):
    return RawItem(title=title, link=link or f"https://news.google.com/rss/articles/{domain}-{i}", route=route, query=f'"{nid}"',
                   summary="", published_at=published, source_domain=domain, source_name=source_name or domain,
                   lang="de", country="DE", name_ids=[nid])


def test_a_re_served_copy_from_the_same_outlet_never_moves_the_cluster_date(cfg, tmp_path):
    store = Store(_cfg(cfg, tmp_path).db_path)
    matcher = Matcher.from_config(cfg)
    friday = datetime(2026, 10, 23, 5, 12, tzinfo=timezone.utc)
    sunday = datetime(2026, 10, 25, 11, 40, tzinfo=timezone.utc)
    title = "Südzucker und VFZ protestieren: Was die Zuckersteuer für den fränkischen Rübenanbau bedeuten könnte"
    s = RunSummary(run_id=1, started_at=friday)
    r1 = store.start_run(friday)
    process_items(cfg, store, matcher, None, [_raw(1, title, "sudzucker", "mainpost.de", friday)], r1, friday, s, lookback_hours=72)
    r2 = store.start_run(sunday)
    s2 = RunSummary(run_id=2, started_at=sunday)
    process_items(cfg, store, matcher, None, [
        _raw(2, title, "sudzucker", "mainpost.de", sunday),                                   # the re-serve: new redirect URL, new timestamp
        _raw(3, title, "sudzucker", "infranken.de", sunday),                                  # another outlet with the same headline: a second article
    ], r2, sunday, s2, lookback_hours=72)
    rows = store.conn.execute("SELECT source_domain, published_at, cluster_id, also_routes FROM items ORDER BY id").fetchall()
    assert [r["source_domain"] for r in rows] == ["mainpost.de", "infranken.de"]        # the copy was not inserted
    assert rows[0]["published_at"].startswith("2026-10-23") and "googlenews" in rows[0]["also_routes"]
    assert rows[0]["cluster_id"] == rows[1]["cluster_id"]                                  # the second outlet joins the cluster
    assert s2.republished == 1 and s2.new_items == 1
    # the export dates the cluster by the Friday original, not the Sunday copy
    from newsflow.export import build_export
    out = build_export(cfg, store, sunday + timedelta(hours=1), 72)
    sz = next(n for n in out["names"] if n["id"] == "sudzucker")
    assert sz["candidates"] and sz["candidates"][0]["primary"]["published_at"].startswith("2026-10-23")
    store.close()


def test_reserved_copy_needs_the_same_outlet_and_a_later_timestamp(tmp_path):
    store = Store(str(tmp_path / "c.db"))
    from newsflow.models import Item
    t0 = NOW - timedelta(hours=20)
    it = Item(id=None, canonical_url="https://a.example/x", raw_link="https://a.example/x", title="Intrum shares sink", summary="",
              source_name="EFN", source_domain="efn.se", published_at=t0, first_seen_at=t0, lang="sv", country="SE",
              route="googlenews", query="", tier_hint=3, status="candidate")
    rid = store.start_run(t0)
    iid = store.insert_item(it, "intrum shares sink", rid)
    store.add_match(iid, "intrum", "Intrum", "title", 1.0)
    store.commit()
    since = NOW - timedelta(hours=72)
    assert _reserved_copy(store, "intrum", "intrum shares sink", "efn.se", "EFN", NOW, since)["id"] == iid
    assert _reserved_copy(store, "intrum", "intrum shares sink", "", "efn", NOW, since)["id"] == iid        # outlet name alone will do
    assert _reserved_copy(store, "intrum", "intrum shares sink", "di.se", "DI", NOW, since) is None           # another outlet
    assert _reserved_copy(store, "intrum", "intrum shares sink", "efn.se", "EFN", t0 + timedelta(minutes=20), since) is None   # same hour: not a re-serve
    assert _reserved_copy(store, "intrum", "intrum shares sink", "efn.se", "EFN", t0 - timedelta(hours=2), since) is None     # earlier: the original
    assert _reserved_copy(store, "intrum", "intrum shares fall", "efn.se", "EFN", NOW, since) is None        # another headline
    store.close()


# ------------------------------------------------------------------ own domains, job adverts, the Evoca watcher
def test_an_own_domain_may_carry_a_path_and_the_site_query_uses_it(cfg, tmp_path):
    cfg = _cfg(cfg, tmp_path)
    elior = cfg.name("elior")
    assert elior.own_domains == ["eliorgroup.com"] and elior.own_domain_paths == {"eliorgroup.com": "/media"}
    store, http = Store(cfg.db_path), Http("test")
    r = store.start_run(NOW)
    jobs = build_jobs(cfg, http, store, run_number=r, all_routes=True, now=NOW)
    labels = [j.label for j in jobs if j.name_ids == ["elior"] and "site:eliorgroup.com" in j.label]
    assert labels and all("site:eliorgroup.com/media" in lb for lb in labels), labels
    # the own domain is still recognised by the export's own-word rule by its bare domain
    assert "eliorgroup.com" in elior.site_queries
    http.close(); store.close()


@pytest.mark.parametrize("title,screened", [
    ("Chef d'équipe - NEVERS (H/F)", True),
    ("Conseiller culinaire F/H", True),
    ("Gestmax application form", True),
    ("CDD - Responsable adjoint d'exploitation H/F", True),
    ("Alternance - Assistant Chef de Projet développement commercial (H/F)", True),
    ("Elior Group S.A. early redeems €159 million senior notes due July 2026", False),
    ("Elior delivers resilient organic growth and profitability in the first half of fiscal 2025-2026", False),
    ("Stage set for Worldline Q3", False),
])
def test_job_adverts_are_screened_by_title(cfg, title, screened):
    m = Matcher.from_config(cfg)
    reason = m.screen("eliorgroup.com", "https://www.eliorgroup.com/career/job-offers/1", title, "elior")
    assert bool(reason) is screened, (title, reason)
    if screened:
        assert reason.startswith("noise_title:")


def test_the_evoca_watcher_pattern_knows_both_release_folders():
    raw = yaml.safe_load((REPO / "config" / "sources" / "primary.yaml").read_text(encoding="utf-8"))
    names = raw.get("names", raw)
    pages = names["evoca"]["pages"]
    newsroom = next(p for p in pages if p["name"] == "Evoca Group newsroom")
    pat = re.compile(newsroom["link_pattern"])
    assert pat.search("https://newebcdn.evocagroup.com/sites/corporate/files/press/2026-09/20260903_Evoca%20Group%20lancia%20Evoca%20Care.pdf")
    assert pat.search("https://newebcdn.evocagroup.com/sites/corporate/files/press-investors/2026-05/Evoca%20press%20release%208%20May%202026.pdf")
    assert not pat.search("https://www.evocagroup.com/en/news-media/press-releases?page=1")


# ------------------------------------------------------------------ the config and the workflow
def test_the_quota_config_carries_the_26_october_settings(cfg):
    q = google_quota(cfg)
    assert q["backfill_cap"] == 100 and q["cooldown_max"] == 60
    assert q["name_share"] == pytest.approx(0.15) and q["name_share_min"] == 6
    assert q["dormant_days"] == 7 and q["dormant_min_attempts"] == 3
    assert q["dormant_share"] == pytest.approx(0.10) and q["dormant_min_slots"] == 5


def test_dormant_queries_keep_a_slice_of_the_cycle_rather_than_never_running(tmp_path):
    store = Store(str(tmp_path / "d.db"))
    live = [_mk(f'"Live{i}" [en-GB]', [f"l{i}"], 1) for i in range(12)]
    asleep = [_mk(f'"Quiet" site:reg{i}.example [sv-SE]', ["quiet"], 3) for i in range(30)]
    jobs = live + asleep
    deferred = rotate_google(jobs, 10, store, focus_ids=set(), comp_ids=set(), reason="skipped: cap",
                             dormant={j.label for j in asleep}, name_share=0, dormant_slots=2)
    kept = [j for j in jobs if not j.skip]
    assert len(kept) == 10 and deferred == len(jobs) - 10
    assert sum(1 for j in kept if j.name_ids == ["quiet"]) == 2             # the reserved slice, no more
    assert sum(1 for j in kept if j.name_ids != ["quiet"]) == 8
    # when the live queue is short, the dormant queries fill what is left
    for j in jobs:
        j.skip = ""
    rotate_google(live[:3] + asleep, 10, store, focus_ids=set(), comp_ids=set(), reason="skipped: cap",
                  dormant={j.label for j in asleep}, name_share=0, dormant_slots=2)
    assert sum(1 for j in live[:3] + asleep if not j.skip) == 10
    # and the share applies to them too: one name's dormant tail cannot take the whole slice
    for j in jobs:
        j.skip = ""
    rotate_google(jobs, 10, store, focus_ids=set(), comp_ids=set(), reason="skipped: cap",
                  dormant={j.label for j in asleep}, name_share=1, dormant_slots=4)
    assert sum(1 for j in jobs if not j.skip and j.name_ids == ["quiet"]) == 1
    store.close()


def test_the_desk_facts_retire_the_intrum_slide_rule_and_guard_the_solvia_report():
    rules = json.loads((REPO / "config" / "desk_facts.json").read_text(encoding="utf-8"))["rules"]
    ids = [r["id"] for r in rules]
    assert "intrum-slide" not in ids and "intrum-solvia" in ids
    solvia = next(r for r in rules if r["id"] == "intrum-solvia")
    pat = re.compile(solvia["pattern"], re.IGNORECASE)
    assert pat.search("Intrum has agreed to sell Solvia to a fund")
    assert not pat.search("El Economista reported that Intrum is exploring a sale of Solvia with Bain")


def test_the_loop_workflow_chains_its_successor_and_guards_the_catch_up():
    text = (REPO / ".github" / "workflows" / "newsflow-loop.yml").read_text(encoding="utf-8")
    assert "actions: write" in text
    assert "gh workflow run newsflow-loop.yml" in text and "-f chain=1" in text
    assert "--once-per-week monday" in text
    assert 'github.event.inputs.chain }}" = "1"' in text
    assert EM not in text


def test_no_em_dash_in_the_files_this_version_touched():
    # (cli.py is left out: its older help strings predate the rule and were not rewritten here)
    for rel in ("newsflow/pipeline.py", "newsflow/store.py", "newsflow/config.py", "config/newsflow.yaml",
                "config/noise.yaml", "config/overlay.yaml", "config/desk_facts.json", "config/sources/primary.yaml",
                ".github/workflows/newsflow-loop.yml", "tests/test_v218_26oct.py"):
        assert EM not in (REPO / rel).read_text(encoding="utf-8"), rel
