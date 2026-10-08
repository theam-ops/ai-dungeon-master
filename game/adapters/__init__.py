"""Concrete implementations of `game/ports.py`, chosen once at start-up.

`DND_MODE=lite` (the default) is one process and needs nothing installed. `prod` is
any number of processes sharing one Postgres database - see `postgres/`.

The rest of the game receives an `Adapters` and never asks which mode it is in.
"""

from dataclasses import dataclass

from ..ports import EventBus, LockManager, Repository, TaskQueue

MODES = ("lite", "prod")


@dataclass
class Adapters:
    bus: EventBus
    locks: LockManager
    queue: TaskQueue
    repo: Repository

    async def start(self):
        """Open whatever needs a running event loop - connection pools, in prod."""

    async def aclose(self):
        """Release what `start` opened."""


def build_adapters(mode="lite"):
    mode = (mode or "lite").strip().lower()
    if mode == "lite":
        from .lite import AsyncioLocks, AsyncioTaskQueue, InMemoryBus, SQLiteRepository
        adapters = Adapters(bus=InMemoryBus(), locks=AsyncioLocks(),
                            queue=AsyncioTaskQueue(), repo=SQLiteRepository())
        adapters.queue.bind(adapters)
        return adapters
    if mode == "prod":
        try:
            import asyncpg  # noqa: F401
        except ImportError:
            raise RuntimeError("DND_MODE=prod needs asyncpg: pip install -r "
                               "requirements-prod.txt") from None
        from .postgres import build
        return build()
    raise ValueError(f"DND_MODE must be one of {', '.join(MODES)}, not {mode!r}")
