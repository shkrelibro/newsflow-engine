"""The primary-source layer: config/sources/primary.yaml and the three engine fixes it needs.

Each test names the failure it guards against, because every one of them was found by reading
the engine against a real newsroom rather than by imagining one.
"""
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

from newsflow.config import load_config
from newsflow.export import build_export
from newsflow.models import RawItem, SourceResult
from newsflow.pipeline import JobSpec, _page_job, run_once
from newsflow.store import Store

REPO = Path(__file__).parent.parent
NOW = datetime(2026, 9, 28, 8, 0, tzinfo=timezone.utc)


def _cfg_with_tmp(cfg, tmp_path):
    cfg.engine["db_path"] = str(tmp_path / "newsflow.db")
    cfg.export["out_dir"] = str(tmp_path / "docs")
    cfg.export["window_hours"] = 24 * 60
    return cfg


# ------------------------------------------------------------------ the overlay file
def _mini_config(tmp_path: Path, primary: dict) -> Path:
    root = tmp_path / "config"
    (root / "names").mkdir(parents=True)
    (root / "sources").mkdir()
    (root / "newsflow.yaml").write_text("engine: {}\nroutes: {}\nexport: {}\n", encoding="utf-8")
    (root / "names" / "acme.yaml").write_text(
        "id: acme\nname: Acme\nkind: name\nhome_country: DE\n"
        "markets:\n  - {country: DE, lang: de}\n"
        "aliases:\n  - {text: Acme, search: true}\n"
        "sources:\n  pages:\n    - {url: 'https://acme.example/news/', name: Acme news (hand), tier: 0}\n",
        encoding="utf-8")
    (root / "names" / "beta.yaml").write_text(
        "id: beta\nname: Beta\nkind: comp\nhome_country: FR\nmarkets:\n  - {country: FR, lang: fr}\n"
        "aliases:\n  - {text: Beta, search: true}\n", encoding="utf-8")
    (root / "sources" / "primary.yaml").write_text(yaml.safe_dump(primary, sort_keys=False), encoding="utf-8")
    return root


def test_primary_overlay_merges_pages_and_feeds_and_skips_urls_the_name_file_already_has(tmp_path):
    root = _mini_config(tmp_path, {"names": {
        "acme": {
            "pages": [
                {"url": "https://acme.example/news/", "name": "Acme news (overlay)", "tier": 0},        # already in the name file
                {"url": "https://acme.example/investors/", "name": "Acme investors", "tier": 0,
                 "country": "DE", "lang": "en", "require_alias": False, "link_pattern": r"acme\.example/investors/.+"},
            ],
            "feeds": [{"url": "https://acme.example/feed.xml", "name": "Acme RSS", "tier": 0, "require_alias": False}],
        },
        "beta": {"pages": [{"url": "https://beta.example/presse/", "name": "Beta presse", "tier": 0, "country": "FR", "lang": "fr"}]},
        "nosuchname": {"pages": [{"url": "https://x.example/", "name": "typo"}]},
    }})
    cfg = load_config(root)
    acme = cfg.name("acme")
    assert [p.url for p in acme.pages] == ["https://acme.example/news/", "https://acme.example/investors/"]
    assert acme.pages[0].name == "Acme news (hand)"                     # the name file wins on a shared URL
    inv = acme.pages[1]
    assert inv.kind == "page" and inv.tier == 0 and inv.country == "DE" and inv.lang == "en"
    assert inv.require_alias is False and inv.link_pattern == r"acme\.example/investors/.+"
    assert inv.name_ids == ["acme"]                                     # attributed to the name it sits under
    assert [f.url for f in acme.feeds] == ["https://acme.example/feed.xml"] and acme.feeds[0].kind == "feed"
    assert [p.url for p in cfg.name("beta").pages] == ["https://beta.example/presse/"]
    assert cfg.primary_unknown == ["nosuchname"]                        # reported, not swallowed
    assert cfg.primary_counts == {"pages": 2, "feeds": 1, "duplicates": 1}


def test_primary_overlay_is_optional(tmp_path):
    root = _mini_config(tmp_path, {"names": {}})
    (root / "sources" / "primary.yaml").unlink()
    cfg = load_config(root)
    assert len(cfg.name("acme").pages) == 1 and cfg.primary_unknown == []


def test_the_committed_primary_layer_loads_and_every_pattern_compiles():
    """The real file: every id has a name file, every link_pattern is a valid regex, and every
    entry carries a URL the watcher can fetch."""
    cfg = load_config(REPO / "config")
    assert cfg.primary_unknown == [], cfg.primary_unknown
    for n in cfg.names:
        for p in n.pages + n.feeds:
            assert p.url.startswith("http"), (n.id, p.url)
            if p.link_pattern:
                re.compile(p.link_pattern)
    assert cfg.primary_counts["pages"] > 200                             # the layer is actually there


# ------------------------------------------------------------------ circuit breaker
def test_circuit_breaker_leaves_the_page_route_alone(cfg, tmp_path):
    """A quiet newsroom answers ok and empty on every visit. That is not a refusal.

    With forty watchers the page route already tripped in the 25 and 28 September runs; with one
    per name it would trip every cycle and cancel the primary sources.
    """
    import time as _time

    cfg = _cfg_with_tmp(cfg, tmp_path)
    cfg.engine["circuit_breaker"] = {"enabled": True, "consecutive_empty": 5}
    cfg.engine["max_workers"] = 1
    store = Store(cfg.db_path)
    calls = {"page": 0, "googlenews": 0}

    def quiet_page():
        calls["page"] += 1
        return [], SourceResult("page", "newsroom", True, 0, "", 0.01)

    def refused_google():
        calls["googlenews"] += 1
        _time.sleep(0.02)                                        # slow and empty: the signature of a refusal
        return [], SourceResult("googlenews", "g", True, 0, "", 0.02)

    jobs = [JobSpec(f"p{i}", "page", quiet_page) for i in range(40)]
    jobs += [JobSpec(f"g{i}", "googlenews", refused_google) for i in range(40)]
    s = run_once(cfg, store, http=None, now=NOW, jobs=jobs)

    assert calls["page"] == 40                                   # every watcher was visited
    assert s.tripped_routes == ["googlenews"]                    # the search index still trips
    assert calls["googlenews"] < 40


def test_circuit_breaker_routes_are_configurable(cfg, tmp_path):
    cfg = _cfg_with_tmp(cfg, tmp_path)
    cfg.engine["circuit_breaker"] = {"enabled": True, "consecutive_empty": 5, "routes": ["page"]}
    cfg.engine["max_workers"] = 1
    store = Store(cfg.db_path)
    jobs = [JobSpec(f"p{i}", "page", lambda: ([], SourceResult("page", "p", True, 0, "", 0.0))) for i in range(20)]
    s = run_once(cfg, store, http=None, now=NOW, jobs=jobs)
    assert s.tripped_routes == ["page"]                          # opted in explicitly, so it trips


# ------------------------------------------------------------------ first visit under backfill
def _newsroom(n: int) -> str:
    rows = "".join(f'<li><a href="/news/release-{i:02d}-a-headline-long-enough">Release {i:02d}: a headline long enough</a></li>' for i in range(n))
    return f"<html><body><nav><a href='/about'>About us</a></nav><ul>{rows}</ul></body></html>"


def test_first_visit_under_backfill_takes_only_the_newest_links(cfg, tmp_path, fake_http_factory):
    """--backfill-days opens the flood gate on every first visit at once. Cap it."""
    from newsflow.config import PageSource

    cfg = _cfg_with_tmp(cfg, tmp_path)
    store = Store(cfg.db_path)
    http = fake_http_factory({"acme.example/news": _newsroom(20)})
    page = PageSource(url="https://acme.example/news/", name="Acme newsroom", tier=0, name_ids=["intrum"],
                      link_pattern=r"acme\.example/news/.+", require_alias=False)

    items, res = _page_job(http, store, page, backfill=True, seed_items=5)
    assert res.ok and len(items) == 5
    assert [it.link for it in items] == [f"https://acme.example/news/release-{i:02d}-a-headline-long-enough" for i in range(5)]
    assert res.error == "seeded with the newest 5 of 20 article links"
    assert store.page_seen_count(page.url) == 21                 # every link is marked seen, nav included

    # the next visit is a normal one: a new link at the top is the event, the archive is not
    http.responses["acme.example/news"] = _newsroom(21).replace("release-20", "release-new")
    items2, res2 = _page_job(http, store, page, backfill=True, seed_items=5)
    assert [it.link for it in items2] == ["https://acme.example/news/release-new-a-headline-long-enough"]


def test_first_visit_without_backfill_still_only_seeds(cfg, tmp_path, fake_http_factory):
    from newsflow.config import PageSource

    cfg = _cfg_with_tmp(cfg, tmp_path)
    store = Store(cfg.db_path)
    http = fake_http_factory({"acme.example/news": _newsroom(6)})
    page = PageSource(url="https://acme.example/news/", name="Acme newsroom", tier=0, name_ids=["intrum"], link_pattern=r"/news/.+")
    items, res = _page_job(http, store, page, backfill=False, seed_items=5)
    assert items == [] and res.error == "seeded"


# ------------------------------------------------------------------ export revalidation
def test_export_keeps_a_newsroom_row_whose_headline_does_not_name_the_company(cfg, tmp_path):
    """"Pricing of EUR 400m senior secured notes" on the company's own newsroom names no alias.

    It is attributed by the watcher (require_alias false, alias "(page)"). Re-testing that
    headline against the alias list at export screened exactly the rows the layer exists for.
    """
    cfg = _cfg_with_tmp(cfg, tmp_path)
    store = Store(cfg.db_path)
    intrum = cfg.name("intrum")
    page = next(p for p in intrum.pages if not p.require_alias)   # any own-newsroom watcher

    def job():
        items = [
            RawItem(title="Pricing of EUR 400m senior secured notes due 2031", link="https://ex.invalid/press/pricing-2031",
                    route="page", query=page.url, published_at=None, name_ids=["intrum"], lang="en", source_name=page.name),
            RawItem(title="Intrum publishes its interim report", link="https://ex.invalid/press/interim",
                    route="page", query=page.url, published_at=None, name_ids=["intrum"], lang="en", source_name=page.name),
        ]
        return items, SourceResult("page", page.name, True, 2, "", 0.1)

    s = run_once(cfg, store, http=None, now=NOW, jobs=[JobSpec(page.name, "page", job)])
    assert s.new_items == 2 and s.candidates == 2

    out = build_export(cfg, store, NOW, window_hours=48)
    entry = next(n for n in out["names"] if n["id"] == "intrum")
    titles = {c["primary"]["title"] for c in entry["candidates"]}
    assert "Pricing of EUR 400m senior secured notes due 2031" in titles
    assert "Intrum publishes its interim report" in titles
    assert not [sc for sc in entry["screened"]["items"] if sc["screen_reason"].startswith("revalidated")]


# ------------------------------------------------------------------ job order
def test_primary_sources_run_before_the_search_routes(cfg, tmp_path, fake_http_factory):
    """The budget tail must never hold a newsroom: pages and name feeds go first, in every run."""
    from newsflow.pipeline import build_jobs

    cfg = _cfg_with_tmp(cfg, tmp_path)
    store = Store(cfg.db_path)
    http = fake_http_factory({})
    for run_number in (2, 3, 4, 12):                       # pages are due on even runs; feeds always
        jobs = build_jobs(cfg, http, store, run_number)
        routes = [j.route for j in jobs]
        primary = [i for i, j in enumerate(jobs) if j.route == "page" or (j.route == "rss" and j.name_ids)]
        others = [i for i, j in enumerate(jobs) if not (j.route == "page" or (j.route == "rss" and j.name_ids))]
        assert primary, f"run {run_number}: no primary jobs at all"
        assert max(primary) < min(others), f"run {run_number}: a primary source sits behind a search job"
        if run_number % 2 == 0:
            assert routes.count("page") >= 200                # the layer is in the job list


# ------------------------------------------------------------------ the check script
def test_check_primary_reports_what_the_watcher_would_accept(fake_http_factory):
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_primary", REPO / "scripts" / "check_primary.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    http = fake_http_factory({"acme.example/news": _newsroom(7), "acme.example/feed": "<rss><channel><item><title>x</title><link>https://acme.example/news/release-01</link></item></channel></rss>"})
    page = mod._check(http, {"id": "acme", "kind": "page", "url": "https://acme.example/news/", "name": "Acme", "pattern": r"acme\.example/news/release-0[0-3].+", "reason": ""})
    assert page["links"] == 8 and page["accepted"] == 4 and page["status"] == "ok"
    feed = mod._check(http, {"id": "acme", "kind": "feed", "url": "https://acme.example/feed.xml", "name": "Acme RSS", "pattern": "", "reason": ""})
    assert feed["accepted"] == 1 and feed["status"] == "ok"
    dead = mod._check(http, {"id": "acme", "kind": "page", "url": "https://nowhere.example/", "name": "dead", "pattern": "", "reason": ""})
    assert dead["status"].startswith("ERROR")


# ------------------------------------------------------------------ the brief's editorial filter
def test_brief_treats_an_own_source_row_as_bearing_even_without_the_alias_in_the_headline():
    """build_brief only offers rows whose headline carries the alias. A newsroom row does not, and
    it is the most bearing row there is."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_brief", REPO / "scripts" / "build_brief.py")
    bb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bb)
    latest = {"names": [{"id": "kiloutou", "name": "Kiloutou", "kind": "name", "candidates": [
        {"cluster_id": 1, "sources": 1, "alert_categories": [], "primary": {
            "title": "Kapla Holding announces pricing of additional EUR 200m senior secured notes due 2031",
            "source": "Kiloutou newsroom", "domain": "kiloutou.com", "country": "FR", "lang": "en",
            "url": "https://www.kiloutou.com/group/en/press-releases/kapla-holding-announces-pricing/",
            "first_seen_at": "2026-09-28T07:00:00+00:00", "published_at": None,
            "alias_where": "none", "confidence": 0.8, "route": "page", "tier_hint": 0}},
        {"cluster_id": 2, "sources": 1, "alert_categories": [], "primary": {
            "title": "Equipment rental demand softens in France, say analysts",
            "source": "Some outlet", "domain": "outlet.example", "country": "FR", "lang": "en",
            "url": "https://outlet.example/2026/09/rental-demand", "first_seen_at": "2026-09-28T07:00:00+00:00",
            "published_at": "2026-09-28T06:00:00+00:00", "alias_where": "none", "confidence": 0.4,
            "route": "googlenews", "tier_hint": 3}},
    ]}]}
    P = bb.partition(bb.collect(latest))
    assert P["A"] == 2 and P["A_bearing"] == 1 and P["A_own_source"] == 1 and P["A_inherited"] == 1
    assert [r["id"] for r in P["A_rows"]] == [1]                      # the newsroom row is offered
    assert P["A_bearing"] + P["A_standfirst"] + P["A_inherited"] == P["A"]   # the reconciliation still adds up
