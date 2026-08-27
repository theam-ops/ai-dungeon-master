"""Dice and character rules. No I/O, no API calls - just the tabletop math.

Shared by the CLI (dnd.py) and the web server (server.py).
"""

import json
import random
import re

from . import i18n

ABILITIES = ["STR", "DEX", "CON", "INT", "WIS", "CHA"]

CLASSES = {
    "Fighter": {"hit_die": 10, "primary": "STR",
                "gear": ["longsword", "shield", "chain mail", "explorer's pack"]},
    "Wizard":  {"hit_die": 6,  "primary": "INT",
                "gear": ["quarterstaff", "spellbook", "component pouch", "scholar's pack"]},
    "Rogue":   {"hit_die": 8,  "primary": "DEX",
                "gear": ["shortsword", "shortbow", "thieves' tools", "leather armor"]},
    "Cleric":  {"hit_die": 8,  "primary": "WIS",
                "gear": ["mace", "chain shirt", "holy symbol", "shield"]},
    "Ranger":  {"hit_die": 10, "primary": "DEX",
                "gear": ["longbow", "two shortswords", "leather armor", "hunting trap"]},
    "Bard":    {"hit_die": 8,  "primary": "CHA",
                "gear": ["rapier", "lute", "leather armor", "diplomat's pack"]},
}

RACES = ["Human", "Elf", "Dwarf", "Halfling", "Half-Orc", "Tiefling", "Dragonborn", "Gnome"]

# Every skill hangs off one ability. The DM used to be asked, in prose, to remember a
# proficiency bonus and pick a modifier; now the numbers are here and it is told them.
SKILLS = {
    "Athletics": "STR",
    "Acrobatics": "DEX", "Sleight of Hand": "DEX", "Stealth": "DEX",
    "Arcana": "INT", "History": "INT", "Investigation": "INT",
    "Nature": "INT", "Religion": "INT",
    "Animal Handling": "WIS", "Insight": "WIS", "Medicine": "WIS",
    "Perception": "WIS", "Survival": "WIS",
    "Deception": "CHA", "Intimidation": "CHA", "Performance": "CHA", "Persuasion": "CHA",
}

# Three apiece, picked so a class reads as itself at a glance. This game does not model
# choosing your skills at creation, and adding that would be a different feature.
CLASS_SKILLS = {
    "Fighter": ["Athletics", "Intimidation", "Survival"],
    "Wizard":  ["Arcana", "History", "Investigation"],
    "Rogue":   ["Stealth", "Sleight of Hand", "Perception"],
    "Cleric":  ["Religion", "Insight", "Medicine"],
    "Ranger":  ["Survival", "Nature", "Animal Handling"],
    "Bard":    ["Persuasion", "Performance", "Deception"],
}


# --------------------------------------------------------------------------- #
# dice
# --------------------------------------------------------------------------- #

DICE_RE = re.compile(r"^\s*(\d*)\s*d\s*(\d+)\s*([+-]\s*\d+)?\s*$", re.I)


def roll_notation(notation, mode="normal"):
    """Roll standard dice notation. Returns (total, human_readable_detail)."""
    m = DICE_RE.match(notation or "")
    if not m:
        raise ValueError("bad dice notation: %r (try '1d20+3' or '2d6')" % notation)

    count = int(m.group(1) or 1)
    sides = int(m.group(2))
    mod = int(m.group(3).replace(" ", "")) if m.group(3) else 0

    if count < 1 or count > 100 or sides < 2 or sides > 1000:
        raise ValueError("dice out of sane range")

    if mode in ("advantage", "disadvantage") and count == 1:
        a, b = random.randint(1, sides), random.randint(1, sides)
        pick = max(a, b) if mode == "advantage" else min(a, b)
        rolls, note = [pick], f"[{a}, {b}] {mode}"
    else:
        rolls = [random.randint(1, sides) for _ in range(count)]
        note = str(rolls)

    total = sum(rolls) + mod
    sign = f"{mod:+d}" if mod else ""
    detail = f"{count}d{sides}{sign} -> {note}{' ' + sign if sign else ''} = {total}"

    crit = None
    if sides == 20 and count == 1:
        if rolls[0] == 20:
            detail += "  ** NATURAL 20 **"
            crit = "success"
        elif rolls[0] == 1:
            detail += "  ** NATURAL 1 **"
            crit = "fail"
    return total, detail, crit


# --------------------------------------------------------------------------- #
# characters
# --------------------------------------------------------------------------- #

def modifier(score):
    return (score - 10) // 2


def proficiency_bonus(level):
    """+2 at levels 1-4, then a point every four levels."""
    return 2 + max(0, (int(level) - 1)) // 4


def skill_modifier(ch, skill):
    """What this character adds to a roll of `skill`."""
    ability = SKILLS.get(skill)
    if ability is None:
        raise ValueError(f"unknown skill: {skill}")
    bonus = proficiency_bonus(ch["level"]) if skill in ch.get("skills", []) else 0
    return modifier(ch["abilities"][ability]) + bonus


def ensure_skills(ch):
    """Give a character its class proficiencies if it predates skills existing.

    Characters live as a JSON blob, so an older one simply has no `skills` key rather
    than a null column. Filling it in on read costs nothing and is undone by deleting
    the key again; the next save persists it. Never overwrites a list already there.
    """
    if not ch.get("skills"):
        ch["skills"] = list(CLASS_SKILLS.get(ch.get("class"), []))
    return ch


def roll_ability():
    """4d6 drop lowest."""
    d = sorted(random.randint(1, 6) for _ in range(4))
    return sum(d[1:])


def roll_stats(klass):
    """Six ability scores, with the best roll moved into the class's primary stat."""
    scores = {a: roll_ability() for a in ABILITIES}
    primary = CLASSES[klass]["primary"]
    best = max(scores, key=lambda a: scores[a])
    scores[best], scores[primary] = scores[primary], scores[best]
    return scores


def new_character(name, race, klass, scores=None, lang="en"):
    if klass not in CLASSES:
        raise ValueError(f"unknown class: {klass}")
    if race not in RACES:
        raise ValueError(f"unknown race: {race}")

    scores = scores or roll_stats(klass)
    max_hp = CLASSES[klass]["hit_die"] + modifier(scores["CON"])
    # gear is localised at creation so a Thai campaign's sheet reads as Thai from turn one
    kit = list(CLASSES[klass]["gear"]) + ["rations (5)", "torch", "waterskin"]
    return {
        "name": name,
        "race": race,
        "class": klass,
        "level": 1,
        "xp": 0,
        "abilities": scores,
        "max_hp": max_hp,
        "hp": max_hp,
        "ac": 12 + max(0, modifier(scores["DEX"])),
        "gold": 25,
        "inventory": [i18n.gear(item, lang) for item in kit],
        "conditions": [],
        "skills": list(CLASS_SKILLS[klass]),
    }


def level_up(ch):
    """Advance one level, rolling a fresh hit die. Returns the HP gained."""
    ch["level"] += 1
    gain = max(1, random.randint(1, CLASSES[ch["class"]]["hit_die"])
               + modifier(ch["abilities"]["CON"]))
    ch["max_hp"] += gain
    ch["hp"] = ch["max_hp"]
    return gain


def sheet(ch, lang="en"):
    """Plain-text character sheet, used by the CLI."""
    ab = "  ".join(
        f"{a} {ch['abilities'][a]:2d} ({modifier(ch['abilities'][a]):+d})" for a in ABILITIES
    )
    lines = [
        i18n.cli("level_line", lang, ch["name"], ch["level"],
                 i18n.name(ch["race"], lang), i18n.name(ch["class"], lang)),
        i18n.cli("vitals", lang, ch["hp"], ch["max_hp"], ch["ac"], ch["xp"], ch["gold"]),
        ab,
        i18n.cli("inventory", lang) + " "
        + (", ".join(ch["inventory"]) or i18n.cli("empty", lang)),
    ]
    if ch["conditions"]:
        lines.append(i18n.cli("conditions", lang) + " " + ", ".join(ch["conditions"]))
    return "\n".join(lines)


def state_block(characters, lang="en"):
    """The party state handed to the DM each turn."""
    party = []
    for ch in characters:
        entry = {}
        if lang != "en":
            # give the DM the exact wording to use, so it stays consistent turn to turn
            entry = {"race_in_language": i18n.name(ch["race"], lang),
                     "class_in_language": i18n.name(ch["class"], lang)}
        party.append({
            **entry,
            "name": ch["name"], "race": ch["race"], "class": ch["class"],
            "level": ch["level"], "xp": ch["xp"],
            "hp": ch["hp"], "max_hp": ch["max_hp"], "ac": ch["ac"], "gold": ch["gold"],
            "abilities": ch["abilities"],
            "modifiers": {a: modifier(ch["abilities"][a]) for a in ABILITIES},
            # The proficient skills with their totals already worked out - a number the
            # DM copies rather than a sum it might get wrong. Only the proficient ones:
            # `modifiers` above covers the other twelve, and this block is re-sent on
            # every turn and echoed after every sheet change.
            "proficiency_bonus": proficiency_bonus(ch["level"]),
            "skill_bonuses": {s: skill_modifier(ch, s)
                              for s in ch.get("skills", []) if s in SKILLS},
            "inventory": ch["inventory"], "conditions": ch["conditions"],
            "status": "UNCONSCIOUS AT 0 HP" if ch["hp"] == 0 else "conscious",
        })
    return json.dumps(party, ensure_ascii=False)
