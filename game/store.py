"""SQLite persistence for campaigns, characters, and the event log.

The event log is what makes the game survive a phone falling asleep: every client
tracks the last sequence number it saw and asks for everything after it on reconnect.
"""

import json
import os
import random
import sqlite3
import string
import time

DB_PATH = os.environ.get("DND_DB", os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "campaign.db"))

# unambiguous alphabet - no O/0, no I/1/L
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
    id          TEXT PRIMARY KEY,
    code        TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    lang        TEXT NOT NULL DEFAULT 'en',
    backend     TEXT NOT NULL DEFAULT '',
    history     TEXT NOT NULL DEFAULT '[]',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS characters (
    id           TEXT PRIMARY KEY,
    campaign_id  TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    player_token TEXT,
    name         TEXT NOT NULL,
    data         TEXT NOT NULL,
    created_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    payload     TEXT NOT NULL,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS media (
    id           TEXT PRIMARY KEY,
    campaign_id  TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    file         TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'handout',
    mime         TEXT NOT NULL,
    bytes        INTEGER NOT NULL,
    width        INTEGER NOT NULL,
    height       INTEGER NOT NULL,
    caption      TEXT NOT NULL DEFAULT '',
    source       TEXT NOT NULL DEFAULT 'upload',
    owner        TEXT,
    created_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS lore (
    id           TEXT PRIMARY KEY,
    campaign_id  TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    text         TEXT NOT NULL,
    created_at   REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_lore_campaign ON lore(campaign_id);
CREATE INDEX IF NOT EXISTS idx_media_campaign ON media(campaign_id);
CREATE INDEX IF NOT EXISTS idx_events_campaign ON events(campaign_id, seq);
CREATE INDEX IF NOT EXISTS idx_characters_campaign ON characters(campaign_id);
"""


def connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


_conn = None


def db():
    global _conn
    if _conn is None:
        _conn = connect()
        _conn.executescript(SCHEMA)
        _migrate(_conn)
        _conn.commit()
    return _conn


def _migrate(conn):
    """Bring a database created by an older version up to date."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(campaigns)")}
    if "lang" not in cols:
        conn.execute("ALTER TABLE campaigns ADD COLUMN lang TEXT NOT NULL DEFAULT 'en'")
    if "backend" not in cols:
        conn.execute("ALTER TABLE campaigns ADD COLUMN backend TEXT NOT NULL DEFAULT ''")
    chcols = {r["name"] for r in conn.execute("PRAGMA table_info(characters)")}
    if "portrait" not in chcols:
        conn.execute("ALTER TABLE characters ADD COLUMN portrait TEXT NOT NULL DEFAULT ''")


def _uid():
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=16))


def new_code():
    conn = db()
    for _ in range(50):
        code = "".join(random.choices(CODE_ALPHABET, k=6))
        if not conn.execute("SELECT 1 FROM campaigns WHERE code=?", (code,)).fetchone():
            return code
    raise RuntimeError("could not allocate a free campaign code")


# --------------------------------------------------------------------------- #
# campaigns
# --------------------------------------------------------------------------- #

def create_campaign(name, lang="en", backend=""):
    conn = db()
    cid, code, now = _uid(), new_code(), time.time()
    conn.execute(
        "INSERT INTO campaigns (id, code, name, lang, backend, history, created_at, updated_at)"
        " VALUES (?,?,?,?,?,'[]',?,?)", (cid, code, name, lang, backend, now, now))
    conn.commit()
    return {"id": cid, "code": code, "name": name, "lang": lang, "backend": backend}


def campaign_lang(cid):
    row = db().execute("SELECT lang FROM campaigns WHERE id=?", (cid,)).fetchone()
    return (row["lang"] if row else None) or "en"


def campaign_backend(cid):
    row = db().execute("SELECT backend FROM campaigns WHERE id=?", (cid,)).fetchone()
    return (row["backend"] if row else "") or ""


def set_campaign_backend(cid, backend):
    conn = db()
    conn.execute("UPDATE campaigns SET backend=?, updated_at=? WHERE id=?",
                 (backend, time.time(), cid))
    conn.commit()


def get_campaign(cid):
    row = db().execute("SELECT * FROM campaigns WHERE id=?", (cid,)).fetchone()
    return dict(row) if row else None


def campaign_by_code(code):
    row = db().execute("SELECT * FROM campaigns WHERE code=?",
                       ((code or "").strip().upper(),)).fetchone()
    return dict(row) if row else None


def get_history(cid):
    row = db().execute("SELECT history FROM campaigns WHERE id=?", (cid,)).fetchone()
    return json.loads(row["history"]) if row else []


def save_history(cid, history):
    conn = db()
    conn.execute("UPDATE campaigns SET history=?, updated_at=? WHERE id=?",
                 (json.dumps(history, ensure_ascii=False), time.time(), cid))
    conn.commit()


def campaigns_for_token(token):
    """Every campaign this browser has a character in, most recent first."""
    rows = db().execute(
        "SELECT c.id, c.code, c.name, c.lang, c.updated_at, ch.name AS character_name"
        " FROM campaigns c JOIN characters ch ON ch.campaign_id = c.id"
        " WHERE ch.player_token=? ORDER BY c.updated_at DESC", (token,)).fetchall()
    return [dict(r) for r in rows]


def delete_campaign(cid):
    conn = db()
    conn.execute("DELETE FROM media WHERE campaign_id=?", (cid,))
    conn.execute("DELETE FROM events WHERE campaign_id=?", (cid,))
    conn.execute("DELETE FROM characters WHERE campaign_id=?", (cid,))
    conn.execute("DELETE FROM campaigns WHERE id=?", (cid,))
    conn.commit()


# --------------------------------------------------------------------------- #
# characters
# --------------------------------------------------------------------------- #

def add_character(cid, char, token):
    conn = db()
    chid, now = _uid(), time.time()
    conn.execute(
        "INSERT INTO characters (id, campaign_id, player_token, name, data, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (chid, cid, token, char["name"], json.dumps(char, ensure_ascii=False), now))
    conn.commit()
    return chid


# --------------------------------------------------------------------------- #
# lore - the player's own campaign documents, which the DM can look things up in
# --------------------------------------------------------------------------- #

def add_lore(cid, name, text):
    """Store one document. Re-importing the same name replaces it rather than duplicating."""
    conn = db()
    conn.execute("DELETE FROM lore WHERE campaign_id=? AND name=?", (cid, name))
    lid = _uid()
    conn.execute("INSERT INTO lore (id, campaign_id, name, text, created_at)"
                 " VALUES (?,?,?,?,?)", (lid, cid, name, text, time.time()))
    conn.commit()
    return {"id": lid, "name": name, "chars": len(text)}


def lore_documents(cid):
    """Names and sizes only - the text itself is far too big to hand around casually."""
    return [{"id": r["id"], "name": r["name"], "chars": len(r["text"])}
            for r in db().execute(
                "SELECT id, name, text FROM lore WHERE campaign_id=? ORDER BY name", (cid,))]


def lore_texts(cid):
    return [(r["name"], r["text"]) for r in db().execute(
        "SELECT name, text FROM lore WHERE campaign_id=? ORDER BY name", (cid,))]


def delete_lore(cid, lid):
    conn = db()
    conn.execute("DELETE FROM lore WHERE campaign_id=? AND id=?", (cid, lid))
    conn.commit()


def party(cid):
    rows = db().execute(
        "SELECT id, player_token, data, portrait FROM characters WHERE campaign_id=?"
        " ORDER BY created_at", (cid,)).fetchall()
    out = []
    for r in rows:
        ch = json.loads(r["data"])
        ch["_id"] = r["id"]
        ch["_token"] = r["player_token"]
        ch["portrait"] = r["portrait"] or ""
        out.append(ch)
    return out


def save_party(characters):
    """Persist every character in the list (they carry their own _id)."""
    conn = db()
    for ch in characters:
        data = {k: v for k, v in ch.items()
                if not k.startswith("_") and k != "portrait"}
        conn.execute("UPDATE characters SET data=?, name=? WHERE id=?",
                     (json.dumps(data, ensure_ascii=False), data["name"], ch["_id"]))
    conn.commit()


def character_for_token(cid, token):
    row = db().execute(
        "SELECT id, data FROM characters WHERE campaign_id=? AND player_token=?",
        (cid, token)).fetchone()
    if not row:
        return None
    ch = json.loads(row["data"])
    ch["_id"] = row["id"]
    return ch


def claim_character(char_id, token):
    """Bind an existing character to this browser - how a character follows you
    from laptop to phone."""
    conn = db()
    conn.execute("UPDATE characters SET player_token=? WHERE id=?", (token, char_id))
    conn.commit()


# --------------------------------------------------------------------------- #
# media
# --------------------------------------------------------------------------- #

def add_media(cid, file, kind, mime, size, width, height, caption="", source="upload",
              owner=None):
    conn = db()
    mid, now = _uid(), time.time()
    conn.execute(
        "INSERT INTO media (id, campaign_id, file, kind, mime, bytes, width, height,"
        " caption, source, owner, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (mid, cid, file, kind, mime, size, width, height, caption, source, owner, now))
    conn.commit()
    return mid


def get_media(cid, mid):
    row = db().execute("SELECT * FROM media WHERE id=? AND campaign_id=?",
                       (mid, cid)).fetchone()
    return dict(row) if row else None


def campaign_media(cid):
    rows = db().execute(
        "SELECT * FROM media WHERE campaign_id=? ORDER BY created_at", (cid,)).fetchall()
    return [dict(r) for r in rows]


def media_count(cid):
    return db().execute("SELECT COUNT(*) AS n FROM media WHERE campaign_id=?",
                        (cid,)).fetchone()["n"]


def file_still_used(cid, file, except_id=None):
    """Files are content-addressed, so two rows can share one file."""
    q = "SELECT COUNT(*) AS n FROM media WHERE campaign_id=? AND file=?"
    args = [cid, file]
    if except_id:
        q += " AND id<>?"
        args.append(except_id)
    return db().execute(q, args).fetchone()["n"] > 0


def delete_media(cid, mid):
    conn = db()
    conn.execute("DELETE FROM media WHERE id=? AND campaign_id=?", (mid, cid))
    conn.execute("UPDATE characters SET portrait='' WHERE campaign_id=? AND portrait=?",
                 (cid, mid))
    conn.commit()


def set_portrait(char_id, mid):
    conn = db()
    conn.execute("UPDATE characters SET portrait=? WHERE id=?", (mid, char_id))
    conn.commit()


# --------------------------------------------------------------------------- #
# events
# --------------------------------------------------------------------------- #

def append_event(cid, kind, payload):
    conn = db()
    cur = conn.execute(
        "INSERT INTO events (campaign_id, kind, payload, created_at) VALUES (?,?,?,?)",
        (cid, kind, json.dumps(payload, ensure_ascii=False), time.time()))
    conn.commit()
    return {**payload, "seq": cur.lastrowid, "kind": kind}


def events_since(cid, since=0, limit=500):
    """Events after `since`, oldest first, capped at `limit`.

    When more than `limit` are waiting - opening a campaign that has run for several
    sessions, or a phone that was asleep a long time - the *newest* window is returned
    rather than the oldest. Taking the oldest looked tidier but was a bug you could
    play into: the client sets `lastSeq` from whatever it received, so a browser
    entering a 1,200-event campaign would replay the first 500 events, land on seq 500,
    and then never ask for 501-1200 again. It sat looking at session one's opening
    scene while the table talked past it, and no reconnect would ever heal it.
    """
    total = db().execute(
        "SELECT COUNT(*) AS n FROM events WHERE campaign_id=? AND seq>?",
        (cid, since)).fetchone()["n"]
    offset = max(0, total - limit)
    rows = db().execute(
        "SELECT seq, kind, payload FROM events WHERE campaign_id=? AND seq>? "
        "ORDER BY seq LIMIT ? OFFSET ?", (cid, since, limit, offset)).fetchall()
    return [{**json.loads(r["payload"]), "seq": r["seq"], "kind": r["kind"]}
            for r in rows]


def last_seq(cid):
    row = db().execute("SELECT MAX(seq) AS s FROM events WHERE campaign_id=?", (cid,)).fetchone()
    return row["s"] or 0


def turns_in_last_minute(cid):
    cutoff = time.time() - 60
    row = db().execute(
        "SELECT COUNT(*) AS n FROM events WHERE campaign_id=? AND kind='player' AND created_at>?",
        (cid, cutoff)).fetchone()
    return row["n"]


# --------------------------------------------------------------------------- #
# export / import
# --------------------------------------------------------------------------- #

def export_campaign(cid):
    c = get_campaign(cid)
    if not c:
        return None
    return {
        "format": "ai-dm-campaign/1",
        "campaign": {"name": c["name"], "code": c["code"], "lang": c["lang"] or "en",
                     "backend": c["backend"] or "", "history": json.loads(c["history"])},
        "characters": [{k: v for k, v in ch.items() if k != "_id"} for ch in party(cid)],
        "events": events_since(cid, 0, limit=100000),
        "media": [{k: v for k, v in m.items() if k not in ("campaign_id",)}
                  for m in campaign_media(cid)],
        # the players' own documents travel with the campaign; they are the one part of
        # it the app can't reconstruct from anything else
        "lore": [{"name": n, "text": t} for n, t in lore_texts(cid)],
    }


def _records(blob, key):
    """One list of dicts out of an export, or a ValueError naming what was wrong.

    An export is a file off someone's disk, so every shape here is attacker-shaped:
    the wrong type in any of these slots used to raise AttributeError deep inside the
    loop, which is not in the caller's `except` list and became an HTTP 500 with a
    stack trace instead of "that file isn't a campaign export"."""
    value = blob.get(key) or []
    if not isinstance(value, list):
        raise ValueError(f"{key!r} should be a list, not {type(value).__name__}")
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(f"{key!r} contains a {type(item).__name__}, "
                             f"not a record")
    return value


def _text(value, default=""):
    """A string for a TEXT column. Anything else in an export is not worth an
    InterfaceError from sqlite three frames down."""
    return value if isinstance(value, str) else default


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def import_campaign(blob):
    if not isinstance(blob, dict):
        raise ValueError("not an AI DM campaign export")
    if blob.get("format") != "ai-dm-campaign/1":
        raise ValueError("not an AI DM campaign export")

    conn = db()
    meta = blob.get("campaign")
    if not isinstance(meta, dict):
        raise ValueError("that export has no campaign in it")
    history = meta.get("history", [])
    if not isinstance(history, list):
        raise ValueError("that export's history is not a list of messages")
    characters = _records(blob, "characters")
    media_rows = _records(blob, "media")
    events = _records(blob, "events")
    lore_rows = _records(blob, "lore")
    cid, now = _uid(), time.time()
    code = meta.get("code")
    if not code or campaign_by_code(code):
        code = new_code()

    conn.execute(
        "INSERT INTO campaigns (id, code, name, lang, backend, history, created_at,"
        " updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (cid, code, meta.get("name", "Imported campaign"), meta.get("lang", "en"),
         meta.get("backend", ""),
         json.dumps(history, ensure_ascii=False), now, now))

    # media first: characters reference their portrait by id
    id_map = {}
    for m in media_rows:
        new_id = _uid()
        id_map[m.get("id")] = new_id
        conn.execute(
            "INSERT INTO media (id, campaign_id, file, kind, mime, bytes, width, height,"
            " caption, source, owner, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (new_id, cid, _text(m.get("file")), _text(m.get("kind"), "handout"),
             _text(m.get("mime"), "image/png"), _int(m.get("bytes")), _int(m.get("width")),
             _int(m.get("height")), _text(m.get("caption")),
             _text(m.get("source"), "upload"), None, now))

    for ch in characters:
        ch = dict(ch)                     # never mutate the caller's blob
        token = ch.pop("_token", None)
        portrait = id_map.get(ch.pop("portrait", "") or "", "")
        chid = add_character(cid, ch, token)
        if portrait:
            conn.execute("UPDATE characters SET portrait=? WHERE id=?", (portrait, chid))

    for ev in events:
        payload = {k: v for k, v in ev.items() if k not in ("seq", "kind")}
        if payload.get("media") in id_map:          # image events point at a media row
            payload["media"] = id_map[payload["media"]]
        conn.execute(
            "INSERT INTO events (campaign_id, kind, payload, created_at) VALUES (?,?,?,?)",
            (cid, ev.get("kind", "narration"), json.dumps(payload, ensure_ascii=False), now))

    for doc in lore_rows:
        name, text = doc.get("name"), doc.get("text")
        if isinstance(name, str) and isinstance(text, str) and name and text:
            conn.execute("INSERT INTO lore (id, campaign_id, name, text, created_at)"
                         " VALUES (?,?,?,?,?)", (_uid(), cid, name, text, now))

    conn.commit()
    return {"id": cid, "code": code, "name": meta.get("name")}
