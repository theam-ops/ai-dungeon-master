"""Initiative and turn order.

A fight is stored on the campaign: who is in it, in what order, whose turn it is. The
order is rolled in Python, shown to the whole table, and handed to the DM every turn.
It guides play without stopping it - a player acting out of turn is never refused.

Initiative is random, so every test that cares whose turn it is rigs the d20. A test
that trusts the dice passes most of the time, which is worse than one that fails.
"""

import asyncio
import json
import time

import pytest

import server
from game import dm, rules
from game.services import turn as turn_service
from .harness import new_table, prompt_of, tool_results


def pc(name, dex=10, klass="Rogue"):
    return rules.new_character(name, "Human", klass, scores={
        "STR": 10, "DEX": dex, "CON": 12, "INT": 10, "WIS": 10, "CHA": 10})


@pytest.fixture
def rig(monkeypatch):
    """rig(14, 3, 9, 1, ...) - the d20s initiative will roll, in the order it rolls them
    (each combatant: its roll, then its tie-break). Anything after the list rolls 10."""
    def install(*values):
        queue = list(values)
        monkeypatch.setattr(rules.random, "randint",
                            lambda a, b: queue.pop(0) if queue else 10)
    return install


def names(combat):
    return [e["name"] for e in combat["order"]]


# --------------------------------------------------------------------------- #
# the order
# --------------------------------------------------------------------------- #

def test_everyone_at_the_table_is_rolled_for(rig):
    rig(5, 1,  12, 1,  9, 1)                     # Vess, Bram, then the goblin
    combat, rolled = rules.join_combat(None, [pc("Vess", 16), pc("Bram")],
                                       [{"name": "Goblin", "bonus": 2}])
    assert names(combat) == ["Bram", "Goblin", "Vess"]       # 12, 11, 8
    assert [(e["roll"], e["bonus"], e["total"]) for e in combat["order"]] == \
           [(12, 0, 12), (9, 2, 11), (5, 3, 8)]
    assert (combat["round"], combat["turn"]) == (1, 0)
    assert len(rolled) == 3


def test_ties_go_to_the_higher_bonus_then_a_kept_roll(rig):
    rig(10, 1,  12, 1,  12, 20)                  # Vess +3=13, Bram 12, Ogre 12+0 tie 20
    combat, _ = rules.join_combat(None, [pc("Vess", 16), pc("Bram")],
                                  [{"name": "Ogre", "bonus": 0}])
    assert names(combat) == ["Vess", "Ogre", "Bram"]


def test_monsters_with_one_name_are_numbered():
    combat, _ = rules.join_combat(None, [], [{"name": "Goblin", "bonus": 2}] * 3)
    assert sorted(names(combat)) == ["Goblin", "Goblin 2", "Goblin 3"]


def test_a_monster_cannot_claim_an_absurd_bonus():
    combat, _ = rules.join_combat(None, [], [{"name": "Lich", "bonus": 99}])
    assert combat["order"][0]["bonus"] == 15


def test_reinforcements_do_not_change_whose_turn_it_is(rig):
    rig(15, 1, 5, 1)
    combat, _ = rules.join_combat(None, [pc("Vess"), pc("Bram")])
    combat, _, _ = rules.next_turn(combat)                    # Bram's turn now
    rig(20, 1)                                                # the ogre rolls high
    combat, rolled = rules.join_combat(combat, [pc("Vess"), pc("Bram")],
                                       [{"name": "Ogre", "bonus": 0}])
    assert [e["name"] for e in rolled] == ["Ogre"]            # only the newcomer
    assert names(combat)[0] == "Ogre"
    assert rules.current_turn(combat)["name"] == "Bram"


def test_turns_go_round_and_rounds_count():
    combat = {"round": 1, "turn": 0, "order": [
        {"name": n, "pc": True, "bonus": 0, "roll": 10, "total": 10, "tie": 1}
        for n in ("A", "B", "C")]}
    seen = []
    for _ in range(4):
        combat, wrapped, _ = rules.next_turn(combat)
        seen.append((combat["round"], rules.current_turn(combat)["name"], wrapped))
    assert seen == [(1, "B", False), (1, "C", False), (2, "A", True), (2, "B", False)]


@pytest.mark.parametrize("turn, remove, expect", [
    (1, ["A"], "C"),            # someone earlier falls: B ends, C is next
    (1, ["B"], "C"),            # the one acting falls: whoever stands in their place
    (1, ["C"], "A"),            # the next one falls: skip them, new round
    (0, ["b", "c"], "A"),       # case-insensitive; alone, A goes again next round
])
def test_the_fallen_leave_the_order(turn, remove, expect):
    combat = {"round": 1, "turn": turn, "order": [
        {"name": n, "pc": False, "bonus": 0, "roll": 10, "total": 10, "tie": 1}
        for n in ("A", "B", "C")]}
    combat, _, removed = rules.next_turn(combat, remove)
    assert rules.current_turn(combat)["name"] == expect
    assert len(removed) == len(remove)


def test_when_the_last_combatant_leaves_the_fight_is_over():
    combat = {"round": 3, "turn": 0, "order": [
        {"name": "Goblin", "pc": False, "bonus": 0, "roll": 1, "total": 1, "tie": 1}]}
    assert rules.next_turn(combat, ["Goblin"])[0] is None


def test_the_dm_is_told_who_is_missing_from_the_order(rig):
    combat, _ = rules.join_combat(None, [pc("Vess")], [{"name": "Goblin", "bonus": 2}])
    view = rules.combat_view(combat, [pc("Vess"), pc("Ilse")])
    assert view["not_in_initiative"] == ["Ilse"]
    assert all(" (NPC)" in o for o in view["order"] if o.startswith("Goblin"))


# --------------------------------------------------------------------------- #
# the prompt
# --------------------------------------------------------------------------- #

def test_acting_out_of_turn_is_flagged_not_refused(rig):
    rig(18, 1, 3, 1)
    party = [pc("Bram"), pc("Vess")]
    combat, _ = rules.join_combat(None, party)                # Bram 18, Vess 3
    prompt = dm.build_prompt(party, "Vess", "I throw a dagger.", combat=combat)
    assert "<combat>" in prompt
    assert "It is Bram's turn, not Vess's" in prompt and "Do not refuse them" in prompt
    assert "<combat_note>" not in dm.build_prompt(party, "Bram", "I swing.", combat=combat)


def test_combat_tools_need_a_campaign():
    async def offered(cid, repo):
        return {t["name"] for t in await dm.tools_for(cid, repo)}
    assert not {"roll_initiative", "next_turn", "end_combat"} & asyncio.run(offered(None, None))
    blocks = asyncio.run(dm.system_blocks("en"))
    assert any("keep the order moving in the fiction" in b["text"] for b in blocks)


# --------------------------------------------------------------------------- #
# whole turns
# --------------------------------------------------------------------------- #

GOBLIN = [("roll_initiative", {"npcs": [{"name": "Goblin", "bonus": 2}],
                               "reason": "ambush"})]
NEXT = [("next_turn", {"remove": []})]


def combat_of(table):
    return table.client.get(f"/api/campaigns/{table.id}").json()["combat"]


def test_a_fight_from_first_roll_to_last(app_client, rig):
    client, stub = app_client
    table = new_table(client, stub)                           # Vess
    table.begin("Something moves in the reeds.")
    rig(15, 1,  4, 1)                                         # Vess first, goblin after
    events = table.act("I draw my blade.", GOBLIN, "A goblin bursts out!")

    start = next(e for e in events if e["kind"] == "combat")
    assert start["what"] == "start" and [e["name"] for e in start["rolled"]] == ["Vess", "Goblin"]
    assert names(combat_of(table)) == ["Vess", "Goblin"]
    assert "Initiative rolled" in tool_results(stub.calls[-1])[-1]

    table.act("I stab it.", NEXT, "It shrieks.")
    assert rules.current_turn(combat_of(table))["name"] == "Goblin"
    table.act("I watch it.", "The goblin lunges.")
    assert '"whose_turn": "Goblin (NPC - you run it)"' in prompt_of(stub.calls[-1])

    # the last enemy falls - the fight does not end itself (enemies flee, players duel);
    # the DM ends it
    events = table.act("I finish it.", [("next_turn", {"remove": ["Goblin"]}),
                                        ("end_combat", {"reason": "won"})], "It falls.")
    assert any(e["kind"] == "combat" and e["what"] == "end" for e in events)
    assert combat_of(table) is None


def test_durations_count_rounds_in_a_fight_not_actions(app_client, rig):
    """The open defect from Phase 1: a 'turns' effect ran down once per player action,
    so five players burned it five times as fast. In a fight it now counts rounds."""
    client, stub = app_client
    table = new_table(client, stub, character={"name": "Bram", "race": "Dwarf",
                                               "class": "Cleric"})
    table.begin("The crypt.")
    rig(15, 1, 4, 1)
    table.act("Fight!", GOBLIN, "Bones rise.")
    base = table.character("Bram")["ac"]
    table.act("Shield of Faith!", [("set_effect", {
        "character_name": "Bram", "name": "Shield of Faith", "ac_bonus": 2, "ac_base": 0,
        "ac_min": 0, "turns": 1, "remove": False})], "Light settles on you.")
    for _ in range(3):                                        # actions, not rounds
        table.act("I hold.", "The skeleton circles.")
    assert table.character("Bram")["ac"] == base + 2

    table.act("Next.", NEXT, "Its turn.")                     # Bram -> Goblin, round 1
    table.act("Next.", NEXT, "Round two.")                    # round 2: fresh flag clears
    events = table.act("Next.", NEXT, "Its turn.")
    events += table.act("Next.", NEXT, "Round three.")        # round 3: one round gone
    expired = [x for e in events if e["kind"] == "combat" for x in e["expired"]]
    assert expired == [{"character": "Bram", "name": "Shield of Faith"}]
    assert table.character("Bram")["ac"] == base


def test_a_fight_survives_export_and_import(app_client, rig):
    client, stub = app_client
    table = new_table(client, stub)
    table.begin("An ambush.")
    rig(15, 1, 4, 1)
    table.act("Fight!", GOBLIN, "Go.")
    blob = client.get(f"/api/campaigns/{table.id}/export").json()
    back = asyncio.run(server.A.repo.import_campaign(blob))
    cid = back if isinstance(back, str) else back["id"]
    assert names(asyncio.run(server.A.repo.get_combat(cid))) == ["Vess", "Goblin"]


# --------------------------------------------------------------------------- #
# the idle-turn clock
# --------------------------------------------------------------------------- #

def wait_for(predicate, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def nudged(stub):
    return any("has not acted for a while" in prompt_of(c) for c in stub.calls)


def test_with_no_clock_an_idle_turn_just_waits(app_client, rig, monkeypatch):
    monkeypatch.setattr(turn_service, "COMBAT_TURN_GRACE", 0)
    client, stub = app_client
    table = new_table(client, stub)
    table.begin("Reeds.")
    rig(15, 1, 4, 1)
    table.act("Fight!", GOBLIN, "Go.")
    time.sleep(0.2)
    assert not nudged(stub)


def test_an_idle_turn_passes_when_the_table_wants_a_clock(app_client, rig, monkeypatch):
    monkeypatch.setattr(turn_service, "COMBAT_TURN_GRACE", 0.1)
    client, stub = app_client
    table = new_table(client, stub)
    table.begin("Reeds.")
    rig(15, 1, 4, 1)                                          # Vess first: her clock starts
    table.act("Fight!", GOBLIN, "Go.")
    assert wait_for(lambda: nudged(stub)), "the DM was never told the turn passed"


def test_a_player_who_acts_in_time_is_not_called_hesitant(app_client, rig, monkeypatch):
    monkeypatch.setattr(turn_service, "COMBAT_TURN_GRACE", 0.4)
    client, stub = app_client
    table = new_table(client, stub)
    table.begin("Reeds.")
    rig(15, 1, 4, 1)
    table.act("Fight!", GOBLIN, "Go.")
    table.act("I slash at it!", "Steel rings.")               # acts; DM forgets next_turn
    time.sleep(0.7)
    assert not nudged(stub)
