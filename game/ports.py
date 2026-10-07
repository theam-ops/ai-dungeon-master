"""The seams between the game and the machinery it runs on.

Everything here is an interface. Nothing in this file knows about SQLite, Redis,
Postgres or asyncio queues - those live in `game/adapters/`, one implementation per
mode. The game reaches infrastructure only through these, which is what lets the same
code run as one process on a laptop (`lite`) or as several behind a load balancer
(`prod`), without an `if REDIS_URL:` anywhere in the logic.

Four of them, because four things pinned this application to a single process:

- `EventBus`    who is watching which campaign, so narration can reach them
- `LockManager` one DM turn at a time per campaign; one opening scene, ever
- `TaskQueue`   work that outlives the request that started it
- `Repository`  every read and write of campaign state

See ARCHITECTURE.md, "The four ports".
"""

from abc import ABC, abstractmethod
from contextlib import AbstractAsyncContextManager
from typing import Any, Awaitable, Callable

Event = dict[str, Any]


# --------------------------------------------------------------------------- #
# EventBus
# --------------------------------------------------------------------------- #

class Subscription(ABC):
    """One browser watching one campaign."""

    @abstractmethod
    async def next(self, timeout: float) -> Event | None:
        """The next event, or None if `timeout` seconds pass without one."""


class EventBus(ABC):
    """Fan events out to every browser watching a campaign.

    Delivery is best effort and that is deliberate: every event is also written to the
    campaign's event log with a sequence number, and a browser that misses one replays
    from its last `seq` when it reconnects. The log is the guarantee; the bus is the
    fast path. An implementation may drop an event for a subscriber that has stopped
    reading - it must never let one stalled phone hold up the whole table.
    """

    @abstractmethod
    async def publish(self, cid: str, event: Event) -> None:
        """Send `event` to everyone currently subscribed to `cid`."""

    @abstractmethod
    def subscribe(self, cid: str) -> AbstractAsyncContextManager[Subscription]:
        """`async with bus.subscribe(cid) as sub:` - registered on entry, so nothing
        published after entry is missed; gone on exit."""


# --------------------------------------------------------------------------- #
# LockManager
# --------------------------------------------------------------------------- #

class LockManager(ABC):
    """Mutual exclusion that holds across every process serving the table.

    Two shapes, because the game needs both:

    `hold` is a critical section. Turns queue on it: a second player acting while the
    DM is mid-sentence waits their turn rather than being refused.

    `claim` is a lease that outlives the request taking it. Pressing Begin has to stay
    claimed for as long as the opening scene takes to write - which is the DM's time,
    not the request's - so it is taken in one place and released in another.
    """

    @abstractmethod
    def hold(self, key: str) -> AbstractAsyncContextManager[None]:
        """Wait for `key`, hold it for the body of the `async with`, then release."""

    @abstractmethod
    async def claim(self, key: str, ttl: float) -> bool:
        """Take `key` if nobody holds it. True if this caller now does.

        `ttl` is a safety net, not a schedule: a process that dies holding a claim must
        not block the campaign for ever. Release explicitly when done.
        """

    @abstractmethod
    async def release(self, key: str) -> None:
        """Give up a claim. Releasing one that is not held is not an error."""


# --------------------------------------------------------------------------- #
# TaskQueue
# --------------------------------------------------------------------------- #

# A job receives the adapters it should use, then its own arguments. Jobs are named and
# registered rather than passed as coroutines because a coroutine cannot be sent to
# another process - a name and some arguments can.
Job = Callable[..., Awaitable[Any]]


class TaskQueue(ABC):
    """Work that outlives the request that asked for it: a DM turn, an illustration."""

    @abstractmethod
    def register(self, name: str, job: Job) -> None:
        """Make `job` runnable as `name`. Called once, at start-up."""

    @abstractmethod
    async def enqueue(self, name: str, **kwargs: Any) -> None:
        """Run the job called `name` with `kwargs`, without waiting for it.

        Arguments must be plain data - a queue that crosses processes will serialise
        them. A job's failures are its own: they are logged, never raised here.
        """


# --------------------------------------------------------------------------- #
# Repository
# --------------------------------------------------------------------------- #

class Repository(ABC):
    """Every read and write of campaign state. The only port that knows about rows.

    The method set mirrors `game/store.py`, the SQLite implementation it grew out of,
    so `lite` is a thin delegation and the names are already familiar. Async even
    though SQLite is not: a Postgres or LibSQL implementation will be, and the callers
    must not have to change when it arrives.

    Characters are plain dicts - the sheet from `rules.new_character` - carrying their
    row id as `_id` and their owner's token as `_token`. `party` returns them already
    brought up to date (`ensure_skills`, `ensure_equipment`, AC recomputed).
    """

    # -- campaigns ----------------------------------------------------------- #
    @abstractmethod
    async def create_campaign(self, name, lang="en", backend=""): ...
    @abstractmethod
    async def get_campaign(self, cid): ...
    @abstractmethod
    async def campaign_by_code(self, code): ...
    @abstractmethod
    async def campaigns_for_token(self, token): ...
    @abstractmethod
    async def campaign_lang(self, cid): ...
    @abstractmethod
    async def campaign_backend(self, cid): ...
    @abstractmethod
    async def set_campaign_backend(self, cid, backend): ...
    @abstractmethod
    async def delete_campaign(self, cid): ...

    # -- characters ---------------------------------------------------------- #
    @abstractmethod
    async def party(self, cid): ...
    @abstractmethod
    async def save_party(self, characters): ...
    @abstractmethod
    async def add_character(self, cid, char, token, notes=""): ...
    @abstractmethod
    async def character_for_token(self, cid, token): ...
    @abstractmethod
    async def claim_character(self, char_id, token): ...
    @abstractmethod
    async def set_character_notes(self, char_id, text): ...
    @abstractmethod
    async def set_portrait(self, char_id, mid): ...

    # -- the transcript the DM reads ----------------------------------------- #
    @abstractmethod
    async def get_history(self, cid): ...
    @abstractmethod
    async def save_history(self, cid, history): ...
    @abstractmethod
    async def note_in_history(self, cid, text): ...

    # -- the event log the browsers replay ----------------------------------- #
    @abstractmethod
    async def append_event(self, cid, kind, payload):
        """Record an event; return it with its `seq`, which must only ever increase."""
    @abstractmethod
    async def events_since(self, cid, since=0, limit=500): ...
    @abstractmethod
    async def last_seq(self, cid): ...
    @abstractmethod
    async def turns_in_last_minute(self, cid): ...

    # -- images -------------------------------------------------------------- #
    @abstractmethod
    async def add_media(self, cid, file, kind, mime, size, width, height,
                        caption="", source="upload", owner=None): ...
    @abstractmethod
    async def get_media(self, cid, mid): ...
    @abstractmethod
    async def update_media(self, cid, mid, caption=None, kind=None): ...
    @abstractmethod
    async def delete_media(self, cid, mid): ...
    @abstractmethod
    async def campaign_media(self, cid): ...
    @abstractmethod
    async def media_count(self, cid): ...
    @abstractmethod
    async def file_still_used(self, cid, file, except_id=None): ...
    @abstractmethod
    async def claim_art_slot(self, cid, every_turns):
        """Take the campaign's illustration slot if free. Must be atomic: two turns
        racing for it may not both win."""

    # -- the campaign library ------------------------------------------------ #
    @abstractmethod
    async def add_lore(self, cid, name, text): ...
    @abstractmethod
    async def lore_documents(self, cid): ...
    @abstractmethod
    async def lore_texts(self, cid): ...
    @abstractmethod
    async def delete_lore(self, cid, lid): ...

    # -- moving a campaign between servers ----------------------------------- #
    @abstractmethod
    async def export_campaign(self, cid): ...
    @abstractmethod
    async def import_campaign(self, blob): ...
