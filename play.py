"""Dice and character-sheet tools for a DM who isn't the game server.

This is the toolbelt that lets Claude Code (or a human) run a campaign without an API
key: the narration comes from the conversation, and every die and every point of damage
comes from here - the same `game/rules.py` the web app uses. Nothing can fudge a roll,
because the roll happens in this process.

    python play.py new "Vess" Elf Rogue
    python play.py roll 1d20+3 --reason "Stealth vs DC 14"
    python play.py update Vess --hp -4 --xp 25 --add "rusty key"
    python play.py sheet Vess
    python play.py list
"""

import argparse
import json
import os
import sys
from datetime import datetime

from game import i18n, rules

SAVE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "saves")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# storage - the same files the terminal client writes
# --------------------------------------------------------------------------- #

def path_for(name):
    import re
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(" ._") or "adventurer"
    return os.path.join(SAVE_DIR, f"{safe[:60]}.json")


def load(name):
    path = path_for(name)
    if not os.path.exists(path):
        # tolerate a partial name, the way a DM would
        for f in os.listdir(SAVE_DIR) if os.path.isdir(SAVE_DIR) else []:
            if f.lower().startswith(name.lower()) and f.endswith(".json"):
                path = os.path.join(SAVE_DIR, f)
                break
        else:
            die(f"no character called {name!r} - try `python play.py list`")
    with open(path, encoding="utf-8") as f:
        return json.load(f), path


def store(data, path):
    os.makedirs(SAVE_DIR, exist_ok=True)
    data["saved_at"] = datetime.now().isoformat(timespec="seconds")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def die(msg):
    print("error: " + msg)
    sys.exit(1)


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #

def cmd_new(a):
    if a.race not in rules.RACES:
        die(f"race must be one of: {', '.join(rules.RACES)}")
    if a.klass not in rules.CLASSES:
        die(f"class must be one of: {', '.join(rules.CLASSES)}")

    char = rules.new_character(a.name, a.race, a.klass, lang=a.lang)
    path = path_for(a.name)
    if os.path.exists(path) and not a.force:
        die(f"{a.name} already exists - pass --force to reroll them")
    store({"version": 3, "lang": a.lang, "character": char, "history": [], "log": []}, path)
    print(rules.sheet(char, a.lang))
    print(f"\nsaved to {path}")


def cmd_sheet(a):
    data, _ = load(a.name)
    print(rules.sheet(data["character"], data.get("lang", "en")))


def cmd_list(a):
    if not os.path.isdir(SAVE_DIR):
        print("no characters yet - `python play.py new \"Name\" Elf Rogue`")
        return
    files = sorted(f for f in os.listdir(SAVE_DIR) if f.endswith(".json"))
    if not files:
        print("no characters yet")
        return
    for f in files:
        with open(os.path.join(SAVE_DIR, f), encoding="utf-8") as fh:
            ch = json.load(fh)["character"]
        print(f"  {ch['name']} - level {ch['level']} {ch['race']} {ch['class']}, "
              f"HP {ch['hp']}/{ch['max_hp']}")


def cmd_roll(a):
    mode = "advantage" if a.adv else "disadvantage" if a.dis else "normal"
    try:
        total, detail, crit = rules.roll_notation(a.notation, mode)
    except ValueError as e:
        die(str(e))
    line = f"{a.reason}: {detail}" if a.reason else detail
    print(line)
    if a.dc is not None:
        print(f"  -> {'SUCCESS' if total >= a.dc else 'FAILURE'} against DC {a.dc}")


def cmd_update(a):
    data, path = load(a.name)
    ch = data["character"]
    lang = data.get("lang", "en")
    log = []

    if a.hp:
        before = ch["hp"]
        ch["hp"] = max(0, min(ch["max_hp"], ch["hp"] + a.hp))
        log.append(f"HP {before} -> {ch['hp']}/{ch['max_hp']}")
        if ch["hp"] == 0:
            log.append("UNCONSCIOUS at 0 HP - death saves from here")
    if a.xp:
        ch["xp"] += a.xp
        log.append(f"XP {ch['xp']}")
    if a.gold:
        ch["gold"] = max(0, ch["gold"] + a.gold)
        log.append(f"GP {ch['gold']}")
    for item in a.add or []:
        ch["inventory"].append(item)
        log.append(f"+ {item}")
    for item in a.remove or []:
        match = next((i for i in ch["inventory"] if i.lower() == item.lower()), None)
        if match:
            ch["inventory"].remove(match)
            log.append(f"- {match}")
        else:
            log.append(f"(not carried: {item})")
    for cond in a.condition or []:
        if cond not in ch["conditions"]:
            ch["conditions"].append(cond)
            log.append(f"condition: {cond}")
    for cond in a.cure or []:
        if cond in ch["conditions"]:
            ch["conditions"].remove(cond)
            log.append(f"cured: {cond}")
    if a.level_up:
        gain = rules.level_up(ch)
        log.append(f"LEVEL {ch['level']}! max HP +{gain} -> {ch['max_hp']}, fully healed")

    store(data, path)
    print("; ".join(log) or "no change")
    print()
    print(rules.sheet(ch, lang))


def cmd_note(a):
    """Append a line to the campaign log, so the story survives between sessions."""
    data, path = load(a.name)
    data.setdefault("log", []).append(
        {"at": datetime.now().isoformat(timespec="seconds"), "note": a.text})
    store(data, path)
    print(f"noted ({len(data['log'])} entries)")


def cmd_recap(a):
    data, _ = load(a.name)
    entries = data.get("log", [])
    if not entries:
        print("nothing recorded yet")
        return
    for e in entries[-a.last:]:
        print(f"- {e.get('note') or e.get('dm', '')}")


# --------------------------------------------------------------------------- #

def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("new", help="roll up a character")
    n.add_argument("name")
    n.add_argument("race", help=", ".join(rules.RACES))
    n.add_argument("klass", metavar="class", help=", ".join(rules.CLASSES))
    n.add_argument("--lang", default="en", choices=list(i18n.LANGUAGES))
    n.add_argument("--force", action="store_true")
    n.set_defaults(fn=cmd_new)

    s = sub.add_parser("sheet", help="show a character sheet")
    s.add_argument("name")
    s.set_defaults(fn=cmd_sheet)

    ls = sub.add_parser("list", help="list characters")
    ls.set_defaults(fn=cmd_list)

    r = sub.add_parser("roll", help="roll dice")
    r.add_argument("notation", help="e.g. 1d20+3, 2d6, 8d6-2")
    r.add_argument("--reason", default="")
    r.add_argument("--dc", type=int, default=None, help="report success or failure")
    r.add_argument("--adv", action="store_true", help="roll with advantage")
    r.add_argument("--dis", action="store_true", help="roll with disadvantage")
    r.set_defaults(fn=cmd_roll)

    u = sub.add_parser("update", help="change a character sheet")
    u.add_argument("name")
    u.add_argument("--hp", type=int, default=0, help="negative for damage")
    u.add_argument("--xp", type=int, default=0)
    u.add_argument("--gold", type=int, default=0)
    u.add_argument("--add", action="append", metavar="ITEM")
    u.add_argument("--remove", action="append", metavar="ITEM")
    u.add_argument("--condition", action="append", metavar="COND")
    u.add_argument("--cure", action="append", metavar="COND")
    u.add_argument("--level-up", action="store_true")
    u.set_defaults(fn=cmd_update)

    nt = sub.add_parser("note", help="record something that happened")
    nt.add_argument("name")
    nt.add_argument("text")
    nt.set_defaults(fn=cmd_note)

    rc = sub.add_parser("recap", help="read back the campaign log")
    rc.add_argument("name")
    rc.add_argument("--last", type=int, default=20)
    rc.set_defaults(fn=cmd_recap)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
