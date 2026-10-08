"""`DND_MODE=prod`: every port over one Postgres database, so several server processes -
on one machine or several - can serve the same tables.

    DATABASE_URL=postgresql://user:pass@host:5432/dnd DND_MODE=prod python server.py

Needs `pip install -r requirements-prod.txt` (asyncpg). Nothing else: no Redis, no
broker. See docs/REFACTORING_PLAN.md, Phase 8, for what this does and does not cover.
"""

import hashlib
import os
import re

from .. import Adapters

SCHEMA_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,39}$")


class Database:
    """The connections one process keeps open, and how this deployment names things.

    `schema` lets the game share a database with something else, and lets the tests
    give every test a schema of its own. Lock ids and NOTIFY channels carry it too, so
    two deployments on one server cannot block or hear each other.
    """

    def __init__(self, url, schema="public", pool_size=10, lock_connections=32):
        if not url:
            raise RuntimeError(
                "DND_MODE=prod needs DATABASE_URL - a Postgres connection string such as "
                "postgresql://user:password@host:5432/dnd")
        if not SCHEMA_NAME.match(schema or ""):
            raise ValueError(f"DND_PG_SCHEMA must be a plain lower-case name, not {schema!r}")
        self.url, self.schema = url, schema
        self.pool_size, self.lock_connections = pool_size, lock_connections
        self.pool = self.lock_pool = None

    def lock_id(self, key):
        """A 64-bit advisory lock id. Hashed here, not with Postgres' hashtext, whose
        output is not promised to stay the same across Postgres versions."""
        digest = hashlib.blake2b(f"{self.schema}:{key}".encode(), digest_size=8).digest()
        return int.from_bytes(digest, "big", signed=True)

    def channel(self, name):
        return f"dnd.{self.schema}.{name}"

    def _settings(self):
        return {"search_path": self.schema, "application_name": "ai-dungeon-master"}

    async def connect(self):
        """A connection of its own, outside the pools - for LISTEN, which must stay on
        one connection for as long as it listens."""
        import asyncpg
        return await asyncpg.connect(self.url, server_settings=self._settings())

    async def open(self):
        import asyncpg

        from .schema import SCHEMA
        self.pool = await asyncpg.create_pool(self.url, min_size=1, max_size=self.pool_size,
                                              server_settings=self._settings())
        self.lock_pool = await asyncpg.create_pool(self.url, min_size=0,
                                                   max_size=self.lock_connections,
                                                   server_settings=self._settings())
        # Several processes starting at once would race CREATE TABLE IF NOT EXISTS,
        # which is not safe against itself in Postgres; one at a time.
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", self.lock_id("schema"))
            await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"')
            await conn.execute(SCHEMA)

    async def close(self):
        for pool in (self.lock_pool, self.pool):
            if pool is not None:
                await pool.close()
        self.pool = self.lock_pool = None


class PostgresAdapters(Adapters):
    db: Database = None
    # False for a server that answers browsers and leaves the DM's turns to the others
    run_jobs: bool = True

    async def start(self):
        await self.db.open()
        await self.bus.start()
        if self.run_jobs:
            await self.queue.start()

    async def aclose(self):
        # the worker first: a job finishing during shutdown may still publish
        if self.run_jobs:
            await self.queue.aclose()
        await self.bus.aclose()
        await self.db.close()


def build(url=None, schema=None):
    from .live import PostgresBus, PostgresLocks, PostgresTaskQueue
    from .repo import PostgresRepository

    db = Database(url if url is not None else os.environ.get("DATABASE_URL", ""),
                  schema or os.environ.get("DND_PG_SCHEMA", "public"),
                  pool_size=int(os.environ.get("DND_PG_POOL", "10")),
                  lock_connections=int(os.environ.get("DND_PG_LOCK_CONNECTIONS", "32")))
    repo = PostgresRepository(db)
    adapters = PostgresAdapters(bus=PostgresBus(db, repo), locks=PostgresLocks(db),
                                queue=PostgresTaskQueue(db), repo=repo)
    adapters.db = db
    adapters.run_jobs = os.environ.get("DND_RUN_JOBS", "1") not in ("0", "false", "no")
    adapters.queue.bind(adapters)
    return adapters
