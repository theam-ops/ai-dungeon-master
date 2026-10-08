"""Items as records, encumbrance, and spell slots.

Inventory used to be a list of display strings in the campaign's language: a Thai
fighter carried "เกราะโซ่", not "chain mail", and five rations were the string
"rations (5)" - so eating one meant removing that string and adding "rations (4)".
Now items are records with a rules key and a real count, the strings are derived from
them, and the two things built on top - what a load weighs, and what a caster has left
to cast - are worked out in Python, like every other number in this game.
"""

import asyncio
import json

import pytest

import server
from game import dm, rules, store
from .harness import new_table, prompt_of, tool_results


def make(klass="Fighter", lang="en", **over):
    scores = {"STR": 15, "DEX": 12, "CON": 12, "INT": 10, "WIS": 10, "CHA": 10, **over}
    return rules.new_character("Test", "Human", klass, scores=scores, lang=lang)


def items(ch):
    return {i["name"]: (i["key"], i["qty"]) for i in ch["items"]}


# --------------------------------------------------------------------------- #
# counts and names
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("text, expected", [
    ("rations (5)", ("rations", 5)),
    ("arrows x20", ("arrows", 20)),
    ("arrows ×20", ("arrows", 20)),
    ("torch", ("torch", 1)),
    ("  rusty   key ", ("rusty key", 1)),
    ("เสบียง (5)", ("เสบียง", 5)),
])
def test_counts_are_read_off_the_name(text, expected):
    assert rules.split_qty(text) == expected


def test_the_starting_kit_becomes_records_with_real_counts():
    ch = make("Fighter")
    assert items(ch)["rations"] == ("rations", 5)
    assert items(ch)["chain mail"] == ("chain mail", 1)
    assert "rations (5)" in ch["inventory"]                # the strings still read the same


def test_a_thai_kit_is_the_same_items_under_thai_names():
    th, en = make("Fighter", lang="th"), make("Fighter")
    assert sorted((k, q) for k, q in items(th).values()) == \
           sorted((k, q) for k, q in items(en).values())
    assert items(th)["เสบียง"] == ("rations", 5)
    assert rules.encumbrance(th)["carried"] == rules.encumbrance(en)["carried"]


def test_picking_up_more_of_something_adds_to_the_count():
    ch = make()
    rules.add_item(ch, "rations (3)")
    rules.add_item(ch, "torch")
    assert items(ch)["rations"][1] == 8
    assert items(ch)["torch"][1] == 2
    assert "torch (2)" in ch["inventory"]


def test_the_dm_can_name_an_item_in_english_on_a_thai_sheet():
    """The model will say 'torch' however the sheet spells it."""
    ch = make(lang="th")
    name, qty = rules.add_item(ch, "torch")
    assert name == "คบไฟ" and items(ch)["คบไฟ"][1] == 2


def test_using_things_up():
    ch = make()
    assert rules.remove_item(ch, "rations") == ("rations", 1, 4)
    assert rules.remove_item(ch, "rations (2)") == ("rations", 2, 2)
    assert rules.remove_item(ch, "rations (9)") == ("rations", 2, 0)   # all there was
    assert "rations" not in items(ch)
    assert rules.remove_item(ch, "a dragon egg") == (None, 0, 0)


def test_an_item_the_rules_do_not_know_is_carried_but_unweighed():
    ch = make()
    before = rules.encumbrance(ch)
    rules.add_item(ch, "Aria's letter")
    after = rules.encumbrance(ch)
    assert items(ch)["Aria's letter"] == (None, 1)
    assert after["carried"] == before["carried"]
    assert after["unweighed"] == before["unweighed"] + 1


# --------------------------------------------------------------------------- #
# old saves
# --------------------------------------------------------------------------- #

def legacy(klass="Fighter", lang="en", extra=()):
    """A sheet as saved before items existed: display strings, nothing else."""
    ch = make(klass, lang=lang)
    for key in ("items", "equipment", "effects", "ac_parts"):
        ch.pop(key, None)
    ch["inventory"] = list(ch["inventory"]) + list(extra)
    ch["ac"] = 13
    return ch


def test_an_old_inventory_becomes_records_without_losing_anything():
    ch = legacy(lang="th", extra=["กุญแจขึ้นสนิม", "torch"])
    rules.ensure_items(ch)
    got = items(ch)
    assert got["เสบียง"] == ("rations", 5)
    assert got["กุญแจขึ้นสนิม"] == (None, 1)                # unrecognised, but kept
    assert got["torch"] == ("torch", 1)                    # its own spelling, its own record
    assert "เกราะโซ่" in got


def test_migration_runs_once_and_never_rebuilds():
    ch = legacy()
    rules.ensure_items(ch)
    rules.remove_item(ch, "rations (5)")
    rules.ensure_items(ch)                                 # must not resurrect them
    assert "rations" not in items(ch)


def test_an_old_thai_fighter_still_wears_their_armour(app_client):
    client, stub = app_client
    table = new_table(client, stub, lang="th",
                      character={"name": "ปรางค์", "race": "Human", "class": "Fighter"})
    blob = legacy(lang="th")
    blob["name"] = "ปรางค์"
    store.db().execute("UPDATE characters SET data=? WHERE campaign_id=?",
                       (json.dumps(blob, ensure_ascii=False), table.id))
    store.db().commit()
    me = table.character("ปรางค์")
    assert me["ac"] == 18
    assert me["equipment"]["armor"] == "เกราะโซ่"
    assert {i["key"] for i in me["items"]} >= {"chain mail", "shield", "rations"}


# --------------------------------------------------------------------------- #
# encumbrance
# --------------------------------------------------------------------------- #

def test_the_standard_rule_is_only_a_carrying_limit():
    ch = make(STR=12)                                      # 139 lb against 180
    assert rules.encumbrance(ch)["status"] is None
    rules.add_item(ch, "plate armor")                      # +65 = 204
    e = rules.encumbrance(ch)
    assert (e["carried"], e["capacity"], e["status"]) == (204, 180, "over capacity")


@pytest.mark.parametrize("strength, status, penalty", [
    (30, None, 0),                       # 139 under 150
    (15, "encumbered", 10),              # 139 over 75
    (12, "heavily encumbered", 20),      # 139 over 120
])
def test_the_variant_rule(strength, status, penalty):
    e = rules.encumbrance(make(STR=strength), variant=True)
    assert (e["status"], e["speed_penalty"]) == (status, penalty)


# --------------------------------------------------------------------------- #
# spell slots
# --------------------------------------------------------------------------- #

def at_level(klass, level):
    ch = make(klass)
    ch["level"] = level
    return ch


@pytest.mark.parametrize("klass, level, slots", [
    ("Wizard", 1, {1: 2}),
    ("Wizard", 4, {1: 4, 2: 3}),
    ("Cleric", 5, {1: 4, 2: 3, 3: 2}),
    ("Bard", 20, {1: 4, 2: 3, 3: 3, 4: 3, 5: 3, 6: 2, 7: 2, 8: 1, 9: 1}),
    ("Ranger", 1, {}),                   # SRD 5.1: nothing at level 1
    ("Ranger", 2, {1: 2}),
    ("Ranger", 9, {1: 4, 2: 3, 3: 2}),
    ("Fighter", 10, {}),
])
def test_slot_tables(klass, level, slots):
    assert rules.slot_max(at_level(klass, level)) == slots


def test_spending_until_there_is_nothing_left():
    ch = at_level("Wizard", 3)                             # 1st: 4, 2nd: 2
    for _ in range(2):
        assert rules.use_slot(ch, 2)[0]
    ok, message = rules.use_slot(ch, 2)
    assert not ok and "0 of 2" in message
    assert rules.slots_left(ch) == {1: (4, 4), 2: (0, 2)}

    ok, message = rules.use_slot(ch, 1)                    # lower levels are untouched
    assert ok and "3 of 4 left" in message


def test_a_refusal_says_what_could_still_be_done():
    ch = at_level("Wizard", 3)
    for _ in range(4):
        rules.use_slot(ch, 1)
    ok, message = rules.use_slot(ch, 1)
    assert not ok and "higher slot: 2nd" in message


@pytest.mark.parametrize("klass, level, why", [
    ("Fighter", 5, "no spell slots"),
    ("Wizard", 1, "no 3rd-level slots"),
    ("Wizard", 1, "cantrips need no slot"),
])
def test_slots_that_do_not_exist(klass, level, why):
    spell_level = {"no 3rd-level slots": 3, "cantrips need no slot": 0}.get(why, 1)
    ok, message = rules.use_slot(at_level(klass, level), spell_level)
    assert not ok and why in message


def test_levelling_up_raises_the_ceiling_and_keeps_what_was_spent():
    ch = at_level("Wizard", 1)
    rules.use_slot(ch, 1)
    ch["level"] = 3
    assert rules.slots_left(ch)[1] == (3, 4)


def test_a_long_rest_restores_hit_points_and_slots():
    ch = at_level("Wizard", 3)
    ch["hp"] = 1
    rules.use_slot(ch, 1)
    rules.use_slot(ch, 2)
    done = rules.long_rest(ch)
    assert ch["hp"] == ch["max_hp"] and done["slots_restored"] == 2
    assert rules.slots_left(ch) == {1: (4, 4), 2: (2, 2)}


# --------------------------------------------------------------------------- #
# what the DM is told, and its tools
# --------------------------------------------------------------------------- #

def test_the_dm_sees_load_and_slots():
    wizard, fighter = at_level("Wizard", 3), make("Fighter", STR=12)
    wizard["name"], fighter["name"] = "Ilse", "Bram"
    rules.use_slot(wizard, 1)
    state = {c["name"]: c for c in json.loads(rules.state_block([wizard, fighter]))}
    assert state["Ilse"]["spell_slots"] == "1st 3/4, 2nd 2/2"
    assert "spell_slots" not in state["Bram"]
    assert state["Bram"]["carrying"] == "139 of 180 lb"

    house = {"variant_encumbrance": True}
    state = {c["name"]: c for c in json.loads(rules.state_block([wizard, fighter], "en", house))}
    assert "HEAVILY ENCUMBERED" in state["Bram"]["carrying"]


def run(name, args, characters):
    return asyncio.run(dm.run_tool(name, args, characters))


def strike(**over):
    base = {"character_name": "Test", "hp_change": 0, "xp_gain": 0, "gold_change": 0,
            "add_items": [], "remove_items": [], "add_conditions": [],
            "remove_conditions": [], "level_up": False, "reason": "x"}
    return {**base, **over}


def test_update_character_counts_down_and_says_so():
    ch = make()
    result, event = run("update_character", strike(remove_items=["rations (2)"]), [ch])
    assert "- rations (2), 3 left" in result
    assert {"t": "item-", "item": "rations (2)", "left": 3} in event["changes"]
    result, _ = run("update_character", strike(remove_items=["rations (9)"]), [ch])
    assert "only had 3" in result and "none left" in result


def test_use_spell_slot_spends_and_refuses():
    ch = at_level("Wizard", 1)
    args = {"character_name": "Test", "level": 1, "spell": "Magic Missile"}
    result, event = run("use_spell_slot", args, [ch])
    assert "1 of 2 left" in result
    assert event["changes"] == [{"t": "slot", "level": 1, "left": 1, "max": 2,
                                 "spell": "Magic Missile"}]
    run("use_spell_slot", args, [ch])
    result, event = run("use_spell_slot", args, [ch])
    assert result.startswith("REFUSED") and "not cast" in result
    assert event["changes"][0]["t"] == "slot-none"         # the table sees why, too


def test_a_long_rest_for_the_party_ends_timed_effects_only():
    a, b = at_level("Wizard", 3), at_level("Cleric", 3)
    a["name"], b["name"] = "Ilse", "Bram"
    for ch in (a, b):
        ch["hp"] = 1
        rules.use_slot(ch, 1)
    rules.set_effect(b, "Shield of Faith", ac_bonus=2, turns=10)
    rules.set_effect(b, "Curse of the Drowned", ac_bonus=-1)
    result, event = run("long_rest", {"character_names": [], "reason": "camp"}, [a, b])
    assert all(c["hp"] == c["max_hp"] for c in (a, b))
    assert all(rules.slots_left(c)[1] == (4, 4) for c in (a, b))
    assert [fx["name"] for fx in b["effects"]] == ["Curse of the Drowned"]
    assert event["character"] == "Ilse, Bram" and event["changes"] == [{"t": "rest"}]


# --------------------------------------------------------------------------- #
# whole turns
# --------------------------------------------------------------------------- #

def party_state(call):
    return json.loads(prompt_of(call).split("<party_state>")[1].split("</party_state>")[0])


def test_a_wizard_runs_dry_mid_fight(app_client):
    client, stub = app_client
    table = new_table(client, stub, character={"name": "Ilse", "race": "Human",
                                               "class": "Wizard"})
    table.begin("Goblins pour out of the culvert.")
    cast = [("use_spell_slot", {"character_name": "Ilse", "level": 1,
                                "spell": "Magic Missile"})]
    table.act("Magic missile!", cast, "Three darts of light.")
    table.act("Again!", cast, "Three more.")
    events = table.act("Once more!", cast, "Nothing comes.")

    refused = [c for e in events if e["kind"] == "sheet" for c in e["changes"]
               if c["t"] == "slot-none"]
    assert refused == [{"t": "slot-none", "level": 1, "spell": "Magic Missile"}]
    assert any(r.startswith("REFUSED") for r in tool_results(stub.calls[-1]))
    assert table.character("Ilse")["slots"] == {"1": [0, 2]}

    table.act("I catch my breath.", "The goblins regroup.")
    assert party_state(stub.calls[-1])[0]["spell_slots"] == "1st 0/2"


def test_turning_on_variant_encumbrance_reaches_the_dm(app_client):
    client, stub = app_client
    table = new_table(client, stub, character={"name": "Bram", "race": "Dwarf",
                                               "class": "Fighter"})
    blob = make("Fighter", STR=12)
    blob["name"] = "Bram"
    store.db().execute("UPDATE characters SET data=? WHERE campaign_id=?",
                       (json.dumps(blob), table.id))
    store.db().commit()
    table.begin("A long road.")
    assert table.character("Bram")["load"]["status"] is None

    r = client.post(f"/api/campaigns/{table.id}/house", json={"variant_encumbrance": True})
    assert r.json()["house"] == {**rules.HOUSE_RULES, "variant_encumbrance": True}
    assert table.character("Bram")["load"]["status"] == "heavily encumbered"
    assert any(e["kind"] == "house" for e in table.events())

    table.act("I trudge on.", "The road climbs.")
    assert "HEAVILY ENCUMBERED" in party_state(stub.calls[-1])[0]["carrying"]


def test_unknown_house_rules_are_dropped(app_client):
    client, stub = app_client
    table = new_table(client, stub)
    r = client.post(f"/api/campaigns/{table.id}/house",
                    json={"variant_encumbrance": "yes", "infinite_gold": True})
    assert r.json()["house"] == {**rules.HOUSE_RULES, "variant_encumbrance": True}


def test_items_and_house_rules_survive_export_and_import(app_client):
    client, stub = app_client
    table = new_table(client, stub, character={"name": "Ilse", "race": "Human",
                                               "class": "Wizard"})
    client.post(f"/api/campaigns/{table.id}/house", json={"variant_encumbrance": True})
    table.begin("A tower.")
    table.act("I take the arrows.", [("update_character", {
        "character_name": "Ilse", "hp_change": 0, "xp_gain": 0, "gold_change": 0,
        "add_items": ["arrows (20)"], "remove_items": ["rations (2)"],
        "add_conditions": [], "remove_conditions": [], "level_up": False,
        "reason": "loot"})], "Done.")

    blob = client.get(f"/api/campaigns/{table.id}/export").json()
    back = asyncio.run(server.A.repo.import_campaign(blob))
    cid = back if isinstance(back, str) else back["id"]
    ilse = asyncio.run(server.A.repo.party(cid))[0]
    assert {i["name"]: i["qty"] for i in ilse["items"]}["arrows"] == 20
    assert {i["name"]: i["qty"] for i in ilse["items"]}["rations"] == 3
    assert asyncio.run(server.A.repo.campaign_house(cid)) == {**rules.HOUSE_RULES, "variant_encumbrance": True}
