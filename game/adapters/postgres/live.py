"""Locks, live events and background work, shared through Postgres.

One server for all of it. The plan this was built from put live events and the queue in
Redis; Postgres already has what they need - advisory locks, LISTEN/NOTIFY, and
`FOR UPDATE SKIP LOCKED` - so a deployment runs one service, not two, and a lock is
released by the same database that would otherwise have to be asked whether its holder
is still alive.
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager

from ...ports import EventBus, LockManager, TaskQueue
from ..lite import AsyncioLocks, InMemoryBus
from .schema import NOW

log = logging.getLogger("dnd.adapters")

# NOTIFY refuses a payload of 8000 bytes or more. Bigger live events - a party of six
# with full sheets is one - go through `bus_spill` and the notification carries its id.
NOTIFY_LIMIT = 7500
SPILL_KEEP_SECONDS = 120


# --------------------------------------------------------------------------- #
# locks
# --------------------------------------------------------------------------- #

class PostgresLocks(LockManager):
    """`hold` is a session advisory lock; `claim` is a row in `leases`.

    An advisory lock belongs to a connection, so it is released when the connection
    ends - a process killed mid-turn frees its campaign at once, with no expiry to wait
    out and no lease to keep renewing. The cost is a connection per lock held, which
    is why one coroutine per key per process reaches Postgres at all: the others queue
    on a local asyncio lock first, without a connection, and the connections come from
    a pool of their own - a holder that needs a connection for its own queries must
    never wait behind the waiters for its lock.
    """

    def __init__(self, db):
        self._db = db
        self._local = AsyncioLocks()

    @asynccontextmanager
    async def hold(self, key):
        lock_id = self._db.lock_id(key)
        async with self._local.hold(key):
            async with self._db.lock_pool.acquire() as conn:
                # if this is cancelled while waiting, the pool resets the connection on
                # its way back - and that reset runs pg_advisory_unlock_all()
                await conn.execute("SELECT pg_advisory_lock($1)", lock_id)
                try:
                    yield
                finally:
                    try:
                        await conn.execute("SELECT pg_advisory_unlock($1)", lock_id)
                    except Exception:
                        # a dead connection holds nothing: its session took the lock
                        # with it. Make sure the pool does not reuse it.
                        conn.terminate()

    async def claim(self, key, ttl):
        taken = await self._db.pool.fetchval(
            f"INSERT INTO leases (key, expires) VALUES ($1, {NOW} + $2)"
            " ON CONFLICT (key) DO UPDATE SET expires = EXCLUDED.expires"
            f" WHERE leases.expires <= {NOW} RETURNING key", key, float(ttl))
        return taken is not None

    async def release(self, key):
        await self._db.pool.execute("DELETE FROM leases WHERE key=$1", key)

    def held(self):
        return self._local.held()


# --------------------------------------------------------------------------- #
# live events
# --------------------------------------------------------------------------- #

class PostgresBus(EventBus):
    """NOTIFY to every process; an in-memory bus to the browsers on this one.

    Each process keeps one connection listening. What arrives is handed to the local
    `InMemoryBus`, so a stalled browser still only loses its own frames.

    A logged event - one with a `seq` - is not sent through NOTIFY at all, only its
    seq. The listener reads it back from the log, along with anything before it that it
    has not delivered. That is what keeps the order right across processes: two servers
    appending to one campaign commit 10 then 11, but their notifications are separate
    statements and may land 11 first. Read back from the log, 11 brings 10 with it, and
    the late 10 is then recognised as already sent. It also recovers whatever went past
    while the listening connection was down.

    Transient events - "the DM is thinking", narration as it streams - carry their own
    payload and are best effort, as on one process: missed ones are rebuilt by the next
    party bar or replaced by the logged narration.
    """

    def __init__(self, db, repo):
        self._db = db
        self._repo = repo
        self._local = InMemoryBus()
        self._watching = {}         # cid -> local subscribers
        self._last = {}             # cid -> the last logged seq delivered here
        self._inbox = asyncio.Queue()
        self._conn = None
        self._tasks = []
        self._closing = False
        self.channel = db.channel("events")

    # -- lifecycle ----------------------------------------------------------- #

    async def start(self):
        await self._listen()
        self._tasks = [asyncio.create_task(self._deliver())]

    async def _listen(self):
        self._conn = await self._db.connect()
        self._conn.add_termination_listener(self._lost)
        await self._conn.add_listener(self.channel, self._heard)

    def _heard(self, _conn, _pid, _channel, payload):
        self._inbox.put_nowait(payload)

    def _lost(self, _conn):
        if not self._closing:
            log.warning("event listener lost its connection; reconnecting")
            self._tasks.append(asyncio.create_task(self._reconnect()))

    async def _reconnect(self):
        delay = 0.5
        while not self._closing:
            try:
                await self._listen()
                break
            except Exception:
                await asyncio.sleep(delay)
                delay = min(delay * 2, 10)
        # whatever was logged while nobody was listening
        for cid in list(self._last):
            self._inbox.put_nowait(json.dumps({"c": cid, "s": None}))

    async def aclose(self):
        self._closing = True
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._conn is not None:
            await self._conn.close()

    # -- the port ------------------------------------------------------------ #

    async def publish(self, cid, event):
        if event.get("seq") is not None:
            note = json.dumps({"c": cid, "s": event["seq"]})
        else:
            note = json.dumps({"c": cid, "e": event}, ensure_ascii=False)
            if len(note.encode("utf-8")) > NOTIFY_LIMIT:
                note = json.dumps({"c": cid, "x": await self._spill(note)})
        await self._db.pool.execute("SELECT pg_notify($1, $2)", self.channel, note)

    async def _spill(self, note):
        async with self._db.pool.acquire() as conn:
            await conn.execute(f"DELETE FROM bus_spill WHERE created_at < {NOW} - $1",
                               float(SPILL_KEEP_SECONDS))
            return await conn.fetchval(
                f"INSERT INTO bus_spill (payload, created_at) VALUES ($1, {NOW})"
                " RETURNING id", note)

    @asynccontextmanager
    async def subscribe(self, cid):
        async with self._local.subscribe(cid) as sub:
            first = not self._watching.get(cid)
            self._watching[cid] = self._watching.get(cid, 0) + 1
            try:
                if first:
                    # from here on, logged events for this campaign are read back from
                    # the log. Anything committed before this is the caller's replay's.
                    self._last[cid] = await self._repo.last_seq(cid)
                yield sub
            finally:
                self._watching[cid] -= 1
                if not self._watching[cid]:
                    del self._watching[cid]
                    self._last.pop(cid, None)

    def channels(self):
        return self._local.channels()

    # -- delivery ------------------------------------------------------------ #

    async def _deliver(self):
        """One at a time, in the order Postgres delivered them."""
        while True:
            raw = await self._inbox.get()
            try:
                await self._handle(json.loads(raw))
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("could not deliver a live event")

    async def _handle(self, note):
        cid = note.get("c")
        if cid not in self._watching:
            return                      # nobody here is watching that campaign
        if "x" in note:
            payload = await self._db.pool.fetchval(
                "SELECT payload FROM bus_spill WHERE id=$1", note["x"])
            if payload is None:
                return                  # pruned before we got to it; best effort
            note = json.loads(payload)
        if "e" in note:
            await self._local.publish(cid, note["e"])
            return
        if cid not in self._last:
            return                      # a subscriber is still finding its place
        seq = note.get("s")
        if seq is not None and seq <= self._last[cid]:
            return                      # already delivered, brought by a later one
        while cid in self._last:
            batch = await self._repo.events_since(cid, self._last[cid])
            if not batch:
                break
            for event in batch:
                await self._local.publish(cid, event)
            if cid in self._last:
                self._last[cid] = batch[-1]["seq"]


# --------------------------------------------------------------------------- #
# background work
# --------------------------------------------------------------------------- #

class PostgresTaskQueue(TaskQueue):
    """Jobs in a table; every process takes them.

    Taken with `FOR UPDATE SKIP LOCKED`, so two processes never take the same one, and
    deleted as they are taken: at most once. A DM turn is not safe to run twice - it
    would roll fresh dice and narrate the same moment again - so a job whose process
    dies half way through is lost, exactly as it is on one process. What is new is
    everything short of that: a queued turn, or an illustration or a combat nudge
    waiting on its delay, now survives a restart.
    """

    def __init__(self, db, concurrency=64):
        self._db = db
        self._jobs = {}
        self._adapters = None
        self._running = set()
        self._slots = asyncio.Semaphore(concurrency)
        self._wake = asyncio.Event()
        self._conn = None
        self._loop_task = None
        self.channel = db.channel("jobs")

    def bind(self, adapters):
        self._adapters = adapters

    def register(self, name, job):
        self._jobs[name] = job

    async def enqueue(self, job_name, /, *, delay=0, **kwargs):
        if job_name not in self._jobs:
            raise KeyError(f"no job registered as {job_name!r}")
        args = json.dumps(kwargs, ensure_ascii=False)       # plain data, or it fails here
        async with self._db.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                f"INSERT INTO jobs (name, kwargs, run_at) VALUES ($1, $2, {NOW} + $3)",
                job_name, args, float(delay or 0))
            await conn.execute("SELECT pg_notify($1, '')", self.channel)

    # -- the worker ---------------------------------------------------------- #

    async def start(self):
        self._conn = await self._db.connect()
        await self._conn.add_listener(self.channel, lambda *_: self._wake.set())
        self._loop_task = asyncio.create_task(self._work())

    async def aclose(self):
        if self._loop_task:
            self._loop_task.cancel()
            await asyncio.gather(self._loop_task, return_exceptions=True)
        for task in list(self._running):
            task.cancel()
        await asyncio.gather(*self._running, return_exceptions=True)
        if self._conn is not None:
            await self._conn.close()

    async def _work(self):
        while True:
            try:
                await self._slots.acquire()
                # cleared before looking, not after: a job queued between finding
                # nothing and starting to wait must still wake us
                self._wake.clear()
                job = await self._take()
                if job is None:
                    self._slots.release()
                    await self._sleep()
                    continue
                task = asyncio.create_task(self._run(*job))
                self._running.add(task)
                task.add_done_callback(self._running.discard)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._slots.release()
                log.exception("the job queue could not reach the database")
                await asyncio.sleep(1)

    async def _take(self):
        row = await self._db.pool.fetchrow(
            "DELETE FROM jobs WHERE id = (SELECT id FROM jobs"
            f" WHERE run_at <= {NOW} ORDER BY run_at, id LIMIT 1 FOR UPDATE SKIP LOCKED)"
            " RETURNING name, kwargs")
        return (row["name"], json.loads(row["kwargs"])) if row else None

    async def _sleep(self):
        """Until a job is enqueued anywhere, the next delayed one falls due, or five
        seconds pass - the last in case a notification went missing."""
        due = await self._db.pool.fetchval(f"SELECT MIN(run_at) - {NOW} FROM jobs")
        wait = 5.0 if due is None else min(5.0, max(0.01, due))
        try:
            await asyncio.wait_for(self._wake.wait(), wait)
        except asyncio.TimeoutError:
            pass

    async def _run(self, name, kwargs):
        try:
            job = self._jobs.get(name)
            if job is None:
                log.error("dropped a job called %r: nothing here is registered by that name",
                          name)
                return
            await job(self._adapters, **kwargs)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("background job %r failed", name)
        finally:
            self._slots.release()
