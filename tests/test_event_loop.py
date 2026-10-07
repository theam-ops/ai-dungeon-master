"""Slow image work must not stall the server.

Every table's narration is streamed from one event loop. Anything slow done *on* that
loop - a pasted link trickling in, Pillow decoding a 40-megapixel upload - freezes the
story mid-sentence for every campaign on the server, not just the one that asked.

Each test runs a heartbeat coroutine beside the request. If the slow work is on the
loop, the heartbeat stops for as long as it takes; if it is in a thread, the heartbeat
keeps time.
"""

import asyncio
import time

import pytest

from game import media
from .conftest import player
from .test_join_midstory import start_campaign

SLOW = 0.6          # seconds the fake slow work takes
TICK = 0.02         # heartbeat interval: ~30 beats expected while it runs


def slow(*args, **kwargs):
    time.sleep(SLOW)                       # blocking, as real network and Pillow are
    raise media.MediaError("gave up")      # the outcome is beside the point


async def beats_during(request):
    """Run `request` with a heartbeat beside it; return (response, beats counted)."""
    beats = 0

    async def heartbeat():
        nonlocal beats
        while True:
            await asyncio.sleep(TICK)
            beats += 1

    hb = asyncio.create_task(heartbeat())
    try:
        response = await request
    finally:
        hb.cancel()
    return response, beats


@pytest.mark.parametrize("route", ["link", "upload"])
def test_slow_image_work_does_not_freeze_other_tables(stub, monkeypatch, route):
    monkeypatch.setattr(media, "fetch", slow)
    monkeypatch.setattr(media, "process", slow)

    async def scenario():
        async with player() as alice:
            cid = (await start_campaign(alice))["id"]
            if route == "link":
                request = alice.post(f"/api/campaigns/{cid}/media/url",
                                     json={"url": "http://img.example/a.png"})
            else:
                request = alice.post(f"/api/campaigns/{cid}/media",
                                     files={"file": ("a.png", b"\x89PNG....", "image/png")},
                                     data={"kind": "handout"})
            return await beats_during(request)

    response, beats = asyncio.run(scenario())
    assert response.status_code == 400                  # the error still comes back
    # On the loop this would be ~0. Allow plenty of slack for a loaded CI machine.
    assert beats >= (SLOW / TICK) * 0.4, f"the loop stalled: only {beats} beats"
