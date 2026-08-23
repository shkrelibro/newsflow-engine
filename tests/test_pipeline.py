import json
from datetime import datetime, timezone

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
    primaries = {c["primary"]["title"] for c in name["candidates"]}
    assert any("525 000 000 EUR" in t for t in primaries)
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
