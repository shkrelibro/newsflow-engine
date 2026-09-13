"""The golden set: known events the engine must have caught, checked against the store.

Every other test in this repository proves that a mechanism works on a fixture. None of them
proves that the machine caught what actually happened. This one does. config/golden.yaml lists
real events, each with the name it concerns, the date it broke, and a fragment that any
headline about it must contain. After every run the store is checked for each one, and the
result is exported alongside the brief and annotated on the workflow, so a miss is a visible
fact rather than an absence nobody notices.

The set is meant to be maintained: when the analyst knows something happened, it goes in, with
the date. A miss that persists is the most valuable signal this system produces, because it
names a hole in coverage with a real example rather than a suspicion.

Entries:
    - id: recordati-cvc-revolt
      name: rossini                    # config/names id
      date: 2026-09-13                 # the day the story broke
      contains: ["revolt", "Recordati"]   # every fragment must appear in one headline (case-insensitive)
      within_hours: 6                  # how quickly it should have been seen after `date` 00:00Z
      note: FT, take-private, the day's lead
      expect: hit | miss               # optional; a documented miss stays in the set as a known hole
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

from .store import Store


@dataclass
class GoldenEvent:
    id: str
    name: str
    date: datetime
    contains: list[str]
    within_hours: float = 24.0
    note: str = ""
    expect: str = "hit"
    any_of: list[str] = field(default_factory=list)   # alternative fragments, any one suffices


@dataclass
class GoldenResult:
    event: GoldenEvent
    hit: bool
    first_seen: Optional[datetime] = None
    latency_hours: Optional[float] = None
    title: str = ""
    source: str = ""
    route: str = ""
    status: str = ""          # candidate | screened
    pending: bool = False     # the event's date is still in the future or inside its window

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.event.id, "name": self.event.name, "date": self.event.date.date().isoformat(),
            "note": self.event.note, "expect": self.event.expect,
            "hit": self.hit, "pending": self.pending,
            "first_seen": self.first_seen.isoformat(timespec="minutes") if self.first_seen else None,
            "latency_hours": round(self.latency_hours, 1) if self.latency_hours is not None else None,
            "title": self.title, "source": self.source, "route": self.route, "status": self.status,
        }


def load_golden(path: str | Path) -> list[GoldenEvent]:
    p = Path(path)
    if not p.exists():
        return []
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    out = []
    for e in raw.get("events", []) or []:
        d = e["date"]
        if not isinstance(d, datetime):
            d = datetime.fromisoformat(str(d))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        out.append(GoldenEvent(
            id=str(e["id"]), name=str(e["name"]), date=d,
            contains=[str(x) for x in (e.get("contains") or [])],
            within_hours=float(e.get("within_hours", 24)),
            note=str(e.get("note", "")), expect=str(e.get("expect", "hit")),
            any_of=[str(x) for x in (e.get("any_of") or [])],
        ))
    return out


def _matches(title: str, ev: GoldenEvent) -> bool:
    t = (title or "").lower()
    if ev.contains and not all(c.lower() in t for c in ev.contains):
        return False
    if ev.any_of and not any(c.lower() in t for c in ev.any_of):
        return False
    return bool(ev.contains or ev.any_of)


def check(store: Store, events: list[GoldenEvent], now: Optional[datetime] = None) -> list[GoldenResult]:
    """For each event, the earliest stored item on that name whose headline matches, if any.

    Screened items count as a hit: the question here is whether the machine SAW it, and a
    screened item was seen and then set aside for a stated reason, which is a separate finding.
    """
    now = now or datetime.now(timezone.utc)
    results = []
    for ev in events:
        window_start = ev.date - timedelta(hours=12)              # a story can break the evening before
        rows = store.conn.execute(
            """SELECT i.title, i.source_name, i.route, i.status, i.first_seen_at
                 FROM items i JOIN matches m ON m.item_id = i.id
                WHERE m.name_id = ? AND i.first_seen_at >= ?
             ORDER BY i.first_seen_at ASC""",
            (ev.name, window_start.isoformat()),
        ).fetchall()
        hit = None
        for r in rows:
            if _matches(r["title"], ev):
                hit = r
                break
        deadline = ev.date + timedelta(hours=ev.within_hours)
        if hit is not None:
            seen = datetime.fromisoformat(hit["first_seen_at"])
            if seen.tzinfo is None:
                seen = seen.replace(tzinfo=timezone.utc)
            results.append(GoldenResult(
                ev, True, seen, (seen - ev.date).total_seconds() / 3600,
                hit["title"], hit["source_name"] or "", hit["route"] or "", hit["status"] or ""))
        else:
            results.append(GoldenResult(ev, False, pending=now < deadline))
    return results


def summarise(results: list[GoldenResult]) -> dict[str, Any]:
    due = [r for r in results if not r.pending]
    hits = [r for r in due if r.hit]
    misses = [r for r in due if not r.hit]
    known = [r for r in misses if r.event.expect == "miss"]
    new_misses = [r for r in misses if r.event.expect != "miss"]
    return {
        "events": len(results), "due": len(due), "pending": len(results) - len(due),
        "hits": len(hits), "misses": len(misses),
        "known_misses": len(known), "new_misses": len(new_misses),
        "surprise_hits": sum(1 for r in hits if r.event.expect == "miss"),
        "median_latency_hours": (sorted(r.latency_hours for r in hits)[len(hits) // 2] if hits else None),
        "results": [r.as_dict() for r in results],
    }
