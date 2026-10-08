"""One DM turn, start to finish, for a whole table.

Runs as a job on the task queue: `/act` and `/begin` enqueue it and answer the browser
at once, and every browser watches its event stream for the result. Everything it
touches comes through the `adapters` it is handed, so the same code serves one process
or several.
"""

import logging
import os
import re

from .. import dm, media as media_files, providers, rules
from .events import broadcast, party_payload, publish
from .memory import maybe_summarize

log = logging.getLogger("dnd")

# Seconds a player character's combat turn may sit idle before it passes. 0 - the default
# - never: a table that wants a clock opts in. When it fires, the DM narrates a moment's
# hesitation and moves on, which costs a turn's worth of tokens nobody asked for.
COMBAT_TURN_GRACE = float(os.environ.get("COMBAT_TURN_GRACE", "0") or 0)


def turn_key(cid):
    """The lock every writer of a campaign's history takes - the turn, and anyone
    adding to the history while a turn might be holding it in memory."""
    return f"turn:{cid}"


SECRETISH_RE = re.compile(r"[{\[<]|(?:sk|gsk|AIza|sk-or|sk-ant)[-_A-Za-z0-9]{6,}")


def player_safe(text):
    """An upstream failure, trimmed to what a player at the table should be shown.

    A provider's error body is written for whoever holds the key, not for six friends
    on a tunnel: it arrives as raw JSON naming the model, the account state, sometimes
    request metadata. `dm.take_turn` folds it verbatim into the `error` and `switch`
    events, which go to everyone. Keep the readable head - "Claude Opus 5: HTTP 429" -
    and drop the body. The full text still goes to the server log, where the person
    who can act on it is looking.
    """
    text = " ".join(str(text or "").split())
    cut = SECRETISH_RE.search(text)
    if cut:
        text = text[:cut.start()].rstrip(" :,-")
        text += ")" * max(0, text.count("(") - text.count(")"))
    return text[:160] or "the AI did not say why"


async def expire_effects(adapters, cid, characters):
    """A player turn has passed: effects with a duration run down.

    Only player turns count - the opening scene is not a turn anyone took. And the
    table is shown an effect wearing off, rather than finding AC quietly lower.
    """
    for ch in characters:
        expired = rules.tick_effects(ch)
        if not expired:
            continue
        before, after = rules.recompute_ac(ch)
        changes = [{"t": "fx-", "name": name} for name in expired]
        if after != before:
            changes.append({"t": "ac", "from": before, "to": after})
        await publish(adapters, cid, "sheet", {"character": ch["name"],
                                               "summary": ", ".join(expired) + " wore off",
                                               "changes": changes})


async def turn_images(adapters, cid, media_ids):
    """The pictures a player attached, read off disk for the DM to look at."""
    out = []
    for mid in (media_ids or [])[:4]:          # a hard cap: images are expensive context
        m = await adapters.repo.get_media(cid, mid)
        if not m:
            continue
        data = media_files.read(cid, m["file"])
        if data:
            out.append((data, m["mime"]))
    return out


async def run_dm_turn(adapters, cid, actor, action, media=None, claim=None):
    """One DM turn, broadcast to the whole table. Serialized per campaign.

    `media` is the ids of pictures the player attached. `claim` is a lease taken by
    `begin` that this turn must give back however it ends - the opening scene stays
    claimed for exactly as long as it takes the DM to write it.
    """
    try:
        async with adapters.locks.hold(turn_key(cid)):
            await _turn(adapters, cid, actor, action,
                        await turn_images(adapters, cid, media))
        # outside the lock: condensing the story is slow, and no turn should wait on it
        await maybe_summarize(adapters, cid)
    finally:
        if claim:
            await adapters.locks.release(claim)


async def _turn(adapters, cid, actor, action, images):
    repo = adapters.repo
    characters = await repo.party(cid)
    history = await repo.get_history(cid)
    house = await repo.campaign_house(cid)
    combat = await repo.get_combat(cid)
    memory = await repo.get_memory(cid)

    await broadcast(adapters, cid, {"kind": "thinking", "on": True})
    try:
        async for event in dm.take_turn(history, characters, actor, action,
                                        await repo.campaign_lang(cid),
                                        await repo.campaign_backend(cid)
                                        or providers.default_id(),
                                        images, cid, repo=repo, house=house,
                                        combat=combat, memory=memory):
            kind = event.pop("kind")
            if kind == "delta":
                await broadcast(adapters, cid, {"kind": "delta", **event})
                continue
            if kind == "backend":
                await repo.set_campaign_backend(cid, event["backend"])
                continue
            if kind == "draw":
                # The DM asked for an illustration. Generating one takes the better
                # part of a minute, and this loop is what feeds the narration to
                # every browser at the table - awaiting it here would freeze the
                # scene mid-sentence for everyone. It goes off on its own and lands
                # in the feed when it is ready, the way a player's upload does.
                await adapters.queue.enqueue("illustrate", cid=cid, prompt=event["prompt"],
                                             caption=event.get("caption", ""))
                continue
            if kind in ("error", "switch"):
                field = "text" if kind == "error" else "reason"
                if event.get(field):
                    log.warning("campaign %s %s: %s", cid, kind, event[field])
                    event[field] = player_safe(event[field])
            await publish(adapters, cid, kind, event)
            if kind == "combat":
                # the order changed; a new round may have worn effects off, and AC with them
                await repo.save_party(characters)
                await broadcast(adapters, cid, await party_payload(adapters, cid))
                await _schedule_nudge(adapters, cid, event.get("state"))
            if kind == "sheet":
                # persist and push straight away so HP bars move as damage lands
                await repo.save_party(characters)
                await broadcast(adapters, cid, await party_payload(adapters, cid))
        # Outside a fight, a timed effect runs down with each player's action. During
        # one it runs down by the round instead (see dm._combat): five players acting
        # once each is one round, not five.
        if actor and not await repo.get_combat(cid):
            await expire_effects(adapters, cid, characters)
    except Exception as e:  # never leave the table hanging on an unexpected fault
        await publish(adapters, cid, "error",
                      {"text": f"The DM stumbled: {type(e).__name__}."})
        raise
    finally:
        await repo.save_party(characters)
        await repo.save_history(cid, history)
        await broadcast(adapters, cid, await party_payload(adapters, cid))
        await broadcast(adapters, cid, {
            "kind": "backend-now",
            "backend": await repo.campaign_backend(cid) or providers.default_id()})
        await broadcast(adapters, cid, {"kind": "thinking", "on": False})


async def _schedule_nudge(adapters, cid, combat):
    """If the turn just passed to a player character, start their clock - when the
    table has asked for one."""
    acting = rules.current_turn(combat)
    if COMBAT_TURN_GRACE <= 0 or not acting or not acting["pc"]:
        return
    await adapters.queue.enqueue(
        "combat_nudge", delay=COMBAT_TURN_GRACE, cid=cid, name=acting["name"],
        round_no=combat["round"], turn=combat["turn"],
        since=await adapters.repo.last_seq(cid))


async def combat_nudge(adapters, cid, name, round_no, turn, since):
    """A player character's turn has sat idle for COMBAT_TURN_GRACE seconds.

    Checked again now, because a lot can happen in the wait: the turn may have moved on,
    the fight may be over, or the player may have acted and the DM simply not yet called
    next_turn - none of which is hesitation. Only when it is still exactly that turn and
    that player has said nothing since does the DM get told their moment passes.
    """
    combat = await adapters.repo.get_combat(cid)
    acting = rules.current_turn(combat)
    if (not acting or acting["name"] != name or combat["round"] != round_no
            or combat["turn"] != turn):
        return
    for event in await adapters.repo.events_since(cid, since):
        if event.get("kind") == "player" and event.get("character") == name:
            return
    await run_dm_turn(adapters, cid, None, (
        f"<table_note>{name} has not acted for a while and their turn passes. Narrate a "
        "brief moment of hesitation - no harm comes of it beyond the lost turn - then call "
        "next_turn and carry on.</table_note>"))
