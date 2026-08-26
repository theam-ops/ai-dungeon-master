"""Joining a campaign that is already under way.

This machinery already existed - `store.campaign_by_code`, the join endpoints, the
lobby. These tests are here to say whether it actually works, so they go through the
real HTTP endpoints a second browser would use, and then look at what the DM was sent.

Everything a test does runs on one event loop: the per-campaign turn lock belongs to
whichever loop first waits on it, and in the real server there is only ever one.
"""

import asyncio

from .conftest import player, settle

import server
from game import store

VESS = {"name": "Vess", "race": "Elf", "class": "Rogue"}
BRAM = {"name": "Bram", "race": "Dwarf", "class": "Cleric"}

OPENING = "Smoke stands over the marsh where the toll bridge used to be."
SECOND = "The rope bridge is three planks short of the far bank."


async def start_campaign(client, character=VESS, lang="en"):
    r = await client.post("/api/campaigns", json={
        "name": "The Salt Road", "lang": lang, "character": character})
    assert r.status_code == 200, r.text
    return r.json()


async def begin(client, cid):
    assert (await client.post(f"/api/campaigns/{cid}/begin")).status_code == 200
    await settle()


async def act(client, cid, text):
    r = await client.post(f"/api/campaigns/{cid}/act", json={"text": text})
    assert r.status_code == 200, r.text
    await settle()


async def join(client, campaign, character=BRAM):
    """A second browser doing exactly what the lobby does: look up the code, then
    roll a character into it."""
    look = await client.post("/api/campaigns/join", json={"code": campaign["code"]})
    assert look.status_code == 200, look.text
    added = await client.post(f"/api/campaigns/{campaign['id']}/characters",
                              json={"character": character})
    assert added.status_code == 200, added.text
    return look.json(), added.json()


def user_messages(call):
    return [m["content"] for m in call["messages"]
            if m["role"] == "user" and isinstance(m["content"], str)]


def arrival_notes(cid):
    return [m["content"] for m in store.get_history(cid)
            if "<table_note>" in str(m["content"])]


# --------------------------------------------------------------------------- #
# joining a story in progress
# --------------------------------------------------------------------------- #

def test_a_player_can_join_a_campaign_that_already_has_history(stub):
    stub.replies = [OPENING, SECOND, "Bram shoulders through the reeds."]

    async def scenario():
        async with player() as alice, player() as bob:
            campaign = await start_campaign(alice)
            cid = campaign["id"]

            # two turns happen before Bob has a character at all
            await begin(alice, cid)
            await act(alice, cid, "I follow the smoke.")

            look, added = await join(bob, campaign)
            assert look["already_in"] is False
            assert [c["name"] for c in look["party"]] == ["Vess"]
            assert added["character"]["name"] == "Bram"

            # the story so far, as the joining browser asks for it: from event zero
            events = (await bob.get(f"/api/campaigns/{cid}/events?since=0")).json()
            detail = (await bob.get(f"/api/campaigns/{cid}")).json()

            # and the newcomer takes a turn of their own
            await act(bob, cid, "I catch up at the water's edge.")
            return cid, events["events"], detail

    cid, events, detail = asyncio.run(scenario())

    # 1. they receive the story so far
    narration = [e["text"] for e in events if e["kind"] == "narration"]
    assert OPENING in narration and SECOND in narration
    assert [e["character"] for e in events if e["kind"] == "player"] == ["Vess"]

    # 2. they are in the campaign, alongside the character who was already there
    assert detail["you"] == "Bram"
    assert sorted(c["name"] for c in detail["party"]) == ["Bram", "Vess"]

    # 3. the DM was told somebody arrived, at the point in the story it happened
    assert len(arrival_notes(cid)) == 1
    assert "Bram" in arrival_notes(cid)[0]

    # 4. and on the next turn the DM sees both the note and a party state with Bram in it
    sent = user_messages(stub.calls[-1])
    assert any("<table_note>" in m and "Bram" in m for m in sent)
    turn = sent[-1]
    assert '"name": "Bram"' in turn and '"name": "Vess"' in turn
    assert "Bram acts: I catch up at the water's edge." in turn


def test_the_newcomer_is_announced_once_and_only_when_new(stub):
    """Claiming an existing character is one player moving to another device, not an
    arrival - the DM must not be told the party grew when it didn't."""
    async def scenario():
        async with player() as alice, player() as phone:
            campaign = await start_campaign(alice)
            cid = campaign["id"]
            await begin(alice, cid)

            look = (await phone.post("/api/campaigns/join",
                                     json={"code": campaign["code"]})).json()
            claimed = await phone.post(f"/api/campaigns/{cid}/characters",
                                       json={"claim_id": look["party"][0]["id"]})
            assert claimed.status_code == 200
            return cid

    assert arrival_notes(asyncio.run(scenario())) == []


def test_joining_before_the_first_turn_tells_the_dm_nothing(stub):
    """`begin` introduces the whole party itself, so an arrival note before the story
    starts would be a note about a story that hasn't happened."""
    async def scenario():
        async with player() as alice, player() as bob:
            campaign = await start_campaign(alice)
            await join(bob, campaign)
            return campaign["id"]

    assert store.get_history(asyncio.run(scenario())) == []


def test_the_whole_story_replays_however_long_it_is():
    """A joining browser asks from event zero. One read of the event log is capped, so
    a long campaign used to replay a prefix and then stream live events from far past
    where the prefix stopped - a hole in the middle of the story."""
    cid = store.create_campaign("The Long Road")["id"]
    for i in range(1200):
        store.append_event(cid, "narration", {"text": f"scene {i}"})

    assert len(store.events_since(cid, 0)) == 500          # one read is still capped
    got = list(server.replay(cid, 0))

    assert [e["text"] for e in got] == [f"scene {i}" for i in range(1200)]
    assert [e["seq"] for e in got] == sorted(e["seq"] for e in got)


# --------------------------------------------------------------------------- #
# two players at one table
# --------------------------------------------------------------------------- #

def test_a_second_player_acting_does_not_corrupt_the_shared_history(stub):
    """Turns are free-form, so two players can act at the same moment. The campaign
    history is one list in one row: a lost update would drop a whole turn."""
    stub.replies = ["The rope parts.", "The door holds, barely."]

    async def scenario():
        async with player() as alice, player() as bob:
            campaign = await start_campaign(alice)
            cid = campaign["id"]
            await join(bob, campaign)

            # hold the first turn open so the second is certainly inside its window
            held = []

            async def hold(backend):
                if not held:
                    held.append(True)
                    await asyncio.sleep(0.05)

            stub.before_reply = hold
            await asyncio.gather(
                alice.post(f"/api/campaigns/{cid}/act", json={"text": "I cut the rope."}),
                bob.post(f"/api/campaigns/{cid}/act", json={"text": "I brace the door."}),
            )
            await settle()
            return cid

    cid = asyncio.run(scenario())
    history = store.get_history(cid)

    # both turns are there, whole, in order, and neither overwrote the other
    assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]
    text = str(history)
    assert text.count("I cut the rope.") == 1
    assert text.count("I brace the door.") == 1
    assert text.count("The rope parts.") == 1
    assert text.count("The door holds, barely.") == 1

    # the second turn was handed the first one, rather than a stale transcript
    assert len(stub.calls[0]["messages"]) == 1
    assert len(stub.calls[1]["messages"]) == 3
    assert stub.calls[1]["messages"][1]["role"] == "assistant"


def test_each_turn_is_labelled_with_the_player_who_took_it(stub):
    """The DM is told who acted; nothing else stops it putting words in the other
    player's mouth."""
    async def scenario():
        async with player() as alice, player() as bob:
            campaign = await start_campaign(alice)
            cid = campaign["id"]
            await join(bob, campaign)
            await act(alice, cid, "I scout ahead.")
            await act(bob, cid, "I wait with the mule.")

    asyncio.run(scenario())

    assert "Vess acts: I scout ahead." in stub.prompts[0]
    assert "Bram acts: I wait with the mule." in stub.prompts[1]
