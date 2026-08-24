"""AI Dungeon Master - terminal client.

The same DM, dice, and rules as the web app (see server.py); this is just a
different front end. Solo play, saved to saves/<name>.json.

    pip install anthropic
    set ANTHROPIC_API_KEY=sk-ant-...
    python dnd.py
"""

import asyncio
import json
import os
import re
import sys
from datetime import datetime

from game import dm, i18n, providers, rules

SAVE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "saves")
WRAP = 92

# --------------------------------------------------------------------------- #
# terminal
# --------------------------------------------------------------------------- #

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

if os.name == "nt":
    os.system("")  # enable ANSI escapes in cmd.exe / older terminals

C = {
    "dm":   "\033[38;5;180m",
    "dice": "\033[38;5;114m",
    "sys":  "\033[38;5;110m",
    "warn": "\033[38;5;209m",
    "dim":  "\033[2m",
    "bold": "\033[1m",
    "off":  "\033[0m",
}


def say(text, color="sys"):
    print(f"{C[color]}{text}{C['off']}")


def rule(label=""):
    bar = "-" * max(0, WRAP - len(label) - 2)
    say(f"{label} {bar}" if label else "-" * WRAP, "dim")


# --------------------------------------------------------------------------- #
# saves
# --------------------------------------------------------------------------- #

def save_path(name):
    # keep non-Latin names intact - strip only what a filesystem can't take, or two
    # Thai-named characters would collapse onto the same save file
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(" ._") or "adventurer"
    return os.path.join(SAVE_DIR, f"{safe[:60]}.json")


def save_game(character, history, log, lang="en"):
    os.makedirs(SAVE_DIR, exist_ok=True)
    path = save_path(character["name"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"version": 3, "saved_at": datetime.now().isoformat(timespec="seconds"),
                   "lang": lang, "character": character, "history": history, "log": log},
                  f, indent=2, ensure_ascii=False)
    return path


def list_saves():
    if not os.path.isdir(SAVE_DIR):
        return []
    return sorted(f for f in os.listdir(SAVE_DIR) if f.endswith(".json"))


def load_save(filename):
    with open(os.path.join(SAVE_DIR, filename), encoding="utf-8") as f:
        data = json.load(f)
    return (data["character"], data.get("history", []), data.get("log", []),
            data.get("lang", "en"))


# --------------------------------------------------------------------------- #
# setup
# --------------------------------------------------------------------------- #

def ask(prompt, options=None, default=None, lang="en"):
    while True:
        try:
            raw = input(f"{C['bold']}{prompt}{C['off']} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)
        if not raw and default is not None:
            return default
        if not options:
            if raw:
                return raw
            continue
        for opt in options:
            if raw.lower() == opt.lower() or (raw.isdigit() and int(raw) == options.index(opt) + 1):
                return opt
        say("  " + i18n.cli("pick_one", lang, ", ".join(options)), "warn")


def choose_language():
    codes = list(i18n.LANGUAGES)
    labels = [f"{i + 1}. {i18n.LANGUAGES[c]['native']}" for i, c in enumerate(codes)]
    say("  Language / ภาษา: " + ", ".join(labels), "dim")
    pick = ask("Language (name or number)?",
               [i18n.LANGUAGES[c]["native"] for c in codes],
               i18n.LANGUAGES[codes[0]]["native"])
    for c in codes:
        if i18n.LANGUAGES[c]["native"] == pick:
            return c
    return "en"


def create_flow(lang):
    rule(i18n.cli("new_character", lang))
    name = ask(i18n.cli("ask_name", lang), lang=lang)

    # show localised labels, but keep the English keys the rules are written against
    race_labels = [i18n.name(r, lang) for r in rules.RACES]
    class_labels = [i18n.name(c, lang) for c in rules.CLASSES]

    say("  " + i18n.cli("races", lang) + " "
        + ", ".join(f"{i + 1}. {r}" for i, r in enumerate(race_labels)), "dim")
    race = rules.RACES[race_labels.index(
        ask(i18n.cli("ask_race", lang), race_labels, lang=lang))]

    say("  " + i18n.cli("classes", lang) + " "
        + ", ".join(f"{i + 1}. {c}" for i, c in enumerate(class_labels)), "dim")
    klass = list(rules.CLASSES)[class_labels.index(
        ask(i18n.cli("ask_class", lang), class_labels, lang=lang))]

    while True:
        ch = rules.new_character(name, race, klass, lang=lang)
        print()
        say(rules.sheet(ch, lang), "sys")
        if ask("\n" + i18n.cli("keep_stats", lang), ["y", "n"], "y", lang) == "y":
            return ch
        say("  " + i18n.cli("rerolling", lang), "dim")


# --------------------------------------------------------------------------- #
# play
# --------------------------------------------------------------------------- #

async def take_turn(state, history, character, action, log, lang="en"):
    """Stream one DM turn to the terminal."""
    party = [character]
    text_parts = []
    printing = False

    async for event in dm.take_turn(history, party, character["name"], action, lang,
                                    state["backend"]):
        kind = event["kind"]
        if kind == "delta":
            if not printing:
                print(C["dm"], end="")
                printing = True
            print(event["text"], end="", flush=True)
        elif kind == "narration":
            if printing:
                print(C["off"])
                printing = False
            text_parts.append(event["text"])
        elif kind == "dice":
            if printing:
                print(C["off"])
                printing = False
            say(f"  [dice] {event['reason']}: {event['detail']}", "dice")
        elif kind == "sheet":
            say(f"  [sheet] {event['character']}: {event['summary']}", "dice")
        elif kind == "switch":
            if printing:
                print(C["off"])
                printing = False
            say(f"  [AI] {event['from']} -> {event['label']}  ({event['reason']})", "warn")
        elif kind == "backend":
            state["backend"] = event["backend"]   # stay on whoever picked up the turn
        elif kind == "error":
            say(f"\n  {event['text']}", "warn")

    if printing:
        print(C["off"])
    if text_parts:
        log.append({"at": datetime.now().isoformat(timespec="seconds"),
                    "player": action, "dm": "\n\n".join(text_parts)})


async def main():
    if not providers.any_available():
        say(i18n.cli("no_key", "en"), "warn")
        say("  PowerShell:  $env:ANTHROPIC_API_KEY = 'sk-ant-...'", "dim")
        say('  Permanent:   setx ANTHROPIC_API_KEY "sk-ant-..."   (then reopen the terminal)', "dim")
        say("  Or a backup AI:  setx OPENROUTER_API_KEY \"sk-or-...\"", "dim")
        sys.exit(1)

    state = {"backend": providers.default_id()}

    print()
    rule()
    say(f"{C['bold']}  A I   D U N G E O N   M A S T E R{C['off']}", "dm")
    rule()

    lang = choose_language()
    current = providers.get(state["backend"])
    say(f"  {current.label if current else state['backend']} - "
        + i18n.cli("tagline", lang), "dim")
    say("  " + i18n.cli("web_hint", lang), "dim")
    rule()

    character = history = log = None
    saves = list_saves()
    if saves:
        say("\n" + i18n.cli("existing", lang), "sys")
        for i, s in enumerate(saves, 1):
            say(f"  {i}. {s[:-5]}", "dim")
        pick = ask(i18n.cli("load_prompt", lang), default="", lang=lang)
        if pick.isdigit() and 1 <= int(pick) <= len(saves):
            character, history, log, lang = load_save(saves[int(pick) - 1])
            say("\n" + i18n.cli("loaded", lang, character["name"]), "sys")
            print()
            say(rules.sheet(character, lang), "sys")
            rule()
            await take_turn(state, history, character,
                            "[The player has returned. Briefly recap where we left off in 2-3 "
                            "sentences, then continue the scene.]", log, lang)

    if character is None:
        character = create_flow(lang)
        history, log = [], []
        rule()
        say(i18n.cli("help", lang), "dim")
        rule()
        await take_turn(state, history, character,
                        f"Begin the campaign. {character['name']} is a level 1 "
                        f"{character['race']} {character['class']}. Invent a hook that puts them "
                        "in immediate motion - no tavern, no scroll of exposition. Open in the "
                        "middle of something happening. Establish where they are, what is wrong, "
                        "and one detail that will matter later. Then hand control to the player.",
                        log, lang)

    while True:
        print()
        try:
            raw = input(f"{C['bold']}> {C['off']}").strip()
        except (EOFError, KeyboardInterrupt):
            raw = "/quit"
        if not raw:
            continue

        low = raw.lower()
        if low in ("/quit", "/q", "/exit"):
            path = save_game(character, history, log, lang)
            say("\n" + i18n.cli("farewell", lang, path, character["name"]), "sys")
            return
        if low in ("/help", "/?"):
            say(i18n.cli("help", lang), "dim")
            continue
        if low in ("/sheet", "/c"):
            print()
            say(rules.sheet(character, lang), "sys")
            continue
        if low == "/save":
            say(i18n.cli("saved", lang, save_game(character, history, log, lang)), "sys")
            continue
        if low.startswith("/ai"):
            arg = raw[3:].strip()
            usable = [b for b in providers.BACKENDS if b.available()]
            if arg:
                pick = next((b for b in usable
                             if b.id == arg or b.label.lower() == arg.lower()), None)
                if pick:
                    state["backend"] = pick.id
                    say(f"  {i18n.cli('ai_now', lang, pick.label)}", "sys")
                else:
                    say(f"  {i18n.cli('ai_unknown', lang, arg)}", "warn")
            else:
                say("  " + i18n.cli("ai_list", lang), "dim")
                for b in providers.BACKENDS:
                    mark = ">" if b.id == state["backend"] else " "
                    tag = "" if b.available() else "  (no key)"
                    say(f"  {mark} {b.label}  [{b.id}]{tag}", "dim")
            continue
        if low.startswith("/roll"):
            try:
                _, detail, _ = rules.roll_notation(raw[5:].strip() or "1d20")
                say(f"  [dice] {detail}", "dice")
            except ValueError as e:
                say(f"  {e}", "warn")
            continue
        if low == "/recap":
            await take_turn(state, history, character,
                            "[Out of character: give me a tight recap of the story so far - "
                            "who I've met, what I'm carrying that matters, and what's unresolved.]",
                            log, lang)
            continue
        if low.startswith("/"):
            say("  " + i18n.cli("unknown_cmd", lang, raw.split()[0]), "warn")
            continue

        await take_turn(state, history, character, raw, log, lang)

        if character["hp"] == 0:
            say("\n  " + i18n.cli("down", lang), "warn")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print()
