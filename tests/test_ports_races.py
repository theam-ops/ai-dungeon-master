"""Read-then-write sequences under a database whose reads really suspend.

In `lite` mode none of this can go wrong: the SQLite repository answers without ever
yielding to the event loop, so a check and the write after it run back to back. A
networked database - the Postgres a `prod` deployment uses - yields on every call, and
then two requests can both read the same roster and both act on it.

So these tests swap in a repository that hands the loop a turn before every call, and
check that correctness comes from locks rather than from the accident of SQLite being
synchronous.
"""

import asyncio
import io

import httpx
import pytest
from PIL import Image

import server
from game import media
from .conftest import player
from .test_join_midstory import start_campaign


class Yielding:
    """Every repository call lets other requests run first, as a network round trip does."""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        method = getattr(self._inner, name)

        async def call(*args, **kwargs):
            await asyncio.sleep(0)
            return await method(*args, **kwargs)
        return call


@pytest.fixture
def networked(monkeypatch):
    monkeypatch.setattr(server.A, "repo", Yielding(server.A.repo))


async def seat(client, cid, name):
    r = await client.post(f"/api/campaigns/{cid}/characters",
                          json={"character": {"name": name, "race": "Dwarf",
                                              "class": "Cleric"}})
    return r.status_code


def test_two_people_cannot_both_join_under_one_name(stub, networked):
    """Two characters called Bram means every blow the DM aims at Bram lands on the
    first one, and the second is invulnerable."""
    async def scenario():
        async with player() as host, player() as one, player() as two:
            cid = (await start_campaign(host))["id"]
            codes = await asyncio.gather(seat(one, cid, "Bram"), seat(two, cid, "Bram"))
            party = (await host.get(f"/api/campaigns/{cid}")).json()["party"]
            return sorted(codes), [c["name"] for c in party]

    codes, names = asyncio.run(scenario())
    assert codes == [200, 400]
    assert names.count("Bram") == 1


def test_a_full_table_stays_full(stub, networked):
    async def scenario():
        async with player() as host:
            cid = (await start_campaign(host))["id"]
            seated = []
            for n in range(4):                          # host + 4 = five seats taken
                async with player() as p:
                    seated.append(await seat(p, cid, f"Early{n}"))
            async with player() as a, player() as b, player() as c:
                late = await asyncio.gather(*(seat(p, cid, f"Late{i}")
                                              for i, p in enumerate((a, b, c))))
            party = (await host.get(f"/api/campaigns/{cid}")).json()["party"]
            return seated, sorted(late), len(party)

    seated, late, size = asyncio.run(scenario())
    assert seated == [200] * 4
    assert late == [200, 400, 400]
    assert size == 6


def test_the_image_limit_holds_against_simultaneous_uploads(stub, networked, monkeypatch):
    monkeypatch.setattr(media, "MAX_PER_CAMPAIGN", 1)

    def png(colour):
        buf = io.BytesIO()
        Image.new("RGB", (16, 16), colour).save(buf, "PNG")
        return buf.getvalue()

    async def upload(client, cid, colour):
        r = await client.post(f"/api/campaigns/{cid}/media",
                              files={"file": ("a.png", png(colour), "image/png")},
                              data={"kind": "handout"})
        return r.status_code

    async def scenario():
        async with player() as host:
            cid = (await start_campaign(host))["id"]
            codes = await asyncio.gather(*(upload(host, cid, (n * 40, 0, 0))
                                           for n in range(3)))
            return sorted(codes), await server.A.repo.media_count(cid)

    codes, count = asyncio.run(scenario())
    assert codes == [200, 400, 400]
    assert count == 1
