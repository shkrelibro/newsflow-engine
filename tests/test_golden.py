"""The accountability test: does the machine catch what is known to have happened."""
from datetime import datetime, timedelta, timezone

from newsflow.golden import GoldenEvent, check, load_golden, summarise
from newsflow.store import Store

D = datetime(2026, 9, 13, tzinfo=timezone.utc)


def _seed(store, name_id, title, seen, status="candidate"):
    store.conn.execute(
        "INSERT INTO items(canonical_url, raw_link, title, title_key, first_seen_at, route, status, "
        "source_name, run_id) VALUES (?,?,?,?,?,?,?,?,?)",
        (f"https://ex.invalid/{abs(hash(title))}", "x", title, title[:12], seen.isoformat(),
         "googlenews", status, "Test Outlet", 1))
    iid = store.conn.execute("SELECT id FROM items WHERE title=?", (title,)).fetchone()["id"]
    store.conn.execute(
        "INSERT INTO matches(item_id, name_id, alias, where_, confidence) VALUES (?,?,?,?,?)",
        (iid, name_id, name_id, "title", 1.0))
    store.conn.commit()


def test_hit_miss_pending_and_latency(tmp_path):
    store = Store(str(tmp_path / "g.db"))
    _seed(store, "rossini", "CVC faces shareholder revolt over €10.7bn Recordati take-private", D + timedelta(hours=4))
    _seed(store, "cerba", "Cerba: something unrelated about labs", D + timedelta(hours=2))

    events = [
        GoldenEvent("recordati", "rossini", D, ["Recordati"], any_of=["revolt", "take-private"], within_hours=8),
        GoldenEvent("cerba", "cerba", D, [], any_of=["bras de fer", "5 milliards"], within_hours=12),
        GoldenEvent("chep", "cheplapharm", D - timedelta(days=12), [], any_of=["950"], expect="miss"),
        GoldenEvent("future", "biogroup", D + timedelta(days=3), [], any_of=["Q2"], within_hours=30),
    ]
    now = D + timedelta(hours=20)
    res = {r.event.id: r for r in check(store, events, now)}

    assert res["recordati"].hit and abs(res["recordati"].latency_hours - 4.0) < 0.01
    assert res["recordati"].route == "googlenews" and res["recordati"].source == "Test Outlet"
    assert not res["cerba"].hit and not res["cerba"].pending          # due and missed: a finding
    assert not res["chep"].hit and not res["chep"].pending            # a documented hole
    assert not res["future"].hit and res["future"].pending             # not yet due

    s = summarise(list(res.values()))
    assert (s["hits"], s["misses"], s["known_misses"], s["new_misses"], s["pending"]) == (1, 2, 1, 1, 1)
    assert s["median_latency_hours"] == 4.0
    store.close()


def test_screened_items_still_count_as_seen(tmp_path):
    """The question is whether the machine SAW it; setting it aside is a separate finding."""
    store = Store(str(tmp_path / "g.db"))
    _seed(store, "intrum", "Intrum atleis 76 darbuotojus", D + timedelta(hours=1), status="screened")
    r = check(store, [GoldenEvent("i76", "intrum", D, ["76"], any_of=["atleis"])], D + timedelta(days=2))[0]
    assert r.hit and r.status == "screened"
    store.close()


def test_the_committed_golden_set_loads_and_names_real_config_ids():
    from pathlib import Path
    from newsflow.config import load_config
    root = Path(__file__).resolve().parent.parent
    events = load_golden(root / "config" / "golden.yaml")
    assert len(events) >= 6
    ids = {n.id for n in load_config(root / "config").names}
    unknown = [e.id for e in events if e.name not in ids]
    assert not unknown, f"golden events on names not in config: {unknown}"
    for e in events:
        assert e.contains or e.any_of, f"{e.id} has nothing to match on"
