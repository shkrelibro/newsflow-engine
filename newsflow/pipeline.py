"""The run: build jobs, fetch, normalise, match, screen, cluster, store."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from .config import Config, NameConfig, Outlet, PageSource
from .dedupe import similar, title_key
from .http import Http, RateLimiter
from .match import Matcher
from .models import Item, RawItem, SourceResult
from .normalize import canonical_url, domain_of, is_google_news_link
from .routes import (
    discover_feed,
    fetch_bing_news,
    fetch_feed,
    fetch_gdelt,
    fetch_google_news,
    fetch_page_links,
    page_items_from_links,
)
from .store import Store

log = logging.getLogger("newsflow")

SEARCH_ROUTES = {"googlenews", "bingnews", "gdelt"}

Job = Callable[[], tuple[list[RawItem], SourceResult]]


@dataclass
class RunSummary:
    run_id: int
    started_at: datetime
    finished_at: Optional[datetime] = None
    jobs: int = 0
    fetched: int = 0
    new_items: int = 0
    candidates: int = 0
    screened: int = 0
    stale: int = 0
    unrelated: int = 0
    alerts: int = 0
    source_results: list[SourceResult] = field(default_factory=list)
    errors: int = 0


def make_http(cfg: Config) -> Http:
    eng = cfg.engine
    rl = eng.get("rate_limits", {}) or {}
    limiter = RateLimiter(
        default_seconds=float(rl.get("default_seconds", 1.0)),
        per_host={
            "news.google.com": float(rl.get("googlenews_seconds", 2.0)),
            "www.bing.com": float(rl.get("bing_seconds", 2.0)),
            "api.gdeltproject.org": float(rl.get("gdelt_seconds", 6.0)),
        },
    )
    return Http(
        user_agent=str(eng.get("user_agent", "newsflow-engine/0.1")),
        timeout=float(eng.get("timeout_seconds", 20)),
        retries=int(eng.get("retries", 2)),
        limiter=limiter,
        honour_robots_for_pages=bool(eng.get("honour_robots_for_pages", True)),
    )


# ----------------------------------------------------------------------
# Job building
# ----------------------------------------------------------------------

def _main_alias(name: NameConfig) -> str:
    for a in name.aliases:
        if a.search:
            return a.text
    return name.name


def _due(every: int, run_number: int) -> bool:
    return every <= 1 or run_number % every == 0


def build_jobs(cfg: Config, http: Http, store: Store, run_number: int, backfill_days: Optional[int] = None) -> list[Job]:
    jobs: list[Job] = []
    routes = cfg.routes
    when_google = f"{backfill_days}d" if backfill_days else str(routes.get("googlenews", {}).get("when", "1d"))
    when_site = f"{backfill_days}d" if backfill_days else str(routes.get("googlenews", {}).get("site_when", "7d"))
    gdelt_span = f"{backfill_days}d" if backfill_days else str(routes.get("gdelt", {}).get("timespan", "24h"))
    outlet_by_domain = {domain_of(o.homepage): o for o in cfg.outlets}
    force = backfill_days is not None

    for name in cfg.names:
        ids = [name.id]
        markets = name.markets or ([] if not name.home_country else [])
        # --- Google News per alias × market
        if cfg.route_enabled("googlenews"):
            for alias in name.aliases:
                if not alias.search or not (force or _due(alias.search_every, run_number)):
                    continue
                for m in markets:
                    if not alias.applies_to(m.lang):
                        continue
                    q = f'"{alias.text}"'
                    jobs.append(lambda q=q, m=m: fetch_google_news(http, q, m.lang, m.country, ids, when_google))
            # --- site-restricted queries for key outlets
            site_every = int(routes.get("googlenews", {}).get("site_every_n_runs", 4))
            if name.site_queries and (force or _due(site_every, run_number)):
                main = _main_alias(name)
                for dom in name.site_queries:
                    o = outlet_by_domain.get(dom)
                    lang = o.lang if o and o.lang else (markets[0].lang if markets else "en")
                    country = o.country if o else (markets[0].country if markets else "GB")
                    q = f'"{main}" site:{dom}'
                    jobs.append(lambda q=q, lang=lang, country=country: fetch_google_news(http, q, lang, country, ids, when_site))
        # --- Bing per market
        if cfg.route_enabled("bingnews"):
            bing_every = int(routes.get("bingnews", {}).get("every_n_runs", 2))
            if force or _due(bing_every, run_number):
                main = _main_alias(name)
                for m in markets:
                    jobs.append(lambda m=m, main=main: fetch_bing_news(http, main, m.lang, m.country, ids))
        # --- GDELT global (+ per language if configured)
        if cfg.route_enabled("gdelt"):
            gdelt_every = int(routes.get("gdelt", {}).get("every_n_runs", 1))
            if force or _due(gdelt_every, run_number):
                main = _main_alias(name)
                jobs.append(lambda main=main: fetch_gdelt(http, f'"{main}"', ids, gdelt_span))
                if routes.get("gdelt", {}).get("per_lang", False):
                    for lang in name.langs:
                        jobs.append(lambda main=main, lang=lang: fetch_gdelt(http, f'"{main}"', ids, gdelt_span, "", lang))
        # --- name-specific feeds
        if cfg.route_enabled("rss"):
            for f in name.feeds:
                jobs.append(lambda f=f: fetch_feed(http, f.url, f.name_ids, f.tier, f.country, f.lang, f.name))
        # --- page watchers
        if cfg.route_enabled("pages"):
            pages_every = int(routes.get("pages", {}).get("every_n_runs", 2))
            if force or _due(pages_every, run_number):
                for p in name.pages:
                    jobs.append(lambda p=p: _page_job(http, store, p, backfill=force))

    # --- shared outlet feeds (fetched once, matched against every name)
    if cfg.route_enabled("rss") and cfg.outlets:
        wanted_countries = {m.country for n in cfg.names for m in n.markets}
        discover_every = int(routes.get("rss", {}).get("discover_every_n_runs", 8))
        for o in cfg.outlets:
            if not o.enabled or o.country not in wanted_countries:
                continue
            jobs.append(lambda o=o: _outlet_job(http, store, o, discover=(run_number == 1 or _due(discover_every, run_number))))
    return jobs


def _page_job(http: Http, store: Store, page: PageSource, backfill: bool = False) -> tuple[list[RawItem], SourceResult]:
    links, result = fetch_page_links(http, page.url)
    if not result.ok:
        return [], result
    now = datetime.now(timezone.utc)
    urls = [u for u, _ in links]
    first_time = store.page_seen_count(page.url) == 0
    new_links = store.unseen_links(page.url, urls)
    store.mark_seen(page.url, urls, now)
    if first_time and not backfill:
        # seed only: do not flood the pile with a page's history
        result.items = 0
        result.error = "seeded"
        return [], result
    new_set = set(new_links)
    items = page_items_from_links(
        [(u, t) for u, t in links if u in new_set], page.url, page.name_ids, page.tier, page.country, page.lang, page.name, page.link_pattern
    )
    for it in items:
        it.tier_hint = page.tier
    result.items = len(items)
    return items, result


def _outlet_job(http: Http, store: Store, outlet: Outlet, discover: bool) -> tuple[list[RawItem], SourceResult]:
    now = datetime.now(timezone.utc)
    feed_url = outlet.feed_url
    if not feed_url:
        row = store.get_feed(outlet.homepage)
        feed_url = row["feed_url"] if row else ""
        if not feed_url and discover:
            feed_url = discover_feed(http, outlet.homepage)
            store.set_feed(outlet.homepage, feed_url, now, "" if feed_url else "no feed found")
    if not feed_url:
        return [], SourceResult("rss", f"{outlet.name} ({outlet.country})", False, 0, "no feed url (discovery pending)", 0.0)
    items, result = fetch_feed(http, feed_url, [], outlet.tier, outlet.country, outlet.lang, f"{outlet.name} ({outlet.country})")
    if result.ok:
        store.feed_ok(outlet.homepage, now)
    else:
        store.feed_error(outlet.homepage, result.error)
    return items, result


# ----------------------------------------------------------------------
# Processing
# ----------------------------------------------------------------------

def _primary_name(matches) -> str:
    return max(matches, key=lambda m: m.confidence).name_id if matches else ""


def process_items(cfg: Config, store: Store, matcher: Matcher, http: Optional[Http], raw_items: list[RawItem], run_id: int, now: datetime, summary: RunSummary, lookback_hours: float) -> None:
    cutoff = now - timedelta(hours=lookback_hours)
    resolve = bool(cfg.engine.get("resolve_redirects", False)) and http is not None
    cluster_window = now - timedelta(hours=float(cfg.engine.get("cluster_window_hours", 72)))
    require_alias_pages = {p.url: p.require_alias for n in cfg.names for p in n.pages}
    require_alias_feeds = {f.url: f.require_alias for n in cfg.names for f in n.feeds}

    for raw in raw_items:
        summary.fetched += 1
        if not raw.title or not raw.link:
            continue
        if raw.published_at and raw.published_at < cutoff:
            summary.stale += 1
            continue
        canon = canonical_url(raw.link)
        if resolve and is_google_news_link(canon):
            resolved = http.head_final_url(canon)  # type: ignore[union-attr]
            if resolved and not is_google_news_link(resolved):
                canon = canonical_url(resolved)
        domain = raw.source_domain or domain_of(canon)

        existing = store.get_item_by_url(canon)
        if existing is not None:
            store.note_also_route(int(existing["id"]), raw.route, raw.query)
            store.update_item_enrichment(int(existing["id"]), published_at=raw.published_at, summary=raw.summary, source_name=raw.source_name)
            continue

        # --- matching
        only = raw.name_ids if (raw.route in SEARCH_ROUTES or raw.route == "page") else None
        matches = matcher.match(raw.title, raw.summary, raw.lang, only=only)
        if not matches:
            attributed = False
            if raw.route in SEARCH_ROUTES and raw.name_ids:
                # the name query returned it: keep, flagged as alias-not-in-text
                matches = [type("M", (), {"name_id": nid, "alias": "(query)", "where": "none", "confidence": 0.4})() for nid in raw.name_ids]
                attributed = True
            elif raw.route == "page" and raw.name_ids and not require_alias_pages.get(raw.query, True):
                matches = [type("M", (), {"name_id": nid, "alias": "(page)", "where": "none", "confidence": 0.8})() for nid in raw.name_ids]
                attributed = True
            elif raw.route == "rss" and raw.name_ids and not require_alias_feeds.get(raw.query, True):
                matches = [type("M", (), {"name_id": nid, "alias": "(feed)", "where": "none", "confidence": 0.9})() for nid in raw.name_ids]
                attributed = True
            if not attributed:
                summary.unrelated += 1
                continue

        primary = _primary_name(matches)
        reason = matcher.screen(domain, canon, raw.title, primary)
        status = "screened" if reason else "candidate"
        item = Item(
            id=None, canonical_url=canon, raw_link=raw.link, title=raw.title.strip(), summary=raw.summary.strip(),
            source_name=raw.source_name, source_domain=domain, published_at=raw.published_at, first_seen_at=now,
            lang=raw.lang, country=raw.country, route=raw.route, query=raw.query, tier_hint=raw.tier_hint,
            status=status, screen_reason=reason,
        )
        tkey = title_key(raw.title, raw.source_name)
        item_id = store.insert_item(item, tkey, run_id)
        summary.new_items += 1
        if status == "screened":
            summary.screened += 1
        else:
            summary.candidates += 1
        for m in matches:
            store.add_match(item_id, m.name_id, m.alias, m.where, float(m.confidence))

        cats, alert = matcher.tier1_categories(raw.title, raw.summary)
        if status == "screened":
            alert = False
        store.set_alert(item_id, cats, alert)
        if alert:
            summary.alerts += 1

        # --- clustering within the primary name
        cluster_id = None
        for row in store.recent_items_for_name(primary, cluster_window):
            if row["id"] == item_id or not row["cluster_id"]:
                continue
            if similar(tkey, row["title_key"]):
                cluster_id = int(row["cluster_id"])
                break
        if cluster_id is None:
            cluster_id = store.new_cluster(primary, now)
        store.set_cluster(item_id, cluster_id)
    store.commit()


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------

def run_once(cfg: Config, store: Store, http: Optional[Http] = None, *, backfill_days: Optional[int] = None, now: Optional[datetime] = None, jobs: Optional[list[Job]] = None) -> RunSummary:
    now = now or datetime.now(timezone.utc)
    own_http = http is None
    http = http or make_http(cfg)
    matcher = Matcher.from_config(cfg)
    run_id = store.start_run(now)
    run_number = store.run_count()
    summary = RunSummary(run_id=run_id, started_at=now)
    lookback = float(backfill_days * 24 + 24) if backfill_days else float(cfg.engine.get("lookback_hours", 72))

    jobs = jobs if jobs is not None else build_jobs(cfg, http, store, run_number, backfill_days)
    summary.jobs = len(jobs)
    workers = max(1, int(cfg.engine.get("max_workers", 4)))
    raw_items: list[RawItem] = []
    results: list[SourceResult] = []
    log.info("run %s: %d jobs, %d workers", run_id, len(jobs), workers)
    if workers == 1:
        for job in jobs:
            items, res = job()
            raw_items.extend(items)
            results.append(res)
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for items, res in ex.map(lambda j: j(), jobs):
                raw_items.extend(items)
                results.append(res)
    summary.source_results = results
    summary.errors = sum(1 for r in results if not r.ok)
    store.add_source_results(run_id, results)

    process_items(cfg, store, matcher, http, raw_items, run_id, now, summary, lookback)
    summary.finished_at = datetime.now(timezone.utc)
    store.finish_run(run_id, summary.finished_at, summary.fetched, summary.new_items, notes=f"errors={summary.errors} stale={summary.stale} unrelated={summary.unrelated}")
    if own_http:
        http.close()
    log.info("run %s done: fetched=%d new=%d candidates=%d screened=%d alerts=%d errors=%d", run_id, summary.fetched, summary.new_items, summary.candidates, summary.screened, summary.alerts, summary.errors)
    return summary
