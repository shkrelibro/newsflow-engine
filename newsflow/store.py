"""SQLite storage. Every fetched mention is kept with provenance, even when screened."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from .models import Item, SourceResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  canonical_url TEXT NOT NULL UNIQUE,
  raw_link TEXT NOT NULL,
  title TEXT NOT NULL,
  title_key TEXT NOT NULL DEFAULT '',
  summary TEXT NOT NULL DEFAULT '',
  source_name TEXT NOT NULL DEFAULT '',
  source_domain TEXT NOT NULL DEFAULT '',
  published_at TEXT,
  first_seen_at TEXT NOT NULL,
  lang TEXT NOT NULL DEFAULT '',
  country TEXT NOT NULL DEFAULT '',
  route TEXT NOT NULL,
  query TEXT NOT NULL DEFAULT '',
  tier_hint INTEGER,
  status TEXT NOT NULL DEFAULT 'new',
  screen_reason TEXT NOT NULL DEFAULT '',
  cluster_id INTEGER,
  alert_categories TEXT NOT NULL DEFAULT '[]',
  alert_candidate INTEGER NOT NULL DEFAULT 0,
  notified_at TEXT,
  run_id INTEGER,
  also_routes TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_items_seen ON items(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_items_pub ON items(published_at);
CREATE INDEX IF NOT EXISTS idx_items_cluster ON items(cluster_id);

CREATE TABLE IF NOT EXISTS matches (
  item_id INTEGER NOT NULL,
  name_id TEXT NOT NULL,
  alias TEXT NOT NULL,
  where_ TEXT NOT NULL,
  confidence REAL NOT NULL,
  PRIMARY KEY (item_id, name_id),
  FOREIGN KEY (item_id) REFERENCES items(id)
);
CREATE INDEX IF NOT EXISTS idx_matches_name ON matches(name_id);

CREATE TABLE IF NOT EXISTS seen_links (
  page_url TEXT NOT NULL,
  link TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  PRIMARY KEY (page_url, link)
);

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  items_fetched INTEGER NOT NULL DEFAULT 0,
  items_new INTEGER NOT NULL DEFAULT 0,
  notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS source_results (
  run_id INTEGER NOT NULL,
  route TEXT NOT NULL,
  source TEXT NOT NULL,
  ok INTEGER NOT NULL,
  items INTEGER NOT NULL,
  error TEXT NOT NULL DEFAULT '',
  seconds REAL NOT NULL DEFAULT 0,
  names TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_sr_run ON source_results(run_id);

CREATE TABLE IF NOT EXISTS feeds (
  homepage TEXT PRIMARY KEY,
  feed_url TEXT NOT NULL DEFAULT '',
  discovered_at TEXT,
  last_ok_at TEXT,
  last_error TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS clusters (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name_id TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        # jobs run in worker threads; the lock serialises access to the single connection
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        try:  # migration for databases created before the coverage ledger
            self.conn.execute("ALTER TABLE source_results ADD COLUMN names TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        self.conn.commit()

    # ---- runs ----------------------------------------------------------
    def start_run(self, now: datetime) -> int:
        cur = self.conn.execute("INSERT INTO runs(started_at) VALUES (?)", (_iso(now),))
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, now: datetime, fetched: int, new: int, notes: str = "") -> None:
        self.conn.execute(
            "UPDATE runs SET finished_at=?, items_fetched=?, items_new=?, notes=? WHERE id=?",
            (_iso(now), fetched, new, notes, run_id),
        )
        self.conn.commit()

    def add_source_results(self, run_id: int, results: Iterable[SourceResult]) -> None:
        self.conn.executemany(
            "INSERT INTO source_results(run_id, route, source, ok, items, error, seconds, names) VALUES (?,?,?,?,?,?,?,?)",
            [(run_id, r.route, r.source, 1 if r.ok else 0, r.items, r.error, round(r.seconds, 2), ",".join(r.names)) for r in results],
        )
        self.conn.commit()

    def last_run_started(self) -> Optional[datetime]:
        row = self.conn.execute("SELECT started_at FROM runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
        return _parse(row["started_at"]) if row else None

    def run_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0])

    # ---- items -----------------------------------------------------------
    def get_item_by_url(self, canonical_url: str) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM items WHERE canonical_url=?", (canonical_url,)).fetchone()

    def insert_item(self, item: Item, title_key: str, run_id: int) -> int:
        cur = self.conn.execute(
            """INSERT INTO items(canonical_url, raw_link, title, title_key, summary, source_name, source_domain,
                                published_at, first_seen_at, lang, country, route, query, tier_hint, status,
                                screen_reason, cluster_id, run_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                item.canonical_url, item.raw_link, item.title, title_key, item.summary, item.source_name,
                item.source_domain, _iso(item.published_at), _iso(item.first_seen_at), item.lang, item.country,
                item.route, item.query, item.tier_hint, item.status, item.screen_reason, item.cluster_id, run_id,
            ),
        )
        return int(cur.lastrowid)

    def note_also_route(self, item_id: int, route: str, query: str) -> None:
        row = self.conn.execute("SELECT also_routes FROM items WHERE id=?", (item_id,)).fetchone()
        routes = json.loads(row["also_routes"] or "[]") if row else []
        entry = f"{route}:{query}"
        if entry not in routes:
            routes.append(entry)
            self.conn.execute("UPDATE items SET also_routes=? WHERE id=?", (json.dumps(routes), item_id))

    def update_item_enrichment(self, item_id: int, *, published_at: Optional[datetime] = None, summary: str = "", source_name: str = "") -> None:
        row = self.conn.execute("SELECT published_at, summary, source_name FROM items WHERE id=?", (item_id,)).fetchone()
        if row is None:
            return
        sets, vals = [], []
        if published_at and not row["published_at"]:
            sets.append("published_at=?"); vals.append(_iso(published_at))
        if summary and not row["summary"]:
            sets.append("summary=?"); vals.append(summary)
        if source_name and not row["source_name"]:
            sets.append("source_name=?"); vals.append(source_name)
        if sets:
            vals.append(item_id)
            self.conn.execute(f"UPDATE items SET {', '.join(sets)} WHERE id=?", vals)

    def set_screen(self, item_id: int, status: str, reason: str) -> None:
        self.conn.execute("UPDATE items SET status=?, screen_reason=? WHERE id=?", (status, reason, item_id))

    def set_alert(self, item_id: int, categories: list[str], candidate: bool) -> None:
        self.conn.execute("UPDATE items SET alert_categories=?, alert_candidate=? WHERE id=?", (json.dumps(categories), 1 if candidate else 0, item_id))

    def set_cluster(self, item_id: int, cluster_id: int) -> None:
        self.conn.execute("UPDATE items SET cluster_id=? WHERE id=?", (cluster_id, item_id))

    def new_cluster(self, name_id: str, now: datetime) -> int:
        cur = self.conn.execute("INSERT INTO clusters(name_id, created_at) VALUES (?,?)", (name_id, _iso(now)))
        return int(cur.lastrowid)

    def add_match(self, item_id: int, name_id: str, alias: str, where: str, confidence: float) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO matches(item_id, name_id, alias, where_, confidence) VALUES (?,?,?,?,?)",
            (item_id, name_id, alias, where, confidence),
        )

    def recent_items_for_name(self, name_id: str, since: datetime) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT i.* FROM items i JOIN matches m ON m.item_id = i.id
               WHERE m.name_id=? AND i.first_seen_at >= ? ORDER BY i.id DESC LIMIT 2000""",
            (name_id, _iso(since)),
        ).fetchall()

    def commit(self) -> None:
        self.conn.commit()

    # ---- seen links (page watchers) --------------------------------------
    def page_seen_count(self, page_url: str) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM seen_links WHERE page_url=?", (page_url,)).fetchone()[0])

    def unseen_links(self, page_url: str, links: Iterable[str]) -> list[str]:
        links = list(dict.fromkeys(links))
        if not links:
            return []
        out = []
        for chunk_start in range(0, len(links), 500):
            chunk = links[chunk_start:chunk_start + 500]
            q = f"SELECT link FROM seen_links WHERE page_url=? AND link IN ({','.join('?' * len(chunk))})"
            seen = {r["link"] for r in self.conn.execute(q, (page_url, *chunk)).fetchall()}
            out.extend(l for l in chunk if l not in seen)
        return out

    def mark_seen(self, page_url: str, links: Iterable[str], now: datetime) -> None:
        self.conn.executemany(
            "INSERT OR IGNORE INTO seen_links(page_url, link, first_seen_at) VALUES (?,?,?)",
            [(page_url, l, _iso(now)) for l in links],
        )
        self.conn.commit()

    # ---- feeds ------------------------------------------------------------
    def get_feed(self, homepage: str) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM feeds WHERE homepage=?", (homepage,)).fetchone()

    def set_feed(self, homepage: str, feed_url: str, now: datetime, error: str = "") -> None:
        self.conn.execute(
            """INSERT INTO feeds(homepage, feed_url, discovered_at, last_error) VALUES (?,?,?,?)
               ON CONFLICT(homepage) DO UPDATE SET feed_url=excluded.feed_url, discovered_at=excluded.discovered_at, last_error=excluded.last_error""",
            (homepage, feed_url, _iso(now), error),
        )
        self.conn.commit()

    def feed_ok(self, homepage: str, now: datetime) -> None:
        self.conn.execute("UPDATE feeds SET last_ok_at=?, last_error='' WHERE homepage=?", (_iso(now), homepage))

    def feed_error(self, homepage: str, error: str) -> None:
        self.conn.execute("UPDATE feeds SET last_error=? WHERE homepage=?", (error[:300], homepage))

    # ---- export queries -----------------------------------------------------
    def candidates(self, name_id: str, since: datetime) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT i.*, m.alias, m.where_, m.confidence FROM items i JOIN matches m ON m.item_id=i.id
               WHERE m.name_id=? AND i.first_seen_at >= ?
               ORDER BY COALESCE(i.published_at, i.first_seen_at) DESC""",
            (name_id, _iso(since)),
        ).fetchall()
        return [dict(r) for r in rows]

    def coverage_jobs(self, since: datetime) -> list[sqlite3.Row]:
        """Job outcomes since `since` for jobs that declared the names they sweep."""
        return self.conn.execute(
            """SELECT sr.names, sr.ok, sr.error FROM source_results sr
               JOIN runs r ON r.id = sr.run_id
               WHERE r.started_at >= ? AND sr.names != ''""",
            (_iso(since),),
        ).fetchall()

    def name_mentions(self, since_recent: datetime) -> dict[str, dict[str, Any]]:
        """Per name: total mentions, mentions since `since_recent`, latest mention time."""
        rows = self.conn.execute(
            """SELECT m.name_id AS nid, COUNT(*) AS total,
                      SUM(CASE WHEN COALESCE(i.published_at, i.first_seen_at) >= ? THEN 1 ELSE 0 END) AS recent,
                      MAX(COALESCE(i.published_at, i.first_seen_at)) AS latest
               FROM matches m JOIN items i ON i.id = m.item_id GROUP BY m.name_id""",
            (_iso(since_recent),),
        ).fetchall()
        return {r["nid"]: {"total": r["total"], "recent": r["recent"], "latest": r["latest"]} for r in rows}

    def source_health(self, last_runs: int = 4) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT sr.route, sr.source,
                      SUM(sr.ok) AS ok_runs, COUNT(*) AS runs, SUM(sr.items) AS items,
                      MAX(CASE WHEN sr.ok=0 THEN sr.error ELSE '' END) AS last_error
               FROM source_results sr
               WHERE sr.run_id IN (SELECT id FROM runs ORDER BY id DESC LIMIT ?)
               GROUP BY sr.route, sr.source ORDER BY sr.route, sr.source""",
            (last_runs,),
        ).fetchall()
        return [dict(r) for r in rows]

    def unnotified_alerts(self, since: datetime) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT i.*, GROUP_CONCAT(m.name_id) AS name_ids FROM items i JOIN matches m ON m.item_id=i.id
               WHERE i.alert_candidate=1 AND i.notified_at IS NULL AND i.status='candidate' AND i.first_seen_at >= ?
               GROUP BY i.id ORDER BY i.id""",
            (_iso(since),),
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_notified(self, item_ids: Iterable[int], now: datetime) -> None:
        self.conn.executemany("UPDATE items SET notified_at=? WHERE id=?", [(_iso(now), i) for i in item_ids])
        self.conn.commit()

    def stats(self) -> dict[str, Any]:
        q = lambda sql, *a: self.conn.execute(sql, a).fetchone()[0]  # noqa: E731
        return {
            "items": q("SELECT COUNT(*) FROM items"),
            "candidates": q("SELECT COUNT(*) FROM items WHERE status='candidate'"),
            "screened": q("SELECT COUNT(*) FROM items WHERE status='screened'"),
            "matches": q("SELECT COUNT(*) FROM matches"),
            "runs": q("SELECT COUNT(*) FROM runs"),
            "clusters": q("SELECT COUNT(*) FROM clusters"),
            "pages_tracked": q("SELECT COUNT(DISTINCT page_url) FROM seen_links"),
            "feeds_known": q("SELECT COUNT(*) FROM feeds WHERE feed_url != ''"),
        }

    def prune(self, keep_days: int, now: datetime) -> int:
        cutoff = _iso(now - timedelta(days=keep_days))
        cur = self.conn.execute("DELETE FROM source_results WHERE run_id IN (SELECT id FROM runs WHERE started_at < ?)", (cutoff,))
        self.conn.execute("DELETE FROM runs WHERE started_at < ?", (cutoff,))
        self.conn.commit()
        return cur.rowcount

    def close(self) -> None:
        self.conn.close()


def _synchronized(fn):
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return fn(self, *args, **kwargs)
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


for _name, _fn in list(Store.__dict__.items()):
    if callable(_fn) and not _name.startswith("_"):
        setattr(Store, _name, _synchronized(_fn))
