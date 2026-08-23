"""The run: build jobs, fetch, normalise, match, screen, cluster, store."""
from __future__ import annotations

import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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
class JobSpec:
    label: str
    route: str
    fn: Job
    name_ids: list[str] = field(default_factory=list)   # names this job sweeps (coverage ledger)


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
    skipped: int = 0


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


def _due(every: int, run_number: int, phase: int = 0) -> bool:
    return every <= 1 or run_number % every == phase % every


def cold_start(cfg: Config, run_number: int) -> bool:
    """The first two cycles of staggered site queries use a wide window so history is captured."""
    site_every = max(1, int(cfg.routes.get("googlenews", {}).get("site_every_n_runs", 4)))
    return run_number <= 2 * site_every


def build_jobs(cfg: Config, http: Http, store: Store, run_number: int, backfill_days: Optional[int] = None, all_routes: bool = False) -> list[JobSpec]:
    jobs: list[JobSpec] = []
    routes = cfg.routes
    cold = cold_start(cfg, run_number)
    cold_window = str(routes.get("googlenews", {}).get("cold_start_when", "30d"))
    when_google = f"{backfill_days}d" if backfill_days else str(routes.get("googlenews", {}).get("when", "1d"))
    when_site = f"{backfill_days}d" if backfill_days else (cold_window if cold else str(routes.get("googlenews", {}).get("site_when", "7d")))
    gdelt_span = f"{backfill_days}d" if backfill_days else str(routes.get("gdelt", {}).get("timespan", "24h"))
    outlet_by_domain = {domain_of(o.homepage): o for o in cfg.outlets}
    force = backfill_days is not None or all_routes   # run every cadenced route regardless of its every_n_runs

    # ------------------------------------------------------------------
    # Search routes are GROUPED so fetch load scales with markets, not names:
    # per market one Google/Bing query carries up to group_size quoted aliases OR'd
    # together. Tier-A names ("name") additionally get a solo query in their home
    # market (keeps the alias-not-in-headline fallback where it matters most), and
    # secondary search aliases keep their per-alias solo behaviour. Comps ("comp")
    # are grouped only, at a slower cadence.
    # ------------------------------------------------------------------
    g = routes.get("googlenews", {})
    group_size = max(2, int(g.get("group_size", 6)))
    home_every = max(1, int(g.get("home_every_n_runs", 2)))
    comp_every = max(1, int(g.get("comp_every_n_runs", 2)))
    bing_every = int(routes.get("bingnews", {}).get("every_n_runs", 2))
    bing_comp_every = int(routes.get("bingnews", {}).get("comp_every_n_runs", 4))
    gdelt_every = int(routes.get("gdelt", {}).get("every_n_runs", 1))
    gdelt_comp_every = int(routes.get("gdelt", {}).get("comp_every_n_runs", 4))

    by_market_a: dict[tuple[str, str], list[tuple[str, str]]] = {}   # (lang, country) -> [(name_id, main alias)]
    by_market_c: dict[tuple[str, str], list[tuple[str, str]]] = {}
    mains_a: list[tuple[str, str]] = []
    mains_c: list[tuple[str, str]] = []
    by_home: dict[str, list[tuple[str, str]]] = {}                   # home country -> [(name_id, main)]

    def _chunks(seq, size):
        for i in range(0, len(seq), size):
            yield i // size, seq[i:i + size]

    def _or_query(entries) -> str:
        return "(" + " OR ".join(f'"{alias}"' for _, alias in entries) + ")"

    for name in cfg.names:
        ids = [name.id]
        markets = name.markets
        main = _main_alias(name)
        bucket = by_market_c if name.kind == "comp" else by_market_a
        (mains_c if name.kind == "comp" else mains_a).append((name.id, main))
        if name.kind != "comp" and name.home_country:
            by_home.setdefault(name.home_country, []).append((name.id, main))
        for m in markets:
            bucket.setdefault((m.lang, m.country), []).append((name.id, main))

        if cfg.route_enabled("googlenews"):
            # solo home-market query for tier-A names (full fallback recall)
            if name.kind != "comp" and (force or _due(home_every, run_number, 0)):
                for m in markets:
                    if m.country == name.home_country:
                        q = f'"{main}"'
                        jobs.append(JobSpec(f"{q} [{m.lang}-{m.country}] home", "googlenews", lambda q=q, m=m, ids=ids: fetch_google_news(http, q, m.lang, m.country, ids, when_google), name_ids=list(ids)))
            # secondary search aliases keep their per-alias solo behaviour
            for alias in name.aliases:
                if not alias.search or alias.text == main:
                    continue
                if not (force or _due(alias.search_every, run_number, sum(alias.text.encode()) )):
                    continue
                for m in markets:
                    if not alias.applies_to(m.lang):
                        continue
                    q = f'"{alias.text}"'
                    jobs.append(JobSpec(f"{q} [{m.lang}-{m.country}]", "googlenews", lambda q=q, m=m, ids=ids: fetch_google_news(http, q, m.lang, m.country, ids, when_google), name_ids=list(ids)))
            # per-name site queries (only names that define them, e.g. Intrum), staggered
            site_every = max(1, int(g.get("site_every_n_runs", 4)))
            if name.site_queries:
                for i, dom in enumerate(name.site_queries):
                    if not force and i % site_every != run_number % site_every:
                        continue
                    o = outlet_by_domain.get(dom)
                    lang = o.lang if o and o.lang else (markets[0].lang if markets else "en")
                    country = o.country if o else (markets[0].country if markets else "GB")
                    q = f'"{main}" site:{dom}'
                    jobs.append(JobSpec(f"{q} [{lang}-{country}]", "googlenews", lambda q=q, lang=lang, country=country, ids=ids: fetch_google_news(http, q, lang, country, ids, when_site), name_ids=list(ids)))
        # name-specific feeds and pages (any tier that defines them)
        if cfg.route_enabled("rss"):
            for f in name.feeds:
                jobs.append(JobSpec(f.name, "rss", lambda f=f: fetch_feed(http, f.url, f.name_ids, f.tier, f.country, f.lang, f.name), name_ids=list(f.name_ids)))
        if cfg.route_enabled("pages"):
            pages_every = int(routes.get("pages", {}).get("every_n_runs", 2))
            if force or _due(pages_every, run_number):
                for p in name.pages:
                    jobs.append(JobSpec(p.name, "page", lambda p=p: _page_job(http, store, p, backfill=force), name_ids=list(p.name_ids)))

    # ---- grouped Google per market ----
    if cfg.route_enabled("googlenews"):
        for (lang, country), entries in sorted(by_market_a.items()):
            for ci, chunk in _chunks(entries, group_size):
                q = _or_query(chunk)
                nids = [nid for nid, _ in chunk]
                jobs.append(JobSpec(f"group{ci} {len(chunk)} names [{lang}-{country}]", "googlenews", lambda q=q, lang=lang, country=country, nids=nids: fetch_google_news(http, q, lang, country, nids, when_google), name_ids=nids))
        if force or _due(comp_every, run_number, 1):
            for (lang, country), entries in sorted(by_market_c.items()):
                for ci, chunk in _chunks(entries, group_size):
                    q = _or_query(chunk)
                    nids = [nid for nid, _ in chunk]
                    jobs.append(JobSpec(f"comps{ci} {len(chunk)} [{lang}-{country}]", "googlenews", lambda q=q, lang=lang, country=country, nids=nids: fetch_google_news(http, q, lang, country, nids, when_google), name_ids=nids))
        # regulator sweep: home-country authority domains x the names based there, staggered
        reg_every = max(1, int(g.get("regulator_every_n_runs", 12)))
        reg_jobs = []
        for cc, entries in sorted(by_home.items()):
            for dom in cfg.regulators.get(cc, []):
                for ci, chunk in _chunks(entries, group_size):
                    reg_jobs.append((cc, dom, ci, chunk))
        for idx, (cc, dom, ci, chunk) in enumerate(reg_jobs):
            if not force and idx % reg_every != run_number % reg_every:
                continue
            q = _or_query(chunk) + f" site:{dom}"
            nids = [nid for nid, _ in chunk]
            lang = "en"
            jobs.append(JobSpec(f"reg {dom} [{cc}]", "googlenews", lambda q=q, cc=cc, nids=nids: fetch_google_news(http, q, "en", cc, nids, when_site), name_ids=nids))

    # ---- grouped Bing per market ----
    if cfg.route_enabled("bingnews"):
        for tier_map, every, phase in ((by_market_a, bing_every, 1), (by_market_c, bing_comp_every, 3)):
            if not (force or _due(every, run_number, phase)):
                continue
            for (lang, country), entries in sorted(tier_map.items()):
                for ci, chunk in _chunks(entries, group_size):
                    q = " OR ".join(f'"{alias}"' for _, alias in chunk)
                    nids = [nid for nid, _ in chunk]
                    jobs.append(JobSpec(f"bing{ci} {len(chunk)} [{lang}-{country}]", "bingnews", lambda q=q, lang=lang, country=country, nids=nids: fetch_bing_news(http, q, lang, country, nids), name_ids=nids))

    # ---- grouped GDELT (global, all languages) ----
    if cfg.route_enabled("gdelt"):
        # GDELT rejects the whole query if any phrase is too short ("NKD", "QVC", "N Brown");
        # such names stay covered by Google/Bing, so drop only the offending phrase here.
        def _gdelt_ok(alias: str) -> bool:
            return len(alias) >= 4 and all(len(w) >= 2 for w in alias.split())
        for mains, every in ((mains_a, gdelt_every), (mains_c, gdelt_comp_every)):
            for ci, chunk in _chunks(mains, group_size):
                if not force and (ci + run_number) % every != 0:
                    continue
                phrases = [(nid, alias) for nid, alias in chunk if _gdelt_ok(alias)]
                if not phrases:
                    continue
                q = "(" + " OR ".join(f'"{alias}"' for _, alias in phrases) + ")"
                nids = [nid for nid, _ in chunk]
                jobs.append(JobSpec(f"gdelt{ci} {len(chunk)}", "gdelt", lambda q=q, nids=nids: fetch_gdelt(http, q, nids, gdelt_span), name_ids=nids))

    # --- shared outlet feeds (fetched once, matched against every name). Feed discovery is capped
    #     per run and retried at most daily per outlet, so the first runs are not swamped by it.
    if cfg.route_enabled("rss") and cfg.outlets:
        wanted_countries = {m.country for n in cfg.names for m in n.markets}
        discover_budget = int(routes.get("rss", {}).get("discover_per_run", 20))
        retry_after = timedelta(hours=float(routes.get("rss", {}).get("discover_retry_hours", 24)))
        now = datetime.now(timezone.utc)
        for o in cfg.outlets:
            if not o.enabled or o.country not in wanted_countries:
                continue
            discover = False
            if not o.feed_url:
                row = store.get_feed(o.homepage)
                known = bool(row and row["feed_url"])
                tried_recently = False
                if row and row["discovered_at"]:
                    try:
                        tried_recently = (now - datetime.fromisoformat(row["discovered_at"])) < retry_after
                    except ValueError:
                        tried_recently = False
                if not known and not tried_recently and discover_budget > 0:
                    discover = True
                    discover_budget -= 1
            jobs.append(JobSpec(f"{o.name} ({o.country})", "rss", lambda o=o, discover=discover: _outlet_job(http, store, o, discover=discover)))

    # Deterministic per-run shuffle: interleaves routes within a run and rotates which jobs
    # land in the time-budget tail, so a skipped source is picked up on the next cycle
    # instead of being starved forever by a fixed construction order.
    random.Random(run_number).shuffle(jobs)
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
    # names whose every search alias is context-guarded (ambiguous words like "Evoca"):
    # the query fallback must also see one of those context terms, else it is noise.
    fallback_ctx: dict[str, list[str]] = {}
    for n in cfg.names:
        search_aliases = [a for a in n.aliases if a.search]
        if search_aliases and all(a.require_context for a in search_aliases):
            fallback_ctx[n.id] = sorted({t.lower() for a in search_aliases for t in a.require_context})
    # comps never get the fallback: they are read-across names swept in groups; an
    # unverifiable 0.4 comp candidate is noise by construction.
    comp_ids = {n.id for n in cfg.names if n.kind == "comp"}

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
        rejected: set = set()
        matches = matcher.match(raw.title, raw.summary, raw.lang, only=only, rejected=rejected)
        if not matches:
            attributed = False
            if raw.route in SEARCH_ROUTES and len(raw.name_ids) == 1:
                # a single-name query returned it: keep, flagged as alias-not-in-text.
                # grouped (OR) queries never fall back - attribution would be a guess.
                nid = raw.name_ids[0]
                text_l = f"{raw.title}\n{raw.summary}".lower()
                ctx = fallback_ctx.get(nid)
                if nid in comp_ids or nid in rejected or (ctx and not any(t in text_l for t in ctx)):
                    pass  # alias rejected for cause, or required context absent: not a candidate
                else:
                    matches = [type("M", (), {"name_id": nid, "alias": "(query)", "where": "none", "confidence": 0.4})()]
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

def run_once(cfg: Config, store: Store, http: Optional[Http] = None, *, backfill_days: Optional[int] = None, all_routes: bool = False, now: Optional[datetime] = None, jobs: Optional[list] = None, budget_minutes: Optional[float] = None) -> RunSummary:
    now = now or datetime.now(timezone.utc)
    own_http = http is None
    http = http or make_http(cfg)
    matcher = Matcher.from_config(cfg)
    run_id = store.start_run(now)
    run_number = store.run_count()
    summary = RunSummary(run_id=run_id, started_at=now)
    if backfill_days:
        lookback = float(backfill_days * 24 + 24)
    elif cold_start(cfg, run_number):
        lookback = float(cfg.engine.get("cold_start_lookback_hours", 31 * 24))
    else:
        lookback = float(cfg.engine.get("lookback_hours", 72))

    specs = jobs if jobs is not None else build_jobs(cfg, http, store, run_number, backfill_days, all_routes)
    specs = [j if isinstance(j, JobSpec) else JobSpec(f"job-{i}", "custom", j) for i, j in enumerate(specs)]
    summary.jobs = len(specs)
    workers = max(1, int(cfg.engine.get("max_workers", 4)))
    budget = float(cfg.engine.get("max_run_minutes", 12)) if budget_minutes is None else float(budget_minutes)
    deadline = time.monotonic() + budget * 60
    results: list[SourceResult] = []
    log.info("run %s (#%d): %d jobs, %d workers, budget %.0f min, lookback %.0f h", run_id, run_number, len(specs), workers, budget, lookback)

    def handle(spec: JobSpec, items: list[RawItem], res: SourceResult) -> None:
        res.names = list(spec.name_ids)
        results.append(res)
        log.info("%s %-10s %-58s items=%-4d %5.1fs %s", "ok " if res.ok else "ERR", res.route, res.source[:58], res.items, res.seconds, res.error[:90])
        if items:
            process_items(cfg, store, matcher, http, items, run_id, now, summary, lookback)

    ex = ThreadPoolExecutor(max_workers=workers)
    futures = {ex.submit(spec.fn): spec for spec in specs}
    pending = set(futures)
    stopped = False
    try:
        for fut in as_completed(futures):
            spec = futures[fut]
            pending.discard(fut)
            try:
                items, res = fut.result()
            except Exception as exc:  # noqa: BLE001 - a job that raises becomes a failed source
                items, res = [], SourceResult(spec.route, spec.label, False, 0, str(exc)[:300], 0.0)
            handle(spec, items, res)
            if time.monotonic() > deadline and pending:
                stopped = True
                break
    finally:
        ex.shutdown(wait=True, cancel_futures=True)
    if stopped:
        for fut in pending:
            spec = futures[fut]
            if fut.cancelled():
                results.append(SourceResult(spec.route, spec.label, False, 0, "skipped: run time budget reached", 0.0, list(spec.name_ids)))
                summary.skipped += 1
            else:
                try:
                    items, res = fut.result()
                except Exception as exc:  # noqa: BLE001
                    items, res = [], SourceResult(spec.route, spec.label, False, 0, str(exc)[:300], 0.0)
                handle(spec, items, res)
        log.warning("time budget of %.0f min reached: %d jobs skipped (they run on the next cycle)", budget, summary.skipped)

    summary.source_results = results
    summary.errors = sum(1 for r in results if not r.ok) - summary.skipped
    store.add_source_results(run_id, results)
    summary.finished_at = datetime.now(timezone.utc)
    store.finish_run(run_id, summary.finished_at, summary.fetched, summary.new_items, notes=f"errors={summary.errors} skipped={summary.skipped} stale={summary.stale} unrelated={summary.unrelated}")
    if own_http:
        http.close()
    log.info("run %s done: fetched=%d new=%d candidates=%d screened=%d alerts=%d errors=%d skipped=%d", run_id, summary.fetched, summary.new_items, summary.candidates, summary.screened, summary.alerts, summary.errors, summary.skipped)
    return summary
