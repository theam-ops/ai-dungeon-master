"""The lite adapters, held to the contracts in `game/ports.py`.

These are what the server did before the ports existed, so most of this is pinning
old behaviour in place. The parts that are new are the clean-up - a lock dropped
while somebody still waits on it would let two turns run at once - and the claim,
which replaced a module-level set guarding the opening scene.
"""

import asyncio

import pytest

from game.adapters import build_adapters
from game.adapters.lite import AsyncioLocks, AsyncioTaskQueue, InMemoryBus


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# EventBus
# --------------------------------------------------------------------------- #

def test_a_subscriber_receives_what_is_published_after_it_joins():
    async def scenario():
        bus = InMemoryBus()
        async with bus.subscribe("c1") as sub:
            await bus.publish("c1", {"kind": "delta", "text": "hello"})
            return await sub.next(timeout=1)
    assert run(scenario()) == {"kind": "delta", "text": "hello"}


def test_campaigns_do_not_hear_each_other():
    async def scenario():
        bus = InMemoryBus()
        async with bus.subscribe("c1") as one, bus.subscribe("c2") as two:
            await bus.publish("c1", {"n": 1})
            return await one.next(timeout=1), await two.next(timeout=0.05)
    assert run(scenario()) == ({"n": 1}, None)


def test_next_returns_none_on_a_quiet_line():
    """The stream sends a keepalive on None, so a timeout must not be an exception."""
    async def scenario():
        async with InMemoryBus().subscribe("c1") as sub:
            return await sub.next(timeout=0.02)
    assert run(scenario()) is None


def test_a_stalled_subscriber_loses_frames_rather_than_blocking_the_table():
    async def scenario():
        bus = InMemoryBus(maxsize=2)
        async with bus.subscribe("c1") as slow, bus.subscribe("c1") as fast:
            for n in range(5):
                await bus.publish("c1", {"n": n})        # must not raise or block
            got = [await fast.next(timeout=0.05) for _ in range(2)]
            return got, bus.channels()
    got, channels = run(scenario())
    assert got == [{"n": 0}, {"n": 1}]
    assert channels == {"c1": 2}


def test_a_campaign_nobody_watches_leaves_no_trace():
    async def scenario():
        bus = InMemoryBus()
        await bus.publish("never-watched", {"n": 1})
        async with bus.subscribe("c1"):
            during = bus.channels()
        return during, bus.channels()
    during, after = run(scenario())
    assert during == {"c1": 1}
    assert after == {}


# --------------------------------------------------------------------------- #
# LockManager: hold
# --------------------------------------------------------------------------- #

def test_holds_on_one_key_run_one_at_a_time_in_order():
    async def scenario():
        locks = AsyncioLocks()
        log = []

        async def turn(n):
            async with locks.hold("turn:c1"):
                log.append(f"start {n}")
                await asyncio.sleep(0.01)
                log.append(f"end {n}")

        await asyncio.gather(*(turn(n) for n in range(3)))
        return log
    assert run(scenario()) == ["start 0", "end 0", "start 1", "end 1", "start 2", "end 2"]


def test_a_lock_is_kept_while_anyone_waits_for_it():
    """Dropping it when the holder leaves - while a second turn is queued on it - would
    let a third turn make a fresh lock and run alongside the second."""
    async def scenario():
        locks = AsyncioLocks()
        inside = 0
        most = 0

        async def turn():
            nonlocal inside, most
            async with locks.hold("turn:c1"):
                inside += 1
                most = max(most, inside)
                await asyncio.sleep(0.01)
                inside -= 1

        first = asyncio.create_task(turn())
        await asyncio.sleep(0)
        second = asyncio.create_task(turn())
        await asyncio.sleep(0.005)
        mid = locks.held()
        third = asyncio.create_task(turn())      # arrives while the second waits
        await asyncio.gather(first, second, third)
        return mid, most, locks.held()

    mid, most, after = run(scenario())
    assert mid == {"turn:c1"}
    assert most == 1                             # never two turns at once
    assert after == set()                        # and nothing kept afterwards


def test_different_keys_do_not_wait_for_each_other():
    async def scenario():
        locks = AsyncioLocks()
        async with locks.hold("turn:c1"):
            async with locks.hold("turn:c2"):   # would deadlock if they shared a lock
                return locks.held()
    assert run(scenario()) == {"turn:c1", "turn:c2"}


def test_a_failing_holder_still_lets_go():
    async def scenario():
        locks = AsyncioLocks()
        with pytest.raises(RuntimeError):
            async with locks.hold("turn:c1"):
                raise RuntimeError("the DM stumbled")
        async with locks.hold("turn:c1"):        # would hang if it had not
            pass
        return locks.held()
    assert run(asyncio.wait_for(scenario(), 2)) == set()


# --------------------------------------------------------------------------- #
# LockManager: claim
# --------------------------------------------------------------------------- #

def test_only_one_caller_can_claim():
    async def scenario():
        locks = AsyncioLocks()
        results = await asyncio.gather(*(locks.claim("begin:c1", ttl=60) for _ in range(5)))
        return results
    assert sorted(run(scenario())) == [False] * 4 + [True]


def test_a_released_claim_can_be_taken_again():
    async def scenario():
        locks = AsyncioLocks()
        await locks.claim("begin:c1", ttl=60)
        await locks.release("begin:c1")
        await locks.release("begin:c1")          # releasing twice is harmless
        return await locks.claim("begin:c1", ttl=60)
    assert run(scenario()) is True


def test_an_abandoned_claim_expires():
    """The safety net for a process that dies mid-opening, holding the claim."""
    async def scenario():
        locks = AsyncioLocks()
        await locks.claim("begin:c1", ttl=0.02)
        blocked = await locks.claim("begin:c1", ttl=60)
        await asyncio.sleep(0.04)
        return blocked, await locks.claim("begin:c1", ttl=60)
    assert run(scenario()) == (False, True)


# --------------------------------------------------------------------------- #
# TaskQueue
# --------------------------------------------------------------------------- #

def test_a_job_is_handed_the_adapters_and_its_arguments():
    async def scenario():
        adapters = build_adapters("lite")
        seen = {}

        async def job(given, **kwargs):
            seen["adapters"] = given
            seen["kwargs"] = kwargs

        adapters.queue.register("note", job)
        await adapters.queue.enqueue("note", cid="c1", text="hi")
        await asyncio.sleep(0.01)
        return adapters, seen
    adapters, seen = run(scenario())
    assert seen["adapters"] is adapters
    assert seen["kwargs"] == {"cid": "c1", "text": "hi"}


def test_enqueue_does_not_wait_for_the_job():
    async def scenario():
        queue = AsyncioTaskQueue()
        queue.bind(None)
        done = asyncio.Event()

        async def slow(_adapters):
            await asyncio.sleep(0.05)
            done.set()

        queue.register("slow", slow)
        await queue.enqueue("slow")
        was_done = done.is_set()
        await asyncio.wait_for(done.wait(), 1)
        return was_done
    assert run(scenario()) is False


def test_a_failing_job_is_logged_not_raised(caplog):
    async def scenario():
        queue = AsyncioTaskQueue()
        queue.bind(None)

        async def broken(_adapters):
            raise ValueError("no artist")

        queue.register("broken", broken)
        await queue.enqueue("broken")             # must not raise here
        await asyncio.sleep(0.01)
    run(scenario())
    assert any("broken" in r.getMessage() and r.exc_info for r in caplog.records)


def test_an_unregistered_job_is_a_loud_mistake():
    async def scenario():
        await AsyncioTaskQueue().enqueue("nonexistent")
    with pytest.raises(KeyError):
        run(scenario())


# --------------------------------------------------------------------------- #
# choosing a mode
# --------------------------------------------------------------------------- #

def test_prod_mode_without_a_database_says_what_it_needs(monkeypatch):
    pytest.importorskip("asyncpg")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        build_adapters("prod")


def test_prod_mode_without_its_driver_says_what_to_install(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "asyncpg", None)       # as if never installed
    with pytest.raises(RuntimeError, match="requirements-prod.txt"):
        build_adapters("prod")


def test_a_job_argument_must_be_plain_data():
    """A queue that crosses processes stores arguments as JSON. The one-process queue
    holds them to the same rule, so a job handed bytes fails in the test suite rather
    than on the first deployment with more than one server."""
    async def scenario():
        queue = AsyncioTaskQueue()
        queue.register("look", lambda adapters, **kw: asyncio.sleep(0))
        await queue.enqueue("look", images=[(b"not json", "image/png")])
    with pytest.raises(TypeError):
        run(scenario())


def test_an_unknown_mode_is_refused():
    with pytest.raises(ValueError, match="lite"):
        build_adapters("serverless")


def test_a_job_may_take_an_argument_called_name():
    """`enqueue(name, **kwargs)` once claimed `name` for itself, so a job whose own
    argument was called `name` - a player's name, say - failed with 'multiple values'."""
    async def scenario():
        adapters = build_adapters("lite")
        seen = {}

        async def greet(_adapters, name, job):
            seen.update(name=name, job=job)

        adapters.queue.register("greet", greet)
        await adapters.queue.enqueue("greet", name="Vess", job="ranger")
        await asyncio.sleep(0.01)
        return seen
    assert run(scenario()) == {"name": "Vess", "job": "ranger"}


def test_a_job_can_be_delayed():
    async def scenario():
        adapters = build_adapters("lite")
        ran = asyncio.Event()

        async def later(_adapters):
            ran.set()

        adapters.queue.register("later", later)
        await adapters.queue.enqueue("later", delay=0.05)
        early = ran.is_set()
        await asyncio.sleep(0.02)
        still_early = ran.is_set()
        await asyncio.wait_for(ran.wait(), 1)
        return early, still_early
    assert run(scenario()) == (False, False)
