#!/usr/bin/env python3
"""Fetch every page in the primary-source layer once and report what the watcher would see.

    python scripts/check_primary.py                    # config/sources/primary.yaml (the loaded layer)
    python scripts/check_primary.py --candidates       # config/sources/primary_candidates.yaml (not loaded)
    python scripts/check_primary.py --only tuigroup,sbb --json report.json

For each page: HTTP outcome, links on the page, links the link_pattern accepts, and the first two
accepted links, so a wrong pattern or a JS shell is visible before the engine ever runs it. Feeds
are parsed with the engine's feed parser and report their entry count. Nothing is written to the
database; this is read-only and uses the engine's own HTTP layer (identified user agent, per-host
spacing, robots.txt honoured for pages), so a page this script cannot see is a page the engine
cannot see either.

Exit code 1 when --strict is given and any loaded page returned an error or zero accepted links.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from newsflow.config import load_config  # noqa: E402
from newsflow.http import FetchError  # noqa: E402
from newsflow.pipeline import make_http  # noqa: E402
from newsflow.routes import extract_links, looks_like_article, parse_feed_text  # noqa: E402


def _entries(args) -> list[dict]:
    cfg = load_config(ROOT / "config")
    out: list[dict] = []
    if args.candidates:
        raw = yaml.safe_load((ROOT / "config" / "sources" / "primary_candidates.yaml").read_text(encoding="utf-8")) or {}
        for nid, items in (raw.get("names") or {}).items():
            for e in items or []:
                out.append({"id": nid, "kind": "page", "url": e["url"], "name": f"{nid} {e.get('kind', '')}",
                            "pattern": e.get("link_pattern", ""), "reason": e.get("reason", "")})
    else:
        raw = yaml.safe_load((ROOT / "config" / "sources" / "primary.yaml").read_text(encoding="utf-8")) or {}
        wanted = set((raw.get("names") or {}).keys())
        for n in cfg.names:
            if n.id not in wanted:
                continue
            for p in n.pages:
                out.append({"id": n.id, "kind": "page", "url": p.url, "name": p.name, "pattern": p.link_pattern, "reason": ""})
            for f in n.feeds:
                out.append({"id": n.id, "kind": "feed", "url": f.url, "name": f.name, "pattern": "", "reason": ""})
    if args.only:
        keep = {x.strip() for x in args.only.split(",") if x.strip()}
        out = [e for e in out if e["id"] in keep]
    return out, cfg


def _check(http, e: dict) -> dict:
    res = dict(e, status="", links=0, accepted=0, sample=[])
    try:
        if e["kind"] == "feed":
            text = http.get_text(e["url"], accept="application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8")
            feed = parse_feed_text(text)
            res["status"] = "ok" if feed.entries else "ok, but no entries"
            res["links"] = res["accepted"] = len(feed.entries)
            res["sample"] = [getattr(x, "link", "") for x in feed.entries[:2]]
            return res
        html = http.get_text(e["url"], is_page=True)
        links = extract_links(html, e["url"])
        res["links"] = len(links)
        acc = [u for u, t in links if looks_like_article(u, t, e["url"], e["pattern"])]
        res["accepted"] = len(acc)
        res["sample"] = acc[:2]
        if not links:
            res["status"] = "ok, but the page has no links at all (JS shell?)"
        elif not acc:
            res["status"] = "ok, but no link matches the pattern"
        else:
            res["status"] = "ok"
    except FetchError as exc:
        res["status"] = f"ERROR {str(exc)[:160]}"
    except Exception as exc:  # noqa: BLE001
        res["status"] = f"ERROR {type(exc).__name__}: {str(exc)[:140]}"
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--candidates", action="store_true", help="check primary_candidates.yaml instead of the loaded layer")
    ap.add_argument("--only", default="", help="comma-separated name ids")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--json", default="", help="write the full report here")
    ap.add_argument("--strict", action="store_true", help="exit 1 if any loaded page errors or accepts no link")
    args = ap.parse_args()

    entries, cfg = _entries(args)
    http = make_http(cfg)
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = {ex.submit(_check, http, e): e for e in entries}
        for fut in as_completed(futs):
            results.append(fut.result())
    http.close()
    results.sort(key=lambda r: (r["id"], r["kind"], r["url"]))

    ok = sum(1 for r in results if r["status"] == "ok")
    empty = [r for r in results if r["status"].startswith("ok, but")]
    errors = [r for r in results if r["status"].startswith("ERROR")]
    print(f"{len(results)} sources checked: {ok} ok, {len(empty)} fetched but empty, {len(errors)} errors\n")
    print(f"{'name id':22} {'kind':5} {'links':>6} {'match':>6}  status / first accepted link")
    for r in results:
        first = r["sample"][0] if r["sample"] else ""
        print(f"{r['id']:22} {r['kind']:5} {r['links']:>6} {r['accepted']:>6}  {r['status']}" + (f"  -> {first[:110]}" if first and r["status"] == "ok" else ""))
    if empty:
        print("\nFETCHED BUT EMPTY (JS shell, wrong pattern or a page with nothing to list):")
        for r in empty:
            print(f"  {r['id']:22} {r['url']}  [{r['status']}]" + (f"  reason on file: {r['reason']}" if r.get("reason") else ""))
    if errors:
        print("\nERRORS (robots, HTTP status, timeout):")
        for r in errors:
            print(f"  {r['id']:22} {r['url']}  {r['status']}")
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"\nfull report: {args.json}")
    if args.strict and not args.candidates and (errors or empty):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
