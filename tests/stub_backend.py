"""A DM that never phones home.

`StubBackend` implements exactly the contract `game/providers.Backend.stream` describes:
an async generator that yields `{"type": "delta", "text": ...}` chunks and finishes with
one `{"type": "message", "content": [...anthropic blocks...], "stop_reason": ...}`.

That is the whole seam. Because every real backend translates itself into that shape,
a session driven through this stub exercises `dm.take_turn`, `dm.run_tool`, the event
log, the SSE fan-out, the character sheet and export/import for real - the only thing
faked is which words the model chose.

Scripting it:

    stub.reply("You push the door open.")                       # narration only
    stub.reply(tools=[("roll_dice", {"notation": "1d20+3",
                                     "reason": "Vess: Stealth vs DC 14",
                                     "mode": "normal"})])       # one tool round
    stub.reply("The guard never looks up.")                     # then the narration

Replies are consumed in order, one per round of `dm._run`'s tool loop. A reply carrying
tools ends with `stop_reason: "tool_use"`, so the loop runs the tools and comes back for
the next reply - which is how a real multi-round turn behaves.
"""

import collections
import copy

from game import providers


class ScriptExhausted(RuntimeError):
    """The turn asked for more from the model than the test scripted.

    Loud on purpose. A silent fallback would let a test pass while the turn loop did
    something completely different from what was written down.
    """


class StubBackend(providers.Backend):
    kind = "stub"
    vision = True
    free = True
    key_env = ""

    def __init__(self, id="stub", label="Stub DM (tests)", model="stub-1"):
        super().__init__(id, label, model)
        self.script = collections.deque()
        self.calls = []          # (system_blocks, messages, tools, images) per request
        self.fail_with = None    # set to an exception instance to make every turn raise
        self._offline = False

    # -- scripting ---------------------------------------------------------- #

    def reply(self, text="", tools=(), stop_reason=None):
        """Queue one model response. Returns self, so calls chain."""
        self.script.append({"text": text, "tools": list(tools),
                            "stop_reason": stop_reason})
        return self

    def turn(self, *replies):
        """Queue a whole turn at once: `turn("narration")` or
        `turn(([("roll_dice", {...})],), "narration")`."""
        for r in replies:
            if isinstance(r, str):
                self.reply(r)
            else:
                self.reply(tools=r[0] if isinstance(r, tuple) else r)
        return self

    def reset(self):
        self.script.clear()
        self.calls.clear()
        self.fail_with = None
        self._offline = False
        return self

    def go_offline(self, exc=None):
        """Make this backend report itself unavailable, the way a revoked key does."""
        self._offline = True
        self.fail_with = exc
        return self

    # -- the Backend contract ----------------------------------------------- #

    def available(self):
        return not self._offline

    async def verify(self):
        return self.available(), "" if self.available() else "stub is offline"

    async def stream(self, system_blocks, messages, tools, images=None):
        # `messages` IS the campaign's history list, and the turn loop keeps appending
        # to it - so a live reference would show every call the same final state. Snap
        # a copy of what this request actually carried.
        self.calls.append({"system": system_blocks, "messages": copy.deepcopy(messages),
                           "tools": tools, "images": images})
        if self.fail_with is not None:
            raise self.fail_with
        if not self.script:
            raise ScriptExhausted(
                f"{self.label}: the turn asked for reply #{len(self.calls)} and the "
                f"script had none left")

        step = self.script.popleft()
        text = step["text"]
        # deltas arrive in pieces, the way a real stream does - this is what catches a
        # consumer that assumes one chunk per turn
        for i in range(0, len(text), 24):
            yield {"type": "delta", "text": text[i:i + 24]}

        content = []
        if text.strip():
            content.append({"type": "text", "text": text})
        for i, (name, args) in enumerate(step["tools"]):
            content.append({"type": "tool_use", "id": f"stub_{len(self.calls)}_{i}",
                            "name": name, "input": args})

        stop = step["stop_reason"] or ("tool_use" if step["tools"] else "end_turn")
        yield {"type": "message", "content": content, "stop_reason": stop}


def install_stub(providers_module, backend=None):
    """Register the stub as a real backend and put it first in the failover order."""
    stub = backend or StubBackend()
    providers_module.BACKENDS[:] = [stub]
    providers_module.BY_ID.clear()
    providers_module.BY_ID[stub.id] = stub
    return stub


def install_many(providers_module, *stubs):
    """Register several stubs, in failover order. Used by the failover tests."""
    providers_module.BACKENDS[:] = list(stubs)
    providers_module.BY_ID.clear()
    for s in stubs:
        providers_module.BY_ID[s.id] = s
    return stubs
