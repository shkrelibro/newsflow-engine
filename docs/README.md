This folder is written by the engine on every run and served by GitHub Pages (Settings → Pages → main branch, /docs).

- `latest.json`  — the candidates pile for the last 24 hours, grouped by name and story cluster (the editorial layer reads this)
- `alerts.json`  — Tier-1 alert candidates in the window
- `health.json`  — per-source success over the last runs, so blind spots are visible
- `daily/YYYY-MM-DD.json` — one file per day, kept 90 days (the archive for recall audits)
- `index.html`   — a readable view of the same
