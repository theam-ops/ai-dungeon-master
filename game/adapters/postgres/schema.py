"""The Postgres schema: the SQLite one in `game/store.py`, plus what several processes
need to share - leases, queued jobs, and oversized live events.

Kept deliberately close to SQLite's. JSON stays in TEXT rather than JSONB: the game reads
these columns whole and never queries inside them, several hold '' to mean "none", which
JSONB cannot, and an export must come back byte-for-byte what went in. Times stay epoch
seconds, as DOUBLE PRECISION - Postgres' REAL is four bytes, and `claim_art_slot`
compares timestamps that differ by less than a float32 can tell apart.

One addition: `pos` on characters and media. Both are listed by `created_at`, and an
import writes a whole party in the same millisecond. SQLite breaks those ties by rowid -
insertion order - without being asked; Postgres has no rowid, and would break them by
the random id, shuffling an imported party.
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
    id          TEXT PRIMARY KEY,
    code        TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    lang        TEXT NOT NULL DEFAULT 'en',
    backend     TEXT NOT NULL DEFAULT '',
    house       TEXT NOT NULL DEFAULT '{}',
    combat      TEXT NOT NULL DEFAULT '',
    memory      TEXT NOT NULL DEFAULT '',
    map         TEXT NOT NULL DEFAULT '',
    history     TEXT NOT NULL DEFAULT '[]',
    last_art    DOUBLE PRECISION NOT NULL DEFAULT 0,
    created_at  DOUBLE PRECISION NOT NULL,
    updated_at  DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS characters (
    id           TEXT PRIMARY KEY,
    campaign_id  TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    player_token TEXT,
    name         TEXT NOT NULL,
    data         TEXT NOT NULL,
    notes        TEXT NOT NULL DEFAULT '',
    portrait     TEXT NOT NULL DEFAULT '',
    created_at   DOUBLE PRECISION NOT NULL,
    pos          BIGSERIAL       -- insertion order; see below
);

CREATE TABLE IF NOT EXISTS events (
    seq         BIGSERIAL PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    payload     TEXT NOT NULL,
    created_at  DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS media (
    id           TEXT PRIMARY KEY,
    campaign_id  TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    file         TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'handout',
    mime         TEXT NOT NULL,
    bytes        BIGINT NOT NULL,
    width        INTEGER NOT NULL,
    height       INTEGER NOT NULL,
    caption      TEXT NOT NULL DEFAULT '',
    source       TEXT NOT NULL DEFAULT 'upload',
    owner        TEXT,
    created_at   DOUBLE PRECISION NOT NULL,
    pos          BIGSERIAL
);

CREATE TABLE IF NOT EXISTS lore (
    id           TEXT PRIMARY KEY,
    campaign_id  TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    text         TEXT NOT NULL,
    created_at   DOUBLE PRECISION NOT NULL
);

-- `LockManager.claim`: a lease that outlives the request taking it
CREATE TABLE IF NOT EXISTS leases (
    key      TEXT PRIMARY KEY,
    expires  DOUBLE PRECISION NOT NULL
);

-- `TaskQueue`: work waiting for any process to pick it up
CREATE TABLE IF NOT EXISTS jobs (
    id       BIGSERIAL PRIMARY KEY,
    name     TEXT NOT NULL,
    kwargs   TEXT NOT NULL,
    run_at   DOUBLE PRECISION NOT NULL
);

-- `EventBus`: a live event too big for NOTIFY's 8000-byte payload waits here briefly
CREATE TABLE IF NOT EXISTS bus_spill (
    id          BIGSERIAL PRIMARY KEY,
    payload     TEXT NOT NULL,
    created_at  DOUBLE PRECISION NOT NULL
);

-- columns added after a database may already exist
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS map TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_lore_campaign ON lore(campaign_id);
CREATE INDEX IF NOT EXISTS idx_media_campaign ON media(campaign_id);
CREATE INDEX IF NOT EXISTS idx_events_campaign ON events(campaign_id, seq);
CREATE INDEX IF NOT EXISTS idx_characters_campaign ON characters(campaign_id);
CREATE INDEX IF NOT EXISTS idx_jobs_run_at ON jobs(run_at, id);
"""

# the database's own clock, as epoch seconds - for leases and jobs, which several
# machines compare, and whose clocks need not agree with each other
NOW = "extract(epoch from clock_timestamp())::double precision"
