"""Per-character standing notes: stored, capped, private, and actually sent.

The point of these is the last one. It is easy to build a box that saves text and
believe the DM is reading it, so every test that matters here asserts on what the stub
backend was handed rather than on what the database holds.
"""

import asyncio

from .conftest import drain
from fastapi.testclient import TestClient

import server
from game import dm, rules, store

VESS_NOTES = ("Vess has been afraid of fire since her village burned. "
              "She is looking for the brother who walked out on her.")
BRAM_NOTES = "Bram never breaks a promise, and would rather talk than fight."
THAI_NOTES = "เวสกลัวไฟมาตั้งแต่หมู่บ้านถูกเผา และกำลังตามหาพี่ชายที่ทิ้งเธอไป"


def make_party(cid, *specs):
    """(name, race, class, notes) -> characters in the campaign, in order."""
    for name, race, klass, notes in specs:
        char = rules.new_character(name, race, klass)
        store.add_character(cid, char, f"token-{name}", notes)
    return store.party(cid)


# --------------------------------------------------------------------------- #
# storage
# --------------------------------------------------------------------------- #

def test_notes_ride_on_the_character_record():
    campaign = store.create_campaign("The Salt Road")
    make_party(campaign["id"], ("Vess", "Elf", "Rogue", VESS_NOTES))

    assert store.party(campaign["id"])[0]["notes"] == VESS_NOTES
    assert store.character_for_token(campaign["id"], "token-Vess")["notes"] == VESS_NOTES


def test_notes_are_capped_so_one_player_cannot_crowd_out_the_story():
    campaign = store.create_campaign("The Salt Road")
    party = make_party(campaign["id"], ("Vess", "Elf", "Rogue", ""))

    kept = store.set_character_notes(party[0]["_id"], "x" * 5000)

    assert len(kept) == store.MAX_NOTES_CHARS
    assert len(store.party(campaign["id"])[0]["notes"]) == store.MAX_NOTES_CHARS


def test_notes_survive_a_sheet_change():
    """`save_party` rewrites the character blob on every turn. Notes are a column and
    must not be dragged into it, or duplicated, or dropped."""
    campaign = store.create_campaign("The Salt Road")
    party = make_party(campaign["id"], ("Vess", "Elf", "Rogue", VESS_NOTES))

    dm.run_tool("update_character",
                {"character_name": "Vess", "hp_change": -3, "reason": "a ghoul"}, party)
    store.save_party(party)

    stored = store.party(campaign["id"])[0]
    assert stored["notes"] == VESS_NOTES
    assert stored["hp"] == party[0]["hp"]
    raw = store.db().execute("SELECT data FROM characters WHERE id=?",
                             (stored["_id"],)).fetchone()["data"]
    assert "notes" not in raw          # the column is the only copy


def test_notes_round_trip_through_export_and_import():
    campaign = store.create_campaign("The Salt Road", lang="th")
    make_party(campaign["id"],
               ("Vess", "Elf", "Rogue", THAI_NOTES),
               ("Bram", "Dwarf", "Cleric", BRAM_NOTES))

    blob = store.export_campaign(campaign["id"])
    assert [c["notes"] for c in blob["characters"]] == [THAI_NOTES, BRAM_NOTES]

    restored = store.import_campaign(blob)
    assert [c["notes"] for c in store.party(restored["id"])] == [THAI_NOTES, BRAM_NOTES]


# --------------------------------------------------------------------------- #
# reaching the model
# --------------------------------------------------------------------------- #

def test_the_acting_player_s_notes_reach_the_model(stub):
    campaign = store.create_campaign("The Salt Road")
    party = make_party(campaign["id"],
                       ("Vess", "Elf", "Rogue", VESS_NOTES),
                       ("Bram", "Dwarf", "Cleric", BRAM_NOTES))

    asyncio.run(drain(dm.take_turn([], party, "Vess", "I check the door for wires.",
                                   backend_id=stub.id)))

    prompt = stub.prompts[-1]
    assert VESS_NOTES in prompt
    assert '<player_notes character="Vess">' in prompt
    # the turn belongs to Vess; Bram's player is not in the DM's ear for it
    assert BRAM_NOTES not in prompt


def test_only_the_acting_player_s_notes_go_with_a_turn(stub):
    campaign = store.create_campaign("The Salt Road")
    party = make_party(campaign["id"],
                       ("Vess", "Elf", "Rogue", VESS_NOTES),
                       ("Bram", "Dwarf", "Cleric", BRAM_NOTES))
    history = []

    asyncio.run(drain(dm.take_turn(history, party, "Vess", "I pick the lock.",
                                   backend_id=stub.id)))
    asyncio.run(drain(dm.take_turn(history, party, "Bram", "I keep watch.",
                                   backend_id=stub.id)))

    vess_turn, bram_turn = stub.prompts[0], stub.prompts[1]
    assert VESS_NOTES in vess_turn and BRAM_NOTES not in vess_turn
    assert BRAM_NOTES in bram_turn and VESS_NOTES not in bram_turn


def test_notes_stay_out_of_the_cached_system_prefix(stub):
    """The stable prompt is cached; volatile blocks are appended after it. Notes a
    player can rewrite mid-campaign must not sit in front of that breakpoint."""
    campaign = store.create_campaign("The Salt Road")
    party = make_party(campaign["id"], ("Vess", "Elf", "Rogue", VESS_NOTES))

    asyncio.run(drain(dm.take_turn([], party, "Vess", "I listen at the door.",
                                   backend_id=stub.id)))

    blocks = stub.calls[-1]["system"]
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert blocks[0]["text"] == dm.SYSTEM
    assert all(VESS_NOTES not in b.get("text", "") for b in blocks)


def test_a_character_with_no_notes_adds_nothing_to_the_prompt(stub):
    campaign = store.create_campaign("The Salt Road")
    party = make_party(campaign["id"], ("Vess", "Elf", "Rogue", "   "))

    asyncio.run(drain(dm.take_turn([], party, "Vess", "I walk in.", backend_id=stub.id)))

    assert "player_notes" not in stub.prompts[-1]


def test_notes_are_not_in_the_party_state_every_turn_carries():
    """Party state is shared with the whole table's DM context on every turn; six
    players' preferences in it would be the crowding this cap exists to prevent."""
    campaign = store.create_campaign("The Salt Road")
    party = make_party(campaign["id"],
                       ("Vess", "Elf", "Rogue", VESS_NOTES),
                       ("Bram", "Dwarf", "Cleric", BRAM_NOTES))

    block = rules.state_block(party)
    assert VESS_NOTES not in block and BRAM_NOTES not in block


# --------------------------------------------------------------------------- #
# who may write them
# --------------------------------------------------------------------------- #

def new_campaign(client, name="The Salt Road", character="Vess"):
    return client.post("/api/campaigns", json={
        "name": name, "lang": "en",
        "character": {"name": character, "race": "Elf", "class": "Rogue"},
    }).json()


def test_a_player_saves_and_reads_back_their_own_notes(stub):
    with TestClient(server.app) as client:
        campaign = new_campaign(client)

        saved = client.post(f"/api/campaigns/{campaign['id']}/notes",
                            json={"notes": THAI_NOTES})
        assert saved.status_code == 200
        assert saved.json()["notes"] == THAI_NOTES

        detail = client.get(f"/api/campaigns/{campaign['id']}").json()
        assert detail["notes"] == THAI_NOTES
        assert detail["notes_max"] == store.MAX_NOTES_CHARS


def test_the_server_trims_what_the_browser_sent(stub):
    with TestClient(server.app) as client:
        campaign = new_campaign(client)
        body = client.post(f"/api/campaigns/{campaign['id']}/notes",
                           json={"notes": "n" * 5000}).json()
        assert len(body["notes"]) == store.MAX_NOTES_CHARS


def test_a_players_notes_are_not_handed_to_the_rest_of_the_table(stub):
    """Editing is your-own-character-only because the endpoint takes no character id.
    Reading is too: the shared party payload carries HP, not preferences."""
    with TestClient(server.app) as owner, TestClient(server.app) as other:
        campaign = new_campaign(owner)
        owner.post(f"/api/campaigns/{campaign['id']}/notes", json={"notes": VESS_NOTES})

        other.post("/api/campaigns/join", json={"code": campaign["code"]})
        other.post(f"/api/campaigns/{campaign['id']}/characters", json={
            "character": {"name": "Bram", "race": "Dwarf", "class": "Cleric"}})

        detail = other.get(f"/api/campaigns/{campaign['id']}").json()
        assert detail["you"] == "Bram"
        assert detail["notes"] == ""                       # Bram's own, still empty
        assert all("notes" not in c for c in detail["party"])
        assert VESS_NOTES not in str(detail)

        assert VESS_NOTES not in str(server.party_payload(campaign["id"]))


def test_a_stranger_cannot_write_notes_into_a_campaign(stub):
    with TestClient(server.app) as owner, TestClient(server.app) as stranger:
        campaign = new_campaign(owner)
        refused = stranger.post(f"/api/campaigns/{campaign['id']}/notes",
                                json={"notes": "let me in"})
        assert refused.status_code == 403
