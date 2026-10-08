"""Copy a SQLite campaign database into Postgres, for moving to DND_MODE=prod.

    python tools/migrate_sqlite_to_pg.py --database-url postgresql://... [--sqlite campaign.db]

Everything is copied as it is - campaign ids, character ids, picture ids and the event
log's sequence numbers - so links, join codes and every browser's bookmark in the story
stay valid. The SQLite file is only read, never written, and it is read through SQLite
itself, so the newest writes - which live in `campaign.db-wal` until a checkpoint, and
which copying the .db file alone would lose - come too. Stop the game first, so nothing
is written while the copy runs.

The target must be empty: this moves a game, it does not merge two. Pictures are files
under `media/`, not rows; copy that folder to wherever DND_MEDIA points on the new
servers.
"""

import argparse
import asyncio
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from game.adapters.postgres import Database      # noqa: E402
from game.adapters.postgres.repo import _t       # noqa: E402

# table -> (columns, the default for each when an older database lacks the column)
TABLES = {
    "campaigns": {"id": None, "code": None, "name": None, "lang": "en", "backend": "",
                  "house": "{}", "combat": "", "memory": "", "history": "[]",
                  "last_art": 0.0, "created_at": 0.0, "updated_at": 0.0},
    "characters": {"id": None, "campaign_id": None, "player_token": None, "name": None,
                   "data": None, "notes": "", "portrait": "", "created_at": 0.0},
    "media": {"id": None, "campaign_id": None, "file": None, "kind": "handout",
              "mime": None, "bytes": 0, "width": 0, "height": 0, "caption": "",
              "source": "upload", "owner": None, "created_at": 0.0},
    "lore": {"id": None, "campaign_id": None, "name": None, "text": None,
             "created_at": 0.0},
    "events": {"seq": None, "campaign_id": None, "kind": None, "payload": None,
               "created_at": 0.0},
}
# insertion order is listing order for characters and pictures - see schema.py, `pos`
ORDER = {"characters": "created_at, rowid", "media": "created_at, rowid",
         "events": "seq", "campaigns": "rowid", "lore": "rowid"}


def read(sqlite_path):
    """Every row, from one consistent snapshot of the SQLite file."""
    if not os.path.exists(sqlite_path):
        raise SystemExit(f"no SQLite database at {sqlite_path}")
    conn = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN")                       # one read transaction for all tables
        present = {r[0] for r in conn.execute("SELECT name FROM sqlite_master"
                                              " WHERE type='table'")}
        rows = {}
        for table, columns in TABLES.items():
            if table not in present:
                rows[table] = []                    # older databases had no lore
                continue
            have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            rows[table] = [
                tuple(_t(r[c]) if c in have else default for c, default in columns.items())
                for r in conn.execute(f"SELECT * FROM {table} ORDER BY {ORDER[table]}")]
        conn.execute("COMMIT")
        return rows
    finally:
        conn.close()


async def migrate(sqlite_path, url, schema="public"):
    """Copy, in one Postgres transaction. Returns {table: rows copied}."""
    rows = read(sqlite_path)
    db = Database(url, schema)
    await db.open()
    try:
        async with db.pool.acquire() as conn, conn.transaction():
            if await conn.fetchval("SELECT EXISTS (SELECT 1 FROM campaigns)"):
                raise SystemExit(f"the Postgres schema {schema!r} already has campaigns in "
                                 "it - this copies a game into an empty database, it does "
                                 "not merge two")
            for table, columns in TABLES.items():
                if rows[table]:
                    names = ", ".join(columns)
                    marks = ", ".join(f"${i}" for i in range(1, len(columns) + 1))
                    await conn.executemany(
                        f"INSERT INTO {table} ({names}) VALUES ({marks})", rows[table])
            # the next event must be numbered after the last one copied
            await conn.execute(
                "SELECT setval(pg_get_serial_sequence('events', 'seq'),"
                " GREATEST((SELECT MAX(seq) FROM events), 1),"
                " (SELECT MAX(seq) FROM events) IS NOT NULL)")
            copied = {t: await conn.fetchval(f"SELECT COUNT(*) FROM {t}") for t in TABLES}
        if copied != {t: len(r) for t, r in rows.items()}:
            raise SystemExit(f"row counts do not match after copying: {copied}")
        return copied
    finally:
        await db.close()


def main():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sqlite", default=os.environ.get("DND_DB",
                                                       os.path.join(here, "campaign.db")))
    ap.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    ap.add_argument("--schema", default=os.environ.get("DND_PG_SCHEMA", "public"))
    args = ap.parse_args()
    if not args.database_url:
        raise SystemExit("--database-url (or DATABASE_URL) is required")
    copied = asyncio.run(migrate(args.sqlite, args.database_url, args.schema))
    for table, n in copied.items():
        print(f"  {table:<11} {n}")
    print("Copied. Now copy the media/ folder to wherever DND_MEDIA points on the new "
          "servers.")


if __name__ == "__main__":
    main()
