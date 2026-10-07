"""Armour class: derived from what a character wears, never written once and frozen.

AC used to be `12 + max(0, DEX mod)`, set at creation and never touched again. Armour
did nothing, shields did nothing, spells did nothing - and the 12 and the clamp were
both wrong besides. These tests hold the rules to the 5e SRD, and the last few drive
whole turns through the stub DM to check the number that actually reaches the model.
"""

import asyncio
import json

import pytest

from game import dm, rules, store
from .harness import new_table, prompt_of, tool_results


def scores(dex=10, **over):
    base = {"STR": 14, "DEX": dex, "CON": 12, "INT": 10, "WIS": 10, "CHA": 10}
    base.update(over)
    return base


def make(klass="Wizard", dex=10, lang="en", **over):
    return rules.new_character("Test", "Human", klass, scores=scores(dex, **over), lang=lang)


def wearing(klass, armour, dex, shield=False):
    """A character of `klass` wearing exactly `armour` (None for nothing)."""
    ch = make(klass, dex)
    ch["items"] = []
    for item in ([armour] if armour else []) + (["shield"] if shield else []):
        rules.add_item(ch, item)
    ch["equipment"] = {"armor": armour, "shield": "shield" if shield else None}
    rules.recompute_ac(ch)
    return ch


# --------------------------------------------------------------------------- #
# the arithmetic
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("armour, dex, expected", [
    (None, 10, 10),                    # unarmoured: 10 + DEX
    (None, 8, 9),                      # a DEX penalty counts - the old clamp did not
    (None, 18, 14),
    ("leather armor", 16, 14),         # light: all of DEX
    ("leather armor", 8, 10),
    ("chain shirt", 18, 15),           # medium: DEX up to +2...
    ("chain shirt", 8, 12),            # ...and a penalty still applies
    ("half plate armor", 14, 17),
    ("chain mail", 18, 16),            # heavy: DEX not at all
    ("chain mail", 8, 16),             # - not even a penalty
    ("plate armor", 10, 18),
])
def test_armour_and_dex(armour, dex, expected):
    assert wearing("Fighter", armour, dex)["ac"] == expected


def test_a_shield_stacks_with_armour():
    assert wearing("Fighter", "chain mail", 10, shield=True)["ac"] == 18


def test_magic_armour_adds_its_bonus():
    ch = wearing("Fighter", "+1 chain mail", 10, shield=True)
    assert ch["ac"] == 19
    assert "+1 chain mail 17" in rules.ac_summary(ch)


@pytest.mark.parametrize("klass, expected", [
    # the classes as they come out of creation, with DEX 12 (+1)
    ("Fighter", 18),                   # chain mail 16, shield 2, heavy ignores DEX
    ("Cleric", 16),                    # chain shirt 13, DEX +1, shield 2
    ("Rogue", 12),                     # leather 11, DEX +1
    ("Ranger", 12),
    ("Bard", 12),
    ("Wizard", 11),                    # nothing: 10 + 1
])
def test_new_characters_start_in_their_armour(klass, expected):
    assert make(klass, dex=12)["ac"] == expected


@pytest.mark.parametrize("klass", list(rules.CLASSES))
def test_a_thai_character_is_armoured_the_same(klass):
    """Starting gear is written in the campaign's language - 'เกราะโซ่', not
    'chain mail' - so the armour has to be recognised by its translated name."""
    assert make(klass, dex=12, lang="th")["ac"] == make(klass, dex=12)["ac"]


def test_unrecognised_armour_changes_nothing():
    """'Dwarven chain mail' is something the DM made up. Guessing a number for it would
    put mechanics on the sheet that nobody decided."""
    assert rules.armour_piece("dwarven chain mail") is None
    assert rules.armour_piece("torch") is None
    assert rules.armour_piece("Plate Armour") == ("armor", "plate armor", 0)
    assert rules.armour_piece("chain mail +2") == ("armor", "chain mail", 2)


# --------------------------------------------------------------------------- #
# effects
# --------------------------------------------------------------------------- #

def test_a_bonus_effect_adds():
    ch = make("Cleric", dex=12)
    rules.set_effect(ch, "Shield of Faith", ac_bonus=2)
    assert rules.recompute_ac(ch) == (16, 18)


def test_mage_armor_replaces_the_unarmoured_base_only():
    wizard = make("Wizard", dex=14)                       # 10 + 2
    rules.set_effect(wizard, "Mage Armor", ac_base=13)
    assert rules.recompute_ac(wizard)[1] == 15            # 13 + 2

    knight = make("Fighter", dex=14)                      # in chain mail
    rules.set_effect(knight, "Mage Armor", ac_base=13)
    assert rules.recompute_ac(knight)[1] == 18            # armour wins; spell does nothing


def test_a_floor_effect_only_ever_raises():
    low = make("Wizard", dex=10)
    rules.set_effect(low, "Barkskin", ac_min=16)
    assert rules.recompute_ac(low)[1] == 16

    high = make("Fighter", dex=10)                        # already 18
    rules.set_effect(high, "Barkskin", ac_min=16)
    assert rules.recompute_ac(high)[1] == 18


def test_an_effect_lasts_its_turns_and_survives_the_turn_it_was_cast():
    ch = make("Cleric", dex=12)
    rules.set_effect(ch, "Shield of Faith", ac_bonus=2, turns=2)
    assert rules.tick_effects(ch) == []                   # the turn it was cast
    assert rules.tick_effects(ch) == []                   # 1 left
    assert rules.tick_effects(ch) == ["Shield of Faith"]  # gone
    assert rules.recompute_ac(ch)[1] == 16


def test_an_effect_without_turns_lasts_until_ended():
    ch = make("Cleric")
    rules.set_effect(ch, "Blessing of the Tide", ac_bonus=1)
    for _ in range(50):
        assert rules.tick_effects(ch) == []
    assert rules.clear_effect(ch, "blessing of the tide")  # by name, any case
    assert ch["effects"] == []


def test_reusing_an_effect_name_replaces_it():
    ch = make("Cleric")
    rules.set_effect(ch, "Cover", ac_bonus=2)
    rules.set_effect(ch, "cover", ac_bonus=5)
    assert [fx["ac_bonus"] for fx in ch["effects"]] == [5]


def test_an_effect_that_changes_nothing_is_refused():
    with pytest.raises(ValueError):
        rules.set_effect(make(), "Vague Feeling")


# --------------------------------------------------------------------------- #
# wearing things
# --------------------------------------------------------------------------- #

def test_armour_must_be_carried_to_be_worn():
    ch = make("Wizard")
    ok, message, _ = rules.wear(ch, "plate armor")
    assert not ok and "not carrying" in message


def test_only_armour_and_shields_can_be_worn():
    ok, message, _ = rules.wear(make("Wizard"), "quarterstaff")
    assert not ok and "not armour" in message


def test_putting_on_new_armour_swaps_the_old():
    ch = make("Rogue", dex=12)                            # leather, 12
    rules.add_item(ch, "chain shirt")
    ok, message, entry = rules.wear(ch, "chain shirt")
    assert ok and "takes off" in message and entry == "chain shirt"
    assert ch["equipment"]["armor"] == "chain shirt"
    assert rules.recompute_ac(ch)[1] == 14                # 13 + 1
    assert "leather armor" in ch["inventory"]             # swapped, not thrown away


def test_taking_armour_off():
    ch = make("Fighter", dex=12)
    ok, _, entry = rules.wear(ch, "shield", on=False)
    assert ok and entry == "shield"
    assert rules.recompute_ac(ch)[1] == 16


def test_wearing_by_english_name_in_a_thai_campaign():
    """The DM may well say 'chain shirt' while the sheet says 'เสื้อเกราะโซ่'."""
    ch = make("Cleric", lang="th")
    rules.wear(ch, "shield", on=False)
    ok, _, entry = rules.wear(ch, "shield")
    assert ok and entry == "โล่"                           # the sheet's own spelling


def test_armour_that_leaves_the_inventory_comes_off():
    ch = make("Fighter", dex=12)
    rules.remove_item(ch, "chain mail")
    assert rules.reconcile_equipment(ch) == ["chain mail"]
    assert rules.recompute_ac(ch)[1] == 13                # 10 + 1 + shield 2


# --------------------------------------------------------------------------- #
# old saves
# --------------------------------------------------------------------------- #

def frozen(klass="Fighter", lang="en"):
    """A character exactly as the old code saved one: a frozen, wrong `ac` and no
    `equipment` or `effects` at all."""
    ch = make(klass, dex=12, lang=lang)
    for key in ("equipment", "effects", "ac_parts"):
        ch.pop(key)
    ch["ac"] = 12 + 1
    return ch


def test_an_old_character_is_put_in_its_armour():
    ch = frozen()
    rules.ensure_equipment(ch)
    assert rules.recompute_ac(ch) == (13, 18)


def test_an_old_character_loads_corrected_from_the_database(app_client):
    client, stub = app_client
    table = new_table(client, stub, character={"name": "Bram", "race": "Dwarf",
                                               "class": "Fighter"})
    blob = frozen()
    blob["name"] = "Bram"
    store.db().execute("UPDATE characters SET data=? WHERE campaign_id=?",
                       (json.dumps(blob), table.id))
    store.db().commit()

    bram = table.character("Bram")
    assert bram["ac"] == 18
    assert bram["equipment"] == {"armor": "chain mail", "shield": "shield"}


def test_ensure_equipment_never_overwrites_a_choice():
    ch = make("Fighter")
    rules.wear(ch, "shield", on=False)                    # deliberately took it off
    rules.ensure_equipment(ch)
    assert ch["equipment"]["shield"] is None


# --------------------------------------------------------------------------- #
# through the DM
# --------------------------------------------------------------------------- #

def test_the_dm_is_told_the_ac_and_how_it_was_reached():
    ch = make("Fighter", dex=12)
    state = json.loads(rules.state_block([ch]))[0]
    assert state["ac"] == 18
    assert state["ac_from"] == "chain mail 16, DEX +0 (capped by armour), shield +2"
    assert state["wearing"] == ["chain mail", "shield"]


def test_equip_armor_tool_recomputes_and_reports():
    ch = make("Rogue", dex=12)
    rules.add_item(ch, "chain shirt")
    result, event = asyncio.run(dm.run_tool("equip_armor", {
        "character_name": "Test", "item": "chain shirt", "wear": True,
        "reason": "takes the guard's shirt"}, [ch]))
    assert "AC 12 -> 14" in result and "chain shirt 13" in result
    assert event["kind"] == "sheet"
    assert {"t": "ac", "from": 12, "to": 14} in event["changes"]
    assert {"t": "wear", "item": "chain shirt"} in event["changes"]


def test_equip_armor_refuses_what_is_not_carried():
    result, event = asyncio.run(dm.run_tool("equip_armor", {
        "character_name": "Test", "item": "plate armor", "wear": True,
        "reason": "x"}, [make("Wizard")]))
    assert result.startswith("ERROR") and event is None


def test_set_effect_tool_and_ending_one_that_is_not_there():
    ch = make("Cleric", dex=12)
    args = {"character_name": "Test", "name": "Shield of Faith", "ac_bonus": 2,
            "ac_base": 0, "ac_min": 0, "turns": 10, "remove": False}
    result, event = asyncio.run(dm.run_tool("set_effect", args, [ch]))
    assert "AC 16 -> 18" in result
    assert {"t": "fx+", "name": "Shield of Faith", "turns": 10} in event["changes"]

    result, event = asyncio.run(dm.run_tool("set_effect", {**args, "name": "Haste", "remove": True}, [ch]))
    assert result.startswith("ERROR") and "Shield of Faith" in result and event is None


def test_losing_worn_armour_through_update_character_drops_ac():
    ch = make("Fighter", dex=12)
    result, event = asyncio.run(dm.run_tool("update_character", {
        "character_name": "Test", "hp_change": 0, "xp_gain": 0, "gold_change": 0,
        "add_items": [], "remove_items": ["chain mail"], "add_conditions": [],
        "remove_conditions": [], "level_up": False, "reason": "rust monster"}, [ch]))
    assert "AC 18 -> 13" in result
    assert {"t": "unwear", "item": "chain mail"} in event["changes"]


def test_a_whole_turn_putting_armour_on(app_client):
    """End to end: the DM dons armour, the table sees it, and the next turn's prompt
    carries the new number - the test that matters is what the model was sent."""
    client, stub = app_client
    table = new_table(client, stub)                       # Vess, a Rogue, in leather
    # Stats are rolled, and at DEX 18 leather (11 + 4) and a chain shirt (13 + a capped
    # 2) tie - so this test once failed whenever the dice were kind. Pin DEX: 14 (+2).
    vess = rules.new_character("Vess", "Elf", "Rogue", scores={
        "STR": 10, "DEX": 14, "CON": 12, "INT": 10, "WIS": 10, "CHA": 10})
    store.db().execute("UPDATE characters SET data=? WHERE campaign_id=?",
                       (json.dumps(vess), table.id))
    store.db().commit()
    table.begin("A guard post, abandoned in a hurry.")
    assert table.character("Vess")["ac"] == 13                     # leather 11 + 2

    events = table.act(
        "I take the chain shirt off the rack and put it on.",
        [("update_character", {
            "character_name": "Vess", "hp_change": 0, "xp_gain": 0, "gold_change": 0,
            "add_items": ["chain shirt"], "remove_items": [], "add_conditions": [],
            "remove_conditions": [], "level_up": False, "reason": "takes the shirt"})],
        [("equip_armor", {"character_name": "Vess", "item": "chain shirt",
                          "wear": True, "reason": "puts it on"})],
        "It is heavier than it looks.")

    sheets = [e for e in events if e["kind"] == "sheet"]
    assert any(c.get("t") == "wear" for e in sheets for c in e["changes"])
    vess = table.character("Vess")
    assert vess["equipment"]["armor"] == "chain shirt"
    assert vess["ac"] == 15                                        # chain shirt 13 + 2
    assert any(f"-> {vess['ac']}" in r for r in tool_results(stub.calls[-1]))

    table.act("I look around.", "Dust, and a draught from the stairs.")
    party = json.loads(prompt_of(stub.calls[-1]).split("<party_state>")[1]
                       .split("</party_state>")[0])
    assert party[0]["ac"] == vess["ac"]
    assert party[0]["ac_from"].startswith("chain shirt 13")


def test_an_effect_wears_off_after_its_turns_and_the_table_is_told(app_client):
    client, stub = app_client
    table = new_table(client, stub, character={"name": "Bram", "race": "Dwarf",
                                               "class": "Cleric"})
    table.begin("The crypt door grinds open.")
    base = table.character("Bram")["ac"]

    table.act("I call on my god to shield me.",
              [("set_effect", {"character_name": "Bram", "name": "Shield of Faith",
                               "ac_bonus": 2, "ac_base": 0, "ac_min": 0, "turns": 1,
                               "remove": False})],
              "A pale light settles on you.")
    assert table.character("Bram")["ac"] == base + 2      # survives the turn it was cast

    # its one further turn: in force while the DM narrates it, gone when it ends
    stub.calls.clear()
    events = table.act("I step inside.", "Cold air, and something shifting.")
    during = json.loads(prompt_of(stub.calls[-1]).split("<party_state>")[1]
                        .split("</party_state>")[0])[0]
    assert during["ac"] == base + 2
    assert table.character("Bram")["ac"] == base
    worn_off = [c for e in events if e["kind"] == "sheet" for c in e["changes"]
                if c.get("t") == "fx-"]
    assert worn_off == [{"t": "fx-", "name": "Shield of Faith"}]
