"""One DM turn, start to finish, for a whole table.

Runs as a job on the task queue: `/act` and `/begin` enqueue it and answer the browser
at once, and every browser watches its event stream for the result. Everything it
touches comes through the `adapters` it is handed, so the same code serves one process
or several.
"""

import logging
import re

from .. import dm, providers, rules
from .events import broadcast, party_payload, publish

log = logging.getLogger("dnd")


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


async def run_dm_turn(adapters, cid, actor, action, images=None, claim=None):
    """One DM turn, broadcast to the whole table. Serialized per campaign.

    `claim` is a lease taken by `begin` that this turn must give back however it ends -
    the opening scene stays claimed for exactly as long as it takes the DM to write it.
    """
    try:
        async with adapters.locks.hold(turn_key(cid)):
            await _turn(adapters, cid, actor, action, images)
    finally:
        if claim:
            await adapters.locks.release(claim)


async def _turn(adapters, cid, actor, action, images):
    repo = adapters.repo
    characters = await repo.party(cid)
    history = await repo.get_history(cid)
    house = await repo.campaign_house(cid)

    await broadcast(adapters, cid, {"kind": "thinking", "on": True})
    try:
        async for event in dm.take_turn(history, characters, actor, action,
                                        await repo.campaign_lang(cid),
                                        await repo.campaign_backend(cid)
                                        or providers.default_id(),
                                        images, cid, repo=repo, house=house):
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
            if kind == "sheet":
                # persist and push straight away so HP bars move as damage lands
                await repo.save_party(characters)
                await broadcast(adapters, cid, await party_payload(adapters, cid))
        if actor:
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
