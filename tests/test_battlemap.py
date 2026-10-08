"""The battle map: drawn by the DM, walked by the players, hidden by the fog.

The one promise everything here leans on: nothing the party has not seen leaves the
server. Every event, every response a browser gets carries `public_view`, never the map.
"""

import asyncio
import json

import pytest

import server
from game import battlemap as bm
from game import dm, rules
from .harness import new_table, prompt_of, second_browser, tool_results, join


def room_map():
    """A 12x8 dungeon: a room on the left, a door, a corridor, a room on the right."""
    m = bm.new_map(12, 8, "wall")
    m, notes = bm.apply(m, {"paint": [
        {"terrain": "room", "x1": 0, "y1": 0, "x2": 5, "y2": 7},
        {"terrain": "room", "x1": 7, "y1": 0, "x2": 11, "y2": 7},
        {"terrain": "door", "x1": 5, "y1": 3, "x2": 5, "y2": 3},
        {"terrain": "floor", "x1": 6, "y1": 3, "x2": 6, "y2": 3},
        {"terrain": "door", "x1": 7, "y1": 3, "x2": 7, "y2": 3}],
        "tokens": [{"name": "Vess", "kind": "pc", "x": 2, "y": 3},
                   {"name": "Ghoul", "kind": "npc", "x": 9, "y": 4}]}, ["Vess"])
    assert notes == []
    return m


# --------------------------------------------------------------------------- #
# the rules of the grid
# --------------------------------------------------------------------------- #

def test_a_room_is_walls_round_a_floor():
    m = room_map()
    assert bm.cell(m, 0, 0) == "#" and bm.cell(m, 2, 2) == "." and bm.cell(m, 5, 3) == "+"


def test_the_party_sees_its_own_room_and_not_through_the_door():
    m = room_map()
    assert bm.seen(m, 1, 1) and bm.seen(m, 4, 6)               # all of Vess's room
    assert bm.seen(m, 5, 3)                                     # the door itself
    assert not bm.seen(m, 6, 3) and not bm.seen(m, 9, 4)        # nothing behind it


def test_the_fog_never_leaves_the_server():
    view = bm.public_view(room_map())
    assert [t["name"] for t in view["tokens"]] == ["Vess"]      # the ghoul is in the dark
    assert view["cells"][4 * 12 + 9] == bm.FOG
    assert "Ghoul" not in json.dumps(view)


def test_revealing_shows_terrain_and_whoever_stands_there():
    m, _ = bm.apply(room_map(), {"reveal": [{"x1": 7, "y1": 0, "x2": 11, "y2": 7}]}, ["Vess"])
    view = bm.public_view(m)
    assert {t["name"] for t in view["tokens"]} == {"Vess", "Ghoul"}
    assert view["cells"][4 * 12 + 9] == "."


def test_a_player_walks_only_where_the_party_can_walk():
    m = room_map()
    assert bm.move(m, "Vess", 9, 4)[1] == "unseen"
    assert bm.move(m, "Vess", 0, 0)[1] == "wall"
    assert bm.move(m, "Vess", 40, 1)[1] == "off_map"
    assert bm.move(m, "Bram", 3, 3)[1] == "not_placed"
    assert bm.move(None, "Vess", 3, 3)[1] == "no_map"
    moved, why = bm.move(m, "Vess", 4, 3)
    assert why is None and bm.token(moved, "Vess")["x"] == 4


def test_dragging_is_walking_not_blinking_through_a_wall():
    m, _ = bm.apply(room_map(), {"reveal_all": True}, ["Vess"])
    m, _ = bm.apply(m, {"paint": [{"terrain": "wall", "x1": 5, "y1": 3, "x2": 5, "y2": 3}]},
                    ["Vess"])                                   # the door is bricked up
    assert bm.move(m, "Vess", 9, 2)[1] == "no_path"


def test_walking_through_the_door_reveals_what_is_beyond():
    m = room_map()
    m, _ = bm.move(m, "Vess", 4, 3)
    m, _ = bm.move(m, "Vess", 5, 3)                             # into the doorway
    assert bm.seen(m, 6, 3) and bm.seen(m, 7, 3)                # the corridor, the far door
    m, _ = bm.move(m, "Vess", 6, 3)
    m, _ = bm.move(m, "Vess", 7, 3)
    assert "Ghoul" in json.dumps(bm.public_view(m))


def test_two_tokens_do_not_share_a_square():
    m, _ = bm.apply(room_map(), {"reveal_all": True,
                                 "tokens": [{"name": "Rat", "kind": "npc", "x": 3, "y": 3}]},
                    ["Vess"])
    assert bm.move(m, "Vess", 3, 3)[1] == "occupied"


def test_the_dms_mistakes_are_reported_and_the_rest_still_happens():
    m, notes = bm.apply(room_map(), {
        "paint": [{"terrain": "lava", "x1": 1, "y1": 1, "x2": 2, "y2": 2},
                  {"terrain": "wall", "x1": 50, "y1": 50, "x2": 60, "y2": 60}],
        "tokens": [{"name": "Nobody", "kind": "pc", "x": 2, "y": 2},
                   {"name": "Bat", "kind": "npc", "x": 0, "y": 0},
                   {"name": "Imp", "kind": "npc", "x": 99, "y": 1},
                   {"name": "Ghoul", "kind": "npc", "x": 8, "y": 1}],
        "remove_tokens": []}, ["Vess"])
    assert len(notes) == 5
    assert bm.token(m, "Ghoul")["x"] == 8                       # the good part landed


def test_a_new_map_is_clamped_and_unseen():
    m = bm.new_map(500, 1)
    assert (m["w"], m["h"]) == (bm.MAX_SIDE, bm.MIN_SIDE)
    assert set(bm.public_view(m)["cells"]) == {bm.FOG}


def test_there_must_be_a_map_before_it_can_be_changed():
    m, notes = bm.apply(None, {"tokens": [{"name": "Vess", "kind": "pc", "x": 1, "y": 1}]},
                        ["Vess"])
    assert m is None and notes[0].startswith("ERROR")


def test_the_dm_reads_coordinates_and_the_fog():
    text = bm.describe(room_map())
    assert "A = Vess (PC) at 2,3" in text
    assert "B = Ghoul (NPC) at 9,4, unseen by the party" in text
    assert "o seen, - not yet" in text


def test_a_mangled_map_is_dropped_not_trusted():
    assert bm.clean({"w": 3, "h": 3, "cells": "x" * 9}) is None
    assert bm.clean({"w": 99, "h": 3, "cells": "." * 297}) is None
    assert bm.clean("a map") is None
    m = bm.clean({"w": 3, "h": 3, "cells": "." * 9, "fog": "not base64!",
                  "tokens": [{"name": "A", "x": 1, "y": 1}, {"name": "B", "x": 9, "y": 9},
                             "junk", {"name": "", "x": 0, "y": 0}]})
    assert [t["name"] for t in m["tokens"]] == ["A"]


# --------------------------------------------------------------------------- #
# at the table
# --------------------------------------------------------------------------- #

def draw(*, tokens=(), reveal_all=False, **extra):
    return ("update_map", {"clear_map": False, "new_width": 12, "new_height": 8,
                           "fill": "wall", "paint": room_map_paint(), "tokens": list(tokens),
                           "remove_tokens": [], "reveal": [], "reveal_all": reveal_all,
                           **extra})


def room_map_paint():
    return [{"terrain": "room", "x1": 0, "y1": 0, "x2": 5, "y2": 7},
            {"terrain": "room", "x1": 7, "y1": 0, "x2": 11, "y2": 7},
            {"terrain": "door", "x1": 5, "y1": 3, "x2": 5, "y2": 3},
            {"terrain": "floor", "x1": 6, "y1": 3, "x2": 6, "y2": 3},
            {"terrain": "door", "x1": 7, "y1": 3, "x2": 7, "y2": 3}]


VESS = {"name": "Vess", "kind": "pc", "x": 2, "y": 3}
GHOUL = {"name": "Ghoul", "kind": "npc", "x": 9, "y": 4}


def mapped_table(app_client):
    client, stub = app_client
    table = new_table(client, stub)
    client.post(f"/api/campaigns/{table.id}/house", json={"battle_map": True})
    table.begin("The crypt door grinds open.")
    return client, stub, table


def tool_names(call):
    return {t["name"] for t in call["tools"]}


def test_without_the_table_rule_there_is_no_map_at_all(app_client):
    client, stub = app_client
    table = new_table(client, stub)
    table.begin("A road.")
    assert "update_map" not in tool_names(stub.calls[-1])
    assert "THE BATTLE MAP" not in json.dumps(stub.calls[-1]["system"])
    events = table.act("I look around.", [draw(tokens=[VESS])], "Fields.")
    assert "does not play with a map" in tool_results(stub.calls[-1])[-1]
    assert not [e for e in events if e["kind"] == "map"]
    r = client.post(f"/api/campaigns/{table.id}/map/move", json={"x": 1, "y": 1})
    assert r.status_code == 400


def test_the_dm_draws_and_the_table_sees_only_what_the_party_sees(app_client):
    client, stub, table = mapped_table(app_client)
    assert "update_map" in tool_names(stub.calls[-1])
    events = table.act("We go in.", [draw(tokens=[VESS, GHOUL])], "Dust and dark.")
    shown = [e for e in events if e["kind"] == "map"]
    assert len(shown) == 1
    assert [t["name"] for t in shown[0]["map"]["tokens"]] == ["Vess"]
    # not in the event, not anywhere in the log, not in the campaign detail
    assert "Ghoul" not in json.dumps(table.events())
    assert "Ghoul" not in client.get(f"/api/campaigns/{table.id}").text
    # but the DM knows where it is: in what the tool told it, and in the next prompt
    assert "Ghoul (NPC) at 9,4, unseen by the party" in tool_results(stub.calls[-1])[-1]
    table.act("I listen.", "Something shuffles.")
    assert "<map>" in prompt_of(stub.calls[-1]) and "Ghoul" in prompt_of(stub.calls[-1])


def test_a_player_drags_their_own_token_and_it_reveals_the_way(app_client):
    client, stub, table = mapped_table(app_client)
    table.act("In.", [draw(tokens=[VESS, GHOUL])], "Dark.")
    mark = table.last_seq()
    for x in (3, 4, 5, 6, 7):
        r = client.post(f"/api/campaigns/{table.id}/map/move", json={"x": x, "y": 3})
        assert r.status_code == 200, r.text
    last = [e for e in table.events(mark) if e["kind"] == "map"][-1]
    assert last["moved"] == "Vess"
    assert "Ghoul" in [t["name"] for t in last["map"]["tokens"]]    # seen at last


def test_a_player_cannot_move_somebody_else(app_client):
    """The request has no name in it: the token moved is always the caller's own."""
    client, stub, table = mapped_table(app_client)
    other = second_browser(server.app)
    try:
        join(other, table.code, {"name": "Bram", "race": "Dwarf", "class": "Cleric"})
        table.act("In.", [draw(tokens=[VESS, {"name": "Bram", "kind": "pc", "x": 3, "y": 5}])],
                  "Dark.")
        r = other.post(f"/api/campaigns/{table.id}/map/move",
                       json={"x": 1, "y": 1, "name": "Vess"})
        assert r.status_code == 200
        m = asyncio.run(server.A.repo.get_map(table.id))
        assert (bm.token(m, "Bram")["x"], bm.token(m, "Vess")["x"]) == (1, 2)
    finally:
        other.__exit__(None, None, None)


def test_a_refused_move_says_why_and_changes_nothing(app_client):
    client, stub, table = mapped_table(app_client)
    table.act("In.", [draw(tokens=[VESS, GHOUL])], "Dark.")
    before = asyncio.run(server.A.repo.get_map(table.id))
    r = client.post(f"/api/campaigns/{table.id}/map/move", json={"x": 9, "y": 4})
    assert r.status_code == 400
    assert r.json()["detail"] == {"code": "unseen", "text": "Nobody has seen that far yet."}
    assert asyncio.run(server.A.repo.get_map(table.id)) == before


def test_a_map_survives_export_and_import(app_client):
    client, stub, table = mapped_table(app_client)
    table.act("In.", [draw(tokens=[VESS, GHOUL])], "Dark.")
    blob = client.get(f"/api/campaigns/{table.id}/export").json()
    back = asyncio.run(server.A.repo.import_campaign(blob))
    assert asyncio.run(server.A.repo.get_map(back["id"])) == \
        asyncio.run(server.A.repo.get_map(table.id))


def test_putting_the_map_away(app_client):
    client, stub, table = mapped_table(app_client)
    table.act("In.", [draw(tokens=[VESS])], "Dark.")
    events = table.act("Out.", [("update_map", {**draw()[1], "clear_map": True})], "Daylight.")
    assert [e["map"] for e in events if e["kind"] == "map"] == [None]
    assert asyncio.run(server.A.repo.get_map(table.id)) is None


def test_a_drag_and_the_dm_redrawing_never_undo_each_other(app_client):
    """Both write the same map. With a read and a write that could interleave, the
    second would quietly put back what the first had changed."""
    client, stub, table = mapped_table(app_client)
    table.act("In.", [draw(tokens=[VESS, GHOUL], reveal_all=True)], "Dark.")

    async def both():
        drag = server.A.repo.change_map(table.id, lambda m: bm.move(m, "Vess", 3, 3)[0])
        dm_move = server.A.repo.change_map(
            table.id, lambda m: bm.apply(m, {"tokens": [{**GHOUL, "x": 8}]}, ["Vess"])[0])
        await asyncio.gather(drag, dm_move)
        return await server.A.repo.get_map(table.id)
    m = asyncio.run(both())
    assert (bm.token(m, "Vess")["x"], bm.token(m, "Ghoul")["x"]) == (3, 8)


# --------------------------------------------------------------------------- #
# the ambience
# --------------------------------------------------------------------------- #

def test_the_dm_sets_the_mood_only_when_the_table_wants_sound(app_client):
    client, stub = app_client
    table = new_table(client, stub)
    table.begin("A road.")
    assert "set_ambience" not in tool_names(stub.calls[-1])
    client.post(f"/api/campaigns/{table.id}/house", json={"ambience": True})
    events = table.act("Into the inn.", [("set_ambience", {"mood": "tavern"})], "Warmth.")
    assert "set_ambience" in tool_names(stub.calls[-2])
    assert "AMBIENCE" in json.dumps(stub.calls[-2]["system"])
    assert [e["mood"] for e in events if e["kind"] == "ambience"] == ["tavern"]


def test_a_mood_that_does_not_exist_is_refused():
    text, event = asyncio.run(dm.run_tool("set_ambience", {"mood": "disco"}, [], cid="c1",
                                          house={"ambience": True}))
    assert text.startswith("ERROR: mood") and event is None


def test_without_the_table_rule_the_dm_cannot_set_a_mood(app_client):
    """The tool is not offered then, but a model may reach for one it saw earlier in
    the story. Found in the browser: an opening scene that said 'no tavern' did."""
    client, stub = app_client
    table = new_table(client, stub)
    events = table.begin([("set_ambience", {"mood": "tavern"})], "A road.")
    assert "does not play with background sound" in tool_results(stub.calls[-1])[-1]
    assert not [e for e in events if e["kind"] == "ambience"]


def test_the_table_rules_default_off():
    assert rules.clean_house({}) == {"variant_encumbrance": False, "battle_map": False,
                                     "ambience": False}


# --------------------------------------------------------------------------- #
# what the server and the browser must agree on
# --------------------------------------------------------------------------- #

def _js(path):
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "static", "js", path), encoding="utf-8") as f:
        return f.read()


@pytest.mark.parametrize("lang", ["en", "th"])
def test_every_refused_move_reads_in_both_languages(lang):
    strings = _js(f"i18n/{lang}.js")
    missing = [code for code in bm.REFUSALS if f"map_{code}:" not in strings]
    assert not missing, f"{lang}.js has no string for {missing}"


def test_every_mood_the_dm_can_pick_is_one_the_browser_can_play():
    import re
    sound = _js("sound.js")
    block = sound[sound.index("const MOODS = {"):]
    playable = set(re.findall(r"^  (\w+):", block, re.M))
    assert playable == set(dm.MOODS)


def test_turning_the_map_off_stops_tokens_moving(app_client):
    """The map stays stored - turn the rule back on and it is where it was - but while
    the table has it off, nobody moves on it. Without a map at all a move is refused
    anyway, which is why this needs a map first to mean anything."""
    client, stub, table = mapped_table(app_client)
    table.act("In.", [draw(tokens=[VESS])], "Dark.")
    client.post(f"/api/campaigns/{table.id}/house", json={"battle_map": False})
    r = client.post(f"/api/campaigns/{table.id}/map/move", json={"x": 3, "y": 3})
    assert r.status_code == 400 and "not playing with a map" in r.json()["detail"]
    client.post(f"/api/campaigns/{table.id}/house", json={"battle_map": True})
    r = client.post(f"/api/campaigns/{table.id}/map/move", json={"x": 3, "y": 3})
    assert r.status_code == 200
