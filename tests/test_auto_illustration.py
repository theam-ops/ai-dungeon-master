"""The DM illustrating a scene by itself.

Every backend here is a stub. Nothing calls a model, nothing needs a key, and the
"images" are four pixels of PNG made by Pillow - which is enough, because the point
under test is the plumbing around the picture, not the picture.
"""

import asyncio
import io
import time

import pytest
from PIL import Image

import server
from game import dm, providers, rules, store

# --------------------------------------------------------------------------- #
# stubs
# --------------------------------------------------------------------------- #


def _png():
    buf = io.BytesIO()
    Image.new("RGB", (2, 2), (40, 30, 20)).save(buf, "PNG")
    return buf.getvalue()


PNG = _png()


class StubDM(providers.Backend):
    """A DM that says exactly what the test scripted, one message per tool round."""

    kind = "stub"

    def __init__(self, script):
        super().__init__("stub-dm", "Stub DM", "stub")
        self.script = list(script)
        self.tools_seen = []

    def available(self):
        return True

    async def stream(self, system_blocks, messages, tools, images=None):
        self.tools_seen.append([t["name"] for t in tools])
        message = self.script.pop(0)
        for block in message["content"]:
            if block.get("type") == "text":
                yield {"type": "delta", "text": block["text"]}
        yield {"type": "message", **message}


class StubArtist(providers.Backend):
    """Something that can draw. Or refuses to, when the test wants that."""

    kind = "stub"
    draws = True

    def __init__(self, fail=None):
        super().__init__("stub-artist", "Stub Artist", "stub")
        self.fail = fail
        self.prompts = []

    def available(self):
        return True

    async def draw(self, prompt):
        self.prompts.append(prompt)
        if self.fail:
            raise self.fail
        return PNG, "image/png"


class SlowArtist(StubArtist):
    """An artist that takes as long as the test tells it to, so the test can look at
    the table while the picture is still being drawn."""

    def __init__(self):
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def draw(self, prompt):
        self.started.set()
        await self.release.wait()
        return await super().draw(prompt)


def says(text):
    return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}


def calls(name, **args):
    return {"content": [{"type": "tool_use", "id": "call-1", "name": name,
                         "input": args}],
            "stop_reason": "tool_use"}


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def registry(monkeypatch):
    """Replace the provider registry with just these stubs.

    Whoever runs the suite may have real keys in their environment, or none at all;
    either way the answer to "can anything here draw?" has to come from the test.
    """
    def install(*backends):
        monkeypatch.setattr(providers, "BACKENDS", list(backends))
        monkeypatch.setattr(providers, "BY_ID", {b.id: b for b in backends})
        return backends
    return install


@pytest.fixture
def campaign():
    c = store.create_campaign("Test campaign", "en")
    store.add_character(c["id"], rules.new_character("Vess", "Elf", "Rogue"), "token-1")
    yield c["id"]
    store.delete_campaign(c["id"])


def kinds(cid, kind):
    return [e for e in store.events_since(cid, 0) if e["kind"] == kind]


async def settle():
    """The drawing is handed to a background task on purpose; wait for it to finish."""
    others = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if others:
        await asyncio.gather(*others)


# --------------------------------------------------------------------------- #
# the tool is only there when something can draw
# --------------------------------------------------------------------------- #


def test_no_artist_means_no_tool(registry, campaign):
    registry(StubDM([]))
    assert "draw_scene" not in [t["name"] for t in dm.tools_for(campaign)]


def test_an_artist_puts_the_tool_on_the_table(registry, campaign):
    registry(StubDM([]), StubArtist())
    assert "draw_scene" in [t["name"] for t in dm.tools_for(campaign)]


def test_the_terminal_client_is_not_offered_it(registry):
    """No campaign means no feed to put a picture in - dnd.py is text and stays text."""
    registry(StubDM([]), StubArtist())
    assert "draw_scene" not in [t["name"] for t in dm.tools_for(None)]


def test_calling_it_anyway_is_an_error_not_a_crash(registry, campaign):
    """The tool is withheld, but a model can still name it. It must not blow up."""
    registry(StubDM([]))
    out, event = dm.run_tool("draw_scene", {"prompt": "a door", "caption": ""},
                             [], "en", campaign)
    assert event is None
    assert out.startswith("ERROR")


# --------------------------------------------------------------------------- #
# the rate limit
# --------------------------------------------------------------------------- #


def test_the_second_picture_is_refused_inside_the_window(registry, campaign, monkeypatch):
    registry(StubArtist())
    monkeypatch.setattr(dm, "ART_EVERY_TURNS", 3)
    ask = {"prompt": "a black door", "caption": "The door"}

    out, event = dm.run_tool("draw_scene", dict(ask), [], "en", campaign)
    assert event and event["kind"] == "draw"

    out, event = dm.run_tool("draw_scene", dict(ask), [], "en", campaign)
    assert event is None
    assert out.startswith("NOT NOW")


def test_the_slot_comes_back_after_enough_player_turns(registry, campaign, monkeypatch):
    registry(StubArtist())
    monkeypatch.setattr(dm, "ART_EVERY_TURNS", 3)
    ask = {"prompt": "a black door", "caption": "The door"}

    assert dm.run_tool("draw_scene", dict(ask), [], "en", campaign)[1]

    # the slot counts player turns logged after it was claimed, and both timestamps
    # come from the same clock - so put daylight between them rather than trusting
    # the resolution of time.time() on whatever machine this runs on
    time.sleep(0.02)
    for _ in range(2):
        store.append_event(campaign, "player", {"character": "Vess", "text": "onward"})
    assert dm.run_tool("draw_scene", dict(ask), [], "en", campaign)[1] is None

    store.append_event(campaign, "player", {"character": "Vess", "text": "onward"})
    assert dm.run_tool("draw_scene", dict(ask), [], "en", campaign)[1]


def test_the_limit_holds_across_whole_turns(registry, campaign, monkeypatch):
    """The end-to-end version: a model that asks for a picture every single turn
    still only gets the ones it is owed."""
    monkeypatch.setattr(dm, "ART_EVERY_TURNS", 3)
    artist = StubArtist()
    greedy = [calls("draw_scene", prompt="the door", caption="The door"),
              says("The door swings inward.")] * 3
    brain = StubDM(greedy)
    registry(brain, artist)
    store.set_campaign_backend(campaign, brain.id)

    async def play():
        for i in range(3):
            store.append_event(campaign, "player", {"character": "Vess", "text": "go"})
            await server.run_dm_turn(campaign, "Vess", "I go on")
            await settle()

    asyncio.run(play())

    assert artist.prompts == ["the door"]                 # asked three times, drew once
    assert len(kinds(campaign, "image")) == 1
    assert len(kinds(campaign, "narration")) == 3         # every turn still narrated


# --------------------------------------------------------------------------- #
# a picture that works
# --------------------------------------------------------------------------- #


def test_a_drawn_scene_lands_in_the_feed(registry, campaign):
    artist = StubArtist()
    brain = StubDM([calls("draw_scene", prompt="a black door in a wet alley",
                          caption="The black door"),
                    says("The door swings inward.")])
    registry(brain, artist)
    store.set_campaign_backend(campaign, brain.id)

    async def play():
        await server.run_dm_turn(campaign, "Vess", "I push the door")
        await settle()

    asyncio.run(play())

    assert "draw_scene" in brain.tools_seen[0]
    assert artist.prompts == ["a black door in a wet alley"]

    rows = store.campaign_media(campaign)
    assert len(rows) == 1
    assert rows[0]["source"] == "dm"
    assert rows[0]["kind"] == "scene"
    assert rows[0]["mime"] == "image/png"

    shown = kinds(campaign, "image")
    assert len(shown) == 1
    assert shown[0]["media"] == rows[0]["id"]
    assert shown[0]["caption"] == "The black door"
    assert shown[0]["source"] == "dm"
    assert shown[0]["character"] == ""            # nobody at the table asked for it

    # and it went through the ordinary media pipeline, so it travels with the campaign
    assert store.export_campaign(campaign)["media"][0]["id"] == rows[0]["id"]


def test_the_campaign_image_cap_still_applies(registry, campaign, monkeypatch):
    monkeypatch.setattr(server.media, "MAX_PER_CAMPAIGN", 0)
    artist = StubArtist()
    brain = StubDM([calls("draw_scene", prompt="a door", caption="The door"),
                    says("The door swings inward.")])
    registry(brain, artist)
    store.set_campaign_backend(campaign, brain.id)

    async def play():
        await server.run_dm_turn(campaign, "Vess", "I push the door")
        await settle()

    asyncio.run(play())

    assert store.campaign_media(campaign) == []
    assert kinds(campaign, "image") == []
    assert len(kinds(campaign, "narration")) == 1
    assert kinds(campaign, "error") == []


# --------------------------------------------------------------------------- #
# and one that doesn't
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("failure", [
    providers.ProviderExhausted("image generation is out of quota"),
    providers.ProviderFailed("the model replied with text, not a picture"),
])
def test_a_failing_artist_leaves_the_turn_intact(registry, campaign, failure):
    artist = StubArtist(fail=failure)
    brain = StubDM([calls("draw_scene", prompt="a door", caption="The door"),
                    says("The door swings inward.")])
    registry(brain, artist)
    store.set_campaign_backend(campaign, brain.id)

    async def play():
        await server.run_dm_turn(campaign, "Vess", "I push the door")
        await settle()

    asyncio.run(play())

    narration = kinds(campaign, "narration")
    assert len(narration) == 1
    assert "swings inward" in narration[0]["text"]
    assert kinds(campaign, "image") == []
    assert kinds(campaign, "error") == []
    assert store.campaign_media(campaign) == []
    assert store.get_history(campaign)          # the turn was recorded as usual


# --------------------------------------------------------------------------- #
# and it never holds up the narration
# --------------------------------------------------------------------------- #


def test_drawing_does_not_stall_the_turn(registry, campaign):
    artist = SlowArtist()
    brain = StubDM([calls("draw_scene", prompt="a door", caption="The door"),
                    says("The door swings inward.")])
    registry(brain, artist)
    store.set_campaign_backend(campaign, brain.id)

    async def play():
        # an implementation that awaited the drawing inside the turn would sit here
        # until the test released the artist, which is exactly the bug: fail rather
        # than hang, so the reason shows up in the output
        await asyncio.wait_for(
            server.run_dm_turn(campaign, "Vess", "I push the door"), timeout=10)

        # the turn is over; the artist has not finished, and must not have been
        # allowed to hold the table up while it worked
        await artist.started.wait()
        assert len(kinds(campaign, "narration")) == 1
        assert store.campaign_media(campaign) == []

        artist.release.set()
        await settle()
        assert len(store.campaign_media(campaign)) == 1

    asyncio.run(play())
