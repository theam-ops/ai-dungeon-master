"""Locks, live events and background work across servers sharing one Postgres.

Each test builds two sets of prod adapters on one database - two servers, as far as
anything in them can tell - and checks the contracts in `game/ports.py` hold between
them, not merely within one: what one server holds the other waits for, what one
publishes the other's browsers see, what one queues the other may run.

Skipped without a Postgres - see tests/pgcluster.py.
"""

import asyncio
import logging
import time

import pytest

TICK = 0.05


# --------------------------------------------------------------------------- #
# locks
# --------------------------------------------------------------------------- #

def test_a_hold_on_one_server_is_waited_for_on_the_other(pg):
    async def run():
        async with pg() as a, pg() as b:
            inside, most, order = 0, 0, []

            async def turn(adapters, n):
                nonlocal inside, most
                async with adapters.locks.hold("turn:c1"):
                    inside += 1
                    most = max(most, inside)
                    order.append(n)
                    await asyncio.sleep(TICK)
                    inside -= 1
            await asyncio.gather(*(turn(x, n) for n, x in enumerate([a, b] * 4)))
            return most, sorted(order)
    assert asyncio.run(run()) == (1, list(range(8)))


def test_different_keys_do_not_wait_across_servers(pg):
    async def run():
        async with pg() as a, pg() as b:
            async with a.locks.hold("turn:c1"):
                started = time.monotonic()
                async with b.locks.hold("turn:c2"):
                    return time.monotonic() - started
    assert asyncio.run(run()) < 1


def test_a_server_that_dies_holding_a_lock_frees_it(pg):
    """The point of an advisory lock: the database lets go when the connection does,
    with no expiry to wait out. Simulated by cutting the holder's connection."""
    async def run():
        async with pg() as a, pg() as b:
            held = asyncio.Event()

            async def doomed():
                async with a.locks.hold("turn:c1"):
                    held.set()
                    await asyncio.sleep(30)

            task = asyncio.create_task(doomed())
            await held.wait()
            # the process "dies": its connection is cut from the server's side
            killed = await b.db.pool.fetchval(
                "SELECT count(pg_terminate_backend(pid)) FROM pg_locks"
                " WHERE locktype = 'advisory' AND granted AND pid <> pg_backend_pid()")
            assert killed == 1
            started = time.monotonic()
            async with b.locks.hold("turn:c1"):
                waited = time.monotonic() - started
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            return waited
    assert asyncio.run(run()) < 5


def test_giving_up_while_waiting_leaves_nothing_held(pg):
    async def run():
        async with pg() as a, pg() as b:
            async with a.locks.hold("turn:c1"):
                waiter = asyncio.create_task(b.locks.hold("turn:c1").__aenter__())
                await asyncio.sleep(0.3)
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)
            # if the cancelled wait had quietly been granted, this would never return
            async def again():
                async with b.locks.hold("turn:c1"):
                    return True
            return await asyncio.wait_for(again(), 5)
    assert asyncio.run(run())


def test_only_one_server_can_claim_and_a_release_frees_it(pg):
    async def run():
        async with pg() as a, pg() as b:
            first = await asyncio.gather(*(x.locks.claim("begin:c1", 60) for x in [a, b] * 5))
            await b.locks.release("begin:c1")
            again = await a.locks.claim("begin:c1", 60)
            await a.locks.release("not:held")       # not an error
            return sum(first), again
    assert asyncio.run(run()) == (1, True)


def test_an_abandoned_claim_expires(pg):
    async def run():
        async with pg() as a, pg() as b:
            assert await a.locks.claim("memory:c1", 0.2)
            assert not await b.locks.claim("memory:c1", 0.2)
            await asyncio.sleep(0.4)
            return await b.locks.claim("memory:c1", 60)
    assert asyncio.run(run())


# --------------------------------------------------------------------------- #
# live events
# --------------------------------------------------------------------------- #

def test_what_one_server_publishes_the_other_server_s_browsers_see(pg):
    async def run():
        async with pg() as a, pg() as b:
            cid = (await a.repo.create_campaign("Two servers"))["id"]
            async with b.bus.subscribe(cid) as sub, b.bus.subscribe("other") as stranger:
                await a.bus.publish(cid, {"kind": "thinking", "on": True})
                event = await a.repo.append_event(cid, "narration", {"text": "Rain."})
                await a.bus.publish(cid, event)
                got = [await sub.next(2), await sub.next(2)]
                return got, await stranger.next(0.3), cid
    got, stranger, cid = asyncio.run(run())
    assert got[0] == {"kind": "thinking", "on": True}
    assert got[1]["text"] == "Rain." and got[1]["seq"] == 1
    assert stranger is None


def test_an_event_too_big_for_notify_still_arrives(pg):
    """NOTIFY refuses 8000 bytes. A party of six with full sheets is bigger."""
    async def run():
        async with pg() as a, pg() as b:
            async with b.bus.subscribe("c1") as sub:
                big = {"kind": "party", "party": [{"name": "ทะเล" * 900}] * 3}
                await a.bus.publish("c1", big)
                return big, await sub.next(2)
    big, got = asyncio.run(run())
    assert got == big


def test_logged_events_arrive_in_log_order_however_their_notices_land(pg):
    """Two servers commit 10 then 11, but their notices may arrive 11 first. A browser
    that saw 11 before 10 would set its bookmark past 10. Forced here by publishing the
    notices backwards."""
    async def run():
        async with pg() as a, pg() as b:
            cid = (await a.repo.create_campaign("Order"))["id"]
            async with b.bus.subscribe(cid) as sub:
                ten = await a.repo.append_event(cid, "narration", {"text": "ten"})
                eleven = await b.repo.append_event(cid, "narration", {"text": "eleven"})
                await b.bus.publish(cid, eleven)
                await a.bus.publish(cid, ten)
                got = [await sub.next(2), await sub.next(2), await sub.next(0.5)]
                return [e and e["text"] for e in got]
    assert asyncio.run(run()) == ["ten", "eleven", None]


def test_events_logged_while_the_listener_was_down_are_caught_up(pg):
    async def run():
        async with pg() as a, pg() as b:
            cid = (await a.repo.create_campaign("Flaky"))["id"]
            async with b.bus.subscribe(cid) as sub:
                b.bus._conn.terminate()             # the listening connection drops
                lost = await a.repo.append_event(cid, "narration", {"text": "missed"})
                await a.bus.publish(cid, lost)      # nobody on b hears this notice
                return (await sub.next(10))["text"]
    assert asyncio.run(run()) == "missed"


def test_the_stream_s_replay_and_the_live_feed_meet_without_a_gap(pg):
    """Subscribe, then replay, then go live - what server.stream does. Everything
    logged around the moment of subscribing must be in one or the other."""
    async def run():
        async with pg() as a, pg() as b:
            cid = (await a.repo.create_campaign("Seam"))["id"]
            stop = asyncio.Event()

            async def narrate():
                n = 0
                while not stop.is_set():
                    event = await a.repo.append_event(cid, "narration", {"n": n})
                    await a.bus.publish(cid, event)
                    n += 1
                    await asyncio.sleep(0.005)

            writer = asyncio.create_task(narrate())
            await asyncio.sleep(0.1)
            seen = set()
            async with b.bus.subscribe(cid) as sub:
                seen.update(e["seq"] for e in await b.repo.events_since(cid, 0, 10000))
                await asyncio.sleep(0.2)
                stop.set()
                await writer
                while (event := await sub.next(0.5)) is not None:
                    seen.add(event["seq"])
            return seen, {e["seq"] for e in await a.repo.events_since(cid, 0, 10000)}
    seen, logged = asyncio.run(run())
    assert seen == logged


# --------------------------------------------------------------------------- #
# background work
# --------------------------------------------------------------------------- #

def test_a_job_queued_on_one_server_runs_on_another(pg):
    async def run():
        ran = asyncio.Event()
        got = {}

        async def job(adapters, cid, name):
            got.update(cid=cid, name=name, adapters=adapters)
            ran.set()
        async with pg(jobs=False) as web, pg() as worker:
            web.queue.register("look", job)
            worker.queue.register("look", job)
            await web.queue.enqueue("look", cid="c1", name="Vess")
            await asyncio.wait_for(ran.wait(), 5)
            return got, worker
    got, worker = asyncio.run(run())
    assert (got["cid"], got["name"]) == ("c1", "Vess") and got["adapters"] is worker


def test_two_workers_never_both_take_one_job(pg):
    async def run():
        done = []

        async def job(adapters, n):
            done.append(n)
        async with pg() as a, pg() as b:
            for x in (a, b):
                x.queue.register("count", job)
            await asyncio.gather(*(a.queue.enqueue("count", n=n) for n in range(40)))
            for _ in range(200):
                if len(done) >= 40:
                    break
                await asyncio.sleep(TICK)
            await asyncio.sleep(0.3)
            return done
    assert sorted(asyncio.run(run())) == list(range(40))


def test_a_delayed_job_waits_and_survives_a_restart(pg):
    """In one process a delayed job lives in memory and dies with it. Here it waits in
    the database - the process that queued it can stop, and another runs it."""
    async def run():
        ran = asyncio.Event()

        async def job(adapters, turn):
            ran.set()
        async with pg(jobs=False) as before:
            before.queue.register("nudge", job)
            queued = time.monotonic()
            await before.queue.enqueue("nudge", delay=0.6, turn=3)
        async with pg() as after:
            after.queue.register("nudge", job)
            await asyncio.wait_for(ran.wait(), 10)
            return time.monotonic() - queued
    assert asyncio.run(run()) >= 0.6


def test_a_failing_job_is_logged_and_the_worker_carries_on(pg, caplog):
    async def run():
        ok = asyncio.Event()

        async def bad(adapters):
            raise RuntimeError("boom")

        async def good(adapters):
            ok.set()
        async with pg() as a:
            a.queue.register("bad", bad)
            a.queue.register("good", good)
            await a.queue.enqueue("bad")
            await a.queue.enqueue("good")
            await asyncio.wait_for(ok.wait(), 5)
    with caplog.at_level(logging.ERROR, logger="dnd.adapters"):
        asyncio.run(run())
    assert "'bad' failed" in caplog.text


def test_job_arguments_must_be_plain_data(pg):
    async def run():
        async with pg() as a:
            a.queue.register("look", lambda *_a, **_k: None)
            await a.queue.enqueue("look", images=[(b"bytes", "image/png")])
    with pytest.raises(TypeError):
        asyncio.run(run())


def test_a_job_queued_while_the_worker_settles_down_to_wait_still_wakes_it(pg):
    """The worker sleeps between jobs and a notification wakes it. Once it cleared that
    wake-up *after* checking for work, so a job queued in the gap - after the check
    found nothing, before the sleep began - waited out the five-second fallback poll: a
    DM turn starting five seconds late, now and then, for no visible reason. It showed
    up as one flaky test in nine. Here a job is put into exactly that gap, every time."""
    class Pool:
        """The worker's pool, with a job slipped in right after its last look."""

        def __init__(self, pool, slip):
            self._pool, self._slip = pool, slip

        def __getattr__(self, name):
            return getattr(self._pool, name)

        async def fetchval(self, query, *args):
            value = await self._pool.fetchval(query, *args)
            if "MIN(run_at)" in query and self._slip:
                slip, self._slip = self._slip, None
                await slip()
            return value

    class Db:
        def __init__(self, db, pool):
            self._db, self.pool = db, pool

        def __getattr__(self, name):
            return getattr(self._db, name)

    async def run():
        started = asyncio.Event()

        async def job(adapters):
            started.set()
        async with pg(jobs=False) as web, pg() as worker:
            for x in (web, worker):
                x.queue.register("turn", job)

            async def slip():
                await web.queue.enqueue("turn")
                for _ in range(100):                # until the notification has landed
                    if worker.queue._wake.is_set():
                        break
                    await asyncio.sleep(0.01)
                slipped.set()
            slipped = asyncio.Event()
            await asyncio.sleep(0.2)                # the worker is asleep on an empty queue
            worker.queue._db = Db(worker.queue._db, Pool(worker.queue._db.pool, slip))
            worker.queue._wake.set()                # wake it to look, find nothing, settle
            await asyncio.wait_for(slipped.wait(), 5)
            began = time.monotonic()
            await asyncio.wait_for(started.wait(), 10)
            return time.monotonic() - began
    assert asyncio.run(run()) < 1
