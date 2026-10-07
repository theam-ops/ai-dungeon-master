"""The campaign's memory: older turns condensed, so the DM is sent a bounded prompt.

Without this, every turn re-sent the whole campaign. Cost grew with the square of its
length, and somewhere past a hundred-odd turns the request outgrew the model's context
and every turn failed - on every backend, since failover sends the same history.

So, once enough turns have piled up, a background job asks the cheapest AI available to
fold the older ones into the story so far. The DM is then sent that synopsis, followed
by the most recent turns word for word. Two things are deliberately never done:

- **Nothing is deleted.** The stored history stays whole: it is what gets exported,
  what another AI reads if this one runs out, and the record of what happened. The
  synopsis only changes what is *sent*.
- **No turn waits for it.** Summarising takes as long as an AI takes, so it runs as a
  queued job outside the turn lock. If it fails, the campaign carries on unchanged and
  the next turn past the threshold tries again.

Alongside the synopsis the summariser lists the people, places and open threads. Those
are written into the campaign library as one document, where `search_lore` can reach
them - which is what lets the DM recall a name from three sessions ago.
"""

import logging
import os
import re

from .. import i18n, providers

log = logging.getLogger("dnd")

# Turns kept word for word after the synopsis. The rest of the story reaches the DM
# condensed.
KEEP_TURNS = int(os.environ.get("SUMMARY_KEEP_TURNS", "20"))
# How far past KEEP_TURNS the verbatim window may grow before it is condensed again -
# so a summary runs every this many turns rather than on every one.
EVERY_TURNS = int(os.environ.get("SUMMARY_EVERY_TURNS", "10"))

DOCUMENT = {"en": "Campaign memory", "th": "ความทรงจำของแคมเปญ"}

INSTRUCTIONS = """\
You keep the memory of a tabletop roleplaying campaign. You are given the story so far,
if there is one, and the next stretch of the game's transcript. Write exactly three
sections, with these headings exactly as written in English:

SYNOPSIS:
One account of everything that has happened, in the past tense - where the party went,
what they did, what it changed. Fold the earlier story in; compress old events harder
than recent ones. At most 500 words. Facts from the transcript only: invent nothing.

PEOPLE AND PLACES:
One line each for every named person, place, faction or item that still matters: who or
what it is, and where things stand with them now.

THREADS:
One line each for every promise, debt, grudge, mystery or goal that is still open.

Write the content of every section in {language}. Lines in the last two sections start
with "- "."""

HEADINGS = re.compile(r"^\s*(SYNOPSIS|PEOPLE AND PLACES|THREADS)\s*:\s*$", re.M | re.I)


def due(history, memory):
    """Where the next summary should end, or None if it is not time yet.

    Summarise once more than KEEP_TURNS + EVERY_TURNS turns sit after the current
    synopsis, and cut so that KEEP_TURNS stay word for word. Cuts fall only where a
    turn begins, so the verbatim part never opens on a tool result whose call it
    cannot see.
    """
    upto = int((memory or {}).get("upto", 0))
    starts = [i for i in providers.turn_starts(history) if i >= upto]
    if len(starts) <= KEEP_TURNS + EVERY_TURNS:
        return None
    return starts[-KEEP_TURNS]


def parse(text):
    """Split the summariser's answer into (synopsis, notes for the library)."""
    sections, current = {}, None
    for line in (text or "").splitlines():
        m = HEADINGS.match(line)
        if m:
            current = m.group(1).upper()
            sections[current] = []
        elif current:
            sections[current].append(line)
    if not sections:                       # it ignored the format: the whole answer is
        return (text or "").strip(), ""    # still a usable synopsis, with no notes
    synopsis = "\n".join(sections.get("SYNOPSIS", [])).strip()
    notes = []
    for heading in ("PEOPLE AND PLACES", "THREADS"):
        body = "\n".join(sections.get(heading, [])).strip()
        if body:
            notes.append(f"{heading.title()}\n\n{body}")
    return synopsis, "\n\n".join(notes)


def summarizer_backends(campaign_backend_id):
    """Who should do the summarising: the free and local AIs first, then the rest.

    Condensing a transcript is a compression job, not a creative one. Spending the
    campaign's own DM - possibly the priciest model configured - to save money on that
    same DM would be backwards.
    """
    order = providers.failover_order(campaign_backend_id)
    return [b for b in order if getattr(b, "free", False)] + \
           [b for b in order if not getattr(b, "free", False)]


async def complete(backend, instructions, prompt):
    """One prompt in, text out, from any backend - including Claude Code, which only
    runs whole turns."""
    system = [{"type": "text", "text": instructions}]
    messages = [{"role": "user", "content": prompt}]
    if hasattr(backend, "run_turn"):
        said = []
        async for event in backend.run_turn(system, messages, [], "en", tools=[],
                                            call_tool=None):
            if event.get("kind") == "narration":
                said.append(event["text"])
        return "\n".join(said)
    message, deltas = None, []
    async for chunk in backend.stream(system, messages, [], None):
        if chunk["type"] == "delta":
            deltas.append(chunk["text"])
        else:
            message = chunk
    if message:
        return "".join(b.get("text", "") for b in message["content"] if b.get("type") == "text")
    return "".join(deltas)


async def maybe_summarize(adapters, cid):
    """After a turn: queue a summary if enough has piled up. Cheap to call every turn."""
    history = await adapters.repo.get_history(cid)
    if due(history, await adapters.repo.get_memory(cid)) is not None:
        await adapters.queue.enqueue("summarize", cid=cid)


async def summarize(adapters, cid):
    """The job. Condense the turns between the current synopsis and the new cut."""
    claim = f"memory:{cid}"
    if not await adapters.locks.claim(claim, ttl=600):
        return                              # one is already running for this campaign
    try:
        repo = adapters.repo
        history = await repo.get_history(cid)
        memory = await repo.get_memory(cid) or {}
        cut = due(history, memory)
        if cut is None:
            return
        lang = i18n.normalise(await repo.campaign_lang(cid))
        language = i18n.LANGUAGES.get(lang, i18n.LANGUAGES["en"])["name"]
        previous = (memory.get("synopsis") or "").strip()
        prompt = ((f"THE STORY SO FAR:\n{previous}\n\n" if previous else "")
                  + "THE NEXT STRETCH OF THE TRANSCRIPT:\n"
                  + providers.render_transcript(history[int(memory.get("upto", 0)):cut],
                                                keep_current=False))
        instructions = INSTRUCTIONS.format(language=language)

        text, last_error = "", None
        for backend in summarizer_backends(await repo.campaign_backend(cid)):
            try:
                text = await complete(backend, instructions, prompt)
                if text.strip():
                    break
            except (providers.ProviderExhausted, providers.ProviderFailed) as e:
                last_error = e
        synopsis, notes = parse(text)
        if not synopsis:
            log.warning("campaign %s: no summary this time (%s)", cid,
                        last_error or "the AI returned nothing usable")
            return

        turns = len([i for i in providers.turn_starts(history) if i < cut])
        stored = await repo.set_memory(cid, {"upto": cut, "synopsis": synopsis,
                                             "turns": turns})
        if stored.get("upto") != cut:
            return                          # a newer summary landed first
        if notes:
            await repo.add_lore(cid, DOCUMENT.get(lang, DOCUMENT["en"]), notes)
        await adapters.bus.publish(cid, {"kind": "memory", "turns": turns,
                                         "synopsis": synopsis})
    except Exception:
        # a background job: nothing would see this otherwise, and the campaign must
        # carry on exactly as it was
        log.exception("campaign %s: summarising failed", cid)
    finally:
        await adapters.locks.release(claim)
