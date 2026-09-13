#!/usr/bin/env python3
"""Render the coverage brief from the engine's exports. Deterministic, no model calls.

The brief used to be rebuilt from a prose instruction on every cut, which meant eight chances a
day for the layout to drift and for the reconciliation arithmetic to stop adding up. Everything
here that can be computed is computed: the editorial filter, every count, the drop table and the
reconciliation. What is left for judgement is the ranking of the shortlist and the "so what" line
under each item, and those arrive through --judgement as JSON.

If no judgement file is supplied the page still renders, complete and correct, with the headline
and the source standing in for the commentary. A thin brief is an acceptable failure; a missing
one, or one whose numbers do not add up, is not.

    python scripts/build_brief.py --docs docs --out brief.html
    python scripts/build_brief.py --docs docs --out brief.html --judgement j.json
    python scripts/build_brief.py --shortlist            # emit the shortlist as JSON and stop

Judgement JSON:
    {"lead": [<cluster_id>, ...],          # order of the lead section; the rest fall to Carried
     "so": {"<cluster_id>": "one paragraph"},
     "translate": {"<cluster_id>": "English rendering of a non-English headline"},
     "tags": {"<cluster_id>": ["low-grade source"]}}
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Re-daters, aggregators and quote-page mills. The authoritative list is config/noise.yaml in the
# repository; this is only the fallback used when that file was not supplied.
#
# It used to be a hardcoded copy with a comment asking whoever changed one to change the other,
# which lasted exactly one day: 54 of the 65 configured domains never reached this filter, among
# them boerse-express.com, which sat one letter from the boerse-express.de that was listed, and
# the kauppalehti.fi registry paths behind 81 Intrum stubs in a single window. A list that has to
# be kept in step by hand is a list that will drift, so read the real one.
FALLBACK_JUNK = {
    "ad-hoc-news.de", "boerse-global.de", "finanztrends.de",
    "boerse-express.de", "boerse-express.com",
    "news.inbox.eu", "tipranks.com", "marketbeat.com", "themarketsdaily.com",
    "tickerreport.com", "dailypolitical.com", "fuelcarmagazine.com",
}
JUNK_DOMAINS: set[str] = set(FALLBACK_JUNK)


def load_junk(path: "Path | None") -> set[str]:
    """Read the domain screen out of config/noise.yaml, falling back to FALLBACK_JUNK.

    Parsed by hand rather than with PyYAML: this script is fetched standalone into a scheduled
    session and must not depend on a package being installed there. The file's shape is a fixed
    "domains:" block of "  - value" lines, so a five-line reader is enough and cannot fail in a
    way that silently empties the screen.
    """
    if path is None or not path.exists():
        return set(FALLBACK_JUNK)
    domains: set[str] = set()
    in_block = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line:
            continue
        if not line.startswith((" ", "\t", "-")):
            in_block = line.strip() == "domains:"
            continue
        if in_block and line.lstrip().startswith("- "):
            value = line.lstrip()[2:].strip().strip("\"'")
            if value:
                domains.add(value)
    # Never let a parse failure quietly widen what gets published.
    return domains | set(FALLBACK_JUNK) if domains else set(FALLBACK_JUNK)

# What moves a bond price, in order. Anything uncategorised sorts after these.
CATEGORY_ORDER = ["rating", "capital_markets", "restructuring", "m_and_a",
                  "regulatory", "litigation", "management"]
CATEGORY_LABEL = {"rating": "Rating", "capital_markets": "Capital markets",
                  "restructuring": "Restructuring", "m_and_a": "M&A",
                  "regulatory": "Regulatory", "litigation": "Litigation",
                  "management": "Management"}



def resolve_and_date(url: str, timeout: float = 6.0) -> tuple[str, int | None]:
    """Follow the aggregator redirect and read the year out of the outlet's own path.

    91% of links arrive as Google News tokens, which carry no date, so the engine cannot check a
    claimed publication date against anything. The outlet's URL usually can: publishers put the
    date in the path when the article is created and it does not move when the page is re-dated.
    On 13 September a 2009 C&A story arrived stamped that morning and nothing in the pipeline
    could contradict it.

    Only the handful of rows the brief intends to publish are resolved, so this is twenty or so
    requests per cut rather than seven hundred per run, which is why it belongs here and not in
    the engine. Best effort: a failure returns the original URL and no year, and an unverifiable
    date is reported as unverifiable rather than assumed good.
    """
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "newsflow-brief/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            final = resp.geturl() or url
    except Exception:                                    # noqa: BLE001 - never fail a cut over this
        return url, None
    return final, url_year(final)


_URL_YEAR = re.compile(r"(?:^|[/\-_.])((?:19|20)\d{2})(?:[/\-_.]|$)")


def url_year(url: str) -> int | None:
    from urllib.parse import urlsplit
    try:
        path = urlsplit(url).path
    except ValueError:
        return None
    years = [int(m.group(1)) for m in _URL_YEAR.finditer(path) if 1990 <= int(m.group(1)) <= 2100]
    return min(years) if years else None

def junky(domain: str) -> bool:
    return any(j in (domain or "") for j in JUNK_DOMAINS)


def rank(row: dict) -> tuple:
    """Lower sorts first: categorised before not, by category priority, then by corroboration."""
    cats = row["cats"]
    pri = min((CATEGORY_ORDER.index(c) for c in cats if c in CATEGORY_ORDER), default=99)
    return (0 if cats else 1, pri, -row["sources"], row["name"])


def collect(latest: dict) -> list[dict]:
    rows = []
    for nm in latest.get("names", []):
        for c in nm.get("candidates", []) or []:
            p = c["primary"]
            rows.append({
                "id": c["cluster_id"], "nid": nm["id"], "name": nm["name"],
                "comp": nm["kind"] == "comp",
                "title": p["title"], "source": p["source"], "domain": p["domain"],
                "country": p["country"], "lang": p["lang"], "url": p["url"],
                "seen": p["first_seen_at"], "published": p.get("published_at"),
                "where": p.get("alias_where"), "confidence": p.get("confidence"),
                "cats": c.get("alert_categories") or [], "sources": c.get("sources") or 1,
            })
    return rows


def new_since(rows: list[dict], hours: float, now: datetime) -> int:
    """Clusters first seen within the last `hours`.

    The brief runs eight times a day against an engine that runs every fifteen minutes, so a cut
    can legitimately land on an unchanged pile. Saying so is the difference between a restatement
    and a silent repeat, and on 13 September a cut published the previous window's items with no
    indication that nothing had moved.
    """
    cutoff = now - timedelta(hours=hours)
    n = 0
    for r in rows:
        try:
            seen = datetime.fromisoformat(r["seen"])
        except (TypeError, ValueError):
            continue
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
        if seen >= cutoff:
            n += 1
    return n


def partition(rows: list[dict]) -> dict:
    """The editorial filter, and every number the reconciliation needs, in one pass."""
    tierA = [r for r in rows if not r["comp"]]
    comps = [r for r in rows if r["comp"]]
    out = {"all": len(rows), "A": len(tierA), "C": len(comps)}
    for key, pool in (("A", tierA), ("C", comps)):
        bearing = [r for r in pool if r["where"] == "title"]
        out[f"{key}_bearing"] = len(bearing)
        out[f"{key}_standfirst"] = sum(1 for r in pool if r["where"] == "summary")
        out[f"{key}_inherited"] = sum(1 for r in pool if r["where"] == "none")
        clean = [r for r in bearing if not junky(r["domain"])]
        out[f"{key}_junk"] = len(bearing) - len(clean)
        out[f"{key}_clean"] = len(clean)
        out[f"{key}_rows"] = sorted(clean, key=rank)
    return out


def quiet_block(coverage: dict) -> dict:
    """Silence, and the proof that it was measured rather than assumed.

    Reported for comps as well as tier A. A brief that only vouches for the names it leads with
    is not a coverage document: the comp set is 230 of the 303 credits swept, and a comp that is
    never heard from is the same diagnostic signal as a tier-A name that is never heard from.
    Inovie, a direct Biogroup comparable, has taken 96 successful queries and produced nothing,
    ever, and that fact was invisible until it was printed.
    """
    names = coverage.get("names", {})
    out: dict = {
        "starved": len(coverage.get("flags", {}).get("starved", [])),
        "no_queries": len(coverage.get("flags", {}).get("no_queries", [])),
    }
    for key, want_comp in (("a", False), ("c", True)):
        pool = [v for v in names.values() if (v.get("kind") == "comp") is want_comp]
        quiet = sorted((v for v in pool if v.get("state") == "quiet"),
                       key=lambda v: -(v.get("ok_24h") or 0))
        out[f"{key}_total"] = len(pool)
        out[f"{key}_quiet"] = quiet
        out[f"{key}_never"] = [v for v in quiet if not v.get("mentions_total")]
        out[f"{key}_jobs"] = sum(v.get("jobs_24h") or 0 for v in pool)
        out[f"{key}_ok"] = sum(v.get("ok_24h") or 0 for v in pool)
        # A name the engine never even queried is the one failure this block exists to catch.
        out[f"{key}_unswept"] = [v["name"] for v in pool if not v.get("ok_24h")]
    out["total"] = out["a_total"] + out["c_total"]
    out["jobs"] = out["a_jobs"] + out["c_jobs"]
    out["ok"] = out["a_ok"] + out["c_ok"]
    out["unswept"] = out["a_unswept"] + out["c_unswept"]
    # kept for the existing template fields
    out["quiet"] = out["a_quiet"]
    out["never"] = out["a_never"]
    out["queries"] = sum(v.get("ok_24h") or 0 for v in out["a_quiet"])
    return out


def health_block(latest: dict) -> dict:
    sh = latest.get("source_health", {}) or {}
    detail = sh.get("detail") or []
    dead = [x for x in detail if (x.get("ok_runs") or 0) == 0]
    buckets = {"budget": 0, "tripped": 0, "rate_limited": 0, "http": 0, "no_feed": 0, "other": 0}
    for x in dead:
        e = (x.get("last_error") or "").lower()
        if "time budget" in e:
            buckets["budget"] += 1
        elif "tripped" in e:
            buckets["tripped"] += 1
        elif "429" in e:
            buckets["rate_limited"] += 1
        elif "no feed url" in e:
            buckets["no_feed"] += 1
        elif "http" in e or "403" in e or "404" in e:
            buckets["http"] += 1
        else:
            buckets["other"] += 1
    total = sh.get("sources") or len(detail)
    return {"total": total, "dead": len(dead), "answering": total - len(dead), **buckets}


# ----------------------------------------------------------------------------- rendering
def e(s) -> str:
    return html.escape(str(s or ""), quote=True)


def item_html(r: dict, j: dict, lead: bool) -> str:
    so = (j.get("so") or {}).get(str(r["id"]))
    trans = (j.get("translate") or {}).get(str(r["id"]))
    extra = (j.get("tags") or {}).get(str(r["id"])) or []
    tags = [f'<span class="tag">{e(r["country"])}</span>',
            f'<span class="tag">{e(r["source"][:34])}</span>']
    if r["sources"] > 1:
        tags.append(f'<span class="tag">{r["sources"]} sources</span>')
    for c in r["cats"]:
        tags.append(f'<span class="tag cat">{e(CATEGORY_LABEL.get(c, c))}</span>')
    for t in extra:
        tags.append(f'<span class="tag">{e(t)}</span>')
    headline = trans or r["title"]
    orig = f'<p class="orig">{e(r["title"])}</p>' if trans else ""
    body = f'<p class="so">{so}</p>' if so else ""
    if lead:
        return (f'<article class="item"><div class="meta"><span class="credit">{e(r["name"])}</span>'
                f'{"".join(tags)}<span>{e(r["seen"][11:16])}Z</span></div>{orig}'
                f'<h3><a href="{e(r["url"])}" rel="noopener">{e(headline)}</a></h3>{body}</article>')
    return (f'<li><div class="meta"><span class="credit">{e(r["name"])}</span>{"".join(tags)}</div>'
            f'<div class="t"><a href="{e(r["url"])}" rel="noopener">{e(headline)}</a></div>'
            f'{body}</li>')


def render(latest: dict, coverage: dict, j: dict, cut: str, since_hours: float = 3.0) -> str:
    rows = collect(latest)
    P = partition(rows)
    Q = quiet_block(coverage)
    H = health_block(latest)
    st = latest.get("stats", {})
    stamp = latest.get("generated_at", "")
    now = datetime.now(timezone.utc)
    try:
        exported = datetime.fromisoformat(stamp)
        if exported.tzinfo is None:
            exported = exported.replace(tzinfo=timezone.utc)
        age_min = int((now - exported).total_seconds() // 60)
    except (TypeError, ValueError):
        age_min = -1
    fresh = new_since(rows, since_hours, now)

    lead_ids = [str(x) for x in (j.get("lead") or [])]
    by_id = {str(r["id"]): r for r in P["A_rows"]}
    lead = [by_id[i] for i in lead_ids if i in by_id]
    if not lead:                                   # no judgement supplied: take the categorised ones
        lead = [r for r in P["A_rows"] if r["cats"]][:6]
    lead_set = {r["id"] for r in lead}
    # Group the lead under its category, keeping the judgement's order within each group and the
    # order in which categories first appear. Run-length grouping alternated headings whenever a
    # categorised and an uncategorised item sat next to each other.
    #
    # "Not flagged by the engine" is deliberate, not a fallback label: the keyword detector missed
    # the FT's Recordati story and the Les Echos Cerba story, the two most important items of
    # 13 September. Naming that on the page keeps the gap visible instead of quietly absorbing it.
    order: list[str] = []
    bucket: dict[str, list[dict]] = {}
    for r in lead:
        label = CATEGORY_LABEL.get(r["cats"][0], "Other") if r["cats"] else "Not flagged by the engine"
        if label not in bucket:
            bucket[label] = []
            order.append(label)
        bucket[label].append(r)
    groups = [(label, bucket[label]) for label in order]
    carried = [r for r in P["A_rows"] if r["id"] not in lead_set and
               (r["cats"] or r["sources"] > 1 or (j.get("so") or {}).get(str(r["id"])))]
    comp_carried = [r for r in P["C_rows"] if r["cats"]][:8]
    published = len(lead) + len(carried)
    review_dropped = P["A_clean"] - published

    dropped = [r for r in P["A_rows"] if r["id"] not in lead_set and r not in carried]
    by_name: dict[str, list] = {}
    for r in dropped:
        by_name.setdefault(r["name"], []).append(r)
    drops = sorted(by_name.items(), key=lambda kv: -len(kv[1]))

    def drop_rows() -> str:
        out = []
        for name, rs in drops[:8]:
            doms = ", ".join(sorted({x["domain"] for x in rs})[:3])
            out.append(f'<tr><td class="n">{len(rs)}</td><td class="name">{e(name)}</td>'
                       f'<td class="c">{e(rs[0]["title"][:96])}…<br><span style="opacity:.7">{e(doms)}</span></td></tr>')
        rest = sum(len(rs) for _, rs in drops[8:])
        if rest:
            out.append(f'<tr><td class="n">{rest}</td><td class="name">{len(drops)-8} others</td>'
                       f'<td class="c">One or two clusters each.</td></tr>')
        return "\n".join(out)

    never = " · ".join(f'{e(v["name"])} ({v.get("ok_24h", 0)}q)' for v in Q["never"])
    quietly = " · ".join(f'{e(v["name"])} ({v.get("ok_24h", 0)}q)'
                         for v in Q["quiet"] if v.get("mentions_total"))

    fields = dict(
        cut=e(cut), date=e(datetime.now(timezone.utc).strftime("%A %-d %B %Y")),
        tier_a=Q["total"], comps=st.get("runs") and (303 - Q["total"]) or 0,
        stamp=e(stamp[11:16]), run=f'{st.get("runs", 0):,}',
        clusters=f'{st.get("clusters", 0):,}', items=f'{st.get("items", 0):,}',
        candidates=P["all"], carried_n=published,
        answering=f'{H["answering"]:,}', sources=f'{H["total"]:,}',
        feeds=st.get("feeds_known", 0),
        dead=H["dead"], budget=H["budget"], tripped=H["tripped"],
        rate=H["rate_limited"], http=H["http"], nofeed=H["no_feed"],
        lead_items="\n".join(
            f'<h3 class="grp">{e(label)}</h3>' + "\n".join(item_html(r, j, True) for r in rs)
            for label, rs in groups) or
            '<p class="method">Nothing cleared the filter into the lead in this window.</p>',
        lead_n=len(lead),
        carried_items="\n".join(item_html(r, j, False) for r in carried) or
                      '<li><div class="t">Nothing else cleared the filter in this window.</div></li>',
        comp_items="\n".join(item_html(r, j, False) for r in comp_carried) or
                   '<li><div class="t">No comp item carried a category in this window.</div></li>',
        comp_cat=sum(1 for r in P["C_rows"] if r["cats"]), comp_pub=len(comp_carried),
        drop_n=review_dropped, drop_rows=drop_rows(),
        q_quiet=len(Q["a_quiet"]), q_total=Q["a_total"], q_never=never or "none",
        q_quietly=quietly or "none", q_queries=f'{Q["queries"]:,}',
        q_starved=Q["starved"], q_noq=Q["no_queries"],
        fresh=fresh, since_h=int(since_hours), age_min=age_min if age_min >= 0 else "unknown",
        stale_note=(
            "<div class=\"stale\"><p><b>Read this as a restatement, not an update.</b> "
            f"No cluster has entered the window in the last {int(since_hours)} hours, and the engine "
            f"last exported {age_min} minutes ago. The pile behind this cut is the same one behind "
            "the previous brief.</p></div>"
            if fresh == 0 else
            ("<div class=\"stale\"><p><b>The pile may be incomplete.</b> The engine last exported "
             f"{age_min} minutes ago, so the most recent hours are not represented. Absence of an item "
             "below is not evidence that nothing happened.</p></div>" if age_min > 120 else "")),
        c_total=Q["c_total"], c_quiet=len(Q["c_quiet"]), c_never_n=len(Q["c_never"]),
        c_never=" · ".join(f'{e(v["name"])} ({v.get("ok_24h", 0)}q)' for v in Q["c_never"]) or "none",
        c_jobs=f'{Q["c_jobs"]:,}', c_ok=f'{Q["c_ok"]:,}',
        all_names=Q["total"], all_jobs=f'{Q["jobs"]:,}', all_ok=f'{Q["ok"]:,}',
        unswept=len(Q["unswept"]),
        unswept_names=", ".join(e(n) for n in Q["unswept"][:12]) or "none",
        r_all=P["all"], r_A=P["A"], r_C=P["C"],
        rA_inherit=P["A_inherited"], rA_stand=P["A_standfirst"], rA_bear=P["A_bearing"],
        rA_junk=P["A_junk"], rA_clean=P["A_clean"], rA_drop=review_dropped, rA_pub=published,
        rC_inherit=P["C_inherited"] + P["C_standfirst"], rC_bear=P["C_bearing"],
        rC_junk=P["C_junk"], rC_clean=P["C_clean"], rC_cat=sum(1 for r in P["C_rows"] if r["cats"]),
        rC_pub=len(comp_carried),
        stamp_full=e(stamp),
    )
    # Targeted substitution, not str.format: the template carries a full stylesheet and every CSS
    # brace would be read as a field. Longest keys first so {r_A} cannot eat part of {rA_bear}.
    out = TEMPLATE
    for k in sorted(fields, key=len, reverse=True):
        out = out.replace("{" + k + "}", str(fields[k]))
    leftover = set(re.findall(r"\{([a-z_]+)\}", out.split("</style>", 1)[-1]))
    if leftover:
        raise SystemExit(f"template has unfilled fields: {sorted(leftover)}")
    return out


TEMPLATE = Path(__file__).with_name("brief_template.html").read_text(encoding="utf-8") \
    if Path(__file__).with_name("brief_template.html").exists() else ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--docs", default="docs", help="directory holding latest.json and coverage.json")
    ap.add_argument("--out", default="brief.html")
    ap.add_argument("--judgement", help="JSON of lead order, so-what text and translations")
    ap.add_argument("--cut", default=None, help="label for this cut, e.g. '13:00 London'")
    ap.add_argument("--noise", default="config/noise.yaml",
                    help="path to config/noise.yaml; the domain screen is read from it")
    ap.add_argument("--since-hours", type=float, default=3.0, dest="since_hours",
                    help="how far back counts as new for this cut (default 3, the usual gap)")
    ap.add_argument("--resolve", action="store_true",
                    help="follow each shortlisted link to the outlet and drop anything whose URL "
                         "path shows an older year; catches re-dated archive pages")
    ap.add_argument("--shortlist", action="store_true",
                    help="print the filtered shortlist as JSON and stop, for the judgement pass")
    a = ap.parse_args()

    global JUNK_DOMAINS
    noise = Path(a.noise) if a.noise else None
    JUNK_DOMAINS = load_junk(noise)
    print(f"domain screen: {len(JUNK_DOMAINS)} domains from "
          f"{noise if noise and noise.exists() else 'the built-in fallback'}", file=sys.stderr)

    docs = Path(a.docs)
    latest = json.loads((docs / "latest.json").read_text(encoding="utf-8"))
    coverage = json.loads((docs / "coverage.json").read_text(encoding="utf-8"))

    if a.shortlist:
        P = partition(collect(latest))
        if a.resolve:
            dropped = []
            this_year = datetime.now(timezone.utc).year
            for key in ("A_rows", "C_rows"):
                keep = []
                for r in P[key]:
                    final, year = resolve_and_date(r["url"])
                    r["resolved_url"] = final
                    r["url_year"] = year
                    if year is not None and year < this_year:
                        dropped.append({"name": r["name"], "title": r["title"],
                                        "year": year, "url": final})
                    else:
                        keep.append(r)
                P[key] = keep
            P["redated"] = dropped
            print(f"resolved links; dropped {len(dropped)} re-dated", file=sys.stderr)
        json.dump({"tier_a": [{k: r[k] for k in
                               ("id", "name", "title", "source", "domain", "country", "lang",
                                "cats", "sources", "seen", "url")} for r in P["A_rows"]],
                   "comps": [{k: r[k] for k in
                              ("id", "name", "title", "source", "cats", "sources")}
                             for r in P["C_rows"] if r["cats"]],
                   "redated": P.get("redated", []),
                   "counts": {k: v for k, v in P.items()
                              if not k.endswith("_rows") and k != "redated"}},
                  sys.stdout, ensure_ascii=False, indent=1)
        return 0

    if not TEMPLATE:
        sys.exit("scripts/brief_template.html is missing; the layout lives there")
    j = json.loads(Path(a.judgement).read_text(encoding="utf-8")) if a.judgement else {}
    cut = a.cut or datetime.now(timezone.utc).strftime("%H:%M UTC")
    Path(a.out).write_text(render(latest, coverage, j, cut, a.since_hours), encoding="utf-8")
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
