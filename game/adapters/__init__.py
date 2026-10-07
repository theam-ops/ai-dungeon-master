"""Concrete implementations of `game/ports.py`, chosen once at start-up.

`DND_MODE=lite` (the default) is one process and needs nothing installed. `prod` is
several processes sharing Postgres and Redis; it is Phase 8 of
docs/REFACTORING_PLAN.md and does not exist yet.

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
        raise NotImplementedError(
            "DND_MODE=prod needs the Postgres and Redis adapters, which are Phase 8 of "
            "docs/REFACTORING_PLAN.md and not built yet. Unset DND_MODE to run as one "
            "process.")
    raise ValueError(f"DND_MODE must be one of {', '.join(MODES)}, not {mode!r}")
