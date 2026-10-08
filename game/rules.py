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
    """The carried item's name that `item` refers to, matched by name or by what it is."""
    names = [i["name"] for i in ensure_items(ch)["items"]]
    want = _norm(item)
    for name in names:
        if _norm(name) == want:
            return name
    piece = armour_piece(item)
    if piece:
        for name in names:
            if armour_piece(name) == piece:
                return name
    return None


def ensure_equipment(ch):
    """Give a character an `equipment` slot map if it predates AC being derived.

    Starting armour and shields are put on: every class that carries armour has been
    wearing it in the fiction since turn one, and the old formula just ignored it.
    Like `ensure_skills`, this fills in on read and never overwrites what is there.
    """
    if not isinstance(ch.get("equipment"), dict):
        worn = {"armor": None, "shield": None}
        for item in ensure_items(ch)["items"]:
            piece = armour_piece(item["name"])
            if piece and worn[piece[0]] is None:
                worn[piece[0]] = item["name"]
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
    carried = {_norm(i["name"]) for i in ensure_items(ch)["items"]}
    gone = []
    for slot in ("armor", "shield"):
        worn = ch["equipment"].get(slot)
        if worn and _norm(worn) not in carried:
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


# --------------------------------------------------------------------------- #
# items
# --------------------------------------------------------------------------- #
#
# `ch["items"]` is what a character carries: records of {name, key, qty}. `name` is the
# item as the sheet spells it - in the campaign's language, or however the DM wrote it.
# `key` is the English rules name when the item is one the rules know ("chain mail",
# "rations"), and None when it is not ("a rusty key", "Aria's letter"). `qty` is real:
# eating a ration takes one away instead of rewriting "rations (5)" as "rations (4)".
#
# `ch["inventory"]` - the plain list of strings every older reader expects - is derived
# from the records by `sync_inventory` after every change, and never written to directly.

# SRD weights, in pounds. Only what the game itself hands out, plus armour. Anything the
# DM invents is carried but unweighed: a weight nobody decided is exactly the kind of
# number this project refuses to make up.
WEIGHTS = {
    # weapons
    "longsword": 3, "shortsword": 2, "two shortswords": 4, "quarterstaff": 4,
    "mace": 4, "rapier": 2, "shortbow": 2, "longbow": 2,
    # armour and shields
    "padded armor": 8, "leather armor": 10, "studded leather armor": 13,
    "hide armor": 12, "chain shirt": 20, "scale mail": 45, "breastplate": 20,
    "half plate armor": 40, "ring mail": 40, "chain mail": 55, "splint armor": 60,
    "plate armor": 65, "shield": 6,
    # gear
    "explorer's pack": 59, "scholar's pack": 10, "diplomat's pack": 36,
    "spellbook": 3, "component pouch": 2, "thieves' tools": 1, "holy symbol": 1,
    "hunting trap": 25, "lute": 2, "rations": 2, "torch": 1, "waterskin": 5,
}

# "rations (5)", "arrows x20", "arrows ×20" - the count the sheet has always used
QTY_RE = re.compile(r"^(.*?)\s*(?:\((\d+)\)|[x×]\s*(\d+))\s*$", re.I)


def _norm(text):
    return " ".join(str(text or "").lower().replace("armour", "armor").split())


def split_qty(text):
    """'rations (5)' -> ('rations', 5). Anything without a count is one of it."""
    text = " ".join(str(text or "").split())
    m = QTY_RE.match(text)
    if m and m.group(1):
        return m.group(1).strip(), max(1, int(m.group(2) or m.group(3)))
    return text, 1


def item_key(name):
    """The rules' English name for an item, or None if the rules do not know it."""
    base = _norm(split_qty(name)[0])
    base = _GEAR_NAMES.get(base, base)
    if base in WEIGHTS:
        return base
    piece = armour_piece(base)
    return piece[1] if piece else None


def shown(item):
    """How one record reads on the sheet: 'torch', 'rations (5)'."""
    return item["name"] if item["qty"] == 1 else f"{item['name']} ({item['qty']})"


def sync_inventory(ch):
    """Rewrite the derived string list from the records."""
    ch["inventory"] = [shown(item) for item in ch["items"]]
    return ch


def _find_item(ch, name):
    """The record `name` refers to: by its spelling first, then by what it is - so the DM
    saying 'torch' finds the 'คบไฟ' a Thai sheet carries."""
    want = _norm(split_qty(name)[0])
    for item in ch["items"]:
        if _norm(item["name"]) == want:
            return item
    key = item_key(name)
    if key:
        for item in ch["items"]:
            if item["key"] == key:
                return item
    return None


def ensure_items(ch):
    """Turn a character that predates item records into one that has them.

    Older saves hold display strings - in the campaign's language, counts and all
    ("เสบียง (5)") - so the count is split off and the name mapped back to a rules key
    through the translation table. Anything unrecognised survives as itself with no key:
    losing somebody's loot to a refactor is not an acceptable price for tidiness.
    Never rebuilds records that are already there.
    """
    if not isinstance(ch.get("items"), list):
        ch["items"] = []
        for entry in ch.get("inventory", []):
            name, qty = split_qty(entry)
            if not name:
                continue
            existing = _find_item(ch, name)
            if existing and _norm(existing["name"]) == _norm(name):
                existing["qty"] += qty
            else:
                ch["items"].append({"name": name, "key": item_key(name), "qty": qty})
    for item in ch["items"]:
        item["qty"] = max(1, int(item.get("qty") or 1))
        item.setdefault("key", item_key(item["name"]))
    return sync_inventory(ch)


def add_item(ch, text):
    """Pick something up. Returns (name as shown, how many)."""
    ensure_items(ch)
    name, qty = split_qty(text)
    if not name:
        return None, 0
    existing = _find_item(ch, name)
    if existing:
        existing["qty"] += qty
        name = existing["name"]
    else:
        ch["items"].append({"name": name, "key": item_key(name), "qty": qty})
    sync_inventory(ch)
    return name, qty


def remove_item(ch, text):
    """Use up, lose or hand over something. Returns (name, how many went, how many left),
    or (None, 0, 0) if the character is not carrying it."""
    ensure_items(ch)
    name, qty = split_qty(text)
    item = _find_item(ch, name)
    if item is None:
        return None, 0, 0
    gone = min(qty, item["qty"])
    item["qty"] -= gone
    if item["qty"] <= 0:
        ch["items"].remove(item)
    sync_inventory(ch)
    return item["name"], gone, max(0, item["qty"])


def carries(ch, name):
    """The record for `name` if the character has it."""
    ensure_items(ch)
    return _find_item(ch, name)


# --------------------------------------------------------------------------- #
# encumbrance
# --------------------------------------------------------------------------- #

# Optional rules a table can turn on. Each is off unless the campaign says otherwise, and
# anything not listed here is dropped on the way in rather than stored.
HOUSE_RULES = {
    "variant_encumbrance": False,     # slower past STR x 5, worse past STR x 10
    "battle_map": False,              # a grid with tokens and fog - see game/battlemap.py
    "ambience": False,                # the DM sets a mood; browsers that opted in play it
}


def clean_house(house):
    """Only known house rules, as booleans."""
    house = house if isinstance(house, dict) else {}
    return {k: bool(house.get(k, default)) for k, default in HOUSE_RULES.items()}


def encumbrance(ch, variant=False):
    """How much is carried against what the character can carry.

    The SRD rule is a single line: capacity is STR x 15 lb. The variant - slower past
    STR x 5, slower still and at a disadvantage past STR x 10 - is a table's choice, so
    it is only applied when the campaign has turned it on.
    """
    ensure_items(ch)
    strength = ch["abilities"]["STR"]
    carried = sum(WEIGHTS[i["key"]] * i["qty"] for i in ch["items"] if i["key"] in WEIGHTS)
    unweighed = sum(1 for i in ch["items"] if i["key"] not in WEIGHTS)
    out = {"carried": carried, "capacity": strength * 15, "unweighed": unweighed,
           "status": None, "speed_penalty": 0, "disadvantage": False}
    if carried > strength * 15:
        out.update(status="over capacity", speed_penalty=None)
    elif variant and carried > strength * 10:
        out.update(status="heavily encumbered", speed_penalty=20, disadvantage=True)
    elif variant and carried > strength * 5:
        out.update(status="encumbered", speed_penalty=10)
    return out


def encumbrance_line(ch, variant=False):
    """For the DM: '139 of 225 lb' plus whatever it costs them."""
    e = encumbrance(ch, variant)
    line = f"{e['carried']} of {e['capacity']} lb"
    if e["unweighed"]:
        line += f" (+{e['unweighed']} unweighed item{'s' if e['unweighed'] > 1 else ''})"
    if e["status"] == "over capacity":
        line += " - OVER CAPACITY: can only push or drag, speed 5 ft"
    elif e["status"] == "heavily encumbered":
        line += (" - HEAVILY ENCUMBERED: speed -20 ft, disadvantage on STR, DEX and CON "
                 "checks, attacks and saves")
    elif e["status"] == "encumbered":
        line += " - ENCUMBERED: speed -10 ft"
    return line


# --------------------------------------------------------------------------- #
# spell slots
# --------------------------------------------------------------------------- #
#
# SRD 5.1 tables. Row n is character level n+1; column m is spell level m+1. Only the
# maximum is a table lookup - `ch["slots_used"]` records what has been spent, so a
# level-up raises the ceiling without anyone touching the sheet.

FULL_CASTER = [
    [2], [3], [4, 2], [4, 3], [4, 3, 2], [4, 3, 3], [4, 3, 3, 1], [4, 3, 3, 2],
    [4, 3, 3, 3, 1], [4, 3, 3, 3, 2], [4, 3, 3, 3, 2, 1], [4, 3, 3, 3, 2, 1],
    [4, 3, 3, 3, 2, 1, 1], [4, 3, 3, 3, 2, 1, 1], [4, 3, 3, 3, 2, 1, 1, 1],
    [4, 3, 3, 3, 2, 1, 1, 1], [4, 3, 3, 3, 2, 1, 1, 1, 1], [4, 3, 3, 3, 3, 1, 1, 1, 1],
    [4, 3, 3, 3, 3, 2, 1, 1, 1], [4, 3, 3, 3, 3, 2, 2, 1, 1],
]
HALF_CASTER = [   # SRD 5.1: a ranger casts nothing at level 1
    [], [2], [3], [3], [4, 2], [4, 2], [4, 3], [4, 3], [4, 3, 2], [4, 3, 2],
    [4, 3, 3], [4, 3, 3], [4, 3, 3, 1], [4, 3, 3, 1], [4, 3, 3, 2], [4, 3, 3, 2],
    [4, 3, 3, 3, 1], [4, 3, 3, 3, 1], [4, 3, 3, 3, 2], [4, 3, 3, 3, 2],
]
CASTERS = {"Wizard": FULL_CASTER, "Cleric": FULL_CASTER, "Bard": FULL_CASTER,
           "Ranger": HALF_CASTER}

ORDINAL = {1: "1st", 2: "2nd", 3: "3rd"}


def ordinal(n):
    return ORDINAL.get(n, f"{n}th")


def casts_spells(ch):
    return ch.get("class") in CASTERS


def slot_max(ch):
    """{spell level: slots} for this class at this level. Empty for non-casters."""
    table = CASTERS.get(ch.get("class"))
    if not table:
        return {}
    row = table[max(1, min(20, int(ch.get("level") or 1))) - 1]
    return {level: count for level, count in enumerate(row, start=1)}


def slots_left(ch):
    """{spell level: (left, max)}."""
    used = ch.get("slots_used") or {}
    return {lvl: (max(0, top - int(used.get(str(lvl), 0))), top)
            for lvl, top in slot_max(ch).items()}


def use_slot(ch, level):
    """Spend one slot of `level`. Returns (ok, message).

    Refuses - and says why - when there is no such slot or none left. The refusal is
    the mechanic: a caster who has spent everything cannot cast, however the scene
    would like them to.
    """
    try:
        level = int(level)
    except (TypeError, ValueError):
        return False, "say which spell level to spend, 1 to 9"
    if not casts_spells(ch):
        return False, f"{ch['name']} is a {ch['class']} and has no spell slots."
    left = slots_left(ch)
    if level < 1:
        return False, "cantrips need no slot - just cast it."
    if level not in left:
        have = ", ".join(ordinal(l) for l in left) or "none yet at this level"
        return False, (f"{ch['name']} has no {ordinal(level)}-level slots at level "
                       f"{ch['level']} (has: {have}).")
    remaining, top = left[level]
    if remaining <= 0:
        higher = [ordinal(l) for l, (r, _) in left.items() if l > level and r > 0]
        hint = (f" They could cast it with a higher slot: {', '.join(higher)}."
                if higher else " Every slot that could cast it is spent.")
        return False, (f"{ch['name']} has no {ordinal(level)}-level slots left "
                       f"(0 of {top}).{hint} A long rest restores them.")
    used = ch.setdefault("slots_used", {})
    used[str(level)] = int(used.get(str(level), 0)) + 1
    return True, (f"{ch['name']} spends a {ordinal(level)}-level slot "
                  f"({remaining - 1} of {top} left).")


def long_rest(ch):
    """Eight hours: hit points and spell slots come back. Returns what changed."""
    before = ch["hp"]
    ch["hp"] = ch["max_hp"]
    had = {lvl: r for lvl, (r, _) in slots_left(ch).items()}
    ch["slots_used"] = {}
    return {"hp_from": before, "hp_to": ch["hp"],
            "slots_restored": sum(top - had[lvl] for lvl, top in slot_max(ch).items())}


def slots_line(ch):
    """For the DM: '1st 3/4, 2nd 0/3'."""
    left = slots_left(ch)
    if not left:
        return "none at this level" if casts_spells(ch) else None
    return ", ".join(f"{ordinal(l)} {r}/{t}" for l, (r, t) in left.items())


# --------------------------------------------------------------------------- #
# combat
# --------------------------------------------------------------------------- #
#
# A fight belongs to the campaign, not to a character:
#
#     {"round": 2, "turn": 1, "order": [{"name": "Vess", "pc": True, "bonus": 3,
#                                         "roll": 17, "total": 20, "tie": 11}, ...]}
#
# and is None outside one. `turn` indexes `order`. Monsters live only here, by name - they
# have no sheet, and the DM keeps their hit points in the narration, as it always has.
#
# The order guides the table; it does not gag it. A player who acts out of turn is not
# refused - the DM is told, and treats it as a reaction or holds it. A strict queue that
# stops the whole campaign because one friend went to make tea is worse than no queue.

MAX_COMBATANTS = 30


def initiative_bonus(ch):
    return modifier(ch["abilities"]["DEX"])


def _entry(name, bonus, pc):
    roll = random.randint(1, 20)
    # ties go to the higher bonus, then to a d20 rolled now and kept - so the order is
    # settled once, in Python, and never re-sorted differently on the next read
    return {"name": name, "pc": pc, "bonus": int(bonus), "roll": roll,
            "total": roll + int(bonus), "tie": random.randint(1, 20)}


def _ordered(order):
    return sorted(order, key=lambda e: (-e["total"], -e["bonus"], -e["tie"]))


def current_turn(combat):
    """The entry whose turn it is, or None."""
    if not combat or not combat.get("order"):
        return None
    return combat["order"][combat["turn"] % len(combat["order"])]


def join_combat(combat, characters, npcs=()):
    """Roll initiative for everyone not already in the fight, starting one if needed.

    Every character at the table is rolled for - nobody is left out of a fight because
    the DM forgot them. Monsters come from `npcs`: [{"name", "bonus"}]. Called again
    mid-fight it adds the newcomers (reinforcements, a player who just sat down)
    without disturbing whose turn it is. Returns (combat, the entries just rolled).
    """
    combat = {"round": 1, "turn": 0, "order": []} if not combat else {
        "round": combat["round"], "turn": combat["turn"], "order": list(combat["order"])}
    taken = {_norm(e["name"]) for e in combat["order"]}
    rolled = []
    for ch in characters:
        if _norm(ch["name"]) not in taken:
            rolled.append(_entry(ch["name"], initiative_bonus(ch), True))
            taken.add(_norm(ch["name"]))
    for npc in npcs or ():
        base = " ".join(str(npc.get("name") or "").split())[:40] or "Foe"
        name, n = base, 2
        while _norm(name) in taken:              # two goblins are Goblin and Goblin 2
            name, n = f"{base} {n}", n + 1
        bonus = max(-5, min(15, int(npc.get("bonus") or 0)))
        rolled.append(_entry(name, bonus, False))
        taken.add(_norm(name))
    if len(combat["order"]) + len(rolled) > MAX_COMBATANTS:
        raise ValueError(f"a fight holds at most {MAX_COMBATANTS} combatants")

    acting = current_turn(combat)
    combat["order"] = _ordered(combat["order"] + rolled)
    if acting:                                   # whoever was acting still is
        combat["turn"] = next(i for i, e in enumerate(combat["order"])
                              if e["name"] == acting["name"])
    return combat, rolled


def next_turn(combat, remove=()):
    """End the current combatant's turn, dropping anyone in `remove` - the fallen, the
    fled. Returns (combat or None if nobody is left, whether a new round began, the
    names actually removed)."""
    old, turn = combat["order"], combat["turn"] % max(1, len(combat["order"]))
    gone = {_norm(n) for n in remove or ()}
    kept = [e for e in old if _norm(e["name"]) not in gone]
    removed = [e["name"] for e in old if _norm(e["name"]) in gone]
    if not kept:
        return None, False, removed
    # where the turn goes: past the current combatant if they are still standing,
    # or to whoever now stands in their place if they are not
    before = sum(1 for e in old[:turn] if _norm(e["name"]) not in gone)
    stays = _norm(old[turn]["name"]) not in gone
    nxt, round_no, wrapped = before + (1 if stays else 0), combat["round"], False
    if nxt >= len(kept):
        nxt, round_no, wrapped = 0, round_no + 1, True
    return {"round": round_no, "turn": nxt, "order": kept}, wrapped, removed


def combat_view(combat, characters=()):
    """The fight as the DM is shown it, every turn."""
    if not combat:
        return None
    acting = current_turn(combat)
    view = {
        "round": combat["round"],
        "whose_turn": acting["name"] + ("" if acting["pc"] else " (NPC - you run it)"),
        "order": [f"{e['name']} {e['total']}" + ("" if e["pc"] else " (NPC)")
                  for e in combat["order"]],
    }
    fighting = {_norm(e["name"]) for e in combat["order"]}
    missing = [c["name"] for c in characters if _norm(c["name"]) not in fighting]
    if missing:
        view["not_in_initiative"] = missing     # joined mid-fight: roll them in
    return view


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
    # the kit becomes item records, then starting armour and shield go on, and AC is
    # worked out from them
    ensure_items(ch)
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
    load = encumbrance(ch)
    lines.append(i18n.cli("carrying", lang, load["carried"], load["capacity"])
                 + (f"  [{load['status']}]" if load["status"] else ""))
    if casts_spells(ch):
        lines.append(i18n.cli("slots", lang) + " " + slots_line(ch))
    if ch["conditions"]:
        lines.append(i18n.cli("conditions", lang) + " " + ", ".join(ch["conditions"]))
    return "\n".join(lines)


def state_block(characters, lang="en", house=None):
    """The party state handed to the DM each turn.

    `house` is the campaign's optional rules - today just `variant_encumbrance`.
    """
    variant = bool((house or {}).get("variant_encumbrance"))
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
            "inventory": ensure_items(ch)["inventory"], "conditions": ch["conditions"],
            "carrying": encumbrance_line(ch, variant),
            **({"spell_slots": slots_line(ch)} if casts_spells(ch) else {}),
            "status": "UNCONSCIOUS AT 0 HP" if ch["hp"] == 0 else "conscious",
        })
    return json.dumps(party, ensure_ascii=False)
