"""Dice and character rules. No I/O, no API calls - just the tabletop math.

Shared by the CLI (dnd.py) and the web server (server.py).
"""

import json
import random
import re

from . import i18n

# How much a player may write in their standing notes for the DM. Every character's
# notes ride along on every one of their turns, so this is a token budget, not a whim.
MAX_NOTES_CHARS = 600

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


# --------------------------------------------------------------------------- #
# armour class
# --------------------------------------------------------------------------- #
#
# AC used to be written once at creation - `12 + max(0, DEX mod)` - and never touched
# again, so armour, shields and spells did nothing, and the 12 and the clamp were both
# wrong besides. Now it is derived: from what the character wears, their DEX, and any
# effects on them. `ch["ac"]` is a cache of `compute_ac(ch)["total"]`, refreshed by
# `recompute_ac`, and is never the authority.

UNARMOURED = 10

# SRD armour: base AC, and how much DEX modifier it lets through - None for all of it
# (light), 2 for up to +2 (medium; a penalty still counts), 0 for none at all (heavy:
# plate ignores DEX entirely, so a clumsy knight is not punished for it either).
ARMOUR = {
    "padded armor":          (11, None),
    "leather armor":         (11, None),
    "studded leather armor": (12, None),
    "hide armor":            (12, 2),
    "chain shirt":           (13, 2),
    "scale mail":            (14, 2),
    "breastplate":           (14, 2),
    "half plate armor":      (15, 2),
    "ring mail":             (14, 0),
    "chain mail":            (16, 0),
    "splint armor":          (17, 0),
    "plate armor":           (18, 0),
}
SHIELDS = {"shield": 2}

MAGIC_RE = re.compile(r"^\s*\+(\d)\s+(.+?)\s*$|^\s*(.+?)\s+\+(\d)\s*$")


def _gear_names():
    """Every name a piece of gear goes by - English key, or any translation of it -
    mapped back to the key. Starting gear is written onto the sheet in the campaign's
    language, so a Thai fighter carries "เกราะโซ่", not "chain mail"."""
    names = {}
    for table in i18n.GEAR.values():
        for key, shown in table.items():
            names[shown.strip().lower()] = key
    return names


_GEAR_NAMES = _gear_names()


def armour_piece(text):
    """Identify a carried item as armour or a shield.

    Returns (slot, key, magic_bonus) - slot is "armor" or "shield" - or None for
    anything that isn't. Understands "+1 chain mail", "Plate Armour", "plate", and the
    translated names of starting gear. Deliberately not fuzzy: "dwarven chain mail" is
    a different item the DM invented, and guessing at it would put numbers on the sheet
    that nobody decided.
    """
    name = " ".join(str(text or "").lower().replace("armour", "armor").split())
    bonus = 0
    m = MAGIC_RE.match(name)
    if m:
        bonus = int(m.group(1) or m.group(4))
        name = m.group(2) or m.group(3)
    name = _GEAR_NAMES.get(name, name)
    for candidate in (name, f"{name} armor", name.removesuffix(" armor")):
        if candidate in ARMOUR:
            return "armor", candidate, bonus
        if candidate in SHIELDS:
            return "shield", candidate, bonus
    return None


def _carried(ch, item):
    """The inventory entry that `item` refers to, matched by name or by what it is."""
    want = str(item or "").strip().lower()
    for entry in ch.get("inventory", []):
        if entry.strip().lower() == want:
            return entry
    piece = armour_piece(item)
    if piece:
        for entry in ch.get("inventory", []):
            if armour_piece(entry) == piece:
                return entry
    return None


def ensure_equipment(ch):
    """Give a character an `equipment` slot map if it predates AC being derived.

    Starting armour and shields are put on: every class that carries armour has been
    wearing it in the fiction since turn one, and the old formula just ignored it.
    Like `ensure_skills`, this fills in on read and never overwrites what is there.
    """
    if not isinstance(ch.get("equipment"), dict):
        worn = {"armor": None, "shield": None}
        for entry in ch.get("inventory", []):
            piece = armour_piece(entry)
            if piece and worn[piece[0]] is None:
                worn[piece[0]] = entry
        ch["equipment"] = worn
    ch["equipment"].setdefault("armor", None)
    ch["equipment"].setdefault("shield", None)
    if not isinstance(ch.get("effects"), list):
        ch["effects"] = []
    return ch


def compute_ac(ch):
    """AC from equipment and active effects, with the working shown.

    The breakdown is the point as much as the total: the DM quotes it when a blow
    glances off, and the dashboard shows why the number is what it is.
    """
    ensure_equipment(ch)
    dex = modifier(ch["abilities"]["DEX"])
    parts = []

    worn = ch["equipment"].get("armor")
    armour = armour_piece(worn) if worn else None
    if armour and armour[0] == "armor":
        base, cap = ARMOUR[armour[1]]
        base += armour[2]
        parts.append({"k": "armor", "item": worn, "v": base})
        dex_applied = dex if cap is None else (0 if cap == 0 else min(dex, cap))
    else:
        # an effect like mage armor replaces the unarmoured base - and only while
        # nothing is worn, which is why it is consulted in this branch alone
        base, label = UNARMOURED, None
        for fx in ch["effects"]:
            if fx.get("ac_base", 0) > base:
                base, label = fx["ac_base"], fx["name"]
        parts.append({"k": "base", "item": label, "v": base})
        dex_applied = dex                    # a negative DEX modifier counts too
        cap = None
    parts.append({"k": "dex", "v": dex_applied, "capped": dex_applied != dex})
    total = base + dex_applied

    held = ch["equipment"].get("shield")
    shield = armour_piece(held) if held else None
    if shield and shield[0] == "shield":
        bump = SHIELDS[shield[1]] + shield[2]
        total += bump
        parts.append({"k": "shield", "item": held, "v": bump})

    for fx in ch["effects"]:
        if fx.get("ac_bonus"):
            total += fx["ac_bonus"]
            parts.append({"k": "effect", "item": fx["name"], "v": fx["ac_bonus"]})
    for fx in ch["effects"]:
        if fx.get("ac_min", 0) > total:
            parts.append({"k": "floor", "item": fx["name"], "v": fx["ac_min"] - total})
            total = fx["ac_min"]

    return {"total": total, "parts": parts}


def recompute_ac(ch):
    """Refresh the cached AC and its breakdown. Returns (old, new)."""
    before = ch.get("ac")
    worked = compute_ac(ch)
    ch["ac"] = worked["total"]
    ch["ac_parts"] = worked["parts"]
    return before, ch["ac"]


def ac_summary(ch):
    """The breakdown as one line, for the DM: 'chain mail 16, DEX +0, shield +2'."""
    out = []
    for p in ch.get("ac_parts") or compute_ac(ch)["parts"]:
        if p["k"] == "dex":
            out.append(f"DEX {p['v']:+d}" + (" (capped by armour)" if p.get("capped") else ""))
        elif p["k"] == "base":
            out.append(f"{p['item'] or 'unarmoured'} {p['v']}")
        elif p["k"] == "armor":
            out.append(f"{p['item']} {p['v']}")
        elif p["k"] == "floor":
            out.append(f"{p['item']} raises it +{p['v']}")
        else:
            out.append(f"{p['item']} {p['v']:+d}")
    return ", ".join(out)


def wear(ch, item, on=True):
    """Put a piece of armour or a shield on, or take it off.

    Returns (ok, message, entry) - entry is the inventory line that went on or came
    off, spelled as the sheet spells it, so it reads in the campaign's language.
    Refusals are phrased for the DM: it must add an item with
    update_character before anyone can wear it, which keeps the inventory the one
    record of what a character owns.
    """
    ensure_equipment(ch)
    if on:
        entry = _carried(ch, item)
        if entry is None:
            return False, (f"{ch['name']} is not carrying {item!r}. Add it with "
                           f"update_character first if they have just acquired it."), None
        piece = armour_piece(entry)
        if piece is None:
            return False, (f"{entry!r} is not armour or a shield, so it does not "
                           f"change AC. Known armour: " + ", ".join(ARMOUR) + ", shield."), None
        slot = piece[0]
        replaced = ch["equipment"].get(slot)
        ch["equipment"][slot] = entry
        if replaced and replaced != entry:
            return True, f"{ch['name']} takes off {replaced} and puts on {entry}.", entry
        return True, f"{ch['name']} puts on {entry}.", entry

    piece = armour_piece(item)
    for slot in ("armor", "shield"):
        current = ch["equipment"].get(slot)
        if current and (current.strip().lower() == str(item).strip().lower()
                        or (piece and armour_piece(current) == piece)):
            ch["equipment"][slot] = None
            return True, f"{ch['name']} takes off {current}.", current
    return False, f"{ch['name']} is not wearing {item!r}.", None


def reconcile_equipment(ch):
    """Unequip anything no longer carried. Returns what came off.

    Armour that is sold, destroyed or stolen leaves the inventory through
    update_character; without this, the AC it gave would quietly stay behind.
    """
    ensure_equipment(ch)
    carried = {e.strip().lower() for e in ch.get("inventory", [])}
    gone = []
    for slot in ("armor", "shield"):
        worn = ch["equipment"].get(slot)
        if worn and worn.strip().lower() not in carried:
            ch["equipment"][slot] = None
            gone.append(worn)
    return gone


def set_effect(ch, name, ac_bonus=0, ac_base=0, ac_min=0, turns=0):
    """Add or replace a named effect. `turns` 0 means until removed.

    An effect is marked fresh so it survives the turn it was cast on: `turns=1` lasts
    the rest of this turn and the whole of the next, rather than expiring at once.
    """
    ensure_equipment(ch)
    name = str(name or "").strip()[:60]
    if not name:
        raise ValueError("an effect needs a name")
    if not (ac_bonus or ac_base or ac_min):
        raise ValueError("an effect needs ac_bonus, ac_base or ac_min - "
                         "otherwise it changes nothing this tool tracks")
    ch["effects"] = [fx for fx in ch["effects"] if fx["name"].lower() != name.lower()]
    ch["effects"].append({
        "name": name,
        "ac_bonus": max(-10, min(10, int(ac_bonus or 0))),
        "ac_base": max(0, min(25, int(ac_base or 0))),
        "ac_min": max(0, min(30, int(ac_min or 0))),
        "turns": max(0, int(turns or 0)) or None,
        "fresh": True,
    })
    return ch["effects"][-1]


def clear_effect(ch, name):
    """Remove a named effect. Returns True if there was one."""
    ensure_equipment(ch)
    before = len(ch["effects"])
    ch["effects"] = [fx for fx in ch["effects"]
                     if fx["name"].lower() != str(name or "").strip().lower()]
    return len(ch["effects"]) < before


def tick_effects(ch):
    """One table turn passes. Returns the names of effects that just ran out."""
    ensure_equipment(ch)
    kept, expired = [], []
    for fx in ch["effects"]:
        if fx.get("fresh"):
            fx["fresh"] = False
        elif fx.get("turns") is not None:
            fx["turns"] -= 1
            if fx["turns"] <= 0:
                expired.append(fx["name"])
                continue
        kept.append(fx)
    ch["effects"] = kept
    return expired


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
    ch = {
        "name": name,
        "race": race,
        "class": klass,
        "level": 1,
        "xp": 0,
        "abilities": scores,
        "max_hp": max_hp,
        "hp": max_hp,
        "gold": 25,
        "inventory": [i18n.gear(item, lang) for item in kit],
        "conditions": [],
        "skills": list(CLASS_SKILLS[klass]),
    }
    # starting armour and shield go on, and AC is worked out from them
    ensure_equipment(ch)
    recompute_ac(ch)
    return ch


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
            "hp": ch["hp"], "max_hp": ch["max_hp"], "gold": ch["gold"],
            # recomputed here rather than trusted: this is what the DM rolls against
            "ac": recompute_ac(ch)[1], "ac_from": ac_summary(ch),
            "wearing": [w for w in (ch["equipment"]["armor"], ch["equipment"]["shield"]) if w],
            "effects": [fx["name"] + (f" ({fx['turns']} turns)" if fx.get("turns") else "")
                        for fx in ch["effects"]],
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
