"""The zero-dependency adapters: one process, nothing to install.

This is exactly what the server did before the ports existed - an asyncio queue per
browser, an asyncio lock per campaign, `create_task` for background work - moved
behind the interfaces in `game/ports.py`. Nothing here survives a restart and nothing
here is shared between processes, which is fine on one machine and is precisely what
the `prod` adapters exist to change.
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from .. import store
from ..ports import EventBus, LockManager, Repository, Subscription, TaskQueue

log = logging.getLogger("dnd.adapters")


# --------------------------------------------------------------------------- #
# events
# --------------------------------------------------------------------------- #

class _QueueSubscription(Subscription):
    def __init__(self, queue):
        self._queue = queue

    async def next(self, timeout):
        try:
            return await asyncio.wait_for(self._queue.get(), timeout)
        except asyncio.TimeoutError:
            return None


class InMemoryBus(EventBus):
    """An asyncio queue per watching browser, grouped by campaign."""

    def __init__(self, maxsize=1000):
        self._maxsize = maxsize
        self._subs = {}                 # cid -> set of queues

    async def publish(self, cid, event):
        for queue in list(self._subs.get(cid, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass        # a stalled client drops frames; it replays from seq later

    @asynccontextmanager
    async def subscribe(self, cid):
        queue = asyncio.Queue(maxsize=self._maxsize)
        self._subs.setdefault(cid, set()).add(queue)
        try:
            yield _QueueSubscription(queue)
        finally:
            watching = self._subs.get(cid)
            if watching is not None:
                watching.discard(queue)
                # the old module-level defaultdict kept an empty set for every
                # campaign ever watched, for the life of the process
                if not watching:
                    del self._subs[cid]

    def channels(self):
        """Campaigns somebody is watching, and how many. For tests and diagnostics."""
        return {cid: len(qs) for cid, qs in self._subs.items()}


# --------------------------------------------------------------------------- #
# locks
# --------------------------------------------------------------------------- #

class AsyncioLocks(LockManager):
    """asyncio locks, created on first use and dropped when nobody needs them."""

    def __init__(self):
        self._locks = {}                # key -> [asyncio.Lock, holders + waiters]
        self._claims = {}               # key -> monotonic expiry

    @asynccontextmanager
    async def hold(self, key):
        entry = self._locks.get(key)
        if entry is None:
            entry = self._locks[key] = [asyncio.Lock(), 0]
        entry[1] += 1
        try:
            async with entry[0]:
                yield
        finally:
            entry[1] -= 1
            # the old defaultdict(asyncio.Lock) grew by one lock per campaign and never
            # shrank, deleted campaigns included
            if entry[1] == 0 and self._locks.get(key) is entry:
                del self._locks[key]

    async def claim(self, key, ttl):
        # no await between the look and the take, so on one event loop this is atomic
        now = time.monotonic()
        expiry = self._claims.get(key)
        if expiry is not None and expiry > now:
            return False
        self._claims[key] = now + ttl
        return True

    async def release(self, key):
        self._claims.pop(key, None)

    def held(self):
        """Keys with a lock in use or waited on. For tests and diagnostics."""
        return set(self._locks)


# --------------------------------------------------------------------------- #
# background work
# --------------------------------------------------------------------------- #

class AsyncioTaskQueue(TaskQueue):
    """`create_task`, with the references kept and the failures logged."""

    def __init__(self):
        self._jobs = {}
        self._running = set()           # asyncio only weakly references a task
        self._adapters = None

    def bind(self, adapters):
        """The adapters every job is handed. Set once, by `build_adapters`."""
        self._adapters = adapters

    def register(self, name, job):
        self._jobs[name] = job

    async def enqueue(self, job_name, /, *, delay=0, **kwargs):
        job = self._jobs.get(job_name)
        if job is None:
            raise KeyError(f"no job registered as {job_name!r}")
        task = asyncio.create_task(self._run(job_name, job, kwargs, delay))
        self._running.add(task)
        task.add_done_callback(self._running.discard)

    async def _run(self, name, job, kwargs, delay=0):
        try:
            if delay:
                await asyncio.sleep(delay)
            await job(self._adapters, **kwargs)
        except asyncio.CancelledError:
            raise
        except Exception:
            # nothing awaits a background job, so an exception here would otherwise
            # surface only as "Task exception was never retrieved" at shutdown
            log.exception("background job %r failed", name)


# --------------------------------------------------------------------------- #
# storage
# --------------------------------------------------------------------------- #

def _delegate(name):
    async def method(self, *args, **kwargs):
        # looked up on every call rather than bound once, so a test that patches
        # `store.something` patches what the server sees too
        return getattr(store, name)(*args, **kwargs)
    method.__name__ = name
    method.__qualname__ = f"SQLiteRepository.{name}"
    method.__doc__ = getattr(Repository, name).__doc__
    return method


# SQLite through `game/store.py`, one connection per thread. Its calls stay on the event
# loop, as they always have: a local SQLite read is microseconds, and moving each one
# to a thread would cost more than it saves.
SQLiteRepository = type("SQLiteRepository", (Repository,), {
    "__module__": __name__,
    "__doc__": "The Repository port over `game/store.py` - SQLite, one file.",
    **{name: _delegate(name) for name in sorted(Repository.__abstractmethods__)},
})
