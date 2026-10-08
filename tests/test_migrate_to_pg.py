"""tools/migrate_sqlite_to_pg.py moves a game without changing a thing a player sees.

Skipped without a Postgres - see tests/pgcluster.py.
"""

import asyncio
import os
import sqlite3
import sys

import pytest

from game import rules, store
from game.adapters.lite import SQLiteRepository

pytest.importorskip("asyncpg")      # the tool imports it; without it there is nothing to test
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "tools"))
import migrate_sqlite_to_pg as tool  # noqa: E402

SCORES = {"STR": 10, "DEX": 16, "CON": 14, "INT": 12, "WIS": 13, "CHA": 8}


async def a_game_in_progress(repo):
    cids = []
    for name in ("The Salt Road", "ถนนเกลือ"):
        c = await repo.create_campaign(name, "th" if cids else "en", "stub")
        cid = c["id"]
        cids.append(cid)
        await repo.set_campaign_house(cid, {"variant_encumbrance": True})
        await repo.set_combat(cid, {"round": 2, "turn": 1, "order": [
            {"name": "Vess", "init": 17, "pc": True}, {"name": "Gull", "init": 9, "pc": False}]})
        await repo.save_history(cid, [{"role": "user", "content": "Begin."},
                                      {"role": "assistant", "content": "Salt wind."}])
        await repo.set_memory(cid, {"upto": 1, "synopsis": "They met.", "turns": 1})
        vess = await repo.add_character(
            cid, rules.new_character("Vess", "Elf", "Rogue", scores=SCORES), "tok-a", "Shy.")
        await repo.add_character(
            cid, rules.new_character("Bran", "Dwarf", "Wizard", scores=SCORES), None)
        mid = await repo.add_media(cid, "f00d.png", "portrait", "image/png", 99, 8, 8,
                                   "Vess", "upload", "tok-a")
        await repo.set_portrait(vess, mid)
        await repo.add_lore(cid, "Gazetteer", "East along the coast.")
        for n in range(30):
            await repo.append_event(cid, "player" if n % 3 else "narration", {"n": n})
        await repo.claim_art_slot(cid, 3)
    return cids


def test_every_campaign_reads_back_exactly_as_it_was(pg_url, pg_schema):
    from game.adapters.postgres import build

    sqlite = SQLiteRepository()
    cids = asyncio.run(a_game_in_progress(sqlite))
    # the newest writes are still in the WAL, not the .db file - they must come too
    assert os.path.getsize(store.DB_PATH + "-wal") > 0

    copied = asyncio.run(tool.migrate(store.DB_PATH, pg_url, pg_schema))
    assert copied == {"campaigns": 2, "characters": 4, "media": 2, "lore": 2, "events": 60}

    async def compare():
        pg = build(pg_url, pg_schema)
        pg.run_jobs = False
        await pg.start()
        try:
            for cid in cids:
                for method in ("export_campaign", "get_campaign", "party", "campaign_media",
                               "lore_documents", "get_memory", "get_combat", "last_seq"):
                    want = await getattr(sqlite, method)(cid)
                    got = await getattr(pg.repo, method)(cid)
                    assert got == want, f"{method} differs after the move"
            assert await pg.repo.campaigns_for_token("tok-a") == \
                await sqlite.campaigns_for_token("tok-a")
            # the log carries on from where it was, so every browser's bookmark holds
            last = max([await sqlite.last_seq(cid) for cid in cids])
            nxt = await pg.repo.append_event(cids[0], "narration", {"text": "Onward."})
            assert nxt["seq"] == last + 1
        finally:
            await pg.aclose()
    asyncio.run(compare())

    with pytest.raises(SystemExit, match="not merge"):
        asyncio.run(tool.migrate(store.DB_PATH, pg_url, pg_schema))


def test_a_database_from_an_older_version_moves_too(pg_url, pg_schema, tmp_path):
    """Before lore, house rules, combat, memory, portraits or notes existed."""
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE campaigns (id TEXT PRIMARY KEY, code TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL, history TEXT NOT NULL DEFAULT '[]',
            created_at REAL NOT NULL, updated_at REAL NOT NULL);
        CREATE TABLE characters (id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL,
            player_token TEXT, name TEXT NOT NULL, data TEXT NOT NULL, created_at REAL NOT NULL);
        CREATE TABLE events (seq INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id TEXT NOT NULL,
            kind TEXT NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL);
        CREATE TABLE media (id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, file TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'handout', mime TEXT NOT NULL, bytes INTEGER NOT NULL,
            width INTEGER NOT NULL, height INTEGER NOT NULL, caption TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'upload', owner TEXT, created_at REAL NOT NULL);
        INSERT INTO campaigns VALUES ('c0000000000000001', 'OLDONE', 'Old', '[]', 1, 2);
        INSERT INTO characters VALUES ('h0000000000000001', 'c0000000000000001', 't', 'Vess',
            '{"name": "Vess", "class": "Rogue", "race": "Elf", "level": 1}', 1);
        INSERT INTO events (campaign_id, kind, payload, created_at)
            VALUES ('c0000000000000001', 'narration', '{"text": "Long ago."}', 1);
    """)
    conn.commit()
    conn.close()
    copied = asyncio.run(tool.migrate(path, pg_url, pg_schema))
    assert copied == {"campaigns": 1, "characters": 1, "media": 0, "lore": 0, "events": 1}

    async def check():
        from game.adapters.postgres import build
        pg = build(pg_url, pg_schema)
        pg.run_jobs = False
        await pg.start()
        try:
            return (await pg.repo.campaign_by_code("oldone"),
                    await pg.repo.events_since("c0000000000000001"))
        finally:
            await pg.aclose()
    campaign, events = asyncio.run(check())
    assert campaign["lang"] == "en" and campaign["house"] == "{}"
    assert events == [{"text": "Long ago.", "seq": 1, "kind": "narration"}]
