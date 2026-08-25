"""The Dungeon Master: prompt, tools, and the async turn loop.

The model narrates and judges. Every die and every point of damage goes through
`rules.py` here in Python, so the DM cannot invent a roll or lose track of your HP.
"""

import os

from . import i18n, lore, providers, rules, store

MAX_TOOL_ROUNDS = 12

# How many player turns must pass between two DM-drawn pictures. Drawing costs real
# money on every provider that offers it, and the model is not the one paying, so this
# is a number enforced in code rather than a request in the prompt.
ART_EVERY_TURNS = int(os.environ.get("DM_ART_EVERY_TURNS", "6"))


SYSTEM = """\
You are the Dungeon Master for a tabletop roleplaying campaign in the style of \
D&D 5th edition. Human players control the party; you control the world, every NPC, \
and the rules.

HOW YOU NARRATE
- Second person, present tense: "You push the door open and the smell hits you first."
- 120-250 words per turn. Vivid but tight. Cut throat-clearing; open on the image or the action.
- Engage more than sight: sound, smell, temperature, the weight of things.
- NPCs get distinct voices, wants, and secrets. They lie, bargain, and remember.
- End most turns by handing control back - a question, a threat closing in, a choice with teeth.
  Never present a numbered menu of options unless the player asks for one.

HOW YOU RUN THE RULES
- Call roll_dice for EVERY die. Never state a result you did not roll. Set the DC before rolling,
  say it out loud, then roll and narrate the outcome honestly - including failure.
- Typical DCs: 10 easy, 15 moderate, 20 hard, 25 very hard.
- Ability checks: 1d20 + the relevant modifier from the state block. Attacks: 1d20 + modifier
  (+2 proficiency at level 1-4). Then roll damage separately.
- Call update_character for every HP, XP, gold, item, or condition change, naming the character
  it applies to. The tool result is the truth; if it contradicts what you just narrated, correct
  yourself in the next line.
- Award 25-100 XP for meaningful encounters. 300 XP is level 2, 900 is level 3, 2700 is level 4.
- Don't roll for trivial actions. Walking across a room is not a check.

RUNNING THE PARTY
- Each player message is labelled with the character who acted. Only that player's character did it.
- Never narrate what another player's character says, thinks, feels, or decides. Ever.
- Address characters by name. If someone has been quiet for a few turns, aim a hook at them -
  an NPC turns to them, something moves on their side of the room.
- With a party of one, ignore all of this and run a tight solo adventure.
- In combat, keep the order moving in the fiction ("the ghoul is already on Vess") rather than
  demanding a rigid initiative count.

HOW YOU HANDLE PLAYERS
- Say yes to creative ideas, or "yes, but" - reward invention with an easier DC, not a lecture.
- If they attempt something impossible, tell them plainly in-world rather than silently failing them.
- Let consequences land. A failed roll should change the situation, not just stall it.
- If they go somewhere you haven't described, invent it confidently and stay consistent afterward.
- Death is possible but earned. At 0 HP a character falls unconscious and rolls death saves.

Keep continuity: names, injuries, debts, and promises persist. This is their story, not yours.
"""


TOOLS = [
    {
        "name": "roll_dice",
        "description": (
            "Roll real dice. You MUST use this for every check, attack, saving throw, "
            "damage roll, and random determination. Never invent a die result yourself."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "notation": {"type": "string",
                             "description": "Dice notation, e.g. '1d20+5', '2d6', '8d6-2'."},
                "reason": {"type": "string",
                           "description": "What the roll is for, e.g. 'Vess: Stealth vs DC 14'."},
                "mode": {"type": "string", "enum": ["normal", "advantage", "disadvantage"],
                         "description": "Roll mode; advantage/disadvantage applies to a single die."},
            },
            "required": ["notation", "reason", "mode"],
            "additionalProperties": False,
        },
    },
    {
        "name": "update_character",
        "description": (
            "Apply a mechanical change to one character's sheet. Call this whenever a "
            "character takes damage, heals, gains XP or gold, picks up or loses items, "
            "levels up, or gains/loses a condition. This is the only source of truth for state."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "character_name": {"type": "string",
                                   "description": "Exact name of the character this applies to."},
                "hp_change": {"type": "integer",
                              "description": "Negative for damage, positive for healing. 0 if none."},
                "xp_gain": {"type": "integer", "description": "XP awarded. 0 if none."},
                "gold_change": {"type": "integer", "description": "Gold gained or spent. 0 if none."},
                "add_items": {"type": "array", "items": {"type": "string"},
                              "description": "Items gained."},
                "remove_items": {"type": "array", "items": {"type": "string"},
                                 "description": "Items consumed or lost."},
                "add_conditions": {"type": "array", "items": {"type": "string"},
                                   "description": "e.g. 'poisoned', 'prone'."},
                "remove_conditions": {"type": "array", "items": {"type": "string"},
                                      "description": "Conditions that ended."},
                "level_up": {"type": "boolean",
                             "description": "True to advance one level (rolls new max HP)."},
                "reason": {"type": "string", "description": "Short reason for the change."},
            },
            "required": ["character_name", "hp_change", "xp_gain", "gold_change", "add_items",
                         "remove_items", "add_conditions", "remove_conditions", "level_up",
                         "reason"],
            "additionalProperties": False,
        },
    },
]

# Only offered to the DM when the campaign has documents to search - a tool with nothing
# behind it is worse than no tool, because the model will still reach for it.
LORE_TOOL = {
    "name": "search_lore",
    "description": (
        "Search the players' own campaign documents and read back the passages that "
        "match. Use it before inventing anything their notes might already settle: a "
        "name, a relationship, a place, what happened in an earlier session. Search for "
        "a short distinctive phrase - a name as they write it - rather than a sentence."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string",
                      "description": "Short phrase to find, in the campaign's own language."},
            "document": {"type": "string",
                         "description": "Narrow to one document by name, or '' to search all."},
        },
        "required": ["query", "document"],
        "additionalProperties": False,
    },
}


# Offered only when some backend can actually draw. Same reasoning as the lore tool:
# a model handed a tool with nothing behind it will still reach for it, and here the
# reach costs a turn's narration to a tool call that was always going to fail.
IMAGE_TOOL = {
    "name": "draw_scene",
    "description": (
        "Illustrate what is in front of the party right now. The picture is drawn while "
        "you keep narrating and appears in the feed for everyone a moment later, so "
        "don't announce it, wait for it, or describe it as arriving. "
        "Save it for a moment that earns a picture - a first sight of somewhere, a "
        "creature revealed, the aftermath of a fight - not for every turn: it costs "
        "money and it is rate limited. If the tool answers NOT NOW, that is normal; "
        "carry on narrating and do not call it again this turn."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string",
                       "description": ("What to draw, for an image model that has not "
                                       "read the story: subject, setting, light, mood. "
                                       "Describe the picture, not the plot. English "
                                       "works best here whatever language you narrate in.")},
            "caption": {"type": "string",
                        "description": ("A short line shown under the picture, in the "
                                        "language you are narrating in.")},
        },
        "required": ["prompt", "caption"],
        "additionalProperties": False,
    },
}


def tools_for(cid):
    """The tool list for this campaign: the base two, plus lore when there is any,
    plus drawing when some backend can draw."""
    tools = list(TOOLS)
    if cid and store.lore_documents(cid):
        tools.append(LORE_TOOL)
    # no campaign means no feed and nowhere to file the picture - that is the terminal
    # client, which is text and stays text
    if cid and providers.image_backend() is not None:
        tools.append(IMAGE_TOOL)
    return tools


def _find(characters, name):
    name = (name or "").strip().lower()
    for ch in characters:
        if ch["name"].lower() == name:
            return ch
    # tolerate the DM using a first name or a near-miss
    for ch in characters:
        if name and (name in ch["name"].lower() or ch["name"].lower().startswith(name)):
            return ch
    return None


def _draw_scene(args, cid):
    """Ask for an illustration. Nothing is drawn here - drawing takes half a minute and
    this runs in the middle of a turn, so all that happens is the rate limit is checked
    and a request is emitted. Whoever is driving the turn does the slow part out of band.

    Every refusal is phrased for the model rather than the player: it is a tool result,
    and the DM should absorb it and keep narrating instead of telling the table about it.
    """
    if providers.image_backend() is None:
        return "ERROR: nothing here can draw. Describe the scene in words instead.", None
    if not cid:
        return "ERROR: this campaign cannot show pictures.", None

    prompt = (args.get("prompt") or "").strip()[:800]
    if not prompt:
        return "ERROR: say what to draw.", None

    if not store.claim_art_slot(cid, ART_EVERY_TURNS):
        return (f"NOT NOW: the table was illustrated recently. Another picture is "
                f"available after {ART_EVERY_TURNS} more player turns. Keep narrating "
                f"and do not mention this."), None

    return ("Drawing it now; it will appear in the feed by itself shortly. Carry on "
            "with the scene and do not refer to the picture.",
            {"kind": "draw", "prompt": prompt,
             "caption": (args.get("caption") or "").strip()[:200]})


def run_tool(name, args, characters, lang="en", cid=None):
    """Execute a DM tool call. Returns (tool_result_text, event_or_None)."""
    if name == "search_lore":
        if not cid:
            return "ERROR: this campaign has no documents to search.", None
        found = lore.search(store.lore_texts(cid), args.get("query"),
                            args.get("document") or None)
        return found, {"kind": "lore", "query": (args.get("query") or "")[:80]}

    if name == "draw_scene":
        return _draw_scene(args, cid)

    if name == "roll_dice":
        try:
            total, detail, crit = rules.roll_notation(args.get("notation"),
                                                      args.get("mode", "normal"))
        except ValueError as e:
            return f"ERROR: {e}", None
        reason = args.get("reason", "roll")
        return (f"{detail}  (total: {total})",
                {"kind": "dice", "reason": reason, "detail": detail,
                 "total": total, "crit": crit})

    if name != "update_character":
        return f"ERROR: unknown tool {name}", None

    ch = _find(characters, args.get("character_name"))
    if ch is None:
        known = ", ".join(c["name"] for c in characters)
        return f"ERROR: no character named {args.get('character_name')!r}. Party: {known}", None

    log = []
    # structured twin of `log`, so the browser can render this in the player's language
    # instead of showing an English sentence built here
    changes = []

    hp_delta = int(args.get("hp_change") or 0)
    if hp_delta:
        before = ch["hp"]
        ch["hp"] = max(0, min(ch["max_hp"], ch["hp"] + hp_delta))
        log.append(f"HP {before} -> {ch['hp']}/{ch['max_hp']}")
        changes.append({"t": "hp", "from": before, "to": ch["hp"], "max": ch["max_hp"],
                        "delta": ch["hp"] - before})
        if ch["hp"] == 0:
            log.append("NOW AT 0 HP AND UNCONSCIOUS - start death saves")
            changes.append({"t": "down"})

    if args.get("xp_gain"):
        ch["xp"] += int(args["xp_gain"])
        log.append(f"XP {ch['xp']}")
        changes.append({"t": "xp", "gain": int(args["xp_gain"]), "total": ch["xp"]})
    if args.get("gold_change"):
        delta = int(args["gold_change"])
        ch["gold"] = max(0, ch["gold"] + delta)
        log.append(f"GP {ch['gold']}")
        changes.append({"t": "gold", "delta": delta, "total": ch["gold"]})

    for item in args.get("add_items") or []:
        ch["inventory"].append(item)
        log.append(f"+ {item}")
        changes.append({"t": "item+", "item": item})
    for item in args.get("remove_items") or []:
        match = next((i for i in ch["inventory"] if i.lower() == item.lower()), None)
        if match:
            ch["inventory"].remove(match)
            log.append(f"- {match}")
            changes.append({"t": "item-", "item": match})
        else:
            log.append(f"(not carried: {item})")

    for cond in args.get("add_conditions") or []:
        if cond not in ch["conditions"]:
            ch["conditions"].append(cond)
            log.append(f"condition: {cond}")
            changes.append({"t": "cond+", "cond": cond})
    for cond in args.get("remove_conditions") or []:
        if cond in ch["conditions"]:
            ch["conditions"].remove(cond)
            log.append(f"cured: {cond}")
            changes.append({"t": "cond-", "cond": cond})

    if args.get("level_up"):
        gain = rules.level_up(ch)
        log.append(f"LEVEL {ch['level']}! max HP +{gain} -> {ch['max_hp']}, fully healed")
        changes.append({"t": "level", "level": ch["level"], "gain": gain, "max": ch["max_hp"]})

    summary = "; ".join(log) or "no change"
    result = f"{summary}\nParty state: {rules.state_block(characters, lang)}"
    return result, {"kind": "sheet", "character": ch["name"], "summary": summary,
                    "changes": changes}


def build_prompt(characters, actor, action, lang="en"):
    """One player turn, labelled so the DM knows who acted."""
    who = f"{actor} acts:" if actor else "The table says:"
    return (f"<party_state>{rules.state_block(characters, lang)}</party_state>\n\n"
            f"{who} {action}")


def system_blocks(lang, cid=None):
    """Base prompt stays cached; the language instruction rides after it as its own
    block, so switching language doesn't invalidate the cached prefix.

    The document list rides last: it names what search_lore can reach, and it changes
    when someone imports more, so it must not sit inside the cached prefix either.
    """
    blocks = [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}]
    extra = i18n.NARRATION_INSTRUCTION.get(lang, "")
    if extra.strip():
        blocks.append({"type": "text", "text": extra})
    if cid:
        listing = lore.manifest(store.lore_documents(cid))
        if listing:
            blocks.append({"type": "text", "text": listing})
    return blocks


async def _run(backend, history, characters, lang, images=None, cid=None):
    """Drive one backend through a turn's tool rounds. Raises to trigger failover."""
    # a backend that owns its own tool loop (Claude Code, running on a subscription)
    # runs the whole turn itself and yields the same events this loop would
    if hasattr(backend, "run_turn"):
        async for event in backend.run_turn(system_blocks(lang, cid), history, characters,
                                            lang, images, cid):
            yield event
        return

    for round_no in range(MAX_TOOL_ROUNDS):
        message = None
        # images ride the first request only; after that the model has already seen them
        # and re-sending would pay for them again on every tool round
        turn_images = images if round_no == 0 else None
        async for chunk in backend.stream(system_blocks(lang, cid), history,
                                          tools_for(cid), turn_images):
            if chunk["type"] == "delta":
                yield {"kind": "delta", "text": chunk["text"]}
            else:
                message = chunk

        if message is None:
            raise providers.ProviderFailed(f"{backend.label}: empty response")

        if message["stop_reason"] == "refusal":
            yield {"kind": "error",
                   "text": "The DM declined to narrate that. Try steering the scene elsewhere."}
            return

        history.append({"role": "assistant", "content": message["content"]})

        text = "".join(b.get("text", "") for b in message["content"]
                       if b.get("type") == "text")
        if text.strip():
            yield {"kind": "narration", "text": text}

        if message["stop_reason"] != "tool_use":
            return

        results = []
        for block in message["content"]:
            if block.get("type") == "tool_use":
                out, event = run_tool(block["name"], dict(block.get("input") or {}),
                                      characters, lang, cid)
                if event:
                    yield event
                results.append({"type": "tool_result", "tool_use_id": block["id"],
                                "content": out})
        history.append({"role": "user", "content": results})

    yield {"kind": "error", "text": "The DM got stuck in a loop and the turn was cut short."}


async def take_turn(history, characters, actor, action, lang="en", backend_id=None,
                    images=None, cid=None):
    """Run one DM turn. Async generator of events; mutates history and characters.

    Yields {"kind": "delta"|"narration"|"dice"|"sheet"|"draw"|"switch"|"error", ...}.

    "draw" is the odd one out: it is a request rather than something that happened, and
    the caller is expected to go and generate the picture without holding up the turn.

    If the chosen AI is out of credit or rate limited, the turn restarts on the next
    configured one and emits a "switch" event naming who took over.
    """
    lang = i18n.normalise(lang)
    history.append({"role": "user", "content": build_prompt(characters, actor, action, lang)})
    mark = len(history)
    images = images or []

    order = providers.failover_order(backend_id)
    if not order:
        history.pop()
        yield {"kind": "error", "text": "No AI is configured to run this game."}
        return

    last_error = None
    for i, backend in enumerate(order):
        if i:
            # a previous backend gave up part-way; drop whatever it wrote and retry clean
            del history[mark:]
            yield {"kind": "switch", "backend": backend.id, "label": backend.label,
                   "from": order[i - 1].label, "reason": str(last_error)}
        try:
            # a backend that can't see images still gets the caption in the prompt
            usable = images if getattr(backend, "vision", False) else None
            async for event in _run(backend, history, characters, lang, usable, cid):
                yield event
            if i:
                yield {"kind": "backend", "backend": backend.id}   # persist the switch
            return
        except (providers.ProviderExhausted, providers.ProviderFailed) as e:
            last_error = e
            continue

    del history[mark:]
    history.pop()
    yield {"kind": "error", "text": f"Every AI turned the turn away ({last_error})."}
