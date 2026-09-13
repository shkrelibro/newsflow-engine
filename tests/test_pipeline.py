import json
from datetime import datetime, timedelta, timezone

from newsflow.export import write_exports
from newsflow.pipeline import _page_job, run_once
from newsflow.routes import fetch_bing_news, fetch_feed, fetch_gdelt, fetch_google_news
from newsflow.store import Store

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)


def _cfg_with_tmp(cfg, tmp_path):
    cfg.engine["db_path"] = str(tmp_path / "newsflow.db")
    cfg.export["out_dir"] = str(tmp_path / "docs")
    cfg.export["window_hours"] = 24 * 60
    return cfg


def _jobs(cfg, http):
    page = next(p for p in cfg.name("intrum").pages if "mononews" in p.url)
    return [
        lambda: fetch_google_news(http, '"Intrum"', "sv", "SE", ["intrum"]),
        lambda: fetch_bing_news(http, "Intrum", "sv", "SE", ["intrum"]),
        lambda: fetch_gdelt(http, '"Intrum"', ["intrum"]),
        lambda: fetch_feed(http, "https://e00-expansion.uecdn.es/rss/empresas.xml", [], 3, "ES", "es", "Expansión Empresas (ES)"),
        lambda: fetch_feed(http, "https://example.invalid/feed.xml", [], 3, "XX", "xx", "Broken feed"),
    ], page


def test_end_to_end(cfg, tmp_path, fake_http_factory, googlenews_xml, bing_xml, gdelt_json, outlet_feed_xml, tagpage_run1, tagpage_run2):
    cfg = _cfg_with_tmp(cfg, tmp_path)
    http = fake_http_factory({
        "news.google.com": googlenews_xml,
        "bing.com": bing_xml,
        "gdeltproject": gdelt_json,
        "expansion.uecdn.es": outlet_feed_xml,
        "mononews.gr/tag": tagpage_run1,
    })
    store = Store(cfg.db_path)
    jobs, page = _jobs(cfg, http)
    jobs.append(lambda: _page_job(http, store, page))

    # ---- run 1 (60-day window so the fixture dates count) ---------------------------------
    s1 = run_once(cfg, store, http=http, backfill_days=60, now=NOW, jobs=jobs)
    assert s1.jobs == 6
    assert s1.errors == 1                         # the broken feed
    assert s1.stale == 2                          # the two GDELT items are older than the window
    assert s1.unrelated == 1                      # BCE item from the outlet feed
    # google: 3 items, one of which shares its URL with a bing item -> 2 new + 1 also_route
    # bing: 2 items (1 new, 1 duplicate url) ; outlet feed: 2 matched ; page: seeded, 0
    assert s1.new_items == 6
    assert s1.screened == 1                       # Kauppalehti dividend page
    assert s1.candidates == 5
    assert s1.alerts == 2                         # bond pricing, Solvia portfolio sale (the Bing bond item is a duplicate URL)

    stats = store.stats()
    assert stats["items"] == 6 and stats["pages_tracked"] == 1
    dup = store.get_item_by_url("https://placera.se/nyheter/intrum-emitterar-nya-obligationer-2026-07-06")
    # jobs process as they complete, so either route may have stored it first; the other is noted
    assert dup is not None and ("bingnews:" in dup["also_routes"] or "googlenews:" in dup["also_routes"])
    seeded = [r for r in s1.source_results if r.route == "page"][0]
    assert seeded.ok and seeded.error == "seeded" and seeded.items == 0

    # ---- run 2: the tag page gained one article -------------------------------------------
    http.responses["mononews.gr/tag"] = tagpage_run2
    jobs2 = [lambda: _page_job(http, store, page)]
    s2 = run_once(cfg, store, http=http, backfill_days=60, now=NOW, jobs=jobs2)
    assert s2.new_items == 1 and s2.candidates == 1
    new = store.conn.execute("SELECT * FROM items WHERE route='page'").fetchone()
    assert "mononews.gr/business/intrum-hellas-polei" in new["canonical_url"]
    m = store.conn.execute("SELECT * FROM matches WHERE item_id=?", (new["id"],)).fetchone()
    assert m["name_id"] == "intrum" and m["alias"] in ("Intrum", "(page)")
    assert new["alert_candidate"] == 1            # Greek "Πώληση χαρτοφυλακίου" -> m_and_a

    # ---- export ---------------------------------------------------------------------------
    out = write_exports(cfg, store, now=NOW)
    latest = json.loads((out / "latest.json").read_text(encoding="utf-8"))
    name = next(n for n in latest["names"] if n["id"] == "intrum")
    assert name["candidate_count"] == 6
    assert name["screened"]["count"] == 1 and "noise_domain" in name["screened"]["by_reason"]
    assert latest["alerts"] and latest["alerts"][0]["alert_candidate"]
    assert latest["source_health"]["failing"] == 1
    # The bond pricing arrives from Google and Bing at the SAME url but under different titles,
    # "Intrum offentliggör prissättning av seniora säkerställda obligationer om 525 000 000 EUR"
    # and "Intrum emitterar nya obligationer". The two jobs run on different threads, so whichever
    # finishes first sets the stored title. Asserting on the Google wording alone failed about one
    # run in six, which is the worst kind of red CI: intermittent, and therefore ignored.
    titles = {c["primary"]["title"] for c in name["candidates"]}
    titles |= {a["title"] for c in name["candidates"] for a in (c.get("also") or [])}
    assert any("obligationer" in t for t in titles), titles
    assert (out / "index.html").exists() and (out / "alerts.json").exists() and (out / "health.json").exists()
    assert (out / "daily" / "2026-08-22.json").exists()
    html = (out / "index.html").read_text(encoding="utf-8")
    assert "Intrum AB" in html and "Alert candidates" in html

    # unresolved Google links are flagged so the editorial layer knows to use the source name
    di = [c for c in name["candidates"] if c["primary"]["domain"] == "di.se"]
    assert di and di[0]["primary"]["url_unresolved"] is True
    store.close()


def test_idempotent_rerun(cfg, tmp_path, fake_http_factory, googlenews_xml, bing_xml, gdelt_json, outlet_feed_xml, tagpage_run1):
    cfg = _cfg_with_tmp(cfg, tmp_path)
    http = fake_http_factory({
        "news.google.com": googlenews_xml, "bing.com": bing_xml, "gdeltproject": gdelt_json,
        "expansion.uecdn.es": outlet_feed_xml, "mononews.gr/tag": tagpage_run1,
    })
    store = Store(cfg.db_path)
    jobs, page = _jobs(cfg, http)
    run_once(cfg, store, http=http, backfill_days=60, now=NOW, jobs=jobs)
    s2 = run_once(cfg, store, http=http, backfill_days=60, now=NOW, jobs=jobs)
    assert s2.new_items == 0                      # everything already stored
    assert store.stats()["items"] == 6
    store.close()


def test_circuit_breaker_cancels_a_route_that_answers_empty(cfg, tmp_path):
    """A route that keeps answering ok-but-empty is being refused upstream, not finding nothing.

    It must be tripped and its queued jobs cancelled, so the rest of the run keeps its budget.
    Google News does exactly this: it answers slowly and empty rather than with an error.
    """
    import time as _time

    from newsflow.models import RawItem, SourceResult
    from newsflow.pipeline import JobSpec

    cfg = _cfg_with_tmp(cfg, tmp_path)
    cfg.engine["circuit_breaker"] = {"enabled": True, "consecutive_empty": 5}
    cfg.engine["max_workers"] = 1
    store = Store(cfg.db_path)

    calls = {"googlenews": 0, "rss": 0}

    def blocked_google():
        calls["googlenews"] += 1
        _time.sleep(0.02)                      # slow and empty, the signature of being refused
        return [], SourceResult("googlenews", "g", True, 0, "", 0.02)

    def working_rss():
        calls["rss"] += 1
        it = RawItem(title="Intrum sells a portfolio", link=f"https://ex.invalid/r{calls['rss']}",
                     route="rss", query="feed", published_at=NOW, name_ids=["intrum"])
        return [it], SourceResult("rss", "r", True, 1, "", 0.01)

    jobs = [JobSpec(f"g{i}", "googlenews", blocked_google) for i in range(40)]
    jobs += [JobSpec(f"r{i}", "rss", working_rss) for i in range(5)]

    s = run_once(cfg, store, http=None, now=NOW, jobs=jobs)

    assert s.tripped_routes == ["googlenews"]        # only the refusing route
    assert s.tripped > 0                             # its queued jobs were cancelled
    assert calls["googlenews"] < 40                  # it did not run them all
    assert calls["rss"] == 5                         # the working route still ran in full
    assert s.skipped == 0                            # the breaker fired, not the time budget
    assert any("tripped" in r.error for r in s.source_results if r.error)


def test_circuit_breaker_does_not_trip_when_a_route_returns_items(cfg, tmp_path):
    """An empty streak broken by a job that finds something must reset the counter.

    A quiet name on a quiet day legitimately returns nothing; that must not trip the route.
    """
    from newsflow.models import RawItem, SourceResult
    from newsflow.pipeline import JobSpec

    cfg = _cfg_with_tmp(cfg, tmp_path)
    cfg.engine["circuit_breaker"] = {"enabled": True, "consecutive_empty": 5}
    cfg.engine["max_workers"] = 1
    store = Store(cfg.db_path)

    seq = {"n": 0}

    def alternating():
        seq["n"] += 1
        if seq["n"] % 3 == 0:                        # every third job finds something
            it = RawItem(title="Intrum sells a portfolio", link=f"https://ex.invalid/{seq['n']}",
                         route="googlenews", query='"Intrum"', published_at=NOW, name_ids=["intrum"])
            return [it], SourceResult("googlenews", "g", True, 1, "", 0.01)
        return [], SourceResult("googlenews", "g", True, 0, "", 0.01)

    jobs = [JobSpec(f"g{i}", "googlenews", alternating) for i in range(30)]
    s = run_once(cfg, store, http=None, now=NOW, jobs=jobs)

    assert s.tripped_routes == []
    assert s.tripped == 0
    assert seq["n"] == 30                            # every job ran


# ------------------------------------------------------- state rollback guard
def test_rollback_guard_stops_a_run_on_state_older_than_the_last_export(cfg, tmp_path, monkeypatch):
    """A cache restore that hands back an older database must stop the run, loudly.

    This is the one failure that would discredit the brief: the engine forgets what it already
    reported and re-publishes weeks-old stories as new, and nothing in the run summary says so.
    """
    import pytest

    from newsflow.pipeline import StateRollback

    cfg = _cfg_with_tmp(cfg, tmp_path)
    monkeypatch.delenv("NEWSFLOW_ALLOW_STATE_RESET", raising=False)
    out = cfg.out_dir
    out.mkdir(parents=True, exist_ok=True)
    (out / "health.json").write_text(json.dumps({"stats": {"runs": 900}}), encoding="utf-8")

    store = Store(cfg.db_path)                       # empty: 0 runs against 900 published
    with pytest.raises(StateRollback) as err:
        run_once(cfg, store, http=None, now=NOW, jobs=[])
    assert "900" in str(err.value)
    assert store.run_count() == 0                    # and it did not record a run on the way out

    # the escape hatch is for a deliberate reset
    monkeypatch.setenv("NEWSFLOW_ALLOW_STATE_RESET", "1")
    s = run_once(cfg, store, http=None, now=NOW, jobs=[])
    assert s.run_id > 0


def test_rollback_guard_allows_a_database_that_is_level_or_ahead(cfg, tmp_path, monkeypatch):
    cfg = _cfg_with_tmp(cfg, tmp_path)
    monkeypatch.delenv("NEWSFLOW_ALLOW_STATE_RESET", raising=False)
    store = Store(cfg.db_path)

    # no export yet: nothing to compare against, so the run proceeds
    assert run_once(cfg, store, http=None, now=NOW, jobs=[]).run_id > 0

    out = cfg.out_dir
    out.mkdir(parents=True, exist_ok=True)
    (out / "health.json").write_text(json.dumps({"stats": {"runs": 1}}), encoding="utf-8")
    assert run_once(cfg, store, http=None, now=NOW, jobs=[]).run_id > 0     # level, then ahead
    assert run_once(cfg, store, http=None, now=NOW, jobs=[]).run_id > 0


def test_prune_keeps_runs_and_items_and_drops_the_old_ledger(cfg, tmp_path):
    """Pruning is what keeps the database inside the cache; it must not touch the run history."""
    from newsflow.models import SourceResult

    cfg = _cfg_with_tmp(cfg, tmp_path)
    cfg.engine["keep_source_results_days"] = 10
    store = Store(cfg.db_path)

    old = store.start_run(NOW.replace(year=2020))
    store.finish_run(old, NOW.replace(year=2020), 0, 0, notes="")
    store.add_source_results(old, [SourceResult("rss", "old feed", True, 1, "", 0.1)])
    assert store.conn.execute("SELECT COUNT(*) FROM source_results").fetchone()[0] == 1

    runs_before = store.run_count()
    run_once(cfg, store, http=None, now=NOW, jobs=[])

    rows = store.conn.execute("SELECT run_id FROM source_results").fetchall()
    assert all(r["run_id"] != old for r in rows)            # the 2020 ledger is gone
    assert store.run_count() == runs_before + 1             # the run history is not


# ------------------------------------------------------------------ stale dates
def test_stale_rejects_undated_search_hits_and_re_dated_archive_urls(cfg, tmp_path):
    """A claimed date is not a fact.

    On 13 September a 2009 C&A story arrived through Google News stamped that morning. Two holes
    let that class through: an item with no date skipped the age check entirely, and a recent
    claimed date was never tested against the year in the URL path.
    """
    from newsflow.models import RawItem, SourceResult
    from newsflow.pipeline import JobSpec

    cfg = _cfg_with_tmp(cfg, tmp_path)
    store = Store(cfg.db_path)
    old = NOW - timedelta(days=400)

    def job():
        items = [
            # 1. honestly old: rejected before, rejected now
            RawItem(title="Intrum old news", link="https://ex.invalid/a", route="googlenews",
                    query="q", published_at=old, name_ids=["intrum"]),
            # 2. undated from a search route: used to sail straight through
            RawItem(title="Intrum undated hit", link="https://ex.invalid/b", route="googlenews",
                    query="q", published_at=None, name_ids=["intrum"]),
            # 3. re-dated archive page: claims today, URL says 2009
            RawItem(title="Intrum re-dated archive piece",
                    link="https://ex.invalid/2009/03/intrum-story/", route="googlenews",
                    query="q", published_at=NOW, name_ids=["intrum"]),
            # 4. genuinely today, with a current year in the path: kept
            RawItem(title="Intrum sells a portfolio",
                    link="https://ex.invalid/2026/09/intrum-sells/", route="googlenews",
                    query="q", published_at=NOW, name_ids=["intrum"]),
            # 5. undated from a PAGE watcher: a new link on a watched newsroom is the event
            RawItem(title="Intrum newsroom item", link="https://ex.invalid/press/new",
                    route="page", query="page", published_at=None, name_ids=["intrum"]),
        ]
        return items, SourceResult("googlenews", "mixed", True, len(items), "", 0.1)

    s = run_once(cfg, store, http=None, now=NOW, jobs=[JobSpec("mixed", "googlenews", job)])

    assert s.fetched == 5
    assert s.stale == 3                       # old, undated-search, re-dated archive
    assert s.new_items == 2                   # the current one and the page watcher's
    kept = {r["title"] for r in store.conn.execute("SELECT title FROM items").fetchall()}
    assert "Intrum sells a portfolio" in kept
    assert "Intrum newsroom item" in kept     # undated is meaningful from a page watcher
    assert "Intrum re-dated archive piece" not in kept
    assert "Intrum undated hit" not in kept


def test_export_revalidates_stored_matches_against_the_current_config(cfg, tmp_path):
    """A config fix must reach the next brief, not the one after the junk ages out.

    Matches are made at collection and stored. On 13 September the context guards merged at
    lunchtime and the 15:08 brief still published American college football under Quick and a
    village carnival procession under Carnival Corporation, because those rows were matched that
    morning and the export re-served them unchanged.
    """
    from newsflow.export import build_export
    from newsflow.models import RawItem, SourceResult
    from newsflow.pipeline import JobSpec

    cfg = _cfg_with_tmp(cfg, tmp_path)
    store = Store(cfg.db_path)

    good = "Intrum emitterar nya obligationer om 525 000 000 EUR"
    junk = "Three Quick Takeaways From No. 11 Oklahoma's Loss to Michigan"

    def job():
        items = [
            RawItem(title=good, link="https://ex.invalid/2026/09/intrum-bond", route="googlenews",
                    query="q", published_at=NOW, name_ids=["intrum"], lang="sv"),
            RawItem(title=junk, link="https://ex.invalid/2026/09/oklahoma", route="googlenews",
                    query="q", published_at=NOW, name_ids=["quick"], lang="en"),
        ]
        return items, SourceResult("googlenews", "mixed", True, 2, "", 0.1)

    run_once(cfg, store, http=None, now=NOW, jobs=[JobSpec("mixed", "googlenews", job)])

    # Force the junk row into the store as a live candidate for Quick, as an older config would
    # have left it, so the export has something to revalidate out.
    row = store.conn.execute("SELECT id FROM items WHERE title=?", (junk,)).fetchone()
    if row is None:                                   # already rejected at collection: also fine
        store.conn.execute(
            "INSERT INTO items(canonical_url, raw_link, title, title_key, first_seen_at, route, "
            "status, cluster_id, run_id) VALUES (?,?,?,?,?,?,?,?,?)",
            ("https://ex.invalid/2026/09/oklahoma", "https://ex.invalid/2026/09/oklahoma", junk,
             "okl", NOW.isoformat(), "googlenews", "candidate", 99, 1))
        iid = store.conn.execute("SELECT id FROM items WHERE title=?", (junk,)).fetchone()["id"]
        store.conn.execute(
            "INSERT INTO matches(item_id, name_id, alias, where_, confidence) VALUES (?,?,?,?,?)",
            (iid, "quick", "Quick", "title", 1.0))
        store.conn.commit()

    data = build_export(cfg, store, NOW, 24.0)
    titles = {c["primary"]["title"] for n in data["names"] for c in n["candidates"]}
    assert good in titles                              # a real match survives
    assert junk not in titles                          # the stale one does not reach the pile
    quick = next(n for n in data["names"] if n["id"] == "quick")
    assert quick["candidate_count"] == 0
