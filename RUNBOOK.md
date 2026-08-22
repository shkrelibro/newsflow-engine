# Runbook — one page

## Option A: GitHub Actions (no server; recommended for the pilot)

You need a GitHub account (free). Total effort: about fifteen minutes, once. Claude can do steps 2–5
for you if you give it a short-lived access token (Settings → Developer settings → Personal access
tokens → Tokens (classic) → scopes `repo` and `workflow`, expiry 7 days); delete the token afterwards.

1. Create a new repository called `newsflow-engine`. **Public** gives unlimited run minutes and the
   results page for free (the engine, its configuration and the headline pile are visible to anyone
   who finds it). **Private** keeps everything hidden but needs GitHub Pro (USD 4/month) for the
   results page and an hourly schedule to stay inside the free minutes (see the `cron` line in
   `.github/workflows/newsflow.yml`).
2. Upload this folder's contents to it. Easiest: on the repository page choose *Add file → Upload files*,
   drag the whole folder in, commit. (Or `git push` if you use git.)
3. Turn on the schedule: open the **Actions** tab, click *I understand my workflows, go ahead and enable them*.
4. Seed the pile: Actions → *newsflow* → *Run workflow* → type `30` in *backfill_days* → Run.
   This first run takes ten to twelve minutes and fetches the last 30 days from every route.
5. Turn on the results page: Settings → **Pages** → Source: *Deploy from a branch* → Branch: `main`, folder `/docs` → Save.
   After the next run the pile is at `https://<your-user>.github.io/newsflow-engine/` — `index.html`
   to read, `latest.json` for the editorial layer. Tell Claude that address once.
6. Done. From now on it runs every 15 minutes on its own. Nothing to maintain.

Optional: Tier-1 alert pushes. Settings → Secrets and variables → Actions → New repository secret
`NEWSFLOW_WEBHOOK_URL` (a Slack incoming-webhook URL or any endpoint that accepts `{"text": ...}`),
then set `alerts.enabled: true` in `config/newsflow.yaml`.

## Option B: a small server (Docker)

Any Linux box with Docker (a EUR 5–10/month VPS is plenty).

```
git clone <your copy of this repo> newsflow-engine && cd newsflow-engine
docker compose up -d --build
docker compose exec engine python -m newsflow run --backfill-days 30   # seed once
```

The engine runs every 15 minutes (the cadence the config is tuned for; no minute limits, and a small
server's fixed IP is treated more kindly by Google than GitHub's shared runners); `http://<server>:8080/latest.json`
and `/index.html` serve the pile. Put it behind HTTPS (Caddy or nginx) if the editorial layer must reach it over the internet.

## Daily operation

Nothing. The brief reads `latest.json` each morning. Look at `index.html` or `health.json` if the
brief says a source is failing.

## When something looks wrong

| Symptom | What to do |
|---|---|
| Brief says "source failing" for an outlet | Open `health.json`; if the outlet feed 403s persistently, set `enabled: false` for it in `outlets.yaml` — the `site:` Google query still covers it. |
| Google News items show `url_unresolved` | Expected for newer Google links; the source name is still known. Set `resolve_redirects: true` on a server (not on Actions) to resolve more. |
| Run exceeds 14 minutes on Actions | Raise `site_every_n_runs` to 8 and `bingnews.every_n_runs` to 4, or split names across two repositories. |
| Too much noise for a name | Add domains or title patterns under `noise:` in that name's yaml; they are still stored, just screened. |
| A story was missed | Find where it was published; add the outlet to `outlets.yaml` (feed) and/or `site_queries`, or add the alias that was used. Commit. That is the whole feedback loop. |
| Database lost (cache evicted) | The engine re-creates it; the daily git backup `data/newsflow.db` can be restored by copying it back. Page watchers re-seed silently. |

## Adding a name

Copy `config/names/intrum.yaml` to `config/names/<id>.yaml`; set id, name, home_country, the full
market list, aliases (mark the ones to search), people, site_queries, pages. Run
`python -m newsflow check-config` (or just commit; the tests workflow runs it). Next run picks it up.
