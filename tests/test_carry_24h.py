"""13 October 2026, evening: carry a line 24 hours at most, except on Mondays.

The desk's complaint: old stories stayed on the page too long. A carried line lived 48 hours, and
any rewrite reset the clock, so the 8 October Cnam preview row was still on the page five days
later. Now a carried line expires once its article is 24 hours old; from Saturday to Monday the page
reaches back to Friday's first cut (24 October: back to Friday's last cut lost Friday's results on
Saturday morning). A line new to the page publishes once whatever its
age (a late row, a story first seen now). Rewriting a carried line does not keep it, except a
line tagged "manual check, not collected by the engine" that this cut rewrote.
"""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TUE = datetime(2026, 10, 13, 19, 0, tzinfo=timezone.utc)      # 20:00 London
MON = datetime(2026, 10, 19, 4, 45, tzinfo=timezone.utc)      # Monday's first cut, 05:45 London
MON_PM = datetime(2026, 10, 19, 14, 45, tzinfo=timezone.utc)  # Monday's last cut
FRI_LAST = datetime(2026, 10, 16, 14, 45, tzinfo=timezone.utc)
FRI_FIRST = datetime(2026, 10, 16, 4, 45, tzinfo=timezone.utc)


def _brief():
    spec = importlib.util.spec_from_file_location("build_brief_24h", REPO / "scripts" / "build_brief.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    mod.EMPLOYER = None
    return mod


def _row(rid, published, nid="cnam"):
    t = published.isoformat()
    return {"id": rid, "nid": nid, "name": nid, "route": "googlenews", "source": "src", "published": t, "seen": t}


def test_the_limit_is_24_hours_on_a_weekday_and_reaches_friday_on_a_monday():
    bb = _brief()
    assert bb.carry_limit_h(TUE) == 24
    assert bb.carry_cutoff(MON) == FRI_FIRST                      # 72 hours back at 05:45 on Monday
    assert bb.carry_cutoff(MON_PM) == FRI_FIRST                   # all of Monday keeps the weekend and Friday
    assert bb.carry_cutoff(MON + timedelta(days=1)) == MON + timedelta(days=1) - timedelta(hours=24)


def test_a_weekday_carries_24_hours_and_a_rewrite_does_not_extend_it():
    bb = _brief()
    rows = {"23h": _row("23h", TUE - timedelta(hours=23)), "25h": _row("25h", TUE - timedelta(hours=25)),
            "rw": _row("rw", TUE - timedelta(hours=30)), "new": _row("new", TUE - timedelta(hours=30)),
            "cnam": _row("cnam", TUE - timedelta(hours=127)), "cnam2": _row("cnam2", TUE - timedelta(hours=127))}
    prev = {"so": {k: "old line" for k in ("23h", "25h", "rw", "cnam", "cnam2")},
            "tags": {"cnam": ["manual check, not collected by the engine"],
                     "cnam2": ["manual check, not collected by the engine"]}}
    delta = {"so": {"rw": "rewritten line", "new": "first line", "cnam": "rechecked at 20:00, no outcome"},
             "tags": {"cnam": ["manual check, not collected by the engine"]}, "lead": ["23h", "new"]}
    j = bb.merge_judgement(prev, delta)
    assert j["added"] == ["new"]
    expired = bb.expire_stale(j, rows, TUE)
    assert set(expired) == {"25h", "rw", "cnam2"}     # cnam2: a manual check not redone this cut
    assert set(j["so"]) == {"23h", "new", "cnam"}
    assert j["lead"] == ["23h"]                        # a 30h story new to the page cannot lead


def test_a_monday_keeps_the_weekend_and_friday_and_drops_thursday():
    bb = _brief()
    sat = _row("sat", datetime(2026, 10, 17, 9, 0, tzinfo=timezone.utc))
    fri_eve = _row("fri_eve", FRI_LAST + timedelta(hours=2))
    fri_am = _row("fri_am", FRI_LAST - timedelta(hours=5))
    thu = _row("thu", FRI_FIRST - timedelta(hours=5))
    rows = {r["id"]: r for r in (sat, fri_eve, fri_am, thu)}
    j = bb.merge_judgement({"so": {k: "line" for k in rows}}, {"lead": ["sat", "fri_eve", "fri_am", "thu"]})
    expired = bb.expire_stale(j, rows, MON)
    assert expired == ["thu"]
    assert j["lead"] == ["sat", "fri_eve", "fri_am"]
    # Tuesday morning the weekend goes
    j = bb.merge_judgement({"so": {k: "line" for k in rows}}, {"lead": []})
    assert set(bb.expire_stale(j, rows, MON + timedelta(days=1))) == {"sat", "fri_eve", "fri_am", "thu"}


def test_lint_holds_the_lead_to_24_hours_but_lets_a_monday_lead_with_saturday():
    bb = _brief()
    sat = _row("sat", datetime(2026, 10, 17, 9, 0, tzinfo=timezone.utc))
    old = _row("old", TUE - timedelta(hours=30))
    errors, _ = bb.lint({"lead": ["sat"], "so": {"sat": "Fitch cut it to B; bonds 2 pts lower"}, "fresh": ["sat"],
                         "added": ["sat"]}, [], {"sat": "cnam"}, rows_by_id={"sat": sat}, now=MON)
    assert errors == []
    errors, warns = bb.lint({"lead": ["old"], "so": {"old": "Fitch cut it to B; bonds 2 pts lower"}, "fresh": ["old"],
                             "added": []}, [], {"old": "cnam"}, rows_by_id={"old": old}, now=TUE)
    assert any("lead[old]: article is 30h old; the lead takes only articles under 24h" in e for e in errors)
    assert any(w.startswith("so[old]: article is 30h old and carried; rewriting does not keep it") for w in warns)


def test_every_monday_cut_reads_back_to_friday_and_other_days_read_24_hours():
    bb = _brief()
    assert bb.auto_window_h(MON, bb.previous_cut(MON)) == 72                 # first cut after the gap
    assert bb.auto_window_h(MON_PM, bb.previous_cut(MON_PM)) == 72           # last Monday cut: Friday is 72h back
    mid = datetime(2026, 10, 19, 9, 45, tzinfo=timezone.utc)
    assert bb.auto_window_h(mid, bb.previous_cut(mid)) == 72                 # 77h back to Friday 04:45Z, capped at the export's 72
    tue = MON + timedelta(days=1)
    assert bb.auto_window_h(tue, bb.previous_cut(tue)) == 24
    assert bb.auto_window_h(TUE, bb.previous_cut(TUE)) == 24


def test_the_financialisation_plan_headlines_match_cnam(cfg):
    """egora's 13 October piece on the plan carried no lab word in its headline and went uncollected."""
    from newsflow.match import Matcher
    m = Matcher.from_config(cfg)

    def hit(title):
        return any(r.name_id == "cnam" for r in m.match(title, "", "fr"))
    assert hit("Financiarisation de la santé : un plan du Gouvernement pour trouver l'équilibre entre indépendance et investissements")
    assert hit("Financiarisation : Stéphanie Rist et Serge Papin présentent dix mesures pour les professionnels de santé")
    assert not hit("La financiarisation de l'agriculture inquiète les syndicats")


def test_weekend_cuts_keep_everything_since_friday_so_monday_has_it():
    """The writer's cron fires on Saturday and Sunday too; those cuts must not expire Saturday's lines."""
    bb = _brief()
    sat_am = datetime(2026, 10, 17, 8, 0, tzinfo=timezone.utc)
    sun_pm = datetime(2026, 10, 18, 14, 45, tzinfo=timezone.utc)
    assert bb.carry_cutoff(sat_am) == FRI_FIRST                           # Saturday morning still has Friday's results
    assert bb.carry_cutoff(sun_pm) == FRI_FIRST
    sat = _row("sat", datetime(2026, 10, 17, 7, 0, tzinfo=timezone.utc))
    j = bb.merge_judgement({"so": {"sat": "line"}}, {"lead": ["sat"]})
    assert bb.expire_stale(j, {"sat": sat}, sun_pm) == [] and j["lead"] == ["sat"]
    assert bb.auto_window_h(sun_pm, bb.previous_cut(sun_pm)) == 59         # 58h to Friday's first cut, plus an hour of margin
    fri_noon = datetime(2026, 10, 16, 11, 0, tzinfo=timezone.utc)   # Friday itself: plain 24 hours
    assert bb.carry_cutoff(fri_noon) == fri_noon - timedelta(hours=24)


def test_friday_results_stay_on_the_weekend_page_24_october():
    """24 October: Intrum's and Sodexo's results (Friday 05:00Z) left the page at Saturday's first cuts."""
    bb = _brief()
    fri_results = _row("intrum_q3", datetime(2026, 10, 23, 5, 0, tzinfo=timezone.utc), nid="intrum")
    sat_pm = datetime(2026, 10, 24, 14, 45, tzinfo=timezone.utc)
    sun_pm = datetime(2026, 10, 25, 19, 45, tzinfo=timezone.utc)
    mon_first = datetime(2026, 10, 26, 4, 45, tzinfo=timezone.utc)
    tue_first = datetime(2026, 10, 27, 4, 45, tzinfo=timezone.utc)
    for now in (sat_pm, sun_pm, mon_first):
        j = bb.merge_judgement({"so": {"intrum_q3": "line"}}, {"lead": ["intrum_q3"]})
        assert bb.expire_stale(j, {"intrum_q3": fri_results}, now) == [], now
    j = bb.merge_judgement({"so": {"intrum_q3": "line"}}, {"lead": []})
    assert bb.expire_stale(j, {"intrum_q3": fri_results}, tue_first) == ["intrum_q3"]
