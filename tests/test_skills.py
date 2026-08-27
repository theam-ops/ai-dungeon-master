"""Skills: the numbers, the migration, and getting them to the DM.

Before this existed the DM was asked, in prose, to remember "+2 proficiency at level 1-4"
and pick a modifier itself. These tests are about the numbers being real.
"""

import json

import pytest

from game import rules, store


def a_rogue(**over):
    ch = rules.new_character("Vess", "Elf", "Rogue",
                             {"STR": 11, "DEX": 16, "CON": 9,
                              "INT": 13, "WIS": 12, "CHA": 14})
    ch.update(over)
    return ch


# --------------------------------------------------------------------------- #
# the numbers
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("level,bonus", [
    (1, 2), (4, 2),          # the band the game actually plays in
    (5, 3), (8, 3),
    (9, 4), (12, 4),
    (13, 5), (16, 5),
    (17, 6), (20, 6),
])
def test_proficiency_climbs_a_point_every_four_levels(level, bonus):
    assert rules.proficiency_bonus(level) == bonus


def test_a_proficient_skill_adds_the_bonus_and_an_unproficient_one_does_not():
    ch = a_rogue()
    # DEX 16 -> +3, and Stealth is a rogue skill, so +2 on top
    assert rules.skill_modifier(ch, "Stealth") == 5
    # INT 13 -> +1, and no rogue is proficient in Arcana
    assert rules.skill_modifier(ch, "Arcana") == 1


def test_a_bad_ability_still_gives_a_negative_modifier():
    ch = a_rogue(abilities={"STR": 6, "DEX": 16, "CON": 9,
                            "INT": 13, "WIS": 12, "CHA": 14})
    assert rules.skill_modifier(ch, "Athletics") == -2      # (6-10)//2, no proficiency


def test_every_skill_hangs_off_a_real_ability():
    assert set(rules.SKILLS.values()) <= set(rules.ABILITIES)


def test_every_class_is_proficient_in_skills_that_exist():
    for klass, skills in rules.CLASS_SKILLS.items():
        assert klass in rules.CLASSES
        for skill in skills:
            assert skill in rules.SKILLS, f"{klass} claims {skill!r}"


def test_an_unknown_skill_is_an_error_rather_than_a_zero():
    with pytest.raises(ValueError):
        rules.skill_modifier(a_rogue(), "Basket Weaving")


# --------------------------------------------------------------------------- #
# the migration
# --------------------------------------------------------------------------- #

def test_a_character_made_before_skills_existed_gets_its_class_proficiencies():
    old = {"name": "Kentuckai", "class": "Wizard", "level": 4,
           "abilities": {"STR": 8, "DEX": 13, "CON": 15,
                         "INT": 19, "WIS": 12, "CHA": 10}}
    rules.ensure_skills(old)
    assert old["skills"] == rules.CLASS_SKILLS["Wizard"]
    # INT 19 -> +4, proficient, level 4 -> +2
    assert rules.skill_modifier(old, "Arcana") == 6


def test_the_migration_never_overwrites_skills_already_chosen():
    ch = a_rogue(skills=["Persuasion"])
    rules.ensure_skills(ch)
    assert ch["skills"] == ["Persuasion"]


def test_reading_a_party_fills_in_missing_skills(tmp_path, monkeypatch):
    cid = store.create_campaign("skill migration")["id"]
    ch = a_rogue()
    ch.pop("skills")                       # exactly what an older row looks like
    store.add_character(cid, ch, token=None)

    got = store.party(cid)[0]
    assert got["skills"] == rules.CLASS_SKILLS["Rogue"]


# --------------------------------------------------------------------------- #
# what the DM is told
# --------------------------------------------------------------------------- #

def test_the_state_block_carries_worked_out_totals_for_proficient_skills():
    entry = json.loads(rules.state_block([a_rogue()]))[0]
    assert entry["proficiency_bonus"] == 2
    # a number to copy, not a sum to get wrong: DEX 16 -> +3, +2 proficient
    assert entry["skill_bonuses"]["Stealth"] == 5


def test_the_state_block_leaves_out_skills_the_character_lacks():
    """What pins the size decision: `modifiers` already covers the other twelve."""
    entry = json.loads(rules.state_block([a_rogue()]))[0]
    assert "Arcana" not in entry["skill_bonuses"]
    assert entry["modifiers"]["INT"] == 1


def test_the_dm_is_no_longer_asked_to_remember_a_proficiency_bonus():
    from game import dm
    assert "+2 proficiency at level 1-4" not in dm.SYSTEM
    assert "skill_bonuses" in dm.SYSTEM


def test_the_state_block_stays_small_enough_to_send_every_turn():
    """It rides on every turn and is echoed after every sheet change, so size matters.

    Eighteen precomputed skill totals per character would be four times this.
    """
    block = rules.state_block([a_rogue() for _ in range(4)])
    assert len(block) < 3000, f"party state grew to {len(block)} bytes"


def test_skills_survive_export_and_import():
    cid = store.create_campaign("round trip")["id"]
    store.add_character(cid, a_rogue(skills=["Persuasion", "Stealth"]), token=None)

    blob = store.export_campaign(cid)
    back = store.import_campaign(blob)
    assert store.party(back["id"])[0]["skills"] == ["Persuasion", "Stealth"]
