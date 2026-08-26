"""The things a developer wouldn't think to check.

Each test here corresponds to an entry in `docs/playtest-findings.md`. Tests for
findings that were *fixed* assert the fixed behaviour. Tests for findings left alone
assert the behaviour as it actually is, and say so - so the next person who changes it
finds out on purpose rather than by surprise.
"""

import io
import json
import threading
import time
import zipfile

import pytest

from game import lore, store
from .harness import new_table, png_bytes, second_browser


# --------------------------------------------------------------------------- #
# FIXED: a long campaign replayed from the wrong end
# --------------------------------------------------------------------------- #

def test_a_long_campaign_replays_its_newest_events(app_client):
    """Opening a campaign that has run for several sessions shows the current scene.

    Before: `events_since(cid, 0)` returned the *oldest* 500. The browser set
    `lastSeq` from that, landed on seq 500, and never asked for anything after it -
    so it sat looking at session one while the table talked past it, and no reconnect
    healed it.
    """
    client, stub = app_client
    table = new_table(client, stub)
    for i in range(700):
        store.append_event(table.id, "player", {"character": "Vess", "text": f"turn {i}"})

    fresh = table.events(since=0)
    assert len(fresh) == 500
    assert fresh[-1]["seq"] == store.last_seq(table.id)      # the newest event is there
    assert fresh[-1]["text"] == "turn 699"

    # and the client can now resume from where it landed without a hole
    assert table.events(since=fresh[-1]["seq"]) == []


def test_a_client_that_was_away_too_long_still_catches_up(app_client):
    client, stub = app_client
    table = new_table(client, stub)
    for i in range(1200):
        store.append_event(table.id, "player", {"character": "Vess", "text": f"turn {i}"})

    away_since = 3
    caught_up = table.events(since=away_since)
    assert len(caught_up) == 500
    assert caught_up[-1]["seq"] == store.last_seq(table.id)


def test_export_still_carries_the_whole_event_log(app_client):
    """The newest-window rule must not quietly truncate an export."""
    client, stub = app_client
    table = new_table(client, stub)
    for i in range(900):
        store.append_event(table.id, "player", {"character": "Vess", "text": f"turn {i}"})
    blob = store.export_campaign(table.id)
    total = store.db().execute("SELECT COUNT(*) n FROM events WHERE campaign_id=?",
                               (table.id,)).fetchone()["n"]
    assert len(blob["events"]) == total > 900


# --------------------------------------------------------------------------- #
# FIXED: a malformed export used to be a 500 with a stack trace
# --------------------------------------------------------------------------- #

MALFORMED = [
    ("campaign is a string", {"format": "ai-dm-campaign/1", "campaign": "nope"}),
    ("campaign is a list", {"format": "ai-dm-campaign/1", "campaign": []}),
    ("campaign missing", {"format": "ai-dm-campaign/1"}),
    ("characters are strings", {"format": "ai-dm-campaign/1", "campaign": {"name": "x"},
                                "characters": ["Vess"]}),
    ("events are strings", {"format": "ai-dm-campaign/1", "campaign": {"name": "x"},
                            "events": ["boom"]}),
    ("media are strings", {"format": "ai-dm-campaign/1", "campaign": {"name": "x"},
                           "media": ["boom"]}),
    ("lore are strings", {"format": "ai-dm-campaign/1", "campaign": {"name": "x"},
                          "lore": ["boom"]}),
    ("history is a string", {"format": "ai-dm-campaign/1",
                             "campaign": {"name": "x", "history": "nope"}}),
    ("not an export at all", {"hello": "world"}),
]


@pytest.mark.parametrize("why,blob", MALFORMED, ids=[m[0] for m in MALFORMED])
def test_a_malformed_export_is_a_400_not_a_500(app_client, why, blob):
    client, _ = app_client
    r = client.post("/api/import", json=blob)
    assert r.status_code == 400, f"{why}: got {r.status_code} {r.text[:200]}"
    assert "campaign export" in r.json()["detail"]


def test_import_does_not_mutate_the_blob_it_was_given(app_client):
    """Importing the same file twice used to lose the portrait the second time - the
    first pass `pop`ped it out of the caller's dict."""
    client, stub = app_client
    table = new_table(client, stub)
    blob = client.get(f"/api/campaigns/{table.id}/export").json()
    before = json.dumps(blob, sort_keys=True, ensure_ascii=False)
    client.post("/api/import", json=blob).raise_for_status()
    assert json.dumps(blob, sort_keys=True, ensure_ascii=False) == before


# --------------------------------------------------------------------------- #
# FIXED: Thai notes in a legacy Windows encoding
# --------------------------------------------------------------------------- #

THAI = "อรุณคือย่าของปรางค์ นางจมไปพร้อมระฆัง"


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16", "cp874"])
def test_thai_notes_survive_whichever_encoding_notepad_used(app_client, encoding):
    """Thai .txt files are very often CP874 or UTF-16 - those are what Windows Notepad
    wrote by default for years, and "Unicode" in its Save As dialog means UTF-16.

    Before: everything was decoded as UTF-8 with replacement. The file imported, the
    document appeared in the list, and `search_lore` then found nothing in it, ever,
    with nothing anywhere saying why.
    """
    client, stub = app_client
    table = new_table(client, stub, lang="th")
    raw = THAI.encode(encoding)

    r = client.post(f"/api/campaigns/{table.id}/library",
                    files=[("files", ("บันทึก.txt", raw, "text/plain"))])
    r.raise_for_status()
    assert r.json()["documents"], r.json()

    # the DM can actually find a name in it
    hit = lore.search(store.lore_texts(table.id), "ปรางค์")
    assert "ปรางค์" in hit
    assert "�" not in hit


def test_a_western_legacy_file_is_not_turned_into_thai():
    """CP874 decodes almost any byte string, so it is only accepted when the result
    actually contains Thai - otherwise a latin-1 file becomes confident nonsense."""
    for text in ("café au lait", "Grüße aus München", "naïve résumé"):
        got = lore.decode(text.encode("cp1252"))
        assert not any("฀" <= ch <= "๿" for ch in got), (text, got)


def test_utf8_is_never_second_guessed():
    assert lore.decode(THAI.encode("utf-8")) == THAI


# --------------------------------------------------------------------------- #
# FIXED: two characters with the same name
# --------------------------------------------------------------------------- #

def test_two_characters_cannot_share_a_name(app_client):
    """`dm.run_tool` finds a character by name. With two Vesses at the table every
    point of damage aimed at either lands on whichever was created first, and the
    other is invulnerable - which looks like the DM cheating."""
    client, stub = app_client
    table = new_table(client, stub, character={"name": "Vess", "race": "Elf",
                                               "class": "Rogue"})
    other = second_browser(client.app)
    try:
        other.post("/api/campaigns/join", json={"code": table.code})
        r = other.post(f"/api/campaigns/{table.id}/characters",
                       json={"character": {"name": "vess", "race": "Dwarf",
                                           "class": "Cleric"}})
        assert r.status_code == 400
        assert "already called" in r.json()["detail"]

        ok = other.post(f"/api/campaigns/{table.id}/characters",
                        json={"character": {"name": "Vandar", "race": "Dwarf",
                                            "class": "Cleric"}})
        assert ok.status_code == 200
    finally:
        other.__exit__(None, None, None)


# --------------------------------------------------------------------------- #
# FIXED: two players pressing Begin at once
# --------------------------------------------------------------------------- #

def test_the_campaign_only_opens_once(app_client):
    """`begin` refused a second call by checking the saved history - which is only
    written when the turn *ends*. Two players tapping Begin together both passed, and
    the campaign opened twice, in two different places."""
    client, stub = app_client
    table = new_table(client, stub)
    stub.reply("Opening one.").reply("Opening two.")

    codes = []
    threads = [threading.Thread(
        target=lambda: codes.append(
            client.post(f"/api/campaigns/{table.id}/begin").status_code))
        for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    deadline = time.time() + 10
    while time.time() < deadline and not any(
            e["kind"] == "narration" for e in table.events()):
        time.sleep(0.02)
    time.sleep(0.3)

    assert sorted(codes) == [200, 400]
    assert sum(1 for e in table.events() if e["kind"] == "narration") == 1
    assert len(stub.script) == 1          # only one opening was ever asked for
    stub.script.clear()


# --------------------------------------------------------------------------- #
# FIXED: the provider's error body was shown to every player
# --------------------------------------------------------------------------- #

def test_a_provider_error_body_is_not_shown_to_the_table(app_client):
    """The error `dm.take_turn` composes carries the upstream response verbatim, and it
    is published to every player - including friends who joined over a tunnel."""
    import server
    from game import providers

    client, stub = app_client
    table = new_table(client, stub)
    stub.fail_with = providers.ProviderFailed(
        'Gemini 3.6 Flash (free): HTTP 400 {"error":{"message":"API key not valid. '
        'Please pass a valid API key.","details":[{"reason":"API_KEY_INVALID"}]}}')

    client.post(f"/api/campaigns/{table.id}/begin")
    deadline = time.time() + 10
    while time.time() < deadline and not any(
            e["kind"] == "error" for e in table.events()):
        time.sleep(0.02)
    stub.fail_with = None

    err = next(e for e in table.events() if e["kind"] == "error")
    assert "API_KEY_INVALID" not in err["text"]
    assert "{" not in err["text"]
    assert "Every AI turned the turn away" in err["text"]
    # the useful half is kept
    assert "Gemini" in err["text"] and "400" in err["text"]

    # and the helper is honest about its own edges
    assert server.player_safe("") == "the AI did not say why"
    assert server.player_safe("Claude Opus 5: HTTP 429") == "Claude Opus 5: HTTP 429"
    assert server.player_safe("x " + "y" * 500).endswith("y")
    assert len(server.player_safe("x " + "y" * 500)) <= 160


# --------------------------------------------------------------------------- #
# FIXED: the document cap counted one upload, not the campaign
# --------------------------------------------------------------------------- #

def test_the_document_limit_is_for_the_campaign_not_the_upload(app_client):
    client, stub = app_client
    table = new_table(client, stub)
    import server

    def upload(prefix, count):
        files = [("files", (f"{prefix}{i}.md", f"note {i}".encode(), "text/markdown"))
                 for i in range(count)]
        return client.post(f"/api/campaigns/{table.id}/library", files=files).json()

    upload("a", 30)
    second = upload("b", 30)
    total = client.get(f"/api/campaigns/{table.id}/lore").json()["documents"]
    assert len(total) == server.MAX_LORE_DOCS
    assert any("limit" in s["why"] for s in second["skipped"])

    # re-uploading a document that is already there replaces it, and costs no slot
    again = upload("a", 5)
    assert not again["skipped"]
    assert len(client.get(f"/api/campaigns/{table.id}/lore").json()["documents"]) \
        == server.MAX_LORE_DOCS


# --------------------------------------------------------------------------- #
# Thai, every hop
# --------------------------------------------------------------------------- #

def test_thai_survives_upload_storage_export_reimport_and_search(app_client):
    client, stub = app_client
    thai_title = "ระฆังจม"
    table = new_table(client, stub, name=thai_title, lang="th",
                      character={"name": "ปรางค์", "race": "Elf", "class": "Rogue"})

    # gear is written onto the sheet in Thai from turn one
    assert "ดาบสั้น" in table.character("ปรางค์")["inventory"]

    # an image with a Thai filename and caption
    up = client.post(f"/api/campaigns/{table.id}/media",
                     files={"file": ("แผนที่เมืองเก่า.png", png_bytes(), "image/png")},
                     data={"kind": "handout", "caption": "แผนที่เมือง"})
    up.raise_for_status()
    assert up.json()["caption"] == "แผนที่เมือง"

    # a Thai document, searched without spaces between words
    client.post(f"/api/campaigns/{table.id}/library",
                files=[("files", ("บันทึก.md", THAI.encode(), "text/markdown"))])
    assert "ระฆัง" in lore.search(store.lore_texts(table.id), "ระฆัง")

    # a Thai turn
    table.begin("ฝนกระหน่ำหอระฆัง เสียงนั้นยังดังอยู่ข้างล่าง")
    assert any("ภาษาไทย" in b["text"] for b in stub.calls[0]["system"])

    # export: a zip, with the real name in RFC 5987 and an ASCII fallback
    dump = client.get(f"/api/campaigns/{table.id}/export")
    dump.raise_for_status()
    assert dump.headers["content-type"] == "application/zip"
    disposition = dump.headers["content-disposition"]
    assert 'filename="campaign.zip"' in disposition
    assert "filename*=UTF-8''" in disposition
    disposition.encode("latin-1")           # headers are latin-1; this must not raise

    z = zipfile.ZipFile(io.BytesIO(dump.content))
    blob = json.loads(z.read("campaign.json").decode("utf-8"))
    assert blob["campaign"]["name"] == thai_title
    assert blob["media"][0]["caption"] == "แผนที่เมือง"
    assert blob["characters"][0]["name"] == "ปรางค์"

    # re-import, and everything is still there, bytes included
    back = client.post("/api/import/archive",
                       files={"file": ("x.zip", dump.content, "application/zip")})
    back.raise_for_status()
    nid = back.json()["id"]
    assert back.json()["name"] == thai_title
    media_rows = client.get(f"/api/campaigns/{nid}/media").json()["media"]
    assert media_rows[0]["caption"] == "แผนที่เมือง"
    served = client.get(f"/api/campaigns/{nid}/media/{media_rows[0]['id']}")
    assert served.status_code == 200 and len(served.content) == media_rows[0]["bytes"]
    assert client.get(f"/api/campaigns/{nid}/lore").json()["documents"][0]["name"] \
        == "บันทึก.md"
    assert "ระฆัง" in lore.search(store.lore_texts(nid), "ระฆัง")


def test_a_thai_filename_becomes_a_thai_caption():
    import server
    assert server._caption_from("NPC_อรุณ.png") == "อรุณ"
    assert server._caption_from("แผนที่_เมือง_เก่า.jpg") == "แผนที่ เมือง เก่า"
    assert server._caption_from("NPC_Aria_Venn.png") == "Aria Venn"


# --------------------------------------------------------------------------- #
# FOUND, NOT FIXED - pinned so a change is deliberate
# --------------------------------------------------------------------------- #

def test_known_history_grows_without_limit(app_client):
    """Nothing truncates the campaign history sent to an API backend. Every turn
    re-sends the whole story, so a long session's cost grows quadratically and will
    eventually exceed the model's context. `claude_code.render_transcript` caps at 40
    turns; no other backend does. Structural - see docs/playtest-findings.md."""
    client, stub = app_client
    table = new_table(client, stub)
    table.begin("It starts.")
    for i in range(6):
        table.act(f"turn {i}", f"narration {i}")

    sent = [len(json.dumps(c["messages"], ensure_ascii=False)) for c in stub.calls]
    assert sent == sorted(sent)                 # every turn strictly bigger
    assert sent[-1] > sent[0] * 3
    # nothing is ever dropped: the request carries the whole stored campaign
    assert len(stub.calls[-1]["messages"]) >= len(store.get_history(table.id)) - 1


def test_known_import_bypasses_the_media_and_lore_caps(app_client):
    """Upload enforces 60 images and 40 documents. Import enforces neither, so a
    hand-edited export can seed a campaign far past both. Not fixed: the right answer
    (refuse? truncate? which ones?) is a product decision, not a patch."""
    client, _ = app_client
    from game import media
    blob = {"format": "ai-dm-campaign/1", "campaign": {"name": "big"},
            "media": [{"id": f"m{i}", "file": f"{i:064x}.png", "mime": "image/png"}
                      for i in range(200)],
            "lore": [{"name": f"d{i}.md", "text": "x"} for i in range(200)]}
    r = client.post("/api/import", json=blob)
    r.raise_for_status()
    cid = r.json()["id"]
    assert store.media_count(cid) == 200 > media.MAX_PER_CAMPAIGN
    assert len(store.lore_documents(cid)) == 200


def test_known_the_dm_can_hit_the_wrong_character_with_a_short_name(app_client):
    """`dm._find` falls back to a substring match, so a one-letter `character_name`
    lands on whoever matches first. Left alone: `game/dm.py` is being changed in
    parallel and a tighter match belongs with that work."""
    from game import dm, rules
    party = [rules.new_character("Vess", "Elf", "Rogue"),
             rules.new_character("Vandar", "Dwarf", "Cleric")]
    assert dm._find(party, "V")["name"] == "Vess"
    assert dm._find(party, "a")["name"] == "Vandar"     # matched on a single letter


def test_known_more_than_four_attached_images_vanish_silently(app_client):
    """`act` takes the first four attachments and drops the rest without a word - not
    even a note in the feed. The player sees six thumbnails go and four arrive."""
    client, stub = app_client
    table = new_table(client, stub)
    ids = []
    for i in range(6):
        r = client.post(f"/api/campaigns/{table.id}/media",
                        files={"file": (f"{i}.png", png_bytes(colour=(i * 30, 9, 9)),
                                        "image/png")},
                        data={"kind": "handout", "caption": f"c{i}", "share": "0"})
        ids.append(r.json()["id"])

    events = table.act("look at these", "You look.", media=ids)
    assert sum(1 for e in events if e["kind"] == "image") == 4
    assert not any(e["kind"] == "error" for e in events)


def test_known_the_rate_limit_is_per_table_not_per_player(app_client, monkeypatch):
    """One impatient player uses up the whole table's budget, and everyone else gets
    the same 429. `begin` is not counted at all."""
    import server
    client, stub = app_client
    monkeypatch.setattr(server, "MAX_TURNS_PER_MIN", 2)
    table = new_table(client, stub)

    other = second_browser(client.app)
    try:
        other.post("/api/campaigns/join", json={"code": table.code})
        other.post(f"/api/campaigns/{table.id}/characters",
                   json={"character": {"name": "Vandar", "race": "Dwarf",
                                       "class": "Cleric"}}).raise_for_status()
        for i in range(2):
            table.act(f"spam {i}", "ok")
        blocked = other.post(f"/api/campaigns/{table.id}/act", json={"text": "my turn"})
        assert blocked.status_code == 429
    finally:
        other.__exit__(None, None, None)
    stub.script.clear()


def test_known_locks_and_subscribers_are_never_cleaned_up(app_client):
    """Two module-level defaultdicts gain an entry per campaign id and lose none, even
    when the campaign is deleted. Small, but it is a leak in a long-lived process."""
    import server
    client, stub = app_client
    before = len(server.locks)
    table = new_table(client, stub)
    table.begin("Hello.")
    assert len(server.locks) == before + 1
    client.delete(f"/api/campaigns/{table.id}").raise_for_status()
    assert len(server.locks) == before + 1        # still there
