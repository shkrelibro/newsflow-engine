"""Exports: latest.json (the candidates pile), daily files, alerts, health, index.html."""
from __future__ import annotations

import re

import html
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import Config
from .normalize import domain_of, is_google_news_link
from .match import Matcher
from .store import Store

ROUTE_PRIORITY = {"rss": 0, "page": 1, "googlenews": 2, "bingnews": 3, "gdelt": 4}


def _item_public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "url": row["canonical_url"],
        "raw_link": row["raw_link"],
        "source": row["source_name"] or row["source_domain"],
        "domain": row["source_domain"],
        "country": row["country"],
        "lang": row["lang"],
        "published_at": row["published_at"],
        "first_seen_at": row["first_seen_at"],
        "route": row["route"],
        "query": row["query"],
        "tier_hint": row["tier_hint"],
        "summary": row["summary"],
        "alias": row.get("alias"),
        "alias_where": row.get("where_"),
        "confidence": row.get("confidence"),
        "also_routes": json.loads(row.get("also_routes") or "[]"),
        "alert_categories": json.loads(row.get("alert_categories") or "[]"),
        "alert_candidate": bool(row.get("alert_candidate")),
        "status": row["status"],
        "screen_reason": row["screen_reason"],
        "url_unresolved": is_google_news_link(row["canonical_url"]),
    }


def _primary_sort_key(it: dict[str, Any]):
    tier = it["tier_hint"] if it["tier_hint"] is not None else 9
    unresolved = 1 if it["url_unresolved"] else 0
    pub = it["published_at"] or "9999"
    return (tier, unresolved, ROUTE_PRIORITY.get(it["route"], 9), pub)


def build_export(cfg: Config, store: Store, now: datetime, window_hours: float) -> dict[str, Any]:
    """Assemble the candidates pile, re-testing every stored match against the CURRENT config.

    A match is made once, when the item is collected, and then stored. That means a config fix
    does not reach the published pile until the bad items age out of the window, which is up to
    24 hours. On 13 September the context guards for Quick, Carnival and the rest merged at
    lunchtime and the 15:08 brief still carried American college football, a village carnival
    procession filed as M&A, and an Adidas dirndl review, because those rows were matched in the
    morning and the export simply re-served them.

    So the guard is applied again here, at export. Collection stays permissive on purpose, the
    store keeps everything, and a rule change takes effect on the next run instead of the next
    day. Rows that no longer pass are counted and reported as revalidated rather than deleted.
    """
    since = now - timedelta(hours=window_hours)
    names_out = []
    all_alerts = []
    matcher = Matcher.from_config(cfg)
    revalidated = 0
    for n in cfg.names:
        rows = store.candidates(n.id, since)
        clusters: dict[int, list[dict[str, Any]]] = defaultdict(list)
        screened: list[dict[str, Any]] = []
        for r in rows:
            pub = _item_public(r)
            # A row attributed by its source rather than by its text ("(page)" from a watcher
            # with require_alias false, "(feed)" from a name's own RSS) has nothing to re-test:
            # "Pricing of EUR 400m senior secured notes" on the company's own newsroom names no
            # alias and never will. Re-testing it here screened exactly the rows the primary
            # layer exists to catch.
            attributed = pub.get("alias") in ("(page)", "(feed)")
            if r["status"] != "screened" and not attributed and not matcher.match(
                    r["title"] or "", r["summary"] or "", r["lang"] or "", only=[n.id]):
                revalidated += 1
                pub["screen_reason"] = "revalidated: no longer matches the current config"
                screened.append(pub)
                continue
            if r["status"] == "screened":
                screened.append(pub)
            else:
                clusters[int(r["cluster_id"] or r["id"])].append(pub)
        cluster_list = []
        for cid, items in clusters.items():
            items.sort(key=_primary_sort_key)
            primary, also = items[0], items[1:11]   # cap the also-reported-by list
            cats = sorted({c for it in items for c in it["alert_categories"]})
            alert = any(it["alert_candidate"] for it in items)
            entry = {
                "cluster_id": cid,
                "primary": primary,
                "also": also,
                "sources": len({it["domain"] for it in items if it["domain"]}),
                "alert_candidate": alert,
                "alert_categories": cats,
                "max_confidence": max((it["confidence"] or 0) for it in items),
                "aliases": sorted({it["alias"] for it in items if it["alias"]}),
                "languages": sorted({it["lang"] for it in items if it["lang"]}),
                "countries": sorted({it["country"] for it in items if it["country"]}),
                "latest": max((it["published_at"] or it["first_seen_at"]) for it in items),
            }
            cluster_list.append(entry)
            if alert:
                all_alerts.append({"name_id": n.id, "name": n.name, **entry})
        cluster_list.sort(key=lambda c: (not c["alert_candidate"], -(c["max_confidence"] or 0), c["latest"]), reverse=False)
        cluster_list.sort(key=lambda c: c["latest"], reverse=True)
        cluster_list.sort(key=lambda c: c["alert_candidate"], reverse=True)
        reasons = Counter(s["screen_reason"].split(":")[0] for s in screened)
        names_out.append(
            {
                "id": n.id,
                "name": n.name,
                "kind": n.kind,
                "markets": [f"{m.country}:{m.lang}" for m in n.markets],
                "candidates": cluster_list,
                "candidate_count": len(cluster_list),
                "kept_from_screen": sum(1 for c in cluster_list if str(c["primary"].get("screen_reason") or "").startswith("kept:")),
                "watch": n.watch,
                "focus": n.focus,          # "name", "comp" (of a focus credit) or "" (config/focus.yaml)
                "comps": list(n.comps),
                "mentions_in_window": len(rows),
                "unresolved_urls": sum(1 for c in cluster_list if c["primary"]["url_unresolved"]),
                "screened": {"count": len(screened), "by_reason": dict(reasons), "items": screened[:25]},
                # v2: churn echoes get their own uncapped-ish list, newest first, so a busy name
                # cannot push the only trace of an event out of the 25 screened items above
                "churn": sorted((x for x in screened if str(x.get("screen_reason") or "").startswith("churn")),
                                key=lambda x: x.get("first_seen_at") or "", reverse=True)[:40],
            }
        )
    health = store.source_health(int(cfg.export.get("health_runs", 4)))
    failing = [h for h in health if h["ok_runs"] == 0]
    # Job-level starvation over a full day. source_health looks at the last few runs, where a
    # budget skip is routine; a source with no completed run in 24 hours is a hole, whatever the
    # name-level coverage says, and it belongs on the page and in the watchdog's view.
    starved = store.sources_starved(now - timedelta(hours=24))
    # Watchers and feeds that answer but never yield: a week of 200s with zero items is a dead
    # link pattern or a script-rendered page, not a quiet issuer. Mutares' two watchers ran 92
    # times over the first weekend of October without one item while the issuer put out three
    # releases; the OK count alone cannot show that.
    zero_yield = store.watchers_zero_yield(now - timedelta(days=7))
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "revalidated_out": revalidated,
        "window_hours": window_hours,
        "names": names_out,
        "alerts": all_alerts,
        "source_health": {"sources": len(health), "failing": len(failing), "starved_24h": len(starved), "starved_24h_detail": starved, "zero_yield_7d": len(zero_yield), "zero_yield_7d_detail": zero_yield, "detail": health},
        "stats": store.stats(),
        "engine": "newsflow-engine",
    }


def build_coverage(cfg: Config, store: Store, now: datetime) -> dict[str, Any]:
    """Per-name proof of sweep: quiet must be provably different from broken.

    States:
      ok       - queries ran in the last 24h and the name had a mention in the last 7 days
      quiet    - queries ran, no mention in 7 days (days_silent says how long)
      STARVED  - queries were built but every one failed or was skipped in the last 24h
      NO_QUERIES - no job swept this name in the last 24h (misconfiguration or engine gap)
    """
    since24 = now - timedelta(hours=24)
    per: dict[str, dict[str, int]] = {n.id: {"jobs": 0, "ok": 0, "failed": 0, "skipped": 0} for n in cfg.names}
    for row in store.coverage_jobs(since24):
        ok, err = bool(row["ok"]), row["error"] or ""
        for nid in row["names"].split(","):
            c = per.get(nid)
            if c is None:
                continue
            c["jobs"] += 1
            if ok:
                c["ok"] += 1
            elif err.startswith("skipped"):
                c["skipped"] += 1
            else:
                c["failed"] += 1
    mentions = store.name_mentions(now - timedelta(days=7))
    # v2: key outlets (overlay.yaml). Per outlet: did a channel to it answer in 24 hours (the name's
    # site: query, or the outlet's own feed), and when did it last carry the name (30 days).
    site_ok: dict[tuple[str, str], list[int]] = {}
    for row in store.coverage_jobs(since24):
        src = row["source"] or ""
        m_site = re.search(r"site:([^\s\]]+)", src)
        if not m_site:
            continue
        dom = m_site.group(1).lower()
        for nid in row["names"].split(","):
            c2 = site_ok.setdefault((nid, dom), [0, 0])
            c2[1] += 1
            c2[0] += 1 if row["ok"] else 0
    outcomes = store.source_outcomes(since24)
    feed_label = {}
    for o in cfg.outlets:
        d = domain_of(o.homepage)
        feed_label.setdefault(d, f"{o.name} ({o.country})")
    last_seen = store.name_domain_last_seen(now - timedelta(days=30))

    def _outlets(n) -> list[dict[str, Any]]:
        out = []
        for dom in n.key_outlets:
            ok_s, n_s = site_ok.get((n.id, dom), [0, 0])
            ok_f, n_f = outcomes.get(feed_label.get(dom, ""), (0, 0))
            hits = [v for (nid, d), v in last_seen.items() if nid == n.id and (d == dom or d.endswith("." + dom))]
            out.append({"domain": dom, "site_ok": ok_s, "site_jobs": n_s, "feed_ok": ok_f, "feed_jobs": n_f,
                        "answering": bool(ok_s or ok_f), "last_hit": max(hits) if hits else None})
        return out

    names_out = {}
    flags = {"starved": [], "no_queries": [], "silent_over_14d": []}
    for n in cfg.names:
        c = per[n.id]
        m = mentions.get(n.id, {"total": 0, "recent": 0, "latest": None})
        latest = m["latest"]
        days_silent = None
        if latest:
            try:
                days_silent = round((now - datetime.fromisoformat(latest)).total_seconds() / 86400, 1)
            except ValueError:
                days_silent = None
        if c["jobs"] == 0:
            state = "NO_QUERIES"
            flags["no_queries"].append(n.id)
        elif c["ok"] == 0:
            state = "STARVED"
            flags["starved"].append(n.id)
        elif m["recent"] and int(m["recent"]) > 0:
            state = "ok"
        else:
            state = "quiet"
            if days_silent is None or days_silent > 14:
                flags["silent_over_14d"].append(n.id)
        names_out[n.id] = {
            "name": n.name, "kind": n.kind, "state": state,
            "jobs_24h": c["jobs"], "ok_24h": c["ok"], "failed_24h": c["failed"], "skipped_24h": c["skipped"],
            "mentions_7d": int(m["recent"] or 0), "mentions_total": int(m["total"] or 0),
            "last_mention": latest, "days_silent": days_silent,
            "outlets": _outlets(n),
        }
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "window_hours": 24,
        "names": names_out,
        "flags": flags,
        "summary": {
            "total": len(names_out),
            "ok": sum(1 for v in names_out.values() if v["state"] == "ok"),
            "quiet": sum(1 for v in names_out.values() if v["state"] == "quiet"),
            "starved": len(flags["starved"]),
            "no_queries": len(flags["no_queries"]),
        },
    }


_COMPACT_KEYS = ("id", "title", "url", "source", "domain", "country", "lang", "published_at",
                 "first_seen_at", "route", "tier_hint", "alias_where", "screen_reason")


def compact_window(data: dict[str, Any]) -> dict[str, Any]:
    """The 72-hour pile in the shape scripts/build_brief.py reads, minus everything heavy."""
    def slim(it: dict[str, Any]) -> dict[str, Any]:
        return {k: it.get(k) for k in _COMPACT_KEYS}
    names = []
    for n in data["names"]:
        names.append({
            "id": n["id"], "name": n["name"], "kind": n["kind"],
            "candidates": [{"cluster_id": c["cluster_id"], "primary": slim(c["primary"]),
                            "sources": c["sources"], "alert_categories": c["alert_categories"]}
                           for c in n["candidates"]],
            "churn": [slim(x) for x in n.get("churn", [])],
        })
    return {"generated_at": data["generated_at"], "window_hours": data["window_hours"], "names": names}


def write_exports(cfg: Config, store: Store, now: datetime | None = None) -> Path:
    now = now or datetime.now(timezone.utc)
    out = cfg.out_dir
    (out / "daily").mkdir(parents=True, exist_ok=True)
    window = float(cfg.export.get("window_hours", 24))
    data = build_export(cfg, store, now, window)
    (out / "latest.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "daily" / f"{now.date().isoformat()}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "alerts.json").write_text(json.dumps({"generated_at": data["generated_at"], "alerts": data["alerts"]}, ensure_ascii=False, indent=1), encoding="utf-8")
    coverage = build_coverage(cfg, store, now)
    # The golden set: did the machine catch what is known to have happened. Exported so the brief
    # can print it and so a miss survives as a fact rather than an absence.
    try:
        from .golden import check as golden_check, load_golden, summarise as golden_summarise
        events = load_golden(cfg.root / "golden.yaml")
        golden = golden_summarise(golden_check(store, events, now)) if events else {"events": 0, "results": []}
    except Exception as exc:  # noqa: BLE001 - never let the accountability check block the exports
        golden = {"events": 0, "results": [], "error": str(exc)[:200]}
    golden["generated_at"] = data["generated_at"]
    (out / "golden.json").write_text(json.dumps(golden, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "coverage.json").write_text(json.dumps(coverage, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "health.json").write_text(json.dumps({"generated_at": data["generated_at"], **data["source_health"], "stats": data["stats"], "coverage": {"summary": coverage["summary"], "flags": coverage["flags"]}}, ensure_ascii=False, indent=1), encoding="utf-8")
    if cfg.export.get("window72", False):
        # v2: a compact 72-hour pile for the Monday cut and for any cut that needs more than a day.
        # Primary fields only (no summaries, no also-list), so it stays a fraction of latest.json.
        try:
            wide = build_export(cfg, store, now, 72.0)
            (out / "window72.json").write_text(json.dumps(compact_window(wide), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - never let the wide window block the main exports
            (out / "window72.json").write_text(json.dumps({"generated_at": data["generated_at"], "error": str(exc)[:200]}), encoding="utf-8")
    (out / "index.html").write_text(render_index(data), encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    keep_days = int(cfg.export.get("keep_days", 90))
    cutoff = (now - timedelta(days=keep_days)).date().isoformat()
    for f in (out / "daily").glob("*.json"):
        if f.stem < cutoff:
            f.unlink()
    return out


def render_index(data: dict[str, Any]) -> str:
    e = html.escape
    parts = [
        "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<title>Newsflow engine — candidates</title>",
        "<style>body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;max-width:960px;margin:32px auto;padding:0 16px;color:#1a2028;background:#f6f7f5}"
        "h1{font-size:22px;margin:0 0 4px}h2{font-size:18px;margin:28px 0 8px}.m{color:#5b6672;font-size:13px}"
        ".c{background:#fff;border:1px solid #d7dce1;border-radius:8px;padding:10px 14px;margin:8px 0}"
        ".t{font-weight:600}.a{display:inline-block;background:#f6e2e0;color:#a8352e;font-size:11px;padding:1px 7px;border-radius:999px;margin-left:6px}"
        ".s{color:#5b6672;font-size:13px}table{border-collapse:collapse;font-size:13px;width:100%}td,th{text-align:left;padding:4px 8px;border-bottom:1px solid #e4e8ec}"
        ".bad{color:#a8352e}.ok{color:#286e51}a{color:#235e8e}</style></head><body>",
        f"<h1>Newsflow engine — candidates pile</h1><div class='m'>generated {e(data['generated_at'])} · window {data['window_hours']}h · "
        f"{data['source_health']['sources']} sources, {data['source_health']['failing']} failing · {data['stats']['items']} items stored</div>",
    ]
    if data["alerts"]:
        parts.append("<h2>Alert candidates</h2>")
        for al in data["alerts"]:
            p = al["primary"]
            parts.append(f"<div class='c'><span class='t'>{e(al['name'])}: <a href='{e(p['url'])}'>{e(p['title'])}</a></span><span class='a'>{e(', '.join(al['alert_categories']))}</span><div class='s'>{e(p['source'])} · {e(p['country'])} · {e(p['lang'])} · {e(p['published_at'] or p['first_seen_at'])}</div></div>")
    for n in data["names"]:
        parts.append(f"<h2>{e(n['name'])} <span class='m'>{n['candidate_count']} candidates · {n['mentions_in_window']} mentions · {n['screened']['count']} screened</span></h2>")
        if not n["candidates"]:
            parts.append("<div class='c s'>No candidates in the window.</div>")
        for c in n["candidates"][:30]:
            p = c["primary"]
            flag = f"<span class='a'>{e(', '.join(c['alert_categories']))}</span>" if c["alert_candidate"] else ""
            also = f" · also reported by {len(c['also'])} more" if c["also"] else ""
            unres = " · url unresolved (Google link)" if p["url_unresolved"] else ""
            parts.append(
                f"<div class='c'><div class='t'><a href='{e(p['url'])}'>{e(p['title'])}</a>{flag}</div>"
                f"<div class='s'>{e(p['source'])} · {e(p['country'])} · {e(p['lang'])} · {e(p['published_at'] or p['first_seen_at'])} · via {e(p['route'])}{also}{unres}</div>"
                + (f"<div class='s'>{e(p['summary'][:300])}</div>" if p["summary"] else "")
                + "</div>"
            )
        if n["screened"]["count"]:
            parts.append(f"<div class='s'>Screened: {e(json.dumps(n['screened']['by_reason']))}</div>")
    parts.append("<h2>Source health (last runs)</h2><table><tr><th>route</th><th>source</th><th>ok/runs</th><th>items</th><th>last error</th></tr>")
    for h in data["source_health"]["detail"]:
        cls = "ok" if h["ok_runs"] == h["runs"] else ("bad" if h["ok_runs"] == 0 else "")
        parts.append(f"<tr><td>{e(h['route'])}</td><td>{e(h['source'])}</td><td class='{cls}'>{h['ok_runs']}/{h['runs']}</td><td>{h['items']}</td><td class='s'>{e(h['last_error'] or '')}</td></tr>")
    parts.append("</table></body></html>")
    return "".join(parts)
