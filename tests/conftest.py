"""Shared fixtures.

There are no model credentials here and there must never be a real API call, so every
test drives a stub backend that records what it was handed. That is also the only way
to assert the thing worth asserting: not that a turn happened, but that the player's
own details and the party they are in actually reached the model.
"""

import asyncio
import contextlib
import copy
import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# read at import time by server.py; a password would 401 every request below
os.environ.pop("APP_PASSWORD", None)

from game import providers, store          # noqa: E402
import server                              # noqa: E402


class StubBackend:
    """Stands in for whichever AI runs the table.

    `dm._run` drives `backend.stream(...)`: an async generator that yields text deltas
    and finishes with one message in Anthropic block format. Every call is kept, so a
    test can look at exactly what the model was sent.
    """

    id = "stub"
    label = "Stub DM"
    model = "stub"
    kind = "stub"
    vision = False
    draws = False
    free = True
    key_env = ""
    key_url = ""

    def __init__(self, replies=None):
        self.calls = []
        self.replies = list(replies or [])
        self.before_reply = None      # optional async hook, to force turns to overlap

    def available(self):
        return True

    def env_name(self):
        return ""

    def describe(self):
        return {"id": self.id, "label": self.label, "model": self.model,
                "kind": self.kind, "available": True, "key_url": "", "key_env": "",
                "free": True, "vision": False, "draws": False}

    # what the DM was told, per round
    @property
    def prompts(self):
        """The text of the last user message of every call - the turn's own prompt."""
        out = []
        for call in self.calls:
            for message in reversed(call["messages"]):
                if message["role"] == "user" and isinstance(message["content"], str):
                    out.append(message["content"])
                    break
        return out

    @property
    def systems(self):
        return ["\n\n".join(b.get("text", "") for b in call["system"])
                for call in self.calls]

    async def stream(self, system_blocks, messages, tools, images=None):
        # deep-copied: `dm._run` keeps mutating the same history list afterwards, and a
        # test asserting on turn one must not be looking at turn two's state
        self.calls.append({"system": copy.deepcopy(system_blocks),
                           "messages": copy.deepcopy(messages),
                           "tools": copy.deepcopy(tools),
                           "images": images})
        if self.before_reply:
            await self.before_reply(self)
        text = self.replies.pop(0) if self.replies else "The torch gutters and holds."
        yield {"type": "delta", "text": text}
        yield {"type": "message",
               "content": [{"type": "text", "text": text}],
               "stop_reason": "end_turn"}


@pytest.fixture
def stub(monkeypatch):
    """A stub DM, wired in as the only backend anything can reach.

    `failover_order` is pinned rather than merely registered: if the machine running
    the tests happens to have a real key in its environment, an unpinned failover could
    quietly spend money on a retry.
    """
    backend = StubBackend()
    monkeypatch.setitem(providers.BY_ID, backend.id, backend)
    monkeypatch.setattr(providers, "failover_order", lambda current_id: [backend])
    monkeypatch.setattr(providers, "any_available", lambda: True)
    monkeypatch.setattr(providers, "default_id", lambda: backend.id)
    return backend


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """A database per test, and a clean set of per-campaign turn locks.

    The locks matter: an `asyncio.Lock` binds to the loop that first waits on it, and
    each test runs its own `asyncio.run`.
    """
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "campaign.db"))
    monkeypatch.setattr(store, "_conn", None)
    server.locks.clear()
    server.subscribers.clear()
    yield
    if store._conn is not None:
        store._conn.close()


async def drain(agen):
    """Collect every event an async generator yields."""
    return [event async for event in agen]


async def settle():
    """Wait for the DM turns the endpoints scheduled.

    `/act` and `/begin` answer the browser straight away and run the turn as a task -
    the browser watches the event stream for the result. A test has to wait for that
    task rather than for the response.
    """
    for _ in range(200):
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if not pending:
            return
        await asyncio.wait(pending, timeout=10)
    raise AssertionError("a DM turn never finished")


@contextlib.asynccontextmanager
async def player():
    """One browser at the table: its own cookie jar, and so its own player token.

    Async rather than `TestClient` so that requests, DM turns and the per-campaign
    turn lock all share one event loop, the way they do under uvicorn.
    """
    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://table") as client:
        yield client
