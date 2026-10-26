"""The run: build jobs, fetch, normalise, match, screen, cluster, store."""
from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait as wait_futures
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from .config import Config, NameConfig, Outlet, PageSource
from .dedupe import similar, title_key
from .http import BudgetExceeded, Http, RateLimiter, UpstreamRefused
from .match import Matcher
from .signal import hard_signal
from .models import Item, RawItem, SourceResult
from .normalize import canonical_url, domain_of, is_google_news_link, title_trailing_date, url_date, url_year
from .routes import (
    bing_market,
    discover_feed,
    fetch_bing_news,
    fetch_feed,
    fetch_gdelt,
    fetch_google_news,
    fetch_page_links,
    enrich_generic_titles,
    date_page_items,
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
    prio: int = 5        # queue order inside a run: 0 runs first. The budget tail is then the least
                         # valuable work rather than a random third of the queue (v2, 11 Oct 2026)
    skip: str = ""       # non-empty: not run this cycle, recorded with this reason (the Google
                         # rotation cap, or a cool-down after Google refused us)
    fallback: bool = False   # a Bing query held in reserve, submitted only while Google is down


_REFUSED_RE = re.compile(r"refused upstream|\bHTTP (?:429|5\d\d)\b")


def is_refusal(error: str) -> bool:
    """A source result that means the host turned us away, not that nothing was found."""
    return bool(error) and bool(_REFUSED_RE.search(error))


# ----------------------------------------------------------------------
# Google News quota. 11 October 2026: the v2 cadences raised the engine's Google traffic from
# about 800 to about 1,200 queries an hour (2.0 s spacing, dedicated lane, no idle time), and
# Google News started answering 503 within 40 minutes of v2 going live, then again 80 minutes
# into a fresh runner, then 30 minutes into the backfill runner. Nothing before v2 that day had
# drawn a single 503. Three rules now bound the route:
#   1. a cap on queries per cycle, rotated by staleness so no query is starved, focus credits
#      and their key outlets first, every query still inside its one-day window;
#   2. on refusal (three 429/5xx in a row) the route trips for the cycle and cools down for a
#      growing interval, probed with one query per cycle; the cap steps down and recovers slowly;
#   3. while Google is down, Bing carries the focus credits and the tier-A home markets.
# ----------------------------------------------------------------------
GOOGLE_HOST = "news.google.com"
STATE_CAP = "googlenews.cap"
STATE_COOLDOWN = "googlenews.cooldown_until"
STATE_TRIPS = "googlenews.trips"
STATE_LAST_REFUSAL = "googlenews.last_refusal"
STATE_BACKFILL = "googlenews.backfill"            # json: {"days", "since_run", "expires"}


def google_quota(cfg: Config) -> dict:
    g = cfg.routes.get("googlenews", {}) or {}
    q = g.get("quota", {}) or {}
    return {
        "cap": int(q.get("max_per_run", 0) or 0),                  # 0 disables the cap
        "floor": int(q.get("floor", 80)),
        "ceiling": int(q.get("ceiling", 200)),
        "step_down": float(q.get("step_down", 0.75)),
        "recover_step": int(q.get("recover_step", 5)),
        "trip_after": int(q.get("refused_trip_after", 3)),
        "cooldown_min": int(q.get("cooldown_minutes", 30)),
        "cooldown_max": int(q.get("cooldown_max_minutes", 180)),
        "backfill_hours": float(q.get("backfill_window_hours", 4)),
        # v2.18 (26 Oct 2026): a catch-up never runs at the ceiling, no single name takes more than
        # a share of a cycle, and a query that has found nothing in a week goes to the back
        "backfill_cap": int(q.get("backfill_max_per_run", 100) or 0),       # 0: the ordinary cap applies
        "name_share": float(q.get("name_share", 0.15)),                      # of the cap, per name, solo queries
        "name_share_min": int(q.get("name_share_min", 6)),
        "dormant_days": float(q.get("dormant_days", 7)),                     # 0 turns the dormant tier off
        "dormant_min_attempts": int(q.get("dormant_min_attempts", 3)),
        "dormant_share": float(q.get("dormant_share", 0.10)),                # of the cap kept for dormant queries ...
        "dormant_min_slots": int(q.get("dormant_min_slots", 5)),             # ... and never fewer than this
    }


def google_state(cfg: Config, store: Store, now: datetime) -> dict:
    """The persisted quota state: current cap, cool-down, consecutive trips, backfill window."""
    q = google_quota(cfg)
    cap_default = q["cap"]
    try:
        cap = int(store.get_state(STATE_CAP) or cap_default)
    except ValueError:
        cap = cap_default
    if cap_default:
        cap = max(q["floor"], min(q["ceiling"], cap or cap_default))
    until = _parse_iso(store.get_state(STATE_COOLDOWN))
    try:
        trips = int(store.get_state(STATE_TRIPS) or 0)
    except ValueError:
        trips = 0
    backfill = {}
    raw = store.get_state(STATE_BACKFILL)
    if raw:
        try:
            backfill = json.loads(raw)
        except ValueError:
            backfill = {}
        exp = _parse_iso(str(backfill.get("expires") or ""))
        if not exp or exp <= now:
            backfill = {}
    return {"cap": cap if cap_default else 0, "cooldown_until": until, "cooling": bool(until and until > now),
            "trips": trips, "last_refusal": store.get_state(STATE_LAST_REFUSAL) or None, "backfill": backfill}


def _parse_iso(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        t = datetime.fromisoformat(s)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def google_refused(cfg: Config, store: Store, now: datetime, state: dict, why: str) -> datetime:
    """Google turned us away: start or extend the cool-down, step the cap down. Returns the
    time the next probe may be sent."""
    q = google_quota(cfg)
    trips = int(state.get("trips") or 0) + 1
    minutes = min(q["cooldown_max"], q["cooldown_min"] * (2 ** (trips - 1)))
    until = now + timedelta(minutes=minutes)
    store.set_state(STATE_TRIPS, str(trips), now)
    store.set_state(STATE_COOLDOWN, until.isoformat(timespec="seconds"), now)
    store.set_state(STATE_LAST_REFUSAL, f"{now.isoformat(timespec='seconds')} {why[:120]}", now)
    if q["cap"]:
        new_cap = max(q["floor"], int(state.get("cap", q["cap"]) * q["step_down"]))
        store.set_state(STATE_CAP, str(new_cap), now)
        state["cap"] = new_cap
    state.update(trips=trips, cooldown_until=until, cooling=True)
    log.warning("googlenews refused us (%s): cooling down %d min until %sZ, cap now %s", why[:80], minutes, until.strftime("%H:%M"), state.get("cap") or "off")
    return until


def google_answered(cfg: Config, store: Store, now: datetime, state: dict) -> None:
    """A clean cycle on Google: clear the cool-down and let the cap recover a step."""
    q = google_quota(cfg)
    if state.get("cooling") or state.get("cooldown_until"):
        store.clear_state(STATE_COOLDOWN)
    if int(state.get("trips") or 0):
        store.set_state(STATE_TRIPS, "0", now)
    if q["cap"] and state.get("cap", q["cap"]) < q["ceiling"]:
        new_cap = min(q["ceiling"], int(state.get("cap", q["cap"])) + q["recover_step"])
        store.set_state(STATE_CAP, str(new_cap), now)
        state["cap"] = new_cap
    state.update(trips=0, cooldown_until=None, cooling=False)


# Inside the open rotation a query's wait is weighted by its value: a tier-A name's own query (prio 1)
# comes round about twice as often as a grouped comp query (prio 4), so a rating action on any
# coverage name is picked up within the hour or two, not the half day a flat rotation could leave.
ROTATION_WEIGHT = {0: 1.0, 1: 1.0, 2: 1.4, 3: 1.8, 4: 2.2, 5: 2.6, 6: 3.0}


def rotate_google(jobs: list[JobSpec], cap: int, store: Store, focus_ids: set[str], comp_ids: set[str], reason: str,
                  dormant: Optional[set[str]] = None, name_share: int = 0, dormant_slots: int = 0) -> int:
    """Keep the `cap` most valuable Google jobs this cycle and mark the rest skipped.

    Order: focus credits' press queries (solo, alias, own-domain site:), then their key-outlet
    site: queries, then the focus comps' solo queries, then everything else; inside each tier by
    how long the query has waited (never-sent first), with the queue's value order as the
    tie-break. Every query looks back a day, so a deferred query loses latency, not stories, and
    the staleness order guarantees it is sent the next time it is due and the queue has room.

    v2.18 (26 Oct 2026), after the Monday catch-up put 249 Intrum queries, 225 of them site:
    searches of regulators that had not returned an item in a week, in front of every other
    name's press query for two hours: a query in `dormant` (no item in a week, see
    Store.zero_yield_labels) ranks behind everything that finds things, but keeps `dormant_slots`
    of the cap to itself so a quiet market is still asked every few hours rather than never; and
    no single name's solo queries take more than `name_share` of the cap in one cycle. What a
    name cannot fit waits for the next cycle with its staleness intact. Returns the number deferred.
    """
    if cap <= 0:
        return 0
    google = [j for j in jobs if j.route == "googlenews" and not j.skip]
    if len(google) <= cap:
        return 0
    last = store.last_attempt_runs("googlenews")
    order = {id(j): i for i, j in enumerate(google)}      # the queue's own value order
    dormant = dormant or set()

    def tier(j: JobSpec) -> int:
        ids = set(j.name_ids)
        if len(ids) == 1 and ids <= focus_ids:
            return 0 if j.prio <= 2 else 1       # press and own-domain queries before key-outlet site: queries
        if len(ids) == 1 and ids <= comp_ids:
            return 2
        return 3

    now_run = max(last.values(), default=0) + 1

    def waited(j: JobSpec) -> float:
        # never sent: waited for ever; otherwise cycles since last sent, divided by the value weight
        sent = last.get(j.label)
        age = float(now_run * 10) if sent is None else float(now_run - sent)
        return -age / ROTATION_WEIGHT.get(j.prio, 3.0)

    key = lambda j: (tier(j), waited(j), j.prio, order[id(j)])   # noqa: E731
    live = sorted((j for j in google if j.label not in dormant), key=key)
    asleep = sorted((j for j in google if j.label in dormant), key=key)
    # the dormant queries' reserved slice: never more than there are dormant queries, never more
    # than the cap leaves after one live query
    reserve = min(max(0, dormant_slots), len(asleep), max(0, cap - 1)) if asleep else 0
    kept = 0
    taken: dict[str, int] = {}

    def take(seq, limit):
        nonlocal kept
        n = 0
        for j in seq:
            if n >= limit:
                j.skip = reason
                continue
            ids = j.name_ids
            if name_share > 0 and len(ids) == 1:
                if taken.get(ids[0], 0) >= name_share:
                    j.skip = reason
                    continue
                taken[ids[0]] = taken.get(ids[0], 0) + 1
            n += 1
        kept += n
        return n

    n_live = take(live, cap - reserve)
    take(asleep, cap - n_live)
    return len(google) - kept


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
    kept_from_screen: int = 0     # tier-A rows a noise domain would have screened, kept flagged
    stale: int = 0
    republished: int = 0          # v2.15: old headlines under a new timestamp, screened
    unrelated: int = 0
    alerts: int = 0
    source_results: list[SourceResult] = field(default_factory=list)
    errors: int = 0
    skipped: int = 0
    tripped: int = 0
    deferred: int = 0             # Google queries held back by the rotation cap or a cool-down
    tripped_routes: list[str] = field(default_factory=list)


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
        max_penalty_seconds=float(rl.get("max_penalty_seconds", 60.0)),
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


_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]")


def build_jobs(cfg: Config, http: Http, store: Store, run_number: int, backfill_days: Optional[int] = None, all_routes: bool = False, now: Optional[datetime] = None) -> list[JobSpec]:
    jobs: list[JobSpec] = []
    routes = cfg.routes
    now = now or datetime.now(timezone.utc)
    cold = cold_start(cfg, run_number)
    cold_window = str(routes.get("googlenews", {}).get("cold_start_when", "30d"))
    # A backfill is cumulative, not one long cycle. `--backfill-days N` opens a window during which
    # every Google query carries an N-day lookback; the per-cycle cap and the staleness rotation
    # then work through the whole queue over the following cycles, focus credits first, and the
    # window closes when every query has been sent once or after backfill_window_hours. One
    # 55-minute cycle at full speed (the first design) drew Google's 503 inside half an hour.
    gstate = google_state(cfg, store, now)
    if backfill_days:
        bf = {"days": int(backfill_days), "since_run": int(run_number),
              "expires": (now + timedelta(hours=google_quota(cfg)["backfill_hours"])).isoformat(timespec="seconds")}
        store.set_state(STATE_BACKFILL, json.dumps(bf), now)
        gstate["backfill"] = bf
    bf_days = int((gstate.get("backfill") or {}).get("days") or 0)
    when_google = f"{bf_days}d" if bf_days else str(routes.get("googlenews", {}).get("when", "1d"))
    # v2.18: the site: and regulator queries keep their seven-day window inside a backfill. A 3-day
    # lookback on a query that already looks back seven days is narrower, not wider.
    when_site = cold_window if cold else str(routes.get("googlenews", {}).get("site_when", "7d"))
    gdelt_span = f"{backfill_days}d" if backfill_days else str(routes.get("gdelt", {}).get("timespan", "24h"))
    outlet_by_domain = {domain_of(o.homepage): o for o in cfg.outlets}
    # run every cadenced route regardless of its every_n_runs: a manual run, or any cycle inside an
    # open backfill window, so the rotation can work through the whole queue with the wide lookback
    force = backfill_days is not None or all_routes or bool(bf_days)
    # v2.18 (26 Oct 2026): a backfill forces the one-day press queries only. The site: queries on
    # key outlets, a name's own long list of outlets and regulators, and the regulator sweeps all
    # look back seven days on their ordinary cadence, so a weekend is covered without forcing them;
    # forced, they were 1,245 deferred queries and two hours of Google's 503 on the Monday catch-up
    # of 26 October. A manual --all-routes run without a backfill still forces everything once.
    force_site = bool(all_routes) and backfill_days is None and not bf_days

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
    foreign_every = max(1, int(g.get("foreign_every_n_runs", 3)))      # tier-A non-home solo queries
    tier_a_group_every = max(1, int(g.get("tier_a_group_every_n_runs", 1)))  # grouped tier-A safety net
    focus_every = max(1, int(g.get("focus_every_n_runs", 2)))          # focus credits: every market, every Nth run
    focus_comp_every = max(1, int(g.get("focus_comp_every_n_runs", 4)))  # comps of focus credits: solo home query
    focus_alias_every = max(1, int(g.get("focus_alias_every_n_runs", 4)))  # focus credits' secondary aliases, at least every Nth run
    focus_alias_min_every = max(1, int(g.get("focus_alias_min_every_n_runs", 1)))  # ... and at most this often (11 Oct)
    alias_min_every = max(1, int(g.get("alias_min_every_n_runs", 1)))  # other names' secondary aliases, at most this often
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

    # Google News returns at most 100 items per query. A group that carries a name with hundreds
    # of headlines a day (Nike, Amazon, Booking) fills the cap on every run and the other five
    # names in the group are truncated out of the result. Names listed under
    # routes.googlenews.heavy_names are therefore chunked among themselves, so a saturated group
    # only ever truncates other heavy names, and the light names keep a full result each.
    heavy_ids = {str(x) for x in (g.get("heavy_names", []) or [])}

    def _grouped_chunks(entries, size):
        light = [e for e in entries if e[0] not in heavy_ids]
        heavy = [e for e in entries if e[0] in heavy_ids]
        ci = 0
        for part in (light, heavy):
            for i in range(0, len(part), size):
                yield ci, part[i:i + size]
                ci += 1

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
            # Solo queries for tier-A names. Until v2 only the home market had one, and every other
            # market rode on a grouped OR query shared with five other names. Google News returns a
            # capped page per query, so a quiet name in a busy group was crowded out: SYNLAB's French
            # market shared one query with Südzucker, Tereos and Quick in the week its Nouvelle-
            # Aquitaine sites were evacuated, and the engine never saw the coverage. Every tier-A
            # market now gets its own query: home every run, the others on a rotation.
            # Focus credits (config/focus.yaml) are searched in every market every focus_every runs,
            # whatever their kind; their comps get a solo query in their home market, because a comp
            # otherwise rides only on grouped OR queries, where a busy neighbour crowds it out of
            # Google's capped result page (Vonovia shared its German query with five other names).
            if name.kind != "comp" or name.focus == "name":
                for mi, m in enumerate(markets):
                    home = (m.country == name.home_country) or len(markets) == 1
                    if name.focus == "name":
                        due = force or home or _due(focus_every, run_number, sum(name.id.encode()) + mi)
                    else:
                        due = force or (_due(home_every, run_number, sum(name.id.encode())) if home else _due(foreign_every, run_number, sum(name.id.encode()) + mi))
                    if due:
                        q = f'"{main}"'
                        # label equals the fetch's own source string, so a skipped run and a
                        # completed run of the same query land in one health row
                        jobs.append(JobSpec(f"{q} [{m.lang}-{m.country}]", "googlenews", lambda q=q, m=m, ids=ids: fetch_google_news(http, q, m.lang, m.country, ids, when_google), name_ids=list(ids), prio=1 if (home or name.focus) else 2))
            elif name.focus == "comp":
                m = next((mk for mk in markets if mk.country == name.home_country), markets[0] if markets else None)
                if m is not None and (force or _due(focus_comp_every, run_number, sum(name.id.encode()))):
                    q = f'"{main}"'
                    jobs.append(JobSpec(f"{q} [{m.lang}-{m.country}]", "googlenews", lambda q=q, m=m, ids=ids: fetch_google_news(http, q, m.lang, m.country, ids, when_google), name_ids=list(ids), prio=2))
            # secondary search aliases keep their per-alias solo behaviour, but v2 queries each one
            # once per LANGUAGE, not once per country: "6-APA" went out in nl-NL and zh-HK, and
            # "Garfunkelux Holdco" in nb-NO, 116 queries a run, mostly duplicates of each other.
            # An alias written in Chinese, Japanese or Korean script only runs in those markets.
            for alias in name.aliases:
                if not alias.search or alias.text == main:
                    continue
                # a focus credit's aliases run at least every focus_alias_every runs and, since the
                # Google quota (11 Oct), at most every focus_alias_min_every: Intrum's five aliases
                # in a dozen languages were 10 queries a cycle on their own
                every = max(min(alias.search_every, focus_alias_every), focus_alias_min_every) if name.focus == "name" else max(alias.search_every, alias_min_every)
                if not (force or _due(every, run_number, sum(alias.text.encode()))):
                    continue
                cjk = bool(_CJK_RE.search(alias.text))
                seen_langs: set[str] = set()
                # 11 Oct: an alias with no language of its own (a chief executive, a former name)
                # runs in the home language and in English, not in every language the name has
                # markets in. "Intrum Justitia" in fifteen languages every four runs was nearly four
                # Google queries a cycle for one alias; the main query already covers those markets.
                home_lang = next((mk.lang for mk in markets if mk.country == name.home_country), markets[0].lang if markets else "en")
                open_alias = "all" in alias.langs or not alias.langs
                for m in sorted(markets, key=lambda mk: mk.country != name.home_country):
                    if alias.search_langs:
                        # 15 Oct: an alias can name the languages it is searched in, which then
                        # replace the rules below: Hippopotamus in French only, Ivar Tollefsen in
                        # Norwegian and Swedish (the main query already covers English)
                        if m.lang not in alias.search_langs and m.lang.split("-")[0] not in alias.search_langs:
                            continue
                    elif not alias.applies_to(m.lang):
                        continue
                    elif open_alias and not cjk and m.lang.split("-")[0] not in (home_lang.split("-")[0], "en"):
                        continue
                    if cjk and m.lang.split("-")[0] not in ("zh", "ja", "ko"):
                        continue
                    if m.lang in seen_langs:
                        continue
                    seen_langs.add(m.lang)
                    # an alias may carry its own query (CNAM, 13 Oct): "Assurance maladie" on its
                    # own returned 74 rows a day of Vietnamese and Togolese social-security news and
                    # nothing on lab tariffs; the query has to carry the sector context itself
                    q = alias.query or f'"{alias.text}"'
                    jobs.append(JobSpec(f"{q} [{m.lang}-{m.country}]", "googlenews", lambda q=q, m=m, ids=ids: fetch_google_news(http, q, m.lang, m.country, ids, when_google), name_ids=list(ids), prio=2))
            # per-name site queries (only names that define them, e.g. Intrum), staggered. The key
            # outlets (overlay.yaml) keep the site_every cadence; a name's own long list of domains
            # (Intrum: 225 outlets and regulators in 23 countries, 14 queries a cycle, a fifth of
            # the whole Google budget) rotates at most site_max_per_name_per_run a cycle. They use a
            # seven-day window, so a domain visited every 28 hours still misses nothing.
            site_every = max(1, int(g.get("site_every_n_runs", 4)))
            site_name_cap = max(1, int(g.get("site_max_per_name_per_run", 2)))
            own_every = max(1, int(g.get("own_site_every_n_runs", 8)))
            if name.site_queries:
                own = [d for d in name.site_queries if d in name.own_domains]
                key = [d for d in name.site_queries if d in name.key_outlets and d not in name.own_domains]
                rest = [d for d in name.site_queries if d not in name.key_outlets and d not in name.own_domains]
                # 15 Oct: the issuer's own domains (overlay.yaml own_domains) are the fallback channel to
                # a newsroom the watcher cannot read, so a focus credit's own domains run every
                # own_site_every runs, phased by name so they spread over the cycle; any other name's
                # run at the key-outlet cadence
                own_due_every = own_every if name.focus == "name" else site_every
                ph = sum(name.id.encode())
                due_own = [d for i, d in enumerate(own) if force or _due(own_due_every, run_number, ph + i)]
                # 15 Oct: the key outlets and short lists are phased by name as well. Phased by list
                # position alone, every name's first outlet fell on the same cycle, so one run in
                # sixteen carried some thirty focus site: queries and the rest of the queue waited.
                due_doms = [d for i, d in enumerate(key) if force_site or (i + ph) % site_every == run_number % site_every]
                if rest:
                    if len(rest) <= site_name_cap * site_every:
                        due_doms += [d for i, d in enumerate(rest) if force_site or (i + ph + len(key)) % site_every == run_number % site_every]
                    else:
                        # v2.18: a long list never floods a cycle, forced or not. It rotates
                        # site_max_per_name_per_run domains a cycle, twice that on a forced run.
                        due_doms += [rest[(run_number * site_name_cap + k) % len(rest)] for k in range(site_name_cap * (2 if force_site else 1))]
                for dom in due_own + due_doms:
                    o = outlet_by_domain.get(dom)
                    lang = o.lang if o and o.lang else (markets[0].lang if markets else "en")
                    country = o.country if o else (markets[0].country if markets else "GB")
                    if dom in name.own_domain_markets:
                        lang, country = name.own_domain_markets[dom]
                    # v2.18: an own domain may name the path its newsroom lives under (overlay.yaml
                    # own_domains path), so the query stops returning the careers pages
                    q = f'"{main}" site:{dom}{name.own_domain_paths.get(dom, "")}'
                    jobs.append(JobSpec(f"{q} [{lang}-{country}]", "googlenews", lambda q=q, lang=lang, country=country, ids=ids: fetch_google_news(http, q, lang, country, ids, when_site), name_ids=list(ids), prio=1 if dom in due_own else 3))
        # name-specific feeds and pages (any tier that defines them)
        if cfg.route_enabled("rss"):
            for f in name.feeds:
                jobs.append(JobSpec(f.name, "rss", lambda f=f: fetch_feed(http, f.url, f.name_ids, f.tier, f.country, f.lang, f.name), name_ids=list(f.name_ids), prio=0))
        if cfg.route_enabled("pages"):
            pages_every = int(routes.get("pages", {}).get("every_n_runs", 2))
            seed_items = int(routes.get("pages", {}).get("backfill_seed_items", 5))
            if force or _due(pages_every, run_number):
                for p in name.pages:
                    jobs.append(JobSpec(p.name, "page", lambda p=p: _page_job(http, store, p, backfill=force, seed_items=seed_items), name_ids=list(p.name_ids), prio=0))

    # ---- grouped Google per market ----
    if cfg.route_enabled("googlenews"):
        # Grouped tier-A queries are now a safety net behind the solo queries above, so they run on
        # their own cadence and after them in the queue.
        # v2: each market's groups take their own phase, so the grouped load is spread evenly over
        # the cycle instead of landing on one run (that run reached 404 Google queries against a
        # capacity of about 390). Every group still runs once per cadence.
        for mi, ((lang, country), entries) in enumerate(sorted(by_market_a.items())):
            if not (force or _due(tier_a_group_every, run_number, 2 + mi)):
                continue
            for ci, chunk in _grouped_chunks(entries, group_size):
                q = _or_query(chunk)
                nids = [nid for nid, _ in chunk]
                jobs.append(JobSpec(f"{q} [{lang}-{country}]", "googlenews", lambda q=q, lang=lang, country=country, nids=nids: fetch_google_news(http, q, lang, country, nids, when_google), name_ids=nids, prio=4))
        for mi, ((lang, country), entries) in enumerate(sorted(by_market_c.items())):
            if not (force or _due(comp_every, run_number, 1 + mi)):
                continue
            for ci, chunk in _grouped_chunks(entries, group_size):
                q = _or_query(chunk)
                nids = [nid for nid, _ in chunk]
                jobs.append(JobSpec(f"{q} [{lang}-{country}]", "googlenews", lambda q=q, lang=lang, country=country, nids=nids: fetch_google_news(http, q, lang, country, nids, when_google), name_ids=nids, prio=4))
        # regulator sweep: home-country authority domains x the names based there, staggered
        reg_every = max(1, int(g.get("regulator_every_n_runs", 12)))
        reg_jobs = []
        for cc, entries in sorted(by_home.items()):
            for dom in cfg.regulators.get(cc, []):
                for ci, chunk in _chunks(entries, group_size):
                    reg_jobs.append((cc, dom, ci, chunk))
        for idx, (cc, dom, ci, chunk) in enumerate(reg_jobs):
            if not force_site and idx % reg_every != run_number % reg_every:
                continue
            q = _or_query(chunk) + f" site:{dom}"
            nids = [nid for nid, _ in chunk]
            lang = "en"
            jobs.append(JobSpec(f"{q} [en-{cc}]", "googlenews", lambda q=q, cc=cc, nids=nids: fetch_google_news(http, q, "en", cc, nids, when_site), name_ids=nids, prio=5))

    # ---- Bing: the focus credits' home market on a slow cadence, and a reserve for Google ----
    if cfg.route_enabled("bingnews"):
        bing_cfg0 = routes.get("bingnews", {}) or {}
        bing_focus_every = int(bing_cfg0.get("focus_every_n_runs", 0) or 0)
        bing_fallback = bool(bing_cfg0.get("fallback", True))
        for name in cfg.names:
            markets = name.markets
            if not markets:
                continue
            main = _main_alias(name)
            home = next((m for m in markets if m.country == name.home_country), markets[0])
            q = f'"{main}"'
            if name.focus == "name" and bing_focus_every and (force or _due(bing_focus_every, run_number, sum(name.id.encode()))):
                jobs.append(JobSpec(f"{q} [{bing_market(home.lang, home.country)}]", "bingnews", lambda q=q, m=home, ids=[name.id]: fetch_bing_news(http, q, m.lang, m.country, ids), name_ids=[name.id], prio=3))
            if not bing_fallback:
                continue
            # Reserve queries: a focus credit in every market, any other tier-A name at home. They
            # are submitted only when Google is refusing us, so they cost Bing nothing otherwise.
            if name.focus == "name":
                for m in sorted(markets, key=lambda mk: mk.country != name.home_country):
                    jobs.append(JobSpec(f"{q} [{bing_market(m.lang, m.country)}]", "bingnews", lambda q=q, m=m, ids=[name.id]: fetch_bing_news(http, q, m.lang, m.country, ids), name_ids=[name.id], prio=1, fallback=True))
            elif name.kind != "comp":
                jobs.append(JobSpec(f"{q} [{bing_market(home.lang, home.country)}]", "bingnews", lambda q=q, m=home, ids=[name.id]: fetch_bing_news(http, q, m.lang, m.country, ids), name_ids=[name.id], prio=2, fallback=True))

    # ---- grouped Bing per market ----
    if cfg.route_enabled("bingnews"):
        # v2: Bing as a narrow lane, not a second sweep. Over the month to 10 October it touched 341
        # clusters against Google's 40,000, but what only Bing found on tier-A names was mostly
        # Latvian and Baltic Russian-language press on airBaltic (nra.lv, jauns.lv, rus.tvnet.lv,
        # rus.postimees.ee), among it the EUR 140m first financing tranche on 29 September. So it can
        # be limited to a set of countries and to tier A; it runs in its own lane and costs Google nothing.
        bing_cfg = routes.get("bingnews", {}) or {}
        bing_countries = {str(c).upper() for c in (bing_cfg.get("countries") or [])}
        bing_maps = [(by_market_a, bing_every, 1)]
        if not bing_cfg.get("tier_a_only", False):
            bing_maps.append((by_market_c, bing_comp_every, 3))
        for tier_map, every, phase in bing_maps:
            if not (force or _due(every, run_number, phase)):
                continue
            for (lang, country), entries in sorted(tier_map.items()):
                if bing_countries and country.upper() not in bing_countries:
                    continue
                for ci, chunk in _grouped_chunks(entries, group_size):
                    q = " OR ".join(f'"{alias}"' for _, alias in chunk)
                    nids = [nid for nid, _ in chunk]
                    jobs.append(JobSpec(f"{q} [{bing_market(lang, country)}]", "bingnews", lambda q=q, lang=lang, country=country, nids=nids: fetch_bing_news(http, q, lang, country, nids), name_ids=nids, prio=3))

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
                jobs.append(JobSpec(f"{q} [all/all]", "gdelt", lambda q=q, nids=nids: fetch_gdelt(http, q, nids, gdelt_span), name_ids=nids, prio=6))

    # --- shared outlet feeds (fetched once, matched against every name). Feed discovery is capped
    #     per run and retried at most daily per outlet, so the first runs are not swamped by it.
    if cfg.route_enabled("rss") and cfg.outlets:
        wanted_countries = {m.country for n in cfg.names for m in n.markets}
        discover_budget = int(routes.get("rss", {}).get("discover_per_run", 20))
        retry_after = timedelta(hours=float(routes.get("rss", {}).get("discover_retry_hours", 24)))
        # v2.18: the run's own clock, as everywhere else in this function. This used to reset `now`
        # to the wall clock for everything built after the outlet feeds.
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
            jobs.append(JobSpec(f"{o.name} ({o.country})", "rss", lambda o=o, discover=discover: _outlet_job(http, store, o, discover=discover), prio=1))

    # Deterministic per-run shuffle: interleaves routes within a run and rotates which jobs
    # land in the time-budget tail, so a skipped source is picked up on the next cycle
    # instead of being starved forever by a fixed construction order.
    random.Random(run_number).shuffle(jobs)
    # Primary sources first. The shuffle rotates which jobs land in the budget tail, which is right
    # for a thousand search queries and wrong for a company's own newsroom: in the 28 September
    # export ten of thirty-nine page watchers read "skipped: run time budget reached". A newsroom
    # fetch is one request to a host nobody else in the run is talking to, so the whole layer costs
    # the run about two minutes of worker time; it goes first so the budget can never take it. The
    # sort is stable, so the rotation still applies within each group.
    # v2: the whole queue is ordered by value, not only the primary sources. Own sources first, then
    # shared outlet feeds and tier-A home queries, then the other tier-A markets and people, then
    # site queries and Bing, then comps and grouped tier A, then regulators and GDELT.
    jobs.sort(key=lambda j: j.prio)
    # The Google cap (see google_quota). Deferred queries are recorded as skipped with the reason,
    # so coverage.json can tell a rotation from a budget skip and a refusal. Reserve Bing jobs
    # are not part of the queue until Google is down.
    cap = int(gstate.get("cap") or 0)
    quota = google_quota(cfg)
    bf = gstate.get("backfill") or {}
    if cap and bf and quota["backfill_cap"]:
        # v2.18: a catch-up runs under its own, lower cap. The 26 October catch-up went out at the
        # 200 ceiling and drew Google's 503 within the hour.
        cap = min(cap, quota["backfill_cap"])
    if cap and cfg.route_enabled("googlenews"):
        focus_ids = {n.id for n in cfg.names if n.focus == "name"}
        comp_ids = {n.id for n in cfg.names if n.focus == "comp"}
        dormant: set[str] = set()
        if quota["dormant_days"] > 0:
            dormant = store.zero_yield_labels("googlenews", now - timedelta(days=quota["dormant_days"]), quota["dormant_min_attempts"])
        share = max(quota["name_share_min"], int(cap * quota["name_share"])) if quota["name_share"] > 0 else 0
        dslots = max(quota["dormant_min_slots"], int(cap * quota["dormant_share"])) if dormant else 0
        deferred = rotate_google(jobs, cap, store, focus_ids, comp_ids, f"skipped: googlenews rotation (cap {cap} a run)",
                                 dormant=dormant, name_share=share, dormant_slots=dslots)
        if deferred:
            log.info("googlenews cap %d: %d queries deferred to later cycles by staleness (%d dormant on %d slots, share %d a name)", cap, deferred, len(dormant), dslots, share)
        if bf and not deferred:
            # every query fitted this cycle, so the backfill window has nothing left to do
            store.clear_state(STATE_BACKFILL)
        elif bf:
            # the window is done once every press query it widened has been sent since it opened;
            # the site: and regulator queries (v2.18) run on their own cadence and seven-day
            # window and are not what the window waits for
            last = store.last_attempt_runs("googlenews")
            since = int(bf.get("since_run") or 0)
            left = [j for j in jobs if j.route == "googlenews" and j.skip and " site:" not in j.label and last.get(j.label, -1) < since]
            if not left:
                store.clear_state(STATE_BACKFILL)
    return jobs


PAGE_RESEED_MAX_NEW = 3       # more unseen article links than this in one fetch, and
PAGE_RESEED_SHARE = 0.5       # at least this share of the listed ones, is an archive re-read
PAGE_MAX_AGE_HOURS = 72.0     # a newsroom article dated older than this is not news


def _page_job(http: Http, store: Store, page: PageSource, backfill: bool = False, seed_items: int = 5) -> tuple[list[RawItem], SourceResult]:
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
    # A watcher that has been running and suddenly sees many article links it has never seen is
    # re-reading its archive (a newsroom re-paginated, a CMS changed its link parameters), not
    # reporting a burst of news: Adler's newsroom did exactly this on 12 October. Those links are
    # now marked seen and nothing is emitted; a real burst is still caught by the search routes.
    if not first_time and not backfill and len(items) > PAGE_RESEED_MAX_NEW:
        listed = len(page_items_from_links(links, page.url, page.name_ids, page.tier, page.country,
                                           page.lang, page.name, page.link_pattern))
        # Half the list new at once is a re-read archive; a few new among many known (the engine
        # was paused for a day) is a real backlog and goes on to the dating step.
        if listed and len(items) >= PAGE_RESEED_SHARE * listed:
            result.items = 0
            result.error = f"reseeded: {len(items)} of {listed} article links unseen at once, none emitted"
            return [], result
    if first_time and backfill and seed_items >= 0 and len(items) > seed_items:
        # A backfill run visits every watcher for the first time with the flood gate open. One
        # newsroom lists twenty releases; three hundred of them would put six thousand undated
        # rows into the pile in a single cycle, all stamped with today's first_seen. Newsrooms
        # list newest first, so the first few links are the recent history worth having and the
        # rest is archive. Set routes.pages.backfill_seed_items to -1 to take everything.
        total = len(items)
        items = items[:seed_items]
        result.error = f"seeded with the newest {seed_items} of {total} article links"
    for it in items:
        it.tier_hint = page.tier
    if items and http is not None:
        enrich_generic_titles(http, items)
        # every new link is dated from the article itself; archive items are dropped here
        items, old, undated = date_page_items(http, items, max_age_hours=PAGE_MAX_AGE_HOURS)
        if old or undated:
            result.error = (result.error + "; " if result.error else "") + f"{old} older than {PAGE_MAX_AGE_HOURS:.0f}h dropped, {undated} undated"
    result.items = len(items)
    return items, result


def _outlet_job(http: Http, store: Store, outlet: Outlet, discover: bool) -> tuple[list[RawItem], SourceResult]:
    now = datetime.now(timezone.utc)
    feed_url = outlet.feed_url
    if not feed_url:
        row = store.get_feed(outlet.homepage)
        feed_url = row["feed_url"] if row else ""
        if not feed_url and discover:
            feed_url, reason = discover_feed(http, outlet.homepage)
            store.set_feed(outlet.homepage, feed_url, now, "" if feed_url else reason)
    if not feed_url:
        row = store.get_feed(outlet.homepage)
        why = (row["last_error"] if row and row["last_error"] else "discovery pending")
        return [], SourceResult("rss", f"{outlet.name} ({outlet.country})", False, 0, f"no feed url ({why})", 0.0)
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


REDATE_GAP_DAYS = 14


def _redated(url: str, title: str, published: Optional[datetime], cutoff: datetime) -> bool:
    """True when the URL path or the headline carries a date that gives the claimed one the lie.

    18 October 2026: three airBaltic lead items were old articles under fresh timestamps (Latvian
    Public Media of 1 April and 6 July, a liepajniekiem.lv copy of a 25 August LETA wire), and
    Orange Actualites re-dated a June clip of Olivier Bertrand. The path date is written when the
    article is created; the " - 03/06" Orange appends is the broadcast date. Either one more than
    REDATE_GAP_DAYS before the claimed date, or before the lookback cutoff when no date is claimed,
    marks the item stale."""
    ref = (published or cutoff).date()
    limit = ref - timedelta(days=REDATE_GAP_DAYS) if published else cutoff.date()
    d = url_date(url)
    if d and d < limit:
        return True
    t = title_trailing_date(title, published) if published else None
    return bool(t and t < limit)


REPUBLISH_MIN_AGE_H = 72
REPUBLISH_LOOKBACK_DAYS = 150
REPUBLISH_JACCARD = 0.85
_DIGITS = re.compile(r"\d+")


class _TitleHistory:
    """Headlines the store first saw between REPUBLISH_LOOKBACK_DAYS and REPUBLISH_MIN_AGE_H ago,
    indexed by token, built once per run. A new item whose headline matches one of them (token
    Jaccard >= REPUBLISH_JACCARD on five or more tokens, and the same numbers) is a republication of
    an old story, not news: liepajniekiem.lv re-posts LETA wires weeks later and the Google News
    route stamps them with the day it found them (v2.15, 18 Oct 2026)."""

    def __init__(self, store: Store, now: datetime) -> None:
        from .dedupe import tokens
        self._tokens = tokens
        self.rows: list[tuple[set, set, str]] = []
        self.index: dict[str, list[int]] = {}
        try:
            hist = store.title_keys_between(now - timedelta(days=REPUBLISH_LOOKBACK_DAYS),
                                            now - timedelta(hours=REPUBLISH_MIN_AGE_H))
        except Exception:                      # noqa: BLE001 - an old schema or empty store: no history
            hist = []
        for key, seen in hist:
            tk = tokens(key)
            if len(tk) < 5:
                continue
            i = len(self.rows)
            self.rows.append((tk, set(_DIGITS.findall(key)), seen[:10]))
            for t in tk:
                self.index.setdefault(t, []).append(i)

    def first_seen(self, key: str) -> str:
        tk = self._tokens(key)
        if len(tk) < 5:
            return ""
        digits = set(_DIGITS.findall(key))
        counts: dict[int, int] = {}
        for t in tk:
            for i in self.index.get(t, ()):
                counts[i] = counts.get(i, 0) + 1
        best = ""
        for i, inter in counts.items():
            ta, da, seen = self.rows[i]
            if da != digits:
                continue
            if inter / len(tk | ta) >= REPUBLISH_JACCARD and (not best or seen < best):
                best = seen
        return best


RESERVE_MIN_GAP_H = 1.0       # a later copy at least this much younger than the original is a re-serve


def _reserved_copy(store: Store, name_id: str, tkey: str, domain: str, source_name: str, published: Optional[datetime], since: datetime):
    """The item the store already holds for this outlet and this headline, if the new one is a
    later copy of it (v2.18). Same normalised headline, same outlet (domain, or the outlet name
    when the domain is the aggregator's), and the original published, or first seen, at least
    RESERVE_MIN_GAP_H earlier than the copy claims. Returns the stored row or None."""
    if not tkey or not name_id:
        return None
    dom = (domain or "").lower()
    src = (source_name or "").strip().lower()
    for row in store.recent_items_for_name(name_id, since):
        if row["title_key"] != tkey:
            continue
        same_outlet = (dom and (row["source_domain"] or "").lower() == dom) or (src and (row["source_name"] or "").strip().lower() == src)
        if not same_outlet:
            continue
        stored = _parse_iso(row["published_at"] or "") or _parse_iso(row["first_seen_at"] or "")
        if stored is None:
            continue
        if published is None:
            return row
        if (published - stored).total_seconds() >= RESERVE_MIN_GAP_H * 3600:
            return row
    return None


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
    watched = {n.id for n in cfg.names if n.watch}
    watch_or_tier_a = {n.id for n in cfg.names if n.kind != "comp"} | watched

    history = getattr(store, "_title_history", None)
    if history is None or getattr(store, "_title_history_run", None) != run_id:
        history = _TitleHistory(store, now)
        store._title_history, store._title_history_run = history, run_id   # type: ignore[attr-defined]

    for raw in raw_items:
        summary.fetched += 1
        if not raw.title or not raw.link:
            continue
        # Staleness, three ways, because a claimed date is not a fact.
        #
        # 1. The claimed date is old. The straightforward case.
        # 2. The claimed date is missing. This used to skip the check entirely, so an undated
        #    item of any age went through. Undated is normal and meaningful for a page watcher,
        #    where a new link on a watched newsroom IS the event; it is not acceptable from a
        #    search route, where an undated hit is simply unverifiable.
        # 3. The claimed date is recent but the URL says otherwise. Publishers re-date archive
        #    pages, and on 13 September a 2009 C&A story arrived stamped that morning. The year
        #    in the URL path is written when the article is created and does not move, so where
        #    the two disagree, the URL wins.
        if raw.published_at and raw.published_at < cutoff:
            summary.stale += 1
            continue
        if not raw.published_at and raw.route in SEARCH_ROUTES:
            summary.stale += 1
            continue
        year = url_year(raw.link)
        if year and year < cutoff.year:
            summary.stale += 1
            log.debug("stale by URL year %s: %s", year, raw.title[:80])
            continue
        # 4. (v2.15) The full date in the path, and a broadcast date at the end of the headline, are
        #    compared with the claimed date: a gap of more than REDATE_GAP_DAYS is a re-date.
        if _redated(raw.link, raw.title, raw.published_at, cutoff):
            summary.stale += 1
            log.debug("stale by URL or headline date: %s", raw.title[:80])
            continue
        canon = canonical_url(raw.link)
        if resolve and is_google_news_link(canon):
            resolved = http.head_final_url(canon)  # type: ignore[union-attr]
            if resolved and not is_google_news_link(resolved):
                canon = canonical_url(resolved)
                # The aggregator link carries no date; the outlet's own path usually does, so
                # this is the first point at which a re-dated archive page can be caught.
                year = url_year(canon)
                if year and year < cutoff.year:
                    summary.stale += 1
                    log.debug("stale by resolved URL year %s: %s", year, raw.title[:80])
                    continue
                if _redated(canon, "", raw.published_at, cutoff):
                    summary.stale += 1
                    log.debug("stale by resolved URL date: %s", raw.title[:80])
                    continue
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
        # The domain screen is for re-daters and churn sites, and on a small tier-A name in
        # restructuring those sites can be the only coverage there is. On 5 October the only trace
        # of Branicks falling 20 per cent eight days before its bondholder vote, with the
        # Sanierungskonzept meeting and the 9 October vote beside it, was three screened headlines,
        # and a screened item never reaches the export. So a tier-A row from a screened domain is
        # kept when its headline carries an engine category or a hard signal (newsflow.signal), or
        # when the name is on watch; it arrives as a candidate whose screen_reason says "kept:" so
        # the brief flags it as a re-dater domain with its date unverified, for judgement to weigh.
        # Comps stay screened: read-across from a churn site is not worth the noise. Title-pattern
        # screens (Kursziel, Chartanalyse) are never overridden; they describe junk, not sources.
        kept_why = ""
        # v2: churn sites are screened as "churn:<domain>" (config/noise.yaml demote_domains); the keep
        # rule applies to them exactly as to noise domains, and what it does not keep is exported as an echo.
        if reason.startswith(("noise_domain:", "churn:")) and primary in watch_or_tier_a and not matcher.screen("", "", raw.title, primary):
            cats_in_title, _ = matcher.tier1_categories(raw.title, "")
            kept_why = ", ".join(cats_in_title) or hard_signal(raw.title)
            if not kept_why and primary in watched:
                kept_why = "watch"
            if kept_why:
                status = "candidate"
                reason = f"kept:{reason}:{kept_why}"
        tkey = title_key(raw.title, raw.source_name)
        # v2.18 (26 Oct 2026): the same outlet serving the same headline again under a later
        # timestamp is the article the store already holds, not a second article. Google News
        # re-served a Friday Main-Post piece on Südzucker on the Sunday with a fresh date and a
        # fresh redirect URL; the copy joined the Friday cluster and moved its date to Sunday. The
        # copy is noted on the original (also_routes) and never inserted, so a cluster's date is
        # the first time the outlet published, not the last time the index served it.
        if raw.route != "page":
            original = _reserved_copy(store, primary, tkey, domain, raw.source_name, raw.published_at, cluster_window)
            if original is not None:
                store.note_also_route(int(original["id"]), raw.route, raw.query)
                summary.republished += 1
                log.debug("re-served copy of item %s, not inserted: %s", original["id"], raw.title[:80])
                continue
        seen_before = history.first_seen(tkey)
        if seen_before:
            # an old story under a new timestamp: stored for the audit, never a candidate
            status, reason, kept_why = "screened", f"republished:{seen_before}", ""
            summary.republished += 1
        item = Item(
            id=None, canonical_url=canon, raw_link=raw.link, title=raw.title.strip(), summary=raw.summary.strip(),
            source_name=raw.source_name, source_domain=domain, published_at=raw.published_at, first_seen_at=now,
            lang=raw.lang, country=raw.country, route=raw.route, query=raw.query, tier_hint=raw.tier_hint,
            status=status, screen_reason=reason,
        )
        item_id = store.insert_item(item, tkey, run_id)
        summary.new_items += 1
        if status == "screened":
            summary.screened += 1
        else:
            summary.candidates += 1
            if kept_why:
                summary.kept_from_screen += 1
        for m in matches:
            store.add_match(item_id, m.name_id, m.alias, m.where, float(m.confidence))

        cats, alert = matcher.tier1_categories(raw.title, raw.summary)
        if status == "screened" or kept_why:
            alert = False          # a churn site never raises an alert, kept or not
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

class StateRollback(RuntimeError):
    """The database this run was handed has seen fewer runs than the last published export."""


def published_run_count(cfg: Config) -> int:
    """Runs recorded in the last export the engine committed, or -1 if there is no export yet.

    The exports are committed to git on every run, so whatever is in the working tree after
    checkout is a floor: the engine cannot legitimately know less than it has already published.
    """
    for name in ("health.json", "latest.json"):
        path = cfg.out_dir / name
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        runs = (data.get("stats") or {}).get("runs")
        if isinstance(runs, int):
            return runs
    return -1


def assert_state_not_rolled_back(cfg: Config, store: Store) -> None:
    """Refuse to run on state older than what was last published.

    The engine's memory of what it has already reported lives in `seen_links` and the cluster
    history, carried between runs in the Actions cache. A cache restore matches by key prefix, so
    when entries are evicted it can hand back an older surviving copy. Running on that copy does
    not fail: it quietly re-reports weeks-old stories as new, which is the one failure mode that
    would discredit the whole brief, and nothing in the run summary would say so.

    So compare what the database knows against what was last published and stop if it went
    backwards. Set NEWSFLOW_ALLOW_STATE_RESET=1 to start deliberately from an empty database.
    """
    if os.environ.get("NEWSFLOW_ALLOW_STATE_RESET"):
        log.warning("NEWSFLOW_ALLOW_STATE_RESET is set: the rollback guard is off for this run")
        return
    published = published_run_count(cfg)
    if published < 0:
        return
    have = store.run_count()
    if have >= published:
        return
    raise StateRollback(
        f"the database has {have} runs but the last published export recorded {published}, so it "
        f"is {published - have} runs behind. The state carried between runs came back from an "
        "older copy: the engine has forgotten part of what it already reported and would publish "
        "those stories again as new. Nothing was run. Either recover the newer copy of "
        f"{cfg.db_path}, or accept the loss by re-running with allow_state_reset ticked "
        "(NEWSFLOW_ALLOW_STATE_RESET=1), which re-reports at most the lookback window once."
    )


def run_once(cfg: Config, store: Store, http: Optional[Http] = None, *, backfill_days: Optional[int] = None, all_routes: bool = False, now: Optional[datetime] = None, jobs: Optional[list] = None, budget_minutes: Optional[float] = None) -> RunSummary:
    now = now or datetime.now(timezone.utc)
    assert_state_not_rolled_back(cfg, store)
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

    specs = jobs if jobs is not None else build_jobs(cfg, http, store, run_number, backfill_days, all_routes, now=now)
    specs = [j if isinstance(j, JobSpec) else JobSpec(f"job-{i}", "custom", j) for i, j in enumerate(specs)]
    summary.jobs = len(specs)
    workers = max(1, int(cfg.engine.get("max_workers", 4)))
    budget = float(cfg.engine.get("max_run_minutes", 12)) if budget_minutes is None else float(budget_minutes)
    deadline = time.monotonic() + budget * 60
    # The HTTP layer gets the same deadline, so a job already in flight when the budget runs out
    # stops at its next wait instead of sitting through its retries against a slow host.
    http.deadline = deadline
    results: list[SourceResult] = []
    log.info("run %s (#%d): %d jobs, %d workers, budget %.0f min, lookback %.0f h", run_id, run_number, len(specs), workers, budget, lookback)

    def handle(spec: JobSpec, items: list[RawItem], res: SourceResult) -> None:
        res.names = list(spec.name_ids)
        results.append(res)
        log.info("%s %-10s %-58s items=%-4d %5.1fs %s", "ok " if res.ok else "ERR", res.route, res.source[:58], res.items, res.seconds, res.error[:90])
        if items:
            process_items(cfg, store, matcher, http, items, run_id, now, summary, lookback)

    # Circuit breaker. An upstream that starts refusing us (Google News in particular) answers
    # slowly and empty rather than with an error, so the route burns the whole time budget on
    # nothing and starves every other route. After enough consecutive ok-but-empty responses the
    # route is tripped for the rest of the run and its queued jobs are cancelled, which hands the
    # remaining budget to the routes that are still working. Any job returning items resets it.
    breaker = cfg.engine.get("circuit_breaker", {}) or {}
    breaker_on = bool(breaker.get("enabled", True))
    trip_after = int(breaker.get("consecutive_empty", 25))
    trip_after_by_route = {str(k): int(v) for k, v in (breaker.get("consecutive_empty_by_route", {}) or {}).items()}
    # The breaker reads "ok but empty" as a refusal, which is right for a search index and wrong
    # for a page watcher: a newsroom with nothing new since the last visit answers ok and empty
    # every time, and that is the normal state of most of them. With forty watchers the page
    # route already tripped in the 25 and 28 September runs ("skipped: page tripped after 25
    # consecutive empty responses" in health.json); with one per name it would trip every cycle
    # and cancel the primary sources the layer exists to fetch. So the breaker only watches the
    # routes listed here, by default the three search indexes.
    breaker_routes = set(breaker.get("routes", ["googlenews", "bingnews", "gdelt"]) or [])
    empty_streak: dict[str, int] = {}
    tripped: set[str] = set()
    # 11 Oct 2026: a second breaker for the other way of being refused, an HTTP 429/5xx. Google
    # News answered 503 for hours and the empty-breaker never fired, because a 503 is an error,
    # not an empty page. Three refusals in a row trip the route, start the cool-down and bring in
    # the Bing reserve. The threshold is shared with Http.fail_fast_after, so the third refusal
    # is also the last request the run sends to that host.
    quota = google_quota(cfg)
    gstate = google_state(cfg, store, now)
    http.fail_fast_after = max(1, quota["trip_after"])
    refused_streak: dict[str, int] = {}
    google_clean = False            # a Google answer this cycle: the route is open again
    fallback_submitted = False

    def _executor(route: str) -> ThreadPoolExecutor:
        lane = lane_of.get(route, "main")
        if lane not in executors:
            n = workers if lane == "main" else max(1, int(lane_cfg.get(lane, 1)))
            executors[lane] = ThreadPoolExecutor(max_workers=n, thread_name_prefix=f"nf-{lane}")
        return executors[lane]

    lane_cfg = cfg.engine.get("lanes", {}) or {}
    lane_of = {r: r for r in ("googlenews", "bingnews", "gdelt")}
    executors: dict[str, ThreadPoolExecutor] = {}
    futures: dict = {}
    pending: set = set()
    submit_order: dict = {}         # completion batches are handled in submission order, so the
                                    # empty-streak and refusal counters read like the queue, not a set

    def _submit(sp: JobSpec):
        fut = _executor(sp.route).submit(sp.fn)
        futures[fut] = sp
        submit_order[fut] = len(submit_order)
        pending.add(fut)
        return fut

    def _skip_all_google(reason: str) -> int:
        n = 0
        for sp in specs:
            if sp.route == "googlenews" and not sp.skip and not sp.fallback:
                sp.skip = reason
                n += 1
        return n

    def _submit_fallback(why: str) -> None:
        nonlocal fallback_submitted
        if fallback_submitted:
            return
        fallback_submitted = True
        reserve = [sp for sp in specs if sp.fallback and sp.route not in tripped]
        for sp in reserve:
            _submit(sp)
        if reserve:
            log.warning("googlenews is down (%s): %d reserve Bing queries submitted for the focus credits and tier-A home markets", why[:60], len(reserve))

    def _trip_google(why: str) -> None:
        """Google refused us: cancel its queued jobs, persist the cool-down, bring in the reserve."""
        if "googlenews" in tripped:
            return
        tripped.add("googlenews")
        until = google_refused(cfg, store, now, gstate, why)
        reason = f"skipped: googlenews refused upstream ({why[:40]}), cooling down until {until.strftime('%H:%M')}Z"
        for pf in list(pending):
            ps = futures[pf]
            if ps.route == "googlenews" and pf.cancel():
                pending.discard(pf)
                results.append(SourceResult(ps.route, ps.label, False, 0, reason, 0.0, list(ps.name_ids)))
                summary.tripped += 1
        _submit_fallback(why)

    # Cool-down probe. While Google is cooling down the cycle sends one query, the most valuable
    # one in the queue, before anything else. An answer reopens the route for the cycle; a
    # refusal extends the cool-down (doubling, capped) and the cycle runs on feeds, pages and the
    # Bing reserve instead. Google sees one request per cycle from a blocked runner, not a
    # hundred, which is also what makes the block lift.
    google_specs = [sp for sp in specs if sp.route == "googlenews" and not sp.skip and not sp.fallback]
    if gstate.get("cooling") and google_specs:
        probe = google_specs[0]
        log.warning("googlenews cooling down until %sZ (trip %d): probing with %s", gstate["cooldown_until"].strftime("%H:%M"), gstate.get("trips", 0), probe.label[:60])
        try:
            p_items, p_res = probe.fn()
        except Exception as exc:  # noqa: BLE001
            p_items, p_res = [], SourceResult(probe.route, probe.label, False, 0, str(exc)[:300], 0.0)
        probe.skip = "probe"            # never submitted again below
        handle(probe, p_items, p_res)
        if p_res.ok:
            google_clean = True
            store.clear_state(STATE_COOLDOWN)
            gstate.update(cooling=False, cooldown_until=None)
            log.warning("googlenews answered the probe: route open again, cap %s", gstate.get("cap") or "off")
        else:
            until = google_refused(cfg, store, now, gstate, p_res.error or "probe failed")
            n = _skip_all_google(f"skipped: googlenews cooling down until {until.strftime('%H:%M')}Z (refused upstream)")
            tripped.add("googlenews")
            log.warning("googlenews still refusing (%s): %d queries skipped this cycle", (p_res.error or "")[:60], n)

    # Submit. Skipped jobs (rotation, cool-down) are recorded, not run; reserve jobs wait.
    for sp in specs:
        if sp.skip == "probe":
            continue
        if sp.skip:
            results.append(SourceResult(sp.route, sp.label, False, 0, sp.skip, 0.0, list(sp.name_ids)))
            summary.deferred += 1
            continue
        if sp.fallback:
            continue
        _submit(sp)
    if "googlenews" in tripped:
        _submit_fallback("cooling down")

    stopped = False
    try:
        while pending:
            done, _ = wait_futures(pending, return_when=FIRST_COMPLETED)
            for fut in sorted(done, key=lambda f: submit_order.get(f, 0)):
                spec = futures[fut]
                pending.discard(fut)
                try:
                    items, res = fut.result()
                except BudgetExceeded:
                    # not a failure of the source: it was in flight when the budget ran out
                    items, res = [], SourceResult(spec.route, spec.label, False, 0, "skipped: run time budget reached", 0.0)
                    summary.skipped += 1
                except UpstreamRefused as exc:
                    items, res = [], SourceResult(spec.route, spec.label, False, 0, str(exc)[:300], 0.0)
                except Exception as exc:  # noqa: BLE001 - a job that raises becomes a failed source
                    items, res = [], SourceResult(spec.route, spec.label, False, 0, str(exc)[:300], 0.0)
                handle(spec, items, res)
                if spec.route in SEARCH_ROUTES and spec.route not in tripped:
                    if res.ok:
                        refused_streak[spec.route] = 0
                        if spec.route == "googlenews":
                            google_clean = True
                    elif is_refusal(res.error):
                        refused_streak[spec.route] = refused_streak.get(spec.route, 0) + 1
                        if refused_streak[spec.route] >= quota["trip_after"]:
                            if spec.route == "googlenews":
                                _trip_google(res.error)
                            else:
                                tripped.add(spec.route)
                                log.warning("route %s tripped after %d refusals in a row", spec.route, refused_streak[spec.route])
                if breaker_on and spec.route in breaker_routes and spec.route not in tripped:
                    if res.ok and res.items == 0:
                        empty_streak[spec.route] = empty_streak.get(spec.route, 0) + 1
                        if empty_streak[spec.route] >= trip_after_by_route.get(spec.route, trip_after):
                            tripped.add(spec.route)
                            log.warning(
                                "route %s tripped after %d consecutive empty responses: cancelling its "
                                "remaining jobs so the rest of the run keeps its budget",
                                spec.route, empty_streak[spec.route],
                            )
                    elif res.items:
                        empty_streak[spec.route] = 0
                if tripped:
                    for pf in list(pending):
                        ps = futures[pf]
                        if ps.route in tripped and pf.cancel():
                            pending.discard(pf)
                            results.append(SourceResult(
                                ps.route, ps.label, False, 0,
                                f"skipped: {ps.route} tripped after {trip_after_by_route.get(ps.route, trip_after)} consecutive empty responses"
                                if ps.route != "googlenews" or not gstate.get("cooling") else
                                f"skipped: googlenews refused upstream, cooling down until {gstate['cooldown_until'].strftime('%H:%M')}Z",
                                0.0, list(ps.name_ids),
                            ))
                            summary.tripped += 1
            if time.monotonic() > deadline and pending:
                stopped = True
                break
    finally:
        for ex in executors.values():
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
                except BudgetExceeded:
                    items, res = [], SourceResult(spec.route, spec.label, False, 0, "skipped: run time budget reached", 0.0)
                    summary.skipped += 1
                except Exception as exc:  # noqa: BLE001
                    items, res = [], SourceResult(spec.route, spec.label, False, 0, str(exc)[:300], 0.0)
                handle(spec, items, res)
        log.warning("time budget of %.0f min reached: %d jobs skipped (they run on the next cycle)", budget, summary.skipped)
    # A cycle on which Google answered and never refused: the cool-down (if any) is over and the
    # cap takes a step back towards its ceiling.
    if google_clean and "googlenews" not in tripped and (gstate.get("trips") or gstate.get("cooldown_until") or (quota["cap"] and gstate.get("cap", 0) < quota["ceiling"])):
        google_answered(cfg, store, now, gstate)

    summary.source_results = results
    summary.tripped_routes = sorted(tripped)
    summary.errors = sum(1 for r in results if not r.ok and not r.error.startswith("skipped"))
    store.add_source_results(run_id, results)
    summary.finished_at = datetime.now(timezone.utc)
    notes = f"errors={summary.errors} skipped={summary.skipped} deferred={summary.deferred} stale={summary.stale} unrelated={summary.unrelated}"
    if summary.tripped:
        notes += f" tripped={summary.tripped} routes={','.join(summary.tripped_routes)}"
    store.finish_run(run_id, summary.finished_at, summary.fetched, summary.new_items, notes=notes)

    # Keep the database small enough to survive in the Actions cache. source_results is the
    # per-job coverage ledger: about 40,000 rows a day against 2,000 items, and by far the largest
    # table. Health reads the last few runs of it and coverage reads 24 hours, so months of it are
    # dead weight. The `runs` and `items` tables are never pruned; the run count has to stay
    # monotonic for the rollback guard, and the item history is the archive.
    keep_days = int(cfg.engine.get("keep_source_results_days", 10))
    if keep_days > 0:
        dropped = store.prune_source_results(keep_days, now, keep_run_id=run_id)
        if dropped:
            log.info("pruned %d source_results rows older than %d days", dropped, keep_days)
        if store.vacuum_if_bloated():
            log.info("vacuumed the database to reclaim the freed pages")

    if own_http:
        http.close()
    log.info("run %s done: fetched=%d new=%d candidates=%d screened=%d alerts=%d errors=%d skipped=%d tripped=%d%s", run_id, summary.fetched, summary.new_items, summary.candidates, summary.screened, summary.alerts, summary.errors, summary.skipped, summary.tripped, f" ({','.join(summary.tripped_routes)})" if summary.tripped_routes else "")
    return summary
