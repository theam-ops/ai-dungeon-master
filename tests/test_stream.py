"""The live event stream, through a real uvicorn server.

Every browser at the table holds `/stream` open for the whole session, and all of the
narration arrives through it. httpx's in-process transport buffers a response before
returning it, so it cannot test a stream that never ends - these run the app on a real
uvicorn in a thread and read the stream over a socket, the way a browser does.
"""

import asyncio
import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn

import server


@pytest.fixture
def live(stub):
    """The app on a real port, for one test."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port,
                                        log_level="warning", lifespan="on"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not srv.started and time.time() < deadline:
        time.sleep(0.02)
    assert srv.started, "uvicorn did not start"
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(10)


def events_from(lines):
    """Parse SSE lines into events, stopping at nothing - the caller decides when."""
    for line in lines:
        if line.startswith("data: "):
            yield json.loads(line[6:])


def test_the_stream_replays_then_goes_live_then_lets_go(live):
    host = httpx.Client(base_url=live, timeout=10)
    guest = httpx.Client(base_url=live, timeout=10)
    try:
        campaign = host.post("/api/campaigns", json={
            "name": "The Salt Road", "lang": "en",
            "character": {"name": "Vess", "race": "Elf", "class": "Rogue"}}).json()
        cid = campaign["id"]
        # something already in the log, for the stream to replay
        asyncio.run(server.publish(cid, "narration", {"text": "Before you arrived."}))

        with host.stream("GET", f"/api/campaigns/{cid}/stream?since=0") as stream:
            lines = stream.iter_lines()
            replayed = []
            for event in events_from(lines):
                if event["kind"] == "ready":
                    break
                replayed.append(event)
            # the whole log comes back in order - creating the campaign wrote its own
            # first event, and what was published after it is last
            assert replayed[-1].get("text") == "Before you arrived."
            assert [e["seq"] for e in replayed] == sorted(e["seq"] for e in replayed)

            # subscribed: the bus knows somebody is watching this campaign
            assert server.A.bus.channels() == {cid: 1}

            # something happening now reaches the open stream - here, somebody joining
            guest.post("/api/campaigns/join", json={"code": campaign["code"]})
            guest.post(f"/api/campaigns/{cid}/characters", json={
                "character": {"name": "Bram", "race": "Dwarf", "class": "Cleric"}
            }).raise_for_status()
            live_event = next(e for e in events_from(lines) if e["kind"] == "join")
            assert live_event["character"] == "Bram"
            assert live_event["seq"] > replayed[-1]["seq"]

        # the browser went away: its subscription must go with it
        deadline = time.time() + 5
        while server.A.bus.channels() and time.time() < deadline:
            time.sleep(0.05)
        assert server.A.bus.channels() == {}
    finally:
        host.close()
        guest.close()


def test_a_stranger_cannot_open_a_campaigns_stream(live):
    host = httpx.Client(base_url=live, timeout=10)
    stranger = httpx.Client(base_url=live, timeout=10)
    try:
        cid = host.post("/api/campaigns", json={
            "name": "Private", "lang": "en",
            "character": {"name": "Vess", "race": "Elf", "class": "Rogue"}}).json()["id"]
        r = stranger.get(f"/api/campaigns/{cid}/stream")
        assert r.status_code == 403
        assert server.A.bus.channels() == {}           # refused before subscribing
    finally:
        host.close()
        stranger.close()
