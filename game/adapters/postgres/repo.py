"""The Repository port over Postgres.

Method for method the SQLite store in `game/store.py`, and it shares that module's
logic wherever there is logic to share - how an old character sheet is upgraded, how an
import is validated - so the two can only differ in SQL.

What changes is concurrency. SQLite's calls never yield to the event loop, so a read and
the write after it ran back to back by accident; here every call is a network round
trip, and other requests - on this process or another - run in between. So every
read-then-write below is a transaction holding a row lock, and the event log is
appended under a per-campaign lock: see `append_event` for why a sequence alone is not
enough.
"""

import json
import random
import time

import asyncpg

from ... import battlemap, rules, store
from ...ports import Repository


def _t(value):
    """A string Postgres will store. SQLite keeps a NUL character in TEXT; Postgres
    refuses the whole row, so a stray one in an uploaded document would fail the upload
    here and nowhere else."""
    return value.replace("\x00", "") if isinstance(value, str) else value


def _dumps(value):
    # json escapes control characters itself, NUL included, so a dump is always storable
    return json.dumps(value, ensure_ascii=False)


# what SQLite's `SELECT *` returns for a picture - `pos` is Postgres' own bookkeeping
MEDIA = ("id, campaign_id, file, kind, mime, bytes, width, height, caption, source, owner,"
         " created_at")


def _code():
    return "".join(random.choices(store.CODE_ALPHABET, k=6))


class PostgresRepository(Repository):
    """Campaign state in Postgres, shared by every process serving the game."""

    def __init__(self, db):
        self._db = db

    @property
    def _pool(self):
        return self._db.pool

    # -- campaigns ----------------------------------------------------------- #

    async def create_campaign(self, name, lang="en", backend=""):
        name, now = _t(name), time.time()
        # the code is UNIQUE, so a clash is the database's to notice - checking first
        # and inserting after would race against another process doing the same
        for _ in range(50):
            cid, code = store._uid(), _code()
            try:
                await self._pool.execute(
                    "INSERT INTO campaigns (id, code, name, lang, backend, history,"
                    " created_at, updated_at) VALUES ($1,$2,$3,$4,$5,'[]',$6,$6)",
                    cid, code, name, lang, backend, now)
            except asyncpg.UniqueViolationError:
                continue
            return {"id": cid, "code": code, "name": name, "lang": lang, "backend": backend}
        raise RuntimeError("could not allocate a free campaign code")

    async def _column(self, cid, column):
        return await self._pool.fetchval(f"SELECT {column} FROM campaigns WHERE id=$1", cid)

    async def campaign_lang(self, cid):
        return await self._column(cid, "lang") or "en"

    async def campaign_backend(self, cid):
        return await self._column(cid, "backend") or ""

    async def campaign_house(self, cid):
        try:
            return rules.clean_house(json.loads(await self._column(cid, "house") or "{}"))
        except (TypeError, ValueError):
            return rules.clean_house({})

    async def set_campaign_house(self, cid, house):
        clean = rules.clean_house(house)
        await self._pool.execute("UPDATE campaigns SET house=$2, updated_at=$3 WHERE id=$1",
                                 cid, json.dumps(clean), time.time())
        return clean

    @staticmethod
    def _loads(text):
        try:
            return json.loads(text) if text else None
        except (TypeError, ValueError):
            return None

    async def get_memory(self, cid):
        return self._loads(await self._column(cid, "memory"))

    async def set_memory(self, cid, memory):
        # read, compare, write - under the row's lock, or two summaries finishing
        # together could both see the older one and the smaller could land last
        async with self._pool.acquire() as conn, conn.transaction():
            current = self._loads(await conn.fetchval(
                "SELECT memory FROM campaigns WHERE id=$1 FOR UPDATE", cid))
            if current and int(current.get("upto", 0)) >= int(memory.get("upto", 0)):
                return current
            memory = {**memory, "at": time.time()}
            await conn.execute("UPDATE campaigns SET memory=$2 WHERE id=$1",
                               cid, _t(_dumps(memory)))
            return memory

    async def get_combat(self, cid):
        return self._loads(await self._column(cid, "combat"))

    async def set_combat(self, cid, combat):
        await self._pool.execute(
            "UPDATE campaigns SET combat=$2, updated_at=$3 WHERE id=$1",
            cid, _t(_dumps(combat)) if combat else "", time.time())
        return combat

    @staticmethod
    def _map(text):
        try:
            return battlemap.clean(json.loads(text)) if text else None
        except (TypeError, ValueError):
            return None

    async def get_map(self, cid):
        return self._map(await self._column(cid, "map"))

    async def change_map(self, cid, change):
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow("SELECT map FROM campaigns WHERE id=$1 FOR UPDATE", cid)
            if row is None:
                return None
            new = change(self._map(row["map"]))
            await conn.execute("UPDATE campaigns SET map=$2, updated_at=$3 WHERE id=$1",
                               cid, _t(_dumps(new)) if new else "", time.time())
            return new

    async def set_campaign_backend(self, cid, backend):
        await self._pool.execute("UPDATE campaigns SET backend=$2, updated_at=$3 WHERE id=$1",
                                 cid, backend, time.time())

    async def get_campaign(self, cid):
        row = await self._pool.fetchrow("SELECT * FROM campaigns WHERE id=$1", cid)
        return dict(row) if row else None

    async def campaign_by_code(self, code):
        row = await self._pool.fetchrow("SELECT * FROM campaigns WHERE code=$1",
                                        (code or "").strip().upper())
        return dict(row) if row else None

    async def get_history(self, cid):
        text = await self._column(cid, "history")
        return json.loads(text) if text is not None else []

    async def save_history(self, cid, history):
        await self._pool.execute(
            "UPDATE campaigns SET history=$2, updated_at=$3 WHERE id=$1",
            cid, _t(_dumps(history)), time.time())

    async def note_in_history(self, cid, text):
        async with self._pool.acquire() as conn, conn.transaction():
            raw = await conn.fetchval(
                "SELECT history FROM campaigns WHERE id=$1 FOR UPDATE", cid)
            history = json.loads(raw) if raw is not None else []
            history.append({"role": "user", "content": text})
            await conn.execute(
                "UPDATE campaigns SET history=$2, updated_at=$3 WHERE id=$1",
                cid, _t(_dumps(history)), time.time())
            return history

    async def campaigns_for_token(self, token):
        rows = await self._pool.fetch(
            "SELECT c.id, c.code, c.name, c.lang, c.updated_at, ch.name AS character_name"
            " FROM campaigns c JOIN characters ch ON ch.campaign_id = c.id"
            " WHERE ch.player_token=$1 ORDER BY c.updated_at DESC", token)
        return [dict(r) for r in rows]

    async def delete_campaign(self, cid):
        # every other table cascades from campaigns
        await self._pool.execute("DELETE FROM campaigns WHERE id=$1", cid)

    # -- characters ---------------------------------------------------------- #

    async def party(self, cid):
        rows = await self._pool.fetch(
            "SELECT id, player_token, data, portrait, notes FROM characters"
            " WHERE campaign_id=$1 ORDER BY created_at, pos", cid)
        out = [store.character_from_row(r) for r in rows]
        return store.with_derived(out, await self.campaign_house(cid) if out else None)

    async def save_party(self, characters):
        rows = []
        for ch in characters:
            data = {k: v for k, v in ch.items()
                    if not k.startswith("_") and k not in store.COLUMN_FIELDS}
            rows.append((ch["_id"], _t(_dumps(data)), _t(data["name"])))
        if rows:
            async with self._pool.acquire() as conn, conn.transaction():
                await conn.executemany(
                    "UPDATE characters SET data=$2, name=$3 WHERE id=$1", rows)

    async def add_character(self, cid, char, token, notes="", *, conn=None):
        chid = store._uid()
        await (conn or self._pool).execute(
            "INSERT INTO characters (id, campaign_id, player_token, name, data, notes,"
            " created_at) VALUES ($1,$2,$3,$4,$5,$6,$7)",
            chid, cid, token, _t(char["name"]), _t(_dumps(char)),
            _t(store.clean_notes(notes)), time.time())
        return chid

    async def character_for_token(self, cid, token):
        row = await self._pool.fetchrow(
            "SELECT id, data, notes FROM characters WHERE campaign_id=$1 AND player_token=$2"
            " ORDER BY created_at, pos LIMIT 1", cid, token)
        if not row:
            return None
        ch = json.loads(row["data"])
        ch["_id"] = row["id"]
        ch["notes"] = row["notes"] or ""
        return ch

    async def claim_character(self, char_id, token):
        await self._pool.execute("UPDATE characters SET player_token=$2 WHERE id=$1",
                                 char_id, token)

    async def set_character_notes(self, char_id, text):
        notes = _t(store.clean_notes(text))
        await self._pool.execute("UPDATE characters SET notes=$2 WHERE id=$1",
                                 char_id, notes)
        return notes

    async def set_portrait(self, char_id, mid):
        await self._pool.execute("UPDATE characters SET portrait=$2 WHERE id=$1",
                                 char_id, mid)

    # -- the transcript, and the event log ----------------------------------- #

    async def append_event(self, cid, kind, payload):
        """Record an event. Its `seq` comes from a sequence, but a sequence alone does
        not keep the log in order for a reader: two writers draw 10 and 11, the one
        holding 11 commits first, a browser reads 11 and moves its bookmark past 10,
        which then appears behind it and is never sent. So appends to one campaign take
        turns - a transaction-scoped lock, held until commit - and within a campaign a
        later seq is always committed after an earlier one. Different campaigns do not
        wait for each other: nobody reads two campaigns' logs as one."""
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)",
                               self._db.lock_id(f"events:{cid}"))
            seq = await conn.fetchval(
                "INSERT INTO events (campaign_id, kind, payload, created_at)"
                " VALUES ($1,$2,$3,$4) RETURNING seq",
                cid, kind, _t(_dumps(payload)), time.time())
        return {**payload, "seq": seq, "kind": kind}

    async def events_since(self, cid, since=0, limit=500):
        rows = await self._pool.fetch(
            "SELECT seq, kind, payload FROM events WHERE campaign_id=$1 AND seq>$2"
            " ORDER BY seq LIMIT $3", cid, int(since or 0), int(limit))
        return [{**json.loads(r["payload"]), "seq": r["seq"], "kind": r["kind"]}
                for r in rows]

    async def last_seq(self, cid):
        return await self._pool.fetchval(
            "SELECT MAX(seq) FROM events WHERE campaign_id=$1", cid) or 0

    async def turns_in_last_minute(self, cid):
        return await self._pool.fetchval(
            "SELECT COUNT(*) FROM events WHERE campaign_id=$1 AND kind='player'"
            " AND created_at>$2", cid, time.time() - 60)

    # -- images -------------------------------------------------------------- #

    async def add_media(self, cid, file, kind, mime, size, width, height,
                        caption="", source="upload", owner=None):
        mid = store._uid()
        await self._pool.execute(
            "INSERT INTO media (id, campaign_id, file, kind, mime, bytes, width, height,"
            " caption, source, owner, created_at)"
            " VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)",
            mid, cid, file, kind, mime, size, width, height, _t(caption), source, owner,
            time.time())
        return mid

    async def get_media(self, cid, mid):
        row = await self._pool.fetchrow(
            f"SELECT {MEDIA} FROM media WHERE id=$1 AND campaign_id=$2", mid, cid)
        return dict(row) if row else None

    async def update_media(self, cid, mid, caption=None, kind=None):
        async with self._pool.acquire() as conn, conn.transaction():
            if caption is not None:
                await conn.execute(
                    "UPDATE media SET caption=$3 WHERE campaign_id=$1 AND id=$2",
                    cid, mid, _t(caption[:400]))
            if kind is not None:
                await conn.execute("UPDATE media SET kind=$3 WHERE campaign_id=$1 AND id=$2",
                                   cid, mid, kind)
            row = await conn.fetchrow(
                f"SELECT {MEDIA} FROM media WHERE id=$1 AND campaign_id=$2", mid, cid)
        return dict(row) if row else None

    async def delete_media(self, cid, mid):
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("DELETE FROM media WHERE id=$1 AND campaign_id=$2", mid, cid)
            await conn.execute(
                "UPDATE characters SET portrait='' WHERE campaign_id=$1 AND portrait=$2",
                cid, mid)

    async def campaign_media(self, cid):
        rows = await self._pool.fetch(
            f"SELECT {MEDIA} FROM media WHERE campaign_id=$1 ORDER BY created_at, pos", cid)
        return [dict(r) for r in rows]

    async def media_count(self, cid):
        return await self._pool.fetchval(
            "SELECT COUNT(*) FROM media WHERE campaign_id=$1", cid)

    async def file_still_used(self, cid, file, except_id=None):
        return bool(await self._pool.fetchval(
            "SELECT EXISTS (SELECT 1 FROM media WHERE campaign_id=$1 AND file=$2"
            " AND id IS DISTINCT FROM $3)", cid, file, except_id or None))

    async def claim_art_slot(self, cid, every_turns):
        """See `store.claim_art_slot`. The row lock is what makes it atomic here: a
        second claim waits for the first to commit, then sees the slot already taken."""
        async with self._pool.acquire() as conn, conn.transaction():
            last = await conn.fetchval(
                "SELECT last_art FROM campaigns WHERE id=$1 FOR UPDATE", cid)
            if last is None:
                return False
            if last:
                since = await conn.fetchval(
                    "SELECT COUNT(*) FROM events WHERE campaign_id=$1 AND kind='player'"
                    " AND created_at>$2", cid, last)
                if since < every_turns:
                    return False
            await conn.execute("UPDATE campaigns SET last_art=$2 WHERE id=$1",
                               cid, time.time())
            return True

    # -- the campaign library ------------------------------------------------ #

    async def add_lore(self, cid, name, text):
        name, text, lid = _t(name), _t(text), store._uid()
        async with self._pool.acquire() as conn, conn.transaction():
            # replacing by name is delete-then-insert; the campaign's row lock stops two
            # imports of one name from both deleting nothing and both inserting
            await conn.execute("SELECT 1 FROM campaigns WHERE id=$1 FOR UPDATE", cid)
            await conn.execute("DELETE FROM lore WHERE campaign_id=$1 AND name=$2", cid, name)
            await conn.execute(
                "INSERT INTO lore (id, campaign_id, name, text, created_at)"
                " VALUES ($1,$2,$3,$4,$5)", lid, cid, name, text, time.time())
        return {"id": lid, "name": name, "chars": len(text)}

    async def lore_documents(self, cid):
        rows = await self._pool.fetch(
            "SELECT id, name, char_length(text) AS chars FROM lore WHERE campaign_id=$1"
            " ORDER BY name", cid)
        return [dict(r) for r in rows]

    async def lore_texts(self, cid):
        rows = await self._pool.fetch(
            "SELECT name, text FROM lore WHERE campaign_id=$1 ORDER BY name", cid)
        return [(r["name"], r["text"]) for r in rows]

    async def delete_lore(self, cid, lid):
        await self._pool.execute("DELETE FROM lore WHERE campaign_id=$1 AND id=$2", cid, lid)

    # -- moving a campaign between servers ----------------------------------- #

    async def export_campaign(self, cid):
        c = await self.get_campaign(cid)
        if not c:
            return None
        return {
            "format": "ai-dm-campaign/1",
            "campaign": {"name": c["name"], "code": c["code"], "lang": c["lang"] or "en",
                         "backend": c["backend"] or "", "history": json.loads(c["history"]),
                         "house": await self.campaign_house(cid),
                         "combat": await self.get_combat(cid),
                         "memory": await self.get_memory(cid),
                         "map": await self.get_map(cid)},
            "characters": [{k: v for k, v in ch.items() if k != "_id"}
                           for ch in await self.party(cid)],
            "events": await self.events_since(cid, 0, limit=100000),
            "media": [{k: v for k, v in m.items() if k not in ("campaign_id",)}
                      for m in await self.campaign_media(cid)],
            "lore": [{"name": n, "text": t} for n, t in await self.lore_texts(cid)],
        }

    async def import_campaign(self, blob):
        """See `store.import_campaign`, whose checks this shares. One transaction: an
        import that fails half way leaves nothing behind."""
        if not isinstance(blob, dict) or blob.get("format") != "ai-dm-campaign/1":
            raise ValueError("not an AI DM campaign export")
        meta = blob.get("campaign")
        if not isinstance(meta, dict):
            raise ValueError("that export has no campaign in it")
        history = meta.get("history", [])
        if not isinstance(history, list):
            raise ValueError("that export's history is not a list of messages")
        characters = store._records(blob, "characters")
        media_rows = store._records(blob, "media")
        events = store._records(blob, "events")
        lore_rows = store._records(blob, "lore")

        code = meta.get("code")
        if not isinstance(code, str) or not code or await self.campaign_by_code(code):
            code = None
        for _ in range(50):
            try:
                return await self._import(meta, history, characters, media_rows, events,
                                          lore_rows, code or _code())
            except asyncpg.UniqueViolationError:
                code = None                 # somebody took that code meanwhile
        raise RuntimeError("could not allocate a free campaign code")

    async def _import(self, meta, history, characters, media_rows, events, lore_rows, code):
        cid, now = store._uid(), time.time()
        name = meta.get("name", "Imported campaign")
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO campaigns (id, code, name, lang, backend, house, combat, memory,"
                " map, history, created_at, updated_at)"
                " VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$11)",
                cid, code, _t(store._text(name, "Imported campaign")),
                store._text(meta.get("lang"), "en"), store._text(meta.get("backend")),
                json.dumps(rules.clean_house(meta.get("house"))),
                _t(_dumps(meta["combat"]))
                if isinstance(meta.get("combat"), dict) and meta["combat"].get("order") else "",
                _t(store._imported_memory(meta.get("memory"), history)),
                _t(store._imported_map(meta.get("map"))),
                _t(_dumps(history)), now)

            # media first: characters reference their portrait by id
            id_map = {}
            for m in media_rows:
                new_id = store._uid()
                id_map[m.get("id")] = new_id
                await conn.execute(
                    "INSERT INTO media (id, campaign_id, file, kind, mime, bytes, width,"
                    " height, caption, source, owner, created_at)"
                    " VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,NULL,$11)",
                    new_id, cid, _t(store._text(m.get("file"))),
                    _t(store._text(m.get("kind"), "handout")),
                    _t(store._text(m.get("mime"), "image/png")), store._int(m.get("bytes")),
                    store._int(m.get("width")), store._int(m.get("height")),
                    _t(store._text(m.get("caption"))),
                    _t(store._text(m.get("source"), "upload")), now)

            for ch in characters:
                ch = dict(ch)                     # never mutate the caller's blob
                token = ch.pop("_token", None)
                portrait = id_map.get(ch.pop("portrait", "") or "", "")
                notes = ch.pop("notes", "")
                chid = await self.add_character(cid, ch, token, notes, conn=conn)
                if portrait:
                    await conn.execute("UPDATE characters SET portrait=$2 WHERE id=$1",
                                       chid, portrait)

            rows = []
            for ev in events:
                payload = {k: v for k, v in ev.items() if k not in ("seq", "kind")}
                if payload.get("media") in id_map:      # image events point at a media row
                    payload["media"] = id_map[payload["media"]]
                rows.append((cid, store._text(ev.get("kind"), "narration"),
                             _t(_dumps(payload)), now))
            if rows:
                await conn.executemany(
                    "INSERT INTO events (campaign_id, kind, payload, created_at)"
                    " VALUES ($1,$2,$3,$4)", rows)

            for doc in lore_rows:
                name_, text = doc.get("name"), doc.get("text")
                if isinstance(name_, str) and isinstance(text, str) and name_ and text:
                    await conn.execute(
                        "INSERT INTO lore (id, campaign_id, name, text, created_at)"
                        " VALUES ($1,$2,$3,$4,$5)", store._uid(), cid, _t(name_), _t(text), now)
        return {"id": cid, "code": code, "name": store._text(name, "Imported campaign")}
