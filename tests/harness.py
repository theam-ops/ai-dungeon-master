"""Driving a whole session through the HTTP API, the way a browser does.

The app answers `POST /act` immediately and runs the DM turn as a background task, so a
test that asserts straight after the POST is asserting against a turn that has not
happened yet. `play()` waits the way the browser does - by watching the event log.
"""

import time

TIMEOUT = 10.0


class Table:
    """One campaign, and the players sitting at it."""

    def __init__(self, client, stub, campaign):
        self.client = client
        self.stub = stub
        self.id = campaign["id"]
        self.code = campaign["code"]
        self.campaign = campaign

    # -- events ------------------------------------------------------------- #

    def events(self, since=0):
        r = self.client.get(f"/api/campaigns/{self.id}/events", params={"since": since})
        r.raise_for_status()
        return r.json()["events"]

    def last_seq(self):
        evs = self.events()
        return evs[-1]["seq"] if evs else 0

    def kinds(self, since=0):
        return [e["kind"] for e in self.events(since)]

    def party(self):
        return self.client.get(f"/api/campaigns/{self.id}").json()["party"]

    def character(self, name):
        return next(c for c in self.party() if c["name"] == name)

    # -- turns -------------------------------------------------------------- #

    def script(self, *replies):
        """Queue model responses. A string is narration; a list is a round of tools."""
        for r in replies:
            if isinstance(r, str):
                self.stub.reply(r)
            else:
                self.stub.reply(tools=r)
        return self

    def begin(self, *replies):
        self.script(*replies)
        mark = self.last_seq()
        r = self.client.post(f"/api/campaigns/{self.id}/begin")
        r.raise_for_status()
        return self._settle(mark)

    def act(self, text, *replies, media=None, expect_status=200):
        self.script(*replies)
        mark = self.last_seq()
        body = {"text": text}
        if media:
            body["media"] = media
        r = self.client.post(f"/api/campaigns/{self.id}/act", json=body)
        assert r.status_code == expect_status, (r.status_code, r.text)
        if r.status_code != 200:
            return []
        return self._settle(mark)

    def _settle(self, mark):
        """Wait for the background turn to finish, then hand back what it produced."""
        deadline = time.time() + TIMEOUT
        started = False
        while time.time() < deadline:
            new = self.events(mark)
            terminal = [e for e in new if e["kind"] in ("narration", "error")]
            if terminal:
                started = True
            if started and not self.stub.script:
                # the loop has consumed the whole script and written a terminal event;
                # give the "finally" block its moment to save the party
                time.sleep(0.05)
                return self.events(mark)
            time.sleep(0.02)
        raise AssertionError(
            f"turn never finished; events so far: {[e['kind'] for e in self.events(mark)]}, "
            f"{len(self.stub.script)} scripted replies left over")


def new_table(client, stub, name="The Sunken Bell", lang="en",
              character=None):
    character = character or {"name": "Vess", "race": "Elf", "class": "Rogue"}
    r = client.post("/api/campaigns", json={"name": name, "lang": lang,
                                            "character": character})
    r.raise_for_status()
    return Table(client, stub, r.json())


def join(client, code, character):
    """A second browser joining an existing campaign by code."""
    look = client.post("/api/campaigns/join", json={"code": code})
    look.raise_for_status()
    cid = look.json()["id"]
    r = client.post(f"/api/campaigns/{cid}/characters", json={"character": character})
    r.raise_for_status()
    return cid


def second_browser(app):
    """A TestClient with its own cookie jar - a different device at the same table."""
    from starlette.testclient import TestClient
    other = TestClient(app)
    other.__enter__()
    other.post("/api/login", json={"password": ""})
    return other


def prompt_of(call):
    """The player-turn prompt in one captured request - the last plain-text user turn."""
    for m in reversed(call["messages"]):
        if m["role"] == "user" and isinstance(m["content"], str):
            return m["content"]
    raise AssertionError("no player prompt in that request")


def tool_results(call):
    """Every tool result that had reached the model by the time of this request."""
    out = []
    for m in call["messages"]:
        if isinstance(m.get("content"), list):
            out += [b["content"] for b in m["content"]
                    if isinstance(b, dict) and b.get("type") == "tool_result"]
    return out


def png_bytes(size=(40, 40), colour=(120, 30, 200)):
    """A real PNG, so `media.process` has something genuine to decode."""
    import io

    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, "PNG")
    return buf.getvalue()


# One roll and one sheet change, the shape almost every real turn has.
def strike(target, damage, reason="attack"):
    return [
        ("roll_dice", {"notation": "1d20+5", "reason": f"{target}: {reason}",
                       "mode": "normal"}),
        ("update_character", {"character_name": target, "hp_change": -damage,
                              "xp_gain": 0, "gold_change": 0, "add_items": [],
                              "remove_items": [], "add_conditions": [],
                              "remove_conditions": [], "level_up": False,
                              "reason": reason}),
    ]


def award(target, xp=0, gold=0, items=(), conditions=(), level_up=False, heal=0,
          reason="reward"):
    return [("update_character", {
        "character_name": target, "hp_change": heal, "xp_gain": xp,
        "gold_change": gold, "add_items": list(items), "remove_items": [],
        "add_conditions": list(conditions), "remove_conditions": [],
        "level_up": level_up, "reason": reason})]
