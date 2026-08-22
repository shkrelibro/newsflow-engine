# newsflow-engine

The acquisition layer of the Daily Newsflow product. It fetches every mention of the names in the
universe from many cheap routes, normalises and deduplicates them, matches them to names with
per-language alias rules, screens obvious noise, stores everything with provenance, and exports a
candidates pile (`docs/latest.json`) that the editorial layer turns into the morning brief.

It is deliberately boring: no model calls, no judgement, deterministic rules, so that recall can be
audited. The judgement happens afterwards, in the brief.

## What it fetches

| Route | What | Per run |
|---|---|---|
| Google News RSS | one query per searched alias × market (language + country), `when:1d`; plus `site:` queries for ~70 key outlets and regulators every 4th run with a 7-day window (these search the body text, so regulator releases that name the company are found) | every run |
| Bing News RSS | the main alias per market; a second, independent index | every 2nd run |
| GDELT DOC 2.0 | the main alias, 24h, all languages; a third independent crawl | every run |
| Outlet feeds | RSS/Atom from ~65 national and trade titles in `config/sources/outlets.yaml`; feed URLs are auto-discovered where not given; every item is matched against every name | every run |
| Page watchers | the company's own newsrooms, the MFN regulatory feed, outlet tag pages and regulator release lists; new links since the last visit become items | every 2nd run |

Intrum alone produces ~90 jobs on a normal run and ~250 on the hourly run that includes the
`site:` queries and the secondary aliases; a run takes three to six minutes within the per-host
rate limits. `python -m newsflow jobs --run-number N` shows the count for any run.

## What it stores and exports

Every fetched mention is kept in `data/newsflow.db` (SQLite) with route, query, source, language,
country, timestamps, the alias that matched, the story cluster and, if screened, the reason.
Nothing a name-query returned is ever discarded: an item whose headline does not contain the alias
is kept with confidence 0.4 and flagged, so the editorial layer can decide.

`docs/` is rewritten on every run: `latest.json` (last 24h, grouped by name and cluster, with
`also` reports, alert flags, screened counts and source health), `alerts.json`, `health.json`,
`daily/YYYY-MM-DD.json` (kept 90 days), and a readable `index.html`.

## Policy

Feed URLs (RSS/Atom, Google News RSS, Bing News RSS, the GDELT API) are fetched the way a feed
reader does: an identified user agent, low frequency, no crawling. HTML page watchers honour
robots.txt and are polled every 30 minutes with change detection; nothing beyond the configured
page is crawled. Paywalled titles contribute headlines, standfirsts and links only. The output is
for the user's own monitoring; check redistribution terms before sharing it beyond that.

## Commands

```
python -m newsflow check-config            # validate and summarise the configuration
python -m newsflow jobs --run-number 4     # how many fetches a given run would make
python -m newsflow run                     # one cycle: fetch, store, export, alerts
python -m newsflow run --backfill-days 30  # first run / golden set: widen every window to 30 days
python -m newsflow loop --every 15         # server mode
python -m newsflow discover-feeds          # find RSS/Atom feeds for outlets without one
python -m newsflow stats                   # database stats and source health
python -m newsflow export                  # rebuild docs/ from the database
```

## Configuration

- `config/newsflow.yaml` — engine settings, route cadences, export, alerts (webhook or SMTP).
- `config/names/<id>.yaml` — one file per name or comp: markets (the footprint, swept in full on
  every run), aliases (`search: true` ones are queried; `inflect: true` catches Intrums/Intrumin/
  Intrum-Aktie; `require_context` guards ambiguous words like Solvia), people, `site_queries`,
  `pages`, `feeds`, and name-specific noise rules.
- `config/sources/outlets.yaml` — shared outlet list per country.
- `config/tier1_terms.yaml` — multilingual patterns that flag alert candidates (rating, capital
  markets, restructuring, M&A, regulatory, management, litigation). A recall net, not a verdict.
- `config/noise.yaml` — domains and title patterns that are stored but screened (stock-quote pages,
  company registers, content farms, phishing warnings).

To add a name: copy `config/names/intrum.yaml`, change id, name, markets, aliases and sources,
run `python -m newsflow check-config`, commit. The next run picks it up.

## Running it

See `RUNBOOK.md`. Two options: GitHub Actions (no server; hourly on a private repository's free
minutes, every 15 minutes on a public repository; results served by GitHub Pages) or Docker on any
small server (every 15 minutes, no limits). On hourly schedules run with `--all-routes` so every
route runs every time; the `every_n_runs` cadences in `config/newsflow.yaml` are for 15-minute runs.

## Tests

`python -m pytest -q` runs offline against fixtures (Google News, Bing, GDELT, an outlet feed and
a tag page) and an end-to-end run that checks deduplication across routes, alias rules, screening,
Tier-1 flags, page seeding and the export shape.
