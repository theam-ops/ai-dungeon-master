"""Telling the table what happened.

Two ways to say something, and the difference matters. `publish` writes the event to
the campaign's log first, so a browser that was asleep replays it later. `broadcast`
only pushes - for transient state like "the DM is thinking" or a fresh party bar, which
a reconnecting browser rebuilds for itself.
"""


def public_character(c):
    """One character as the whole table may see it.

    A player's standing notes are theirs: they steer the DM on that player's own turns
    and nobody else needs them to draw an HP bar, so they never ride a payload that goes
    to every browser at the table.
    """
    return {k: v for k, v in c.items() if not k.startswith("_") and k != "notes"}


async def broadcast(adapters, cid, event):
    """Push to everyone watching, without recording it."""
    await adapters.bus.publish(cid, event)


async def publish(adapters, cid, kind, payload):
    """Record an event in the campaign's log, then push it to everyone watching."""
    event = await adapters.repo.append_event(cid, kind, payload)
    await adapters.bus.publish(cid, event)
    return event


async def party_payload(adapters, cid):
    return {"kind": "party",
            "party": [public_character(c) for c in await adapters.repo.party(cid)]}
