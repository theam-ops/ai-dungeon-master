"""The Postgres repository does what the SQLite one does - checked by doing it on both.

One scenario touches every method of the Repository port, on SQLite and then on
Postgres, and the two transcripts must match once the things that are meant to differ -
random ids, campaign codes, clock readings - are put in their place. A difference in
anything else is a difference a player would see on a `prod` deployment and never on
their laptop, which is exactly the kind of bug a test suite run only in `lite` misses.

After that, what only a database whose calls really interleave can get wrong: every
read-then-write raced against itself.

Skipped without a Postgres - see tests/pgcluster.py.
"""

import asyncio
import contextlib
import copy
import json
import re

from game import battlemap, rules
from game.adapters.lite import SQLiteRepository

SCORES = {"STR": 10, "DEX": 16, "CON": 14, "INT": 12, "WIS": 13, "CHA": 8}
VESS = rules.new_character("Vess", "Elf", "Rogue", scores=SCORES)
BRAN = rules.new_character("Bran", "Dwarf", "Wizard", scores=SCORES)


async def scenario(repo):
    """Every method, with the edge each one is known for. Returns what was observed."""
    seen = []

    def saw(label, value):
        seen.append((label, copy.deepcopy(value)))

    c = await repo.create_campaign("The Salt Road", "th", "stub")
    other = await repo.create_campaign("Elsewhere")
    cid = c["id"]
    saw("created", c)

    saw("lang", await repo.campaign_lang(cid))
    saw("lang of nothing", await repo.campaign_lang("missing"))
    saw("backend", await repo.campaign_backend(cid))
    await repo.set_campaign_backend(cid, "gemini")
    saw("backend now", await repo.campaign_backend(cid))
    saw("house", await repo.campaign_house(cid))
    saw("house set", await repo.set_campaign_house(cid, {"variant_encumbrance": True,
                                                          "nonsense": 1}))
    saw("house now", await repo.campaign_house(cid))

    saw("memory", await repo.get_memory(cid))
    saw("memory set", await repo.set_memory(cid, {"upto": 4, "synopsis": "They met.",
                                                   "turns": 2}))
    saw("memory older", await repo.set_memory(cid, {"upto": 2, "synopsis": "Stale.",
                                                     "turns": 1}))
    saw("memory now", await repo.get_memory(cid))

    saw("combat", await repo.get_combat(cid))
    fight = {"round": 1, "turn": 0, "order": [{"name": "Vess", "init": 17, "pc": True}]}
    saw("combat set", await repo.set_combat(cid, fight))
    saw("combat now", await repo.get_combat(cid))
    await repo.set_combat(cid, None)
    saw("combat over", await repo.get_combat(cid))

    saw("map", await repo.get_map(cid))
    saw("map drawn", await repo.change_map(cid, lambda m: battlemap.apply(m, {
        "new_width": 8, "new_height": 6, "fill": "wall",
        "paint": [{"terrain": "room", "x1": 0, "y1": 0, "x2": 7, "y2": 5}],
        "tokens": [{"name": "Vess", "kind": "pc", "x": 2, "y": 2},
                   {"name": "Gull", "kind": "npc", "x": 5, "y": 3}]}, ["Vess"])[0]))
    saw("map moved", await repo.change_map(cid, lambda m: battlemap.move(m, "Vess", 3, 2)[0]))
    saw("map now", await repo.get_map(cid))
    saw("map of nothing", await repo.change_map("missing", lambda m: m))
    saw("campaign", await repo.get_campaign(cid))
    saw("by code", await repo.campaign_by_code(" " + c["code"].lower() + " "))
    saw("by no code", await repo.campaign_by_code("ZZZZZZ"))
    saw("no campaign", await repo.get_campaign("missing"))

    saw("history", await repo.get_history(cid))
    await repo.save_history(cid, [{"role": "user", "content": "เริ่ม"},
                                  {"role": "assistant", "content": "The road is salt."}])
    saw("noted", await repo.note_in_history(cid, "<table_note>Bran joins</table_note>"))
    saw("history now", await repo.get_history(cid))

    vess = await repo.add_character(cid, copy.deepcopy(VESS), "tok-a", notes="x" * 900)
    bran = await repo.add_character(cid, copy.deepcopy(BRAN), "tok-b")
    saw("ids", [vess, bran])
    saw("party", await repo.party(cid))
    saw("mine", await repo.character_for_token(cid, "tok-a"))
    saw("not mine", await repo.character_for_token(cid, "tok-z"))
    await repo.claim_character(bran, "tok-c")
    saw("claimed", await repo.character_for_token(cid, "tok-c"))
    saw("notes", await repo.set_character_notes(vess, "  Afraid of the sea.  "))
    saw("my campaigns", await repo.campaigns_for_token("tok-a"))

    party = await repo.party(cid)
    party[0]["hp"] -= 3
    party[0]["portrait"] = "must not be written into the sheet"
    await repo.save_party(party)
    saw("saved", await repo.party(cid))

    m1 = await repo.add_media(cid, "abc.png", "npc", "image/png", 1200, 64, 48,
                              "The ferryman", "upload", "tok-a")
    m2 = await repo.add_media(cid, "abc.png", "scene", "image/png", 1200, 64, 48)
    saw("media", await repo.get_media(cid, m1))
    saw("media elsewhere", await repo.get_media(other["id"], m1))
    saw("updated", await repo.update_media(cid, m1, caption="c" * 500, kind="portrait"))
    saw("all media", await repo.campaign_media(cid))
    saw("count", await repo.media_count(cid))
    saw("shared file", await repo.file_still_used(cid, "abc.png"))
    saw("shared but me", await repo.file_still_used(cid, "abc.png", except_id=m1))
    await repo.set_portrait(vess, m1)
    saw("portrait", [c_["portrait"] for c_ in await repo.party(cid)])
    await repo.delete_media(cid, m1)
    saw("portrait gone", [c_["portrait"] for c_ in await repo.party(cid)])
    saw("file now", await repo.file_still_used(cid, "abc.png", except_id=m2))

    saw("art first", await repo.claim_art_slot(cid, 2))
    saw("art again", await repo.claim_art_slot(cid, 2))
    saw("art nowhere", await repo.claim_art_slot("missing", 2))
    for kind, payload in [("player", {"character": "Vess", "text": "I wade in."}),
                          ("narration", {"text": "Cold. ทะเลเย็น"}),
                          ("player", {"character": "Bran", "text": "Me too."}),
                          ("image", {"media": m2, "caption": "the bay"})]:
        saw("appended", await repo.append_event(cid, kind, payload))
    saw("art after two turns", await repo.claim_art_slot(cid, 2))
    saw("events", await repo.events_since(cid))
    saw("events after one", await repo.events_since(cid, 1, limit=2))
    saw("last seq", await repo.last_seq(cid))
    saw("last seq of nothing", await repo.last_seq(other["id"]))
    saw("turns", await repo.turns_in_last_minute(cid))

    saw("lore", await repo.add_lore(cid, "Gazetteer", "The Salt Road runs east."))
    saw("lore again", await repo.add_lore(cid, "Gazetteer", "It runs west, actually."))
    lid = (await repo.add_lore(cid, "Appendix", "Tides."))["id"]
    saw("documents", await repo.lore_documents(cid))
    saw("texts", await repo.lore_texts(cid))
    await repo.delete_lore(cid, lid)
    saw("documents now", await repo.lore_documents(cid))

    blob = await repo.export_campaign(cid)
    saw("export", blob)
    saw("export of nothing", await repo.export_campaign("missing"))
    copy_ = await repo.import_campaign(json.loads(json.dumps(blob)))
    saw("imported", copy_)
    saw("import exported", await repo.export_campaign(copy_["id"]))
    saw("import keeps code", copy_["code"] == c["code"])
    for bad in ([], {"format": "x"}, {"format": "ai-dm-campaign/1"},
                {"format": "ai-dm-campaign/1", "campaign": {"history": "no"}},
                {"format": "ai-dm-campaign/1", "campaign": {}, "characters": "no"}):
        try:
            await repo.import_campaign(bad)
            saw("bad import", "accepted")
        except ValueError as e:
            saw("bad import", str(e))

    await repo.delete_campaign(cid)
    saw("deleted", await repo.get_campaign(cid))
    saw("deleted events", await repo.events_since(cid))
    saw("deleted lore", await repo.lore_texts(cid))
    saw("my campaigns after", await repo.campaigns_for_token("tok-a"))
    return seen


TIMES = {"created_at", "updated_at", "at", "last_art"}
UID = re.compile(r"^[a-z0-9]{16}$")


def normalise(transcript):
    """Ids become <id1>, <id2>... in order of first appearance; codes and clock readings
    become placeholders. What is left must match exactly."""
    names = {}

    def walk(v, key=None):
        if isinstance(v, dict):
            return {k: walk(x, k) for k, x in v.items() if k not in TIMES}
        if isinstance(v, (list, tuple)):
            return [walk(x) for x in v]
        if key == "code" and isinstance(v, str):
            return "<code>"
        if isinstance(v, str) and UID.match(v):
            return names.setdefault(v, f"<id{len(names) + 1}>")
        if isinstance(v, str) and v[:1] in "[{":       # a JSON column, read raw
            try:
                return {"json": walk(json.loads(v))}
            except ValueError:
                pass
        return v
    return [(label, walk(value)) for label, value in transcript]


def test_postgres_does_what_sqlite_does(pg):
    async def on_postgres():
        async with pg() as adapters:
            return await scenario(adapters.repo)

    sqlite = normalise(asyncio.run(scenario(SQLiteRepository())))
    postgres = normalise(asyncio.run(on_postgres()))
    assert [label for label, _ in postgres] == [label for label, _ in sqlite]
    for (label, want), (_, got) in zip(sqlite, postgres):
        assert got == want, f"{label}: Postgres differs from SQLite"


# --------------------------------------------------------------------------- #
# what only a database whose calls interleave can get wrong
# --------------------------------------------------------------------------- #

@contextlib.asynccontextmanager
async def campaign_row_held(adapters, cid):
    """Hold a campaign's row from outside, so every writer started meanwhile has done
    whatever it does before it locks, and then they all go at once.

    Racing the writers without this proves little: the first usually finishes before
    the second has a connection, and an unprotected read-then-write passes. With it, a
    writer that reads without locking reads the same stale value as all the others."""
    async with adapters.db.pool.acquire() as conn:
        transaction = conn.transaction()
        await transaction.start()
        await conn.execute("SELECT 1 FROM campaigns WHERE id=$1 FOR UPDATE", cid)
        try:
            yield
            await asyncio.sleep(0.5)            # every writer is in, and waiting
        finally:
            await transaction.commit()


async def all_at_once(adapters, cid, calls):
    async with campaign_row_held(adapters, cid):
        tasks = [asyncio.create_task(call) for call in calls]
    return await asyncio.gather(*tasks)


def test_a_summary_never_goes_backwards_however_they_race(pg):
    async def run():
        async with pg() as a, pg() as b:
            cid = (await a.repo.create_campaign("Race"))["id"]
            # newest first: if they land in the order they started, the oldest lands last
            await all_at_once(a, cid, [x.repo.set_memory(cid, {"upto": n, "synopsis": str(n)})
                                       for n, x in zip(range(16, 0, -1), [a, b] * 8)])
            return await a.repo.get_memory(cid)
    assert asyncio.run(run())["upto"] == 16


def test_only_one_of_many_simultaneous_turns_wins_the_illustration(pg):
    """The slot is all that stands between a chatty model and a large bill."""
    async def run():
        async with pg() as a, pg() as b:
            cid = (await a.repo.create_campaign("Race"))["id"]
            return await all_at_once(a, cid, [x.repo.claim_art_slot(cid, 3)
                                              for x in [a, b] * 8])
    assert sum(asyncio.run(run())) == 1


def test_notes_added_at_once_are_all_kept(pg):
    async def run():
        async with pg() as a, pg() as b:
            cid = (await a.repo.create_campaign("Race"))["id"]
            await all_at_once(a, cid, [x.repo.note_in_history(cid, f"note {n}")
                                       for n, x in enumerate([a, b] * 8)])
            return await a.repo.get_history(cid)
    assert sorted(m["content"] for m in asyncio.run(run())) == sorted(
        f"note {n}" for n in range(16))


def test_one_document_name_is_one_document_however_it_is_raced(pg):
    async def run():
        async with pg() as a, pg() as b:
            cid = (await a.repo.create_campaign("Race"))["id"]
            await all_at_once(a, cid, [x.repo.add_lore(cid, "Gazetteer", f"draft {n}")
                                       for n, x in enumerate([a, b] * 4)])
            return await a.repo.lore_documents(cid)
    assert len(asyncio.run(run())) == 1


def test_map_changes_made_at_once_are_all_kept(pg):
    """A dozen tokens placed at once, from two servers: every one lands."""
    async def run():
        async with pg() as a, pg() as b:
            cid = (await a.repo.create_campaign("Race"))["id"]
            await a.repo.change_map(cid, lambda m: battlemap.new_map(20, 20))

            def place(n):
                return lambda m: battlemap.apply(
                    m, {"tokens": [{"name": f"Rat {n}", "kind": "npc", "x": n, "y": n}]},
                    [])[0]
            await all_at_once(a, cid, [x.repo.change_map(cid, place(n))
                                       for n, x in enumerate([a, b] * 6)])
            return await a.repo.get_map(cid)
    assert len(asyncio.run(run())["tokens"]) == 12


def test_appends_from_two_servers_make_one_gapless_ordered_log(pg):
    async def run():
        async with pg() as a, pg() as b:
            cid = (await a.repo.create_campaign("Race"))["id"]
            sent = await asyncio.gather(*(r.repo.append_event(cid, "narration", {"n": n})
                                          for n, r in enumerate([a, b] * 25)))
            return sent, await a.repo.events_since(cid)
    sent, log = asyncio.run(run())
    seqs = [e["seq"] for e in log]
    assert seqs == sorted(seqs) and len(set(seqs)) == 50
    assert sorted(e["n"] for e in log) == list(range(50))
    assert sorted(e["seq"] for e in sent) == seqs


def test_two_new_campaigns_never_share_a_code(pg, monkeypatch):
    """The code is UNIQUE and a clash is retried. Forced here: every first guess is
    the same code."""
    from game.adapters.postgres import repo as pg_repo
    guesses = iter(["SAMEAA", "SAMEAA", "OTHERB"] + ["FRESHC"] * 10)
    monkeypatch.setattr(pg_repo, "_code", lambda: next(guesses))

    async def run():
        async with pg() as a:
            return [(await a.repo.create_campaign(n))["code"] for n in ("one", "two")]
    assert asyncio.run(run()) == ["SAMEAA", "OTHERB"]


def test_text_postgres_cannot_hold_is_cleaned_not_refused(pg):
    """SQLite keeps a NUL inside TEXT; Postgres rejects the whole row. A document
    pasted from somewhere odd must not fail to upload on one deployment only."""
    async def run():
        async with pg() as a:
            cid = (await a.repo.create_campaign("Nul\x00l"))["id"]
            await a.repo.add_lore(cid, "Odd\x00doc", "a\x00b")
            await a.repo.append_event(cid, "player", {"text": "c\x00d"})
            return (await a.repo.get_campaign(cid))["name"], await a.repo.lore_texts(cid), \
                (await a.repo.events_since(cid))[0]["text"]
    assert asyncio.run(run()) == ("Null", [("Odddoc", "ab")], "c\x00d")


def test_a_database_from_before_the_map_gains_the_column_on_start(pg):
    """Phase 8 deployments made their tables without `map`. CREATE TABLE IF NOT EXISTS
    would leave them that way; the next start must add it."""
    async def run():
        async with pg() as a:
            cid = (await a.repo.create_campaign("Old"))["id"]
            await a.db.pool.execute("ALTER TABLE campaigns DROP COLUMN map")
        async with pg() as b:                      # the next server to start
            await b.repo.change_map(cid, lambda m: battlemap.new_map(5, 5))
            return await b.repo.get_map(cid)
    assert asyncio.run(run())["w"] == 5
