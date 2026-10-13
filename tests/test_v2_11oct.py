"""11 October 2026: engine and brief v2, after the weekend that missed Synlab and Vonovia.

SYNLAB's Gironde wildfire disruption ran in trade press (Biologiste365) with the company named in
the standfirst, and the French market was reached only by a six-name OR query. The Welt am Sonntag
interview with Vonovia's chief executive was in the export as a comp candidate and never reviewed,
and the churn sites that echoed it ran under his name. Each test below names the mechanism that now
catches it: per-market solo queries, the executives overlay, churn sites demoted to echoes, source
grades and the comp review flag, the Monday delta, the 72-hour export and the desk lint.
"""
from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

from newsflow.config import load_config
from newsflow.export import compact_window
from newsflow.match import Matcher

REPO = Path(__file__).resolve().parent.parent


def _brief():
    spec = importlib.util.spec_from_file_location("build_brief_11oct", REPO / "scripts" / "build_brief.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ------------------------------------------------------------------ engine
def test_executive_names_attribute_a_headline_to_the_company(cfg):
    m = Matcher.from_config(cfg)
    hit = m.match("Mucic stoppt Neubau in Berlin", "", "de")
    assert hit and hit[0].name_id == "vonovia"
    assert cfg.people_counts.get("people", 0) >= 40 and cfg.people_counts.get("unknown") == 0


def test_churn_sites_screen_as_churn_and_junk_titles_stay_noise(cfg):
    m = Matcher.from_config(cfg)
    assert m.screen("ad-hoc-news.de", "https://ad-hoc-news.de/x", "Vonovia Halts Berlin Construction", "vonovia").startswith("churn:")
    assert m.screen("www.boerse-express.com", "https://www.boerse-express.com/x", "Vonovia Aktie: Mucic stoppt Neubau in Berlin", "vonovia").startswith("churn:")
    # a title pattern beats the churn reason, so a Kursziel page never becomes an echo
    assert m.screen("finanztrends.de", "https://finanztrends.de/x", "Branicks Group Aktie: Kursziel 0,50 Euro", "branicks").startswith("noise_title:")
    # an EQS release is never screened, wherever it was republished
    assert m.screen("ad-hoc-news.de", "https://ad-hoc-news.de/x", "EQS-Adhoc: Branicks Group AG: Hauptversammlung", "branicks") == ""


def test_every_tier_a_market_gets_a_solo_query_and_the_load_fits(cfg, tmp_path):
    from newsflow.http import Http
    from newsflow.pipeline import _main_alias, build_jobs
    from newsflow.store import Store
    cfg.engine["db_path"] = str(tmp_path / "x.db")
    store, http = Store(cfg.db_path), Http("test")
    syn = cfg.name("synlab")
    main = _main_alias(syn)
    fe = int(cfg.routes["googlenews"]["foreign_every_n_runs"])
    swept, per_run, built = set(), [], []
    for r in range(1, 25):
        jobs = [j for j in build_jobs(cfg, http, store, run_number=r) if j.route == "googlenews"]
        built.append(len(jobs))
        sent = [j for j in jobs if not j.skip]                         # the rotation cap holds the rest back
        per_run.append(len(sent))
        if r <= fe:
            swept |= {j.label.rsplit("[", 1)[1].rstrip("]") for j in sent if j.label.startswith(f'"{main}" [')}
    assert {f"{m.lang}-{m.country}" for m in syn.markets} <= swept       # FR included, every hour; a focus credit is never deferred
    cap = float(cfg.engine["max_run_minutes"]) * 60 / float(cfg.engine["rate_limits"]["googlenews_seconds"])
    assert max(per_run) <= 0.9 * cap, (max(per_run), cap)                # headroom on the busiest run
    quota = int(cfg.routes["googlenews"]["quota"]["max_per_run"])
    assert max(per_run) <= quota and max(built) > quota                 # the cap is what binds, not the budget
    http.close(); store.close()


def test_search_hosts_run_in_their_own_lanes(cfg):
    lanes = cfg.engine.get("lanes") or {}
    assert lanes.get("googlenews", 0) >= 1 and int(cfg.engine["max_workers"]) >= 8


def test_window72_is_compact_and_carries_echoes():
    data = {"generated_at": "2026-10-11T09:45:00+00:00", "window_hours": 72.0, "names": [{
        "id": "vonovia", "name": "Vonovia", "kind": "comp",
        "candidates": [{"cluster_id": 1, "sources": 1, "alert_categories": [], "primary": {
            "id": 9, "title": "Vonovias Reaktion auf das rote Berlin", "url": "u", "source": "WELT", "domain": "welt.de",
            "country": "DE", "lang": "de", "published_at": None, "first_seen_at": "2026-10-10T16:01:00+00:00",
            "route": "rss", "tier_hint": 3, "alias_where": "title", "screen_reason": "", "summary": "long text"}}],
        "churn": [{"id": 5, "title": "Vonovia Aktie: Mucic stoppt Neubau in Berlin", "domain": "boerse-express.com",
                   "screen_reason": "churn:boerse-express.com", "first_seen_at": "2026-10-10T14:01:00+00:00",
                   "summary": "long text"}]}]}
    out = compact_window(data)
    p = out["names"][0]["candidates"][0]["primary"]
    assert "summary" not in p and p["alias_where"] == "title"
    assert out["names"][0]["churn"][0]["screen_reason"].startswith("churn")


# ------------------------------------------------------------------ brief
def _latest():
    """The 10 October Vonovia and Branicks picture, reduced to the rows that matter."""
    def prim(i, title, domain, seen, where="title", reason=""):
        return {"id": i, "title": title, "url": f"https://{domain}/{i}", "source": domain, "domain": domain,
                "country": "DE", "lang": "de", "published_at": None, "first_seen_at": seen, "route": "googlenews",
                "tier_hint": 3, "alias_where": where, "screen_reason": reason, "alias": "Vonovia"}
    welt = prim(1, "Vonovias Reaktion auf das rote Berlin – jetzt offenbaren sich die wahren Pläne", "welt.de", "2026-10-10T16:01:00+00:00")
    echoes_v = [prim(10 + i, t, "ad-hoc-news.de", f"2026-10-10T1{i}:20:00+00:00", reason="churn:ad-hoc-news.de") for i, t in enumerate([
        "Vonovia Halts Berlin Construction as Fee Business Takes Center Stage",
        "Vonovia Aktie: Mucic stoppt Neubau in Berlin",
        "Vonovia's Berlin Freeze and Veveus Rollout Collide Ahead of November Update"])]
    echoes_b = [prim(20 + i, t, "ad-hoc-news.de", f"2026-10-11T0{i}:18:00+00:00", reason="churn:ad-hoc-news.de") for i, t in enumerate([
        "Branicks Group Aktie: Fortbestand unmittelbar gefährdet",
        "Branicks Group Faces Insolvency Threat as Shareholder Vote Blocks Rescue Plan",
        "Branicks Group Aktie: Kursziel 0,40 Euro"])]
    for x in echoes_b:
        x["alias"] = "Branicks"
    return {"generated_at": "2026-10-11T09:45:00+00:00", "names": [
        {"id": "vonovia", "name": "Vonovia", "kind": "comp",
         "candidates": [{"cluster_id": 71753, "primary": welt, "sources": 1, "alert_categories": []}],
         "churn": echoes_v, "screened": {"items": []}},
        {"id": "branicks", "name": "Branicks Group", "kind": "name", "watch": True,
         "candidates": [], "churn": echoes_b, "screened": {"items": []}},
    ]}


def test_the_welt_interview_is_flagged_for_review_with_its_echoes():
    bb = _brief()
    bb.CHURN_DOMAINS = set(bb.FALLBACK_CHURN)
    bb.JUNK_DOMAINS = set(bb.FALLBACK_JUNK)
    P = bb.prepare(_latest(), since=datetime(2026, 10, 9, 14, 45, tzinfo=timezone.utc))
    welt = P["C_rows"][0]
    assert welt["grade"] == "premium" and welt["echo_n"] >= 2
    assert welt["review"] is True and welt["new"] is True
    assert P["C_review"] == 1


def test_a_tier_a_event_only_churn_sites_carried_becomes_one_echo_row():
    bb = _brief()
    bb.CHURN_DOMAINS = set(bb.FALLBACK_CHURN)
    bb.JUNK_DOMAINS = set(bb.FALLBACK_JUNK)
    P = bb.prepare(_latest())
    rows = P["A_echo_rows"]
    assert len(rows) == 1 and rows[0]["nid"] == "branicks" and rows[0]["echo"] and rows[0]["redater"]
    assert rows[0]["echo_n"] == 2                                  # the Kursziel row is never an echo
    assert str(rows[0]["id"]).startswith("e")
    html = bb.item_html(rows[0], {}, False)
    assert "churn echo" in html and "date unverified" in html


def test_monday_first_cut_reaches_back_to_friday_close():
    bb = _brief()
    mon = datetime(2026, 10, 12, 4, 52, tzinfo=timezone.utc)
    assert bb.previous_cut(mon) == datetime(2026, 10, 9, 14, 45, tzinfo=timezone.utc)
    tue = datetime(2026, 10, 13, 9, 50, tzinfo=timezone.utc)
    assert bb.previous_cut(tue) == datetime(2026, 10, 13, 7, 45, tzinfo=timezone.utc)
    sun = datetime(2026, 10, 11, 4, 50, tzinfo=timezone.utc)
    assert bb.previous_cut(sun) == datetime(2026, 10, 10, 14, 45, tzinfo=timezone.utc)


def test_window_merge_adds_older_candidates_once():
    bb = _brief()
    latest = _latest()
    wide = _latest()
    wide["names"][0]["candidates"].append({"cluster_id": 5, "sources": 1, "alert_categories": [], "primary": dict(
        wide["names"][0]["candidates"][0]["primary"], id=99, title="Vonovia older row", first_seen_at="2026-10-09T08:00:00+00:00")})
    now = datetime(2026, 10, 12, 4, 50, tzinfo=timezone.utc)
    assert bb.merge_window(latest, wide, 72, now) == 1
    assert bb.merge_window(latest, wide, 72, now) == 0


def test_lint_blocks_the_employer_dashes_and_email_and_warns_on_desk_facts():
    bb = _brief()
    bb.EMPLOYER = __import__("re").compile(r"acme\s*bank", __import__("re").IGNORECASE)   # the real one is passed at run time
    facts = [{"id": "boots-no-agreement", "names": ["boots"], "pattern": "no (signed )?agreement",
              "severity": "error", "message": "signed 7 October"},
             {"id": "sandals", "pattern": "Sandals", "message": "23 September"}]
    j = {"lead": ["1", "e5"], "so": {
        "1": "A broker at Acme Bank says so; fine otherwise",
        "2": "The vote failed — badly",
        "3": "Contact a.b@example.com",
        "4": "There is no agreement yet on Boots",
        "6": "Royal Caribbean and Sandals"}}
    nid_of = {"1": "x", "2": "x", "3": "x", "4": "boots", "6": "rcl", "e5": "branicks"}
    errors, warns = bb.lint(j, facts, nid_of, low_ids={"e5"})
    text = " ".join(errors)
    assert "forbidden name" in text and "dash" in text and "email" in text and "boots-no-agreement" in text
    assert any("sandals" in w for w in warns)
    assert any("lead[e5]" in w for w in warns)
    clean, _ = bb.lint({"so": {"1": "Fitch cut the rating to B; the notes fell, per the release."}}, facts, {"1": "x"})
    assert clean == []


def test_desk_facts_and_grades_files_parse():
    import json
    import re
    facts = json.loads((REPO / "config" / "desk_facts.json").read_text(encoding="utf-8"))["rules"]
    ids = {p.stem for p in (REPO / "config" / "names").glob("*.yaml")}
    for f in facts:
        re.compile(f["pattern"])
        assert set(f.get("names", [])) <= ids, f["id"]
    grades = json.loads((REPO / "config" / "source_grades.json").read_text(encoding="utf-8"))
    assert "biologiste365.fr" in grades["trade"] and grades["people"]["vonovia"][0] == "Luka Mucic"


def test_golden_set_carries_the_two_weekend_misses():
    import yaml
    ev = {e["id"]: e for e in yaml.safe_load((REPO / "config" / "golden.yaml").read_text(encoding="utf-8"))["events"]}
    assert ev["synlab-gironde-fire-2026-10-09"]["expect"] == "miss"
    assert ev["vonovia-welt-interview-2026-10-10"]["name"] == "vonovia"
    cfg = load_config()
    assert all(cfg.name(e["name"]) for e in ev.values())


def test_the_next_cut_carries_the_judgement_forward_and_judges_only_what_is_new(tmp_path):
    bb = _brief()
    prev = {"cut_at": "2026-10-11T08:52+00:00", "export_at": "2026-10-11T08:45:00+00:00",
            "lead": ["1", "2"], "so": {"1": "kept", "2": "to drop", "3": "comp line"},
            "translate": {"1": "English"}, "tags": {}}
    page = tmp_path / "index.html"
    page.write_text("<p>page</p>\n<script type=\"application/json\" id=\"nf-judgement\">"
                    + __import__("json").dumps(prev).replace("</", "<\\/") + "</script>\n", encoding="utf-8")
    got = bb.load_prev(page)
    assert got["export_at"] == prev["export_at"] and got["so"]["2"] == "to drop"
    merged = bb.merge_judgement(got, {"so": {"4": "new row"}, "drop": ["2"], "lead": ["4", "1"]})
    assert merged["lead"] == ["4", "1"] and set(merged["so"]) == {"1", "3", "4"}
    assert merged["translate"] == {"1": "English"}
    kept = bb.merge_judgement(got, {"so": {"1": "rewritten"}})
    assert kept["so"]["1"] == "rewritten" and kept["lead"] == ["1", "2"]


def test_render_embeds_the_judgement_for_the_next_cut():
    bb = _brief()
    if not bb.TEMPLATE:
        return
    bb.CHURN_DOMAINS = set(bb.FALLBACK_CHURN)
    bb.JUNK_DOMAINS = set(bb.FALLBACK_JUNK)
    latest = _latest()
    j = {"so": {"71753": "Welt interview, headline only", "999": "not in the export"}}
    html = bb.render(latest, {"names": {}}, j, "08:45 London", since=datetime(2026, 10, 11, 7, 45, tzinfo=timezone.utc))
    import json
    import re
    state = json.loads(re.search(r'id="nf-judgement">(.*?)</script>', html, re.S).group(1).replace("<\\/", "</"))
    assert state["so"] == {"71753": "Welt interview, headline only"}       # only what was published
    assert state["export_at"] == latest["generated_at"]


def test_bing_is_a_narrow_baltic_lane_for_tier_a(cfg, tmp_path):
    """Over the month to 10 October what only Bing found on tier-A names was Baltic press on
    airBaltic, the EUR 140m first financing tranche (nra.lv, 29 September) among it."""
    from newsflow.http import Http
    from newsflow.pipeline import build_jobs
    from newsflow.store import Store
    cfg.engine["db_path"] = str(tmp_path / "b.db")
    store, http = Store(cfg.db_path), Http("test")
    allb = [j for j in build_jobs(cfg, http, store, run_number=1) if j.route == "bingnews"]
    jobs = [j for j in allb if not j.fallback and " OR " in j.label]      # the grouped Baltic lane
    assert jobs and len(jobs) <= 6
    assert all(j.label.rsplit("[", 1)[1].rstrip("]").split("-")[-1].upper() in {"LV", "LT", "EE"} for j in jobs)
    assert any("airbaltic" in j.name_ids for j in jobs)
    assert not any(cfg.name(n).kind == "comp" for j in jobs for n in j.name_ids)
    # 11 Oct: plus the focus credits' home market on a slow cadence, and a reserve held back for
    # the cycles on which Google refuses us (see test_google_quota_11oct.py)
    solos = [j for j in allb if not j.fallback and " OR " not in j.label]
    assert solos and all(cfg.name(j.name_ids[0]).focus == "name" for j in solos)
    reserve = [j for j in allb if j.fallback]
    assert len(reserve) >= len(cfg.focus) * 2
    http.close(); store.close()


# ------------------------------------------------------------------ the focus list
def test_focus_list_marks_credits_and_their_comps(cfg):
    assert "synlab" in cfg.focus and cfg.name("synlab").focus == "name"
    assert cfg.name("vonovia").focus == "comp"                        # a comp of Heimstaden
    assert cfg.name("nexi").focus == "name"                           # a comp file can be a focus credit
    assert not cfg.focus_unknown
    assert not cfg.name("tuigroup").watch                             # focus does not imply watch


def test_focus_credits_get_every_market_and_comps_a_solo_query(cfg, tmp_path):
    from newsflow.http import Http
    from newsflow.pipeline import _main_alias, build_jobs
    from newsflow.store import Store
    cfg.engine["db_path"] = str(tmp_path / "f.db")
    store, http = Store(cfg.db_path), Http("test")
    syn, von = cfg.name("synlab"), cfg.name("vonovia")
    every = int(cfg.routes["googlenews"]["focus_every_n_runs"])
    cevery = int(cfg.routes["googlenews"]["focus_comp_every_n_runs"])
    swept, comp_solo = set(), 0
    for r in range(1, max(every, cevery) + 1):
        for j in build_jobs(cfg, http, store, run_number=r):
            if j.route == "googlenews" and j.label.startswith(f'"{_main_alias(syn)}" ['):
                swept.add(j.label.rsplit("[", 1)[1].rstrip("]"))
            if j.route == "googlenews" and j.name_ids == ["vonovia"] and j.label.startswith(f'"{_main_alias(von)}" ['):
                comp_solo += 1
    assert {f"{m.lang}-{m.country}" for m in syn.markets} <= swept
    assert comp_solo >= 1
    http.close(); store.close()


def test_focus_table_reports_page_new_and_measured_silence():
    bb = _brief()
    bb.CHURN_DOMAINS = set(bb.FALLBACK_CHURN)
    bb.JUNK_DOMAINS = set(bb.FALLBACK_JUNK)
    latest = _latest()
    latest["names"].append({"id": "heimstaden", "name": "Heimstaden", "kind": "name", "comps": ["vonovia"], "candidates": []})
    bb.set_focus(latest, ["heimstaden", "branicks", "nosuchname"])
    assert bb.FOCUS["vonovia"] == "comp" and bb.FOCUS_OF["vonovia"] == ["heimstaden"]
    P = bb.prepare(latest, since=datetime(2026, 10, 9, 14, 45, tzinfo=timezone.utc))
    welt = P["C_rows"][0]
    assert welt["focus"] == "comp" and "comp of heimstaden" in welt["why"]
    cov = {"names": {"heimstaden": {"ok_24h": 400, "skipped_24h": 0, "last_mention": "2026-10-10T09:54:00+00:00"},
                     "branicks": {"ok_24h": 0}}}
    F = bb.focus_status(P, latest, cov, [welt])
    by = {f["id"]: f for f in F}
    assert by["heimstaden"]["comp_pub"] == 1 and by["heimstaden"]["ok_24h"] == 400
    assert by["nosuchname"]["known"] is False
    html = bb.focus_rows_html(F)
    assert "Nothing new; 1 comp item" in html and "no fetch answered in 24h" in html and "Not in this export" in html


def test_lint_warns_when_a_line_names_no_instrument():
    bb = _brief()
    _, warns = bb.lint({"so": {"1": "A lovely new dress collection for autumn."}}, [], {"1": "x"})
    assert any("names no instrument" in w for w in warns)
    _, ok = bb.lint({"so": {"1": "Fitch cut the rating; the 2029 notes are the instrument."}}, [], {"1": "x"})
    assert not ok


def test_focus_file_parses_without_a_yaml_library():
    bb = _brief()
    ids = bb.load_focus(REPO / "config" / "focus.yaml")
    assert 15 <= len(ids) <= 40 and "mpt" in ids and "paragon" in ids and "cheplapharm" in ids
    cfg = load_config()
    assert set(ids) == set(cfg.focus)


# ------------------------------------------------------------------ key outlets
def test_key_outlets_become_site_queries_and_are_reported(cfg, tmp_path):
    from newsflow.export import build_coverage
    from newsflow.store import Store
    intrum = cfg.name("intrum")
    assert "di.se" in intrum.key_outlets and "di.se" in intrum.site_queries
    assert "textilwirtschaft.de" in cfg.name("takko").key_outlets
    store = Store(cfg.db_path)
    cov = build_coverage(cfg, store, datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc))
    ko = {o["domain"]: o for o in cov["names"]["intrum"]["outlets"]}
    assert set(intrum.key_outlets) == set(ko) and ko["di.se"]["answering"] is False   # empty store: nothing answered
    store.close()


def test_focus_table_prints_key_outlet_status():
    bb = _brief()
    F = [{"id": "intrum", "name": "Intrum", "known": True, "pub": 0, "lead": 0, "comp_pub": 0, "rows": 0, "new": 0,
          "comp_new": 0, "ok_24h": 900, "skipped_24h": 0, "last": None,
          "outlets": [{"domain": "di.se", "answering": True, "last_hit": "2026-10-10T06:35:28+00:00"},
                      {"domain": "finanswatch.se", "answering": False, "site_jobs": 3, "last_hit": None},
                      {"domain": "realtid.se", "answering": False, "site_jobs": 0, "feed_jobs": 0, "last_hit": None}]}]
    html = bb.focus_rows_html(F)
    assert "key outlets 1 of 3 answering (dark: finanswatch.se; not yet searched: realtid.se)" in html
    assert "last in di.se 10 Oct" in html


def test_every_focus_credit_has_key_outlets_and_executive_or_context(cfg):
    thin = [nid for nid in cfg.focus if len(cfg.name(nid).key_outlets) < 3]
    assert not thin, thin


def test_carry_forward_reoffers_rows_older_than_the_previous_window(tmp_path):
    """Monday's first cut reaches back to Friday; the Sunday cut it continues covered 24 hours only,
    so Saturday's rows were never judged and must be offered, not held."""
    import json, subprocess, sys
    bb = _brief()
    docs = tmp_path / "docs"
    docs.mkdir()
    latest = _latest()
    latest["generated_at"] = "2026-10-12T04:30:00+00:00"
    def cand(cid, title, seen):
        return {"cluster_id": cid, "sources": 1, "alert_categories": [], "primary": {
            "id": cid, "title": title, "url": "u", "source": "handelsblatt.com", "domain": "handelsblatt.com",
            "country": "DE", "lang": "de", "published_at": None, "first_seen_at": seen, "route": "rss",
            "tier_hint": 3, "alias_where": "title", "screen_reason": "", "alias": "Branicks"}}
    latest["names"][1]["candidates"] = [cand(1, "Branicks Anleihe Samstag", "2026-10-10T10:00:00+00:00"),
                                        cand(2, "Branicks Anleihe Sonntag", "2026-10-11T12:00:00+00:00"),
                                        cand(3, "Branicks Anleihe Montag", "2026-10-12T04:00:00+00:00")]
    (docs / "latest.json").write_text(json.dumps(latest), encoding="utf-8")
    (docs / "coverage.json").write_text(json.dumps({"names": {}}), encoding="utf-8")
    prev = tmp_path / "prev.json"
    prev.write_text(json.dumps({"cut_at": "2026-10-11T16:00+00:00", "export_at": "2026-10-11T15:52:00+00:00",
                                "window_hours": 24, "lead": [], "so": {}}), encoding="utf-8")
    out = subprocess.run([sys.executable, str(REPO / "scripts" / "build_brief.py"), "--docs", str(docs),
                          "--noise", str(REPO / "config" / "noise.yaml"), "--focus", "", "--prev", str(prev),
                          "--since", "2026-10-09T14:45:00+00:00", "--shortlist"],
                         capture_output=True, text=True, check=True).stdout
    sl = json.loads(out)
    # the shortlist reads ages against the real clock: once these rows are over 72 hours old they
    # are offered under "late" rather than tier_a (13 October), which is still offered, not held
    ids = {r["id"] for r in sl["tier_a"]} | {int(x.split("|")[0]) for x in sl.get("late") or []}
    assert ids == {1, 3}          # Saturday (outside the Sunday window) and Monday (new); Sunday was judged
