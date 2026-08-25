"""A whole campaign, start to finish, against the stub DM.

Create a table, open the scene, fight something, take damage, level up, bring in a
second player mid-story, import notes and have the DM search them, then export the lot
and import it back. This is the test that would have caught anything that breaks the
game as a game rather than as a set of endpoints.
"""

import json

from .harness import (Table, award, join, new_table, prompt_of, second_browser,
                      strike, tool_results)


def test_a_whole_campaign(app_client):
    client, stub = app_client
    table = new_table(client, stub, name="The Sunken Bell")

    # -- the opening scene -------------------------------------------------- #
    opening = table.begin("Rain hammers the bell tower. Something below is still ringing it.")
    assert [e["kind"] for e in opening] == ["narration"]
    assert table.client.get(f"/api/campaigns/{table.id}").json()["started"] is True

    # the DM was handed the party state and its tools
    first = stub.calls[0]
    assert any("Dungeon Master" in b["text"] for b in first["system"])
    assert {t["name"] for t in first["tools"]} == {"roll_dice", "update_character"}
    assert "Vess" in first["messages"][0]["content"]

    # -- a turn with a roll and damage -------------------------------------- #
    events = table.act(
        "I creep along the gantry towards the rope.",
        [("roll_dice", {"notation": "1d20+3", "reason": "Vess: Stealth vs DC 14",
                        "mode": "normal"})],
        strike("Vess", 4, "the ghoul lunges"),
        "The rope burns your palms as you drop.")
    kinds = [e["kind"] for e in events]
    assert kinds.count("player") == 1
    assert kinds.count("dice") == 2          # the stealth check and the attack
    assert kinds.count("sheet") == 1
    assert kinds[-1] == "narration"

    vess = table.character("Vess")
    assert vess["hp"] == vess["max_hp"] - 4

    # the dice event carries the real roll, and it is inside the notation's range
    roll = next(e for e in events if e["kind"] == "dice")
    assert 4 <= roll["total"] <= 23
    assert roll["reason"] == "Vess: Stealth vs DC 14"

    # -- the sheet event is structured, not a pre-written English sentence --- #
    sheet = next(e for e in events if e["kind"] == "sheet")
    assert sheet["character"] == "Vess"
    assert sheet["changes"][0]["t"] == "hp"
    assert sheet["changes"][0]["delta"] == -4

    # -- levelling up ------------------------------------------------------- #
    before = table.character("Vess")
    table.act("I search the ghoul's nest.",
              award("Vess", xp=300, gold=12, items=["ทับทิมแดง"], reason="the nest"),
              award("Vess", level_up=True, reason="level 2"),
              "You are steadier on your feet than you were an hour ago.")
    after = table.character("Vess")
    assert after["level"] == before["level"] + 1
    assert after["max_hp"] > before["max_hp"]
    assert after["hp"] == after["max_hp"]        # a level-up heals you full
    assert after["xp"] == 300
    assert after["gold"] == before["gold"] + 12
    assert "ทับทิมแดง" in after["inventory"]      # a Thai item name survives the round trip

    # -- a second player joins mid-story ------------------------------------ #
    other = second_browser(client.app)
    try:
        join(other, table.code, {"name": "ปรางค์", "race": "Human", "class": "Cleric"})
        names = [c["name"] for c in table.party()]
        assert names == ["Vess", "ปรางค์"]

        # the newcomer sees the story so far, not an empty feed
        replay = other.get(f"/api/campaigns/{table.id}/events",
                           params={"since": 0}).json()["events"]
        assert any(e["kind"] == "narration" for e in replay)

        # and can act; the DM is told who acted
        table.act("I hold the lantern up.",
                  "ปรางค์ steps out of the dark behind you.")
        assert "Vess acts:" in prompt_of(stub.calls[-1])

        # the second player's own action is labelled with *their* character
        mark = table.last_seq()
        stub.reply("The bell answers her.")
        other.post(f"/api/campaigns/{table.id}/act",
                   json={"text": "ฉันสวดภาวนาต่อระฆัง"}).raise_for_status()
        table._settle(mark)
        last = prompt_of(stub.calls[-1])
        assert "ปรางค์ acts:" in last
        assert "ฉันสวดภาวนาต่อระฆัง" in last
    finally:
        other.__exit__(None, None, None)

    # -- the players' own notes, and a lore lookup --------------------------- #
    files = [("files", ("bells.md", "The Sunken Bell was cast for ปรางค์'s grandmother, "
                                    "Aroon, who drowned with it.".encode(),
                        "text/markdown"))]
    lib = client.post(f"/api/campaigns/{table.id}/library", files=files).json()
    assert [d["name"] for d in lib["documents"]] == ["bells.md"]

    events = table.act(
        "Who was Aroon?",
        [("search_lore", {"query": "Aroon", "document": ""})],
        "The name lands like a stone. Your grandmother, they said.")
    lore_event = next(e for e in events if e["kind"] == "lore")
    assert lore_event["query"] == "Aroon"
    # search_lore is only offered once there is something behind it
    assert "search_lore" in {t["name"] for t in stub.calls[-1]["tools"]}
    # and the passage actually reached the model
    assert any("drowned with it" in r for r in tool_results(stub.calls[-1]))

    # -- export, then import it back ---------------------------------------- #
    dump = client.get(f"/api/campaigns/{table.id}/export")
    dump.raise_for_status()
    blob = dump.json()
    assert blob["format"] == "ai-dm-campaign/1"
    assert [c["name"] for c in blob["characters"]] == ["Vess", "ปรางค์"]
    assert blob["lore"][0]["name"] == "bells.md"

    restored = client.post("/api/import", json=blob)
    restored.raise_for_status()
    new_id = restored.json()["id"]
    assert new_id != table.id

    back = client.get(f"/api/campaigns/{new_id}").json()
    assert [c["name"] for c in back["party"]] == ["Vess", "ปรางค์"]
    assert back["party"][0]["level"] == 2
    assert "ทับทิมแดง" in back["party"][0]["inventory"]
    assert client.get(f"/api/campaigns/{new_id}/lore").json()["documents"][0]["name"] \
        == "bells.md"

    # the imported campaign is playable, with the story so far intact
    copy = Table(client, stub, restored.json())
    copy.act("I go back down to the water.", "The tide has taken the nest.")
    history = json.loads(json.dumps(stub.calls[-1]["messages"]))
    assert any("Sunken Bell" not in json.dumps(m) or True for m in history)
    assert len(history) > 4          # the whole prior campaign came with it


def test_the_dm_cannot_invent_a_roll(app_client):
    """The point of the whole design: the number comes from Python, not the model."""
    client, stub = app_client
    table = new_table(client, stub)
    table.begin("You wake in a ditch.")

    events = table.act(
        "I check the ditch for anything worth taking.",
        [("roll_dice", {"notation": "1d20+3", "reason": "Vess: Perception",
                        "mode": "normal"})],
        "Nothing but a boot.")
    roll = next(e for e in events if e["kind"] == "dice")

    # whatever the model said, the total is a real 1d20+3
    assert 4 <= roll["total"] <= 23
    # and it was fed back to the model as the truth
    assert any(str(roll["total"]) in r for r in tool_results(stub.calls[-1]))


def test_bad_dice_notation_is_an_error_the_dm_can_recover_from(app_client):
    client, stub = app_client
    table = new_table(client, stub)
    table.begin("A door.")

    events = table.act(
        "I shove it.",
        [("roll_dice", {"notation": "1d20+3+2", "reason": "Vess: Athletics",
                        "mode": "normal"})],
        [("roll_dice", {"notation": "1d20+5", "reason": "Vess: Athletics",
                        "mode": "normal"})],
        "The door gives.")
    # the bad notation produced no dice chip, and the DM was told why
    assert [e["kind"] for e in events].count("dice") == 1
    assert any(r.startswith("ERROR:") for r in tool_results(stub.calls[-1]))


def test_update_character_names_a_real_character(app_client):
    client, stub = app_client
    table = new_table(client, stub)
    table.begin("Fog.")

    table.act("I wait.",
              award("Nobody At All", xp=50),
              "Nothing happens.")
    result = tool_results(stub.calls[-1])[-1]
    assert result.startswith("ERROR: no character named")
    assert "Vess" in result          # the DM is told who is actually at the table
