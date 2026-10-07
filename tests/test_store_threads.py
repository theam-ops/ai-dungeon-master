"""The store's connections: one per thread, and loud when misused.

The store used to hold one sqlite3 connection for the whole process with
`check_same_thread=False`. That was safe only because every call happened to run on
the event loop's thread. Image work now runs in worker threads, so the store must be
correct from any thread - and a connection wandering between threads must be an
error, not a silent interleaving.
"""

import sqlite3
import threading

import pytest

from game import store


def in_thread(fn):
    """Run fn in a fresh thread; return its result or re-raise its exception."""
    box = {}

    def run():
        try:
            box["value"] = fn()
        except BaseException as e:          # noqa: BLE001 - re-raised below
            box["error"] = e

    t = threading.Thread(target=run)
    t.start()
    t.join(10)
    if "error" in box:
        raise box["error"]
    return box.get("value")


def test_each_thread_gets_its_own_connection():
    mine = store.db()
    assert store.db() is mine                       # stable within a thread
    theirs = in_thread(store.db)
    assert theirs is not mine


def test_a_connection_used_from_another_thread_fails_loudly():
    """The point of leaving check_same_thread on: misuse is an error, not a race."""
    conn = store.db()
    with pytest.raises(sqlite3.ProgrammingError):
        in_thread(lambda: conn.execute("SELECT 1").fetchone())


def test_a_write_in_a_worker_thread_is_seen_on_this_one():
    campaign = in_thread(lambda: store.create_campaign("Written elsewhere"))
    assert store.get_campaign(campaign["id"])["name"] == "Written elsewhere"


def test_concurrent_writers_all_land():
    cid = store.create_campaign("Busy table")["id"]
    threads, per = 8, 25
    start = threading.Barrier(threads)

    def writer(n):
        start.wait()                                # maximise the overlap
        for i in range(per):
            store.append_event(cid, "player", {"who": n, "i": i})

    workers = [threading.Thread(target=writer, args=(n,)) for n in range(threads)]
    for w in workers:
        w.start()
    for w in workers:
        w.join(30)

    rows = store.db().execute(
        "SELECT seq FROM events WHERE campaign_id=?", (cid,)).fetchall()
    seqs = [r["seq"] for r in rows]
    assert len(seqs) == threads * per
    assert len(set(seqs)) == len(seqs)              # the replay cursor stays unique


def test_a_fresh_database_has_every_column_from_the_schema_alone():
    """The table's shape used to be split between SCHEMA and _migrate."""
    cols = {r["name"] for r in store.db().execute("PRAGMA table_info(characters)")}
    assert {"portrait", "notes"} <= cols


def test_an_old_database_is_brought_up_to_date(tmp_path, monkeypatch):
    old = tmp_path / "old.db"
    conn = sqlite3.connect(old)
    conn.executescript("""
        CREATE TABLE campaigns (id TEXT PRIMARY KEY, code TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL, history TEXT NOT NULL DEFAULT '[]',
            created_at REAL NOT NULL, updated_at REAL NOT NULL);
        CREATE TABLE characters (id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL,
            player_token TEXT, name TEXT NOT NULL, data TEXT NOT NULL,
            created_at REAL NOT NULL);
    """)
    conn.commit()
    conn.close()

    store.close_all()
    monkeypatch.setattr(store, "DB_PATH", str(old))
    ch = {r["name"] for r in store.db().execute("PRAGMA table_info(characters)")}
    cp = {r["name"] for r in store.db().execute("PRAGMA table_info(campaigns)")}
    assert {"portrait", "notes"} <= ch
    assert {"lang", "backend", "last_art"} <= cp


def test_close_all_really_closes():
    conn = store.db()
    store.close_all()
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")
    assert store.db() is not conn                   # and the next call reopens
