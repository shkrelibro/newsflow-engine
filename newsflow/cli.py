"""Command line: run | loop | backfill | export | discover-feeds | stats | check-config | jobs."""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone

from . import __version__
from .alerts import push_alerts
from .config import load_config
from .export import write_exports
from .pipeline import build_jobs, make_http, run_once
from .routes import discover_feed
from .store import Store


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )


def cmd_run(args) -> int:
    cfg = load_config(args.config)
    store = Store(cfg.db_path)
    try:
        summary = run_once(cfg, store, backfill_days=args.backfill_days)
        write_exports(cfg, store)
        sent = push_alerts(cfg, store)
        print(json.dumps({
            "run_id": summary.run_id, "jobs": summary.jobs, "fetched": summary.fetched, "new": summary.new_items,
            "candidates": summary.candidates, "screened": summary.screened, "stale": summary.stale,
            "unrelated": summary.unrelated, "alert_candidates": summary.alerts, "source_errors": summary.errors,
            "alerts_sent": sent, "out_dir": str(cfg.out_dir),
        }, indent=1))
        return 0
    finally:
        store.close()


def cmd_loop(args) -> int:
    every = max(1, int(args.every)) * 60
    while True:
        started = time.monotonic()
        try:
            cmd_run(args)
        except Exception as exc:  # noqa: BLE001 - keep looping
            logging.getLogger("newsflow").exception("run failed: %s", exc)
        sleep_for = max(30.0, every - (time.monotonic() - started))
        time.sleep(sleep_for)


def cmd_export(args) -> int:
    cfg = load_config(args.config)
    store = Store(cfg.db_path)
    try:
        out = write_exports(cfg, store)
        print(f"exported to {out}")
        return 0
    finally:
        store.close()


def cmd_discover(args) -> int:
    cfg = load_config(args.config)
    store = Store(cfg.db_path)
    http = make_http(cfg)
    now = datetime.now(timezone.utc)
    found, missing = 0, []
    try:
        for o in cfg.outlets:
            if o.feed_url:
                print(f"{o.country} {o.name}: configured {o.feed_url}")
                found += 1
                continue
            url = discover_feed(http, o.homepage)
            store.set_feed(o.homepage, url, now, "" if url else "no feed found")
            if url:
                found += 1
                print(f"{o.country} {o.name}: {url}")
            else:
                missing.append(f"{o.country} {o.name}")
                print(f"{o.country} {o.name}: NOT FOUND (site-restricted Google News queries still cover it)")
        print(f"\n{found} feeds known, {len(missing)} outlets without a feed")
        return 0
    finally:
        http.close()
        store.close()


def cmd_stats(args) -> int:
    cfg = load_config(args.config)
    store = Store(cfg.db_path)
    try:
        print(json.dumps(store.stats(), indent=1))
        for h in store.source_health(int(args.runs)):
            flag = "OK " if h["ok_runs"] == h["runs"] else ("BAD" if h["ok_runs"] == 0 else "~  ")
            print(f"{flag} {h['route']:10} {h['ok_runs']}/{h['runs']} items={h['items']:4} {h['source'][:70]} {h['last_error'][:60] if h['last_error'] else ''}")
        return 0
    finally:
        store.close()


def cmd_check(args) -> int:
    cfg = load_config(args.config)
    print(f"config: {cfg.root}")
    print(f"db: {cfg.db_path}\nout: {cfg.out_dir}")
    for n in cfg.names:
        searched = [a.text for a in n.aliases if a.search]
        print(f"- {n.id}: {n.name} [{n.kind}] markets={len(n.markets)} langs={','.join(n.langs)} aliases={len(n.aliases)} searched={searched} pages={len(n.pages)} feeds={len(n.feeds)} site_queries={len(n.site_queries)}")
    print(f"outlets: {len(cfg.outlets)} · tier1 categories: {list(cfg.tier1_terms)} · noise domains: {len(cfg.noise_domains)}")
    return 0


def cmd_jobs(args) -> int:
    cfg = load_config(args.config)
    store = Store(cfg.db_path)
    http = make_http(cfg)
    try:
        jobs = build_jobs(cfg, http, store, run_number=int(args.run_number))
        print(f"{len(jobs)} jobs would run on run #{args.run_number}")
        return 0
    finally:
        http.close()
        store.close()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="newsflow", description=f"newsflow-engine {__version__}")
    p.add_argument("--config", default="config", help="config directory (default: config)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="one fetch run, then export and alerts")
    r.add_argument("--backfill-days", type=int, default=None, help="widen the window to N days (golden set / first run)")
    r.set_defaults(fn=cmd_run)

    lp = sub.add_parser("loop", help="run forever every N minutes (server mode)")
    lp.add_argument("--every", type=int, default=15)
    lp.add_argument("--backfill-days", type=int, default=None)
    lp.set_defaults(fn=cmd_loop)

    bf = sub.add_parser("backfill", help="alias for run --backfill-days N")
    bf.add_argument("days", type=int)
    bf.set_defaults(fn=lambda a: cmd_run(argparse.Namespace(config=a.config, verbose=a.verbose, backfill_days=a.days)))

    ex = sub.add_parser("export", help="rebuild the export files from the database")
    ex.set_defaults(fn=cmd_export)

    d = sub.add_parser("discover-feeds", help="find RSS/Atom feeds for the outlets in sources/outlets.yaml")
    d.set_defaults(fn=cmd_discover)

    st = sub.add_parser("stats", help="database stats and source health")
    st.add_argument("--runs", type=int, default=4)
    st.set_defaults(fn=cmd_stats)

    ck = sub.add_parser("check-config", help="validate and summarise the configuration")
    ck.set_defaults(fn=cmd_check)

    jb = sub.add_parser("jobs", help="count the jobs a run would execute")
    jb.add_argument("--run-number", type=int, default=1)
    jb.set_defaults(fn=cmd_jobs)

    args = p.parse_args(argv)
    _setup_logging(args.verbose)
    return int(args.fn(args) or 0)
