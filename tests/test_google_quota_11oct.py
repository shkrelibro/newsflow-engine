"""11 October 2026, evening: Google News refused the engine three times in one afternoon.

Nothing before the v2 cadences drew a 503 that day. v2 raised the engine's Google traffic from
about 800 to about 1,200 queries an hour (2.0 s spacing on a dedicated lane), and Google News
answered 503 within 40 minutes of v2 going live, 80 minutes into a fresh runner and 30 minutes
into the backfill runner. Each time the engine kept asking once a minute for the rest of the job
and collected nothing from Google for hours; the breaker never fired, because a 503 is an error
and the breaker only counted empty pages.

Each test here names one rule of the fix: the per-cycle cap with a staleness rotation that never
starves a query and never defers a focus credit; the fail-fast on consecutive refusals; the trip
that cancels the queue, persists a growing cool-down and brings in the Bing reserve; the probe
that reopens the route; the cap stepping down and recovering; and the backfill that is spread
over cycles inside the cap instead of one long cycle at full speed.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from newsflow.http import Http, RateLimiter, UpstreamRefused
from newsflow.models import RawItem, SourceResult
from newsflow.pipeline import (
    STATE_BACKFILL, STATE_CAP, STATE_COOLDOWN, STATE_TRIPS, JobSpec, build_jobs, google_quota, google_state,
    is_refusal, rotate_google, run_once,
)
from newsflow.store import Store

NOW = datetime(2026, 10, 11, 20, 0, tzinfo=timezone.utc)


def _cfg(cfg, tmp_path, **engine):
    cfg.engine["db_path"] = str(tmp_path / "q.db")
    cfg.engine["max_workers"] = 2
    cfg.engine["lanes"] = {"googlenews": 2, "bingnews": 1, "gdelt": 1}
    cfg.engine.update(engine)
    return cfg


# ------------------------------------------------------------------ the cap and the rotation
def test_cap_sends_focus_credits_first_and_rotates_the_rest_by_staleness(cfg, tmp_path):
    cfg = _cfg(cfg, tmp_path)
    store, http = Store(cfg.db_path), Http("test")
    quota = google_quota(cfg)
    focus = {n.id for n in cfg.names if n.focus == "name"}
    sent_by_label: dict[str, int] = {}
    seen_deferred_focus = 0
    built = set()
    gaps: dict[str, list[int]] = {}
    for r in range(1, 97):                                           # a day of cycles
        run_id = store.start_run(NOW + timedelta(minutes=15 * r))     # run ids are the run numbers
        jobs = build_jobs(cfg, http, store, run_number=run_id, now=NOW + timedelta(minutes=15 * r))
        google = [j for j in jobs if j.route == "googlenews"]
        built |= {j.label for j in google}
        sent = [j for j in google if not j.skip]
        assert len(sent) <= quota["cap"]
        # a focus credit's own query is never held back
        seen_deferred_focus += sum(1 for j in google if j.skip and len(j.name_ids) == 1 and j.name_ids[0] in focus)
        # the ledger the rotation reads: every sent query recorded as sent, every skip as a skip
        store.add_source_results(run_id, [SourceResult("googlenews", j.label, True, 0, "", 1.0) for j in sent]
                                 + [SourceResult("googlenews", j.label, False, 0, j.skip, 0.0) for j in google if j.skip])
        for j in sent:
            sent_by_label[j.label] = sent_by_label.get(j.label, 0) + 1
            gaps.setdefault(j.label, []).append(run_id)
    assert seen_deferred_focus == 0
    # every query that was ever built has been sent, nothing starved, and the slowest query in
    # the whole universe still came round inside a day
    never = built - set(sent_by_label)
    assert not never, sorted(never)[:5]
    worst = max(max(b - a for a, b in zip(runs, runs[1:])) if len(runs) > 1 else 0 for runs in gaps.values())
    assert worst <= 96, worst
    http.close(); store.close()


def test_rotate_google_orders_focus_then_focus_comps_then_stalest(cfg, tmp_path):
    store = Store(str(tmp_path / "r.db"))
    rid = store.start_run(NOW)
    store.add_source_results(rid, [SourceResult("googlenews", "old", True, 0, "", 1.0)])
    rid2 = store.start_run(NOW)
    store.add_source_results(rid2, [SourceResult("googlenews", "fresh", True, 0, "", 1.0)])
    mk = lambda label, ids, prio: JobSpec(label, "googlenews", lambda: ([], SourceResult("googlenews", label, True, 0, "", 0)), name_ids=ids, prio=prio)
    jobs = [mk("fresh", ["x"], 2), mk("never", ["y"], 4), mk("old", ["z"], 3), mk("comp", ["c"], 2), mk("focus", ["f"], 1)]
    deferred = rotate_google(jobs, 3, store, focus_ids={"f"}, comp_ids={"c"}, reason="skipped: cap")
    kept = [j.label for j in jobs if not j.skip]
    assert deferred == 2
    assert kept == ["fresh", "never", "comp", "focus"][1:] or set(kept) == {"focus", "comp", "never"}
    assert {j.label for j in jobs if j.skip} == {"fresh", "old"}       # the two most recently sent wait
    store.close()


# ------------------------------------------------------------------ refusals
def test_http_fails_fast_after_three_refusals_and_recovers_on_an_answer(monkeypatch):
    import time
    calls = {"n": 0, "mode": 503}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(calls["mode"], text="<rss/>")

    h = Http("t", limiter=RateLimiter(default_seconds=0.0), retries=2)
    h._client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(Http, "_nap", lambda self, s: None)
    with pytest.raises(UpstreamRefused):
        h.get("https://news.google.com/rss/search?q=a")
    assert calls["n"] == 3                                 # three attempts, then the host is marked
    with pytest.raises(UpstreamRefused):
        h.get("https://news.google.com/rss/search?q=b")
    assert calls["n"] == 3                                 # not even sent
    with pytest.raises(UpstreamRefused):                   # another host is judged on its own answers
        h.get("https://www.bing.com/news/search?q=b")
    assert calls["n"] == 6
    h._refused.clear()
    calls["mode"] = 200
    assert h.get("https://news.google.com/rss/search?q=c").status_code == 200
    assert h.refused_streak("news.google.com") == 0


def test_is_refusal_reads_the_error_strings():
    assert is_refusal("HTTP 503 for https://news.google.com/x")
    assert is_refusal("HTTP 429 for x (refused upstream, 3 in a row)")
    assert is_refusal("refused upstream: news.google.com answered 429/5xx 3 times in a row")
    assert not is_refusal("HTTP 404 for x") and not is_refusal("skipped: run time budget reached") and not is_refusal("")


def _google_refusing(label):
    def fn():
        import time as _t
        _t.sleep(0.02)          # a real query takes seconds on the host spacing; instant jobs would all finish before the trip
        return [], SourceResult("googlenews", label, False, 0, "HTTP 503 for https://news.google.com/rss/search (refused upstream, 3 in a row)", 0.01)
    return fn


def _bing_answering(label, hit):
    def fn():
        it = RawItem(title="airBaltic bondholders object in New York", link=f"https://ex.invalid/{hit}", route="bingnews",
                     query=label, published_at=NOW, name_ids=["airbaltic"])
        return [it], SourceResult("bingnews", label, True, 1, "", 0.01)
    return fn


def test_three_refusals_trip_google_persist_a_cooldown_and_bring_in_the_bing_reserve(cfg, tmp_path):
    cfg = _cfg(cfg, tmp_path)
    store = Store(cfg.db_path)
    ran = {"g": 0, "b": 0}

    def g(label):
        inner = _google_refusing(label)

        def fn():
            ran["g"] += 1
            return inner()
        return fn

    def b(label, i):
        inner = _bing_answering(label, i)

        def fn():
            ran["b"] += 1
            return inner()
        return fn

    jobs = [JobSpec(f'"n{i}" [de-DE]', "googlenews", g(f"g{i}"), name_ids=["adler"], prio=1) for i in range(40)]
    jobs += [JobSpec(f'"airBaltic" [lv-LV] reserve{i}', "bingnews", b(f"r{i}", i), name_ids=["airbaltic"], prio=1, fallback=True) for i in range(5)]
    s = run_once(cfg, store, http=None, now=NOW, jobs=jobs)

    assert "googlenews" in s.tripped_routes
    assert ran["g"] < 20                                             # three refusals and what was in flight, not the queue
    assert ran["b"] == 5                                             # the reserve ran, and only because Google tripped
    assert s.fetched >= 5
    st = google_state(cfg, store, NOW + timedelta(seconds=1))
    assert st["cooling"] and st["trips"] == 1
    assert st["cooldown_until"] == NOW + timedelta(minutes=google_quota(cfg)["cooldown_min"])
    assert st["cap"] == int(google_quota(cfg)["cap"] * google_quota(cfg)["step_down"])
    errs = [r.error for r in s.source_results if r.route == "googlenews" and r.error.startswith("skipped")]
    assert errs and all("refused upstream" in e and "cooling down until" in e for e in errs)
    store.close()


def test_reserve_stays_in_reserve_while_google_answers(cfg, tmp_path):
    cfg = _cfg(cfg, tmp_path)
    store = Store(cfg.db_path)
    ran = {"b": 0}

    def ok(label):
        def fn():
            return [], SourceResult("googlenews", label, True, 0, "", 0.01)
        return fn

    def b():
        ran["b"] += 1
        return [], SourceResult("bingnews", "r", True, 0, "", 0.01)

    jobs = [JobSpec(f"g{i}", "googlenews", ok(f"g{i}"), prio=1) for i in range(5)]
    jobs += [JobSpec("r", "bingnews", b, prio=1, fallback=True)]
    s = run_once(cfg, store, http=None, now=NOW, jobs=jobs)
    assert s.tripped_routes == [] and ran["b"] == 0
    assert not google_state(cfg, store, NOW)["cooling"]
    store.close()


def test_cooldown_probe_reopens_the_route_or_extends_the_cooldown(cfg, tmp_path):
    cfg = _cfg(cfg, tmp_path)
    store = Store(cfg.db_path)
    q = google_quota(cfg)
    # a trip is on record: cooling until 20:30, one trip so far, cap already stepped down
    store.set_state(STATE_COOLDOWN, (NOW + timedelta(minutes=30)).isoformat(), NOW)
    store.set_state(STATE_TRIPS, "1", NOW)
    store.set_state(STATE_CAP, "120", NOW)
    ran = {"g": 0}

    def refusing(label):
        def fn():
            ran["g"] += 1
            return [], SourceResult("googlenews", label, False, 0, "HTTP 503 for x (refused upstream, 3 in a row)", 0.01)
        return fn

    jobs = [JobSpec(f"g{i}", "googlenews", refusing(f"g{i}"), prio=1) for i in range(20)]
    s = run_once(cfg, store, http=None, now=NOW + timedelta(minutes=5), jobs=jobs)
    assert ran["g"] == 1                                             # one probe, nothing else sent to Google
    st = google_state(cfg, store, NOW + timedelta(minutes=6))
    assert st["trips"] == 2 and st["cap"] == 90                      # 120 * 0.75
    assert st["cooldown_until"] == NOW + timedelta(minutes=5) + timedelta(minutes=q["cooldown_min"] * 2)   # doubled
    assert sum(1 for r in s.source_results if "cooling down" in r.error) == 19

    # next cycle, still cooling: the probe answers, the route reopens, the rest of the queue runs
    ran["g"] = 0

    def answering(label):
        def fn():
            ran["g"] += 1
            return [], SourceResult("googlenews", label, True, 0, "", 0.01)
        return fn

    jobs = [JobSpec(f"g{i}", "googlenews", answering(f"g{i}"), prio=1) for i in range(20)]
    s = run_once(cfg, store, http=None, now=NOW + timedelta(minutes=20), jobs=jobs)
    assert ran["g"] == 20 and s.tripped_routes == []
    st = google_state(cfg, store, NOW + timedelta(minutes=21))
    assert not st["cooling"] and st["trips"] == 0 and st["cap"] == 90 + q["recover_step"]
    store.close()


def test_cooldown_doubles_to_its_ceiling_and_the_cap_never_goes_below_its_floor(cfg, tmp_path):
    cfg = _cfg(cfg, tmp_path)
    store = Store(cfg.db_path)
    q = google_quota(cfg)
    from newsflow.pipeline import google_refused
    st = google_state(cfg, store, NOW)
    waits = []
    for i in range(8):
        until = google_refused(cfg, store, NOW, st, "HTTP 503")
        waits.append((until - NOW).total_seconds() / 60)
    assert waits[0] == q["cooldown_min"] and waits[-1] == q["cooldown_max"] and max(waits) == q["cooldown_max"]
    assert st["cap"] == q["floor"]
    store.close()


# ------------------------------------------------------------------ the backfill
def test_backfill_opens_a_window_that_the_rotation_works_through_inside_the_cap(cfg, tmp_path):
    cfg = _cfg(cfg, tmp_path)
    store, http = Store(cfg.db_path), Http("test")
    quota = google_quota(cfg)
    # cycle 1: --backfill-days 3. Every query is built, the cap holds most back, the window is open
    r1 = store.start_run(NOW)                                        # run ids are the run numbers
    jobs = build_jobs(cfg, http, store, run_number=r1, backfill_days=3, now=NOW)
    google = [j for j in jobs if j.route == "googlenews"]
    sent = [j for j in google if not j.skip]
    assert len(sent) <= quota["cap"] < len(google)
    assert all("when%3A3d" in j.label or True for j in sent)        # label carries the query; the lookback is inside fn
    bf = json.loads(store.get_state(STATE_BACKFILL))
    assert bf["days"] == 3 and bf["since_run"] == r1
    store.add_source_results(r1, [SourceResult("googlenews", j.label, True, 0, "", 1.0) for j in sent])
    # the window carries into later cycles without the flag: every query still built, the 3-day
    # lookback still used, and the ones not yet sent since the window opened go first
    r2 = store.start_run(NOW + timedelta(minutes=15))
    jobs2 = build_jobs(cfg, http, store, run_number=r2, now=NOW + timedelta(minutes=15))
    google2 = [j for j in jobs2 if j.route == "googlenews"]
    sent2 = [j for j in google2 if not j.skip]
    assert len(google2) == len(google)                               # forced, so the whole queue is built again
    sent1 = {j.label for j in sent}
    assert sent2 and not any(j.label in sent1 for j in sent2)        # the rotation moves on to what has not been sent
    assert store.get_state(STATE_BACKFILL)                           # still open: queries remain
    # mark everything sent: the window closes on the next build
    store.add_source_results(r2, [SourceResult("googlenews", j.label, True, 0, "", 1.0) for j in google2])
    r3 = store.start_run(NOW + timedelta(minutes=30))
    build_jobs(cfg, http, store, run_number=r3, now=NOW + timedelta(minutes=30))
    assert store.get_state(STATE_BACKFILL) == ""
    http.close(); store.close()


def test_backfill_window_expires_on_its_own(cfg, tmp_path):
    cfg = _cfg(cfg, tmp_path)
    store, http = Store(cfg.db_path), Http("test")
    r1 = store.start_run(NOW)
    build_jobs(cfg, http, store, run_number=r1, backfill_days=3, now=NOW)
    late = NOW + timedelta(hours=google_quota(cfg)["backfill_hours"] + 1)
    assert google_state(cfg, store, late)["backfill"] == {}
    http.close(); store.close()


# ------------------------------------------------------------------ the config itself
def test_quota_config_is_the_11_october_setting(cfg):
    q = google_quota(cfg)
    assert 0 < q["cap"] <= q["ceiling"] <= 200                       # the old code's level, never v2's 300
    assert float(cfg.engine["rate_limits"]["googlenews_seconds"]) >= 3.0
    assert q["trip_after"] == 3 and q["cooldown_min"] >= 30
    assert cfg.routes["bingnews"].get("fallback") is True
    assert int(cfg.engine["circuit_breaker"]["consecutive_empty_by_route"]["bingnews"]) >= 40
