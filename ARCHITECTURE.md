# Architecture

How AI Dungeon Master is built, why it is built that way, and where the seams are.

Written for two audiences: a contributor opening the repo for the first time, and an AI
agent asked to change something in it. Both need the same thing — the invariants, and
which lines of code they live on.

- [The one invariant](#the-one-invariant)
- [Current architecture](#current-architecture)
- [The turn loop](#the-turn-loop)
- [Data model](#data-model)
- [Module map](#module-map)
- [Target architecture](#target-architecture)
- [The four ports](#the-four-ports)
- [Known defects](#known-defects)
- [What this project deliberately does not do](#what-this-project-deliberately-does-not-do)
- [Extension points](#extension-points)

---

## The one invariant

**The language model narrates. Python owns every number.**

The model may never state a die result, an HP total, an AC, or a skill bonus that it did
not receive back from a tool call. Everything else in this document is downstream of that
sentence.

Three consequences that constrain every design decision:

1. **Mechanics live in `game/rules.py`, which does no I/O.** It is pure functions over
   plain dicts. It imports no database, no network, no framework. If a rule cannot be
   expressed as a pure function of character state, it does not belong there.
2. **A tool call is the only way state changes.** `roll_dice` and `update_character` are
   not conveniences for the model — they are the enforcement boundary. Adding a mechanic
   means adding a tool, not adding a sentence to the prompt.
3. **A weaker model degrades into prose, not into lies.** If a small local model forgets
   to call the dice tool, no dice chip appears and no sheet changes. The failure is
   visible. This is the property that makes a 3B model acceptable and it must not be
   traded away for convenience.

> **For AI agents working in this repo:** if a change would let the model decide a
> number, the change is wrong, however much simpler it looks. Prefer adding a tool.

---

## Current architecture

```mermaid
graph TB
    subgraph Browser["Browser (no build step)"]
        UI["static/app.js"]
        SSE["EventSource<br/>tracks last seq"]
    end

    subgraph Process["One uvicorn process (DND_MODE=lite)"]
        API["server.py<br/>HTTP: auth, endpoints"]
        SVC["game/services/<br/>the turn, events"]
        DM["game/dm.py<br/>prompt + tools"]
        RULES["game/rules.py<br/>pure, no I/O"]
        PROV["game/providers.py<br/>6 backends + failover"]
        PORTS{{"game/ports.py<br/>EventBus · LockManager<br/>TaskQueue · Repository"}}
        LITE["game/adapters/lite.py<br/>asyncio queues, locks, tasks<br/>SQLite via store.py"]
    end

    subgraph Disk
        DB[("campaign.db<br/>SQLite WAL")]
        MEDIA[("media/&lt;cid&gt;/&lt;sha256&gt;.ext")]
    end

    subgraph External["AI providers"]
        CA["Anthropic API"]
        CC["Claude Code<br/>local subscription"]
        OTHER["Gemini · Groq<br/>OpenRouter · Ollama"]
    end

    UI --> API
    SSE -.->|"GET /stream"| API
    API --> SVC
    API --> PORTS
    SVC --> PORTS
    SVC --> DM --> RULES
    DM --> PROV --> CA & CC & OTHER
    DM -->|"repo, lent by the turn"| PORTS
    PORTS --> LITE --> DB
    API --> MEDIA

    style PORTS fill:#2d3d5a,color:#fff
    style LITE fill:#5a2d2d,color:#fff
```

Everything that used to pin the server to a single process - who is watching a campaign,
whose turn it is, the guard on the opening scene, background tasks - now lives behind the
ports in `game/ports.py`. In `lite` mode the adapters (red) are still in-process, which is
what `lite` means: one machine, nothing to install. The difference is that it is now one
file's worth of decision rather than a property of the whole server.

Run two `lite` instances behind a load balancer and the old problems are all still there:

- a narration streamed by the instance handling the turn never reaches players whose SSE
  connection landed on the other instance;
- two players acting at once on different instances take the same turn twice, because
  neither instance's lock knows about the other.

`DND_MODE=prod` - Postgres and Redis adapters, Phase 8 of the plan - is what removes them.
It does not exist yet, and `build_adapters("prod")` says so rather than half-working. This
is also why serverless hosting (Vercel, Netlify, Lambda) still cannot run this application.

Slow image work — fetching a pasted link, decoding an upload — runs in worker threads via
`asyncio.to_thread`, never on the loop. The store gives each thread its own connection.

---

## The turn loop

The heart of the system. `POST /api/campaigns/{cid}/act`.

```mermaid
sequenceDiagram
    actor P as Player
    participant API as server.py
    participant L as adapters.locks
    participant DM as dm.py
    participant R as rules.py
    participant AI as provider
    participant DB as adapters.repo
    participant S as adapters.bus

    P->>API: POST /act {action, media_ids}
    API->>DB: turns_in_last_minute(cid)
    Note over API: 429 if over MAX_TURNS_PER_MIN
    API->>L: acquire
    Note over L: serialises turns per campaign

    API->>S: {kind:"player"}
    API->>S: {kind:"thinking"}

    API->>DM: build_prompt(chars, actor, action, lang)
    Note over DM: system blocks<br/>+ <party_state> JSON<br/>+ actor's own notes<br/>+ history

    loop until no tool calls (max MAX_TOOL_ROUNDS)
        DM->>AI: stream(messages, tools_for(cid))
        AI-->>DM: text delta
        DM->>S: {kind:"delta"}
        AI-->>DM: tool_use
        DM->>R: roll_notation / update / skill_modifier
        R-->>DM: result
        DM->>S: {kind:"dice"} / {kind:"sheet"} / {kind:"lore"}
        DM->>AI: tool_result
    end

    API->>DB: save_history · save_party
    API->>S: {kind:"narration"}
    API->>L: release
    API-->>P: 200
```

**Failover.** A provider error matching "out of credit / rate limited / bad key" discards
the partial text, walks `providers.failover_order()`, and retries on the next available
backend, emitting `{kind:"switch"}`. Partial text is discarded deliberately: half a
paragraph in one voice and half in another is worse than a one-second pause.

**`draw_scene` never joins this loop.** Image generation takes most of a minute, and this
loop is what streams narration to every browser at the table. Waiting here would freeze
the scene mid-sentence for everyone. It is dispatched independently and lands in the feed
when ready. Every failure path leaves the turn untouched.

**The replay contract.** Every state change is appended to `events` with a monotonic
`seq`. The browser remembers the last `seq` it saw; `GET /stream?since=N` replays the gap
before going live. This is what makes "join a four-hour-old campaign" and "my phone went
to sleep" the same code path, and it is the single most important thing to preserve when
touching the event system.

---

## Data model

Five tables. SQLite, WAL mode — note that **recent writes live in `campaign.db-wal`**, so
copying `campaign.db` alone loses data.

```mermaid
erDiagram
    campaigns ||--o{ characters : has
    campaigns ||--o{ events : logs
    campaigns ||--o{ media : holds
    campaigns ||--o{ lore : holds
    characters |o--o| media : portrait

    campaigns {
        TEXT id PK
        TEXT code UK "6 chars, the join code"
        TEXT lang "DM narration language"
        TEXT backend "which AI, per campaign"
        TEXT history "ENTIRE transcript as JSON"
        REAL last_art "art slot refill clock"
    }
    characters {
        TEXT id PK
        TEXT player_token "FK to the signed cookie"
        TEXT data "the whole sheet as JSON"
        TEXT notes "player's standing DM notes"
        TEXT portrait
    }
    events {
        INTEGER seq PK "AUTOINCREMENT, the replay cursor"
        TEXT kind
        TEXT payload
    }
    media {
        TEXT id PK
        TEXT file "sha256 content address"
        TEXT kind "portrait|scene|map|handout"
        TEXT owner
    }
    lore {
        TEXT id PK
        TEXT name
        TEXT text "plain text, searched by substring"
    }
```

### Two blob columns carry most of the system

**`characters.data`** is the whole sheet as JSON: abilities, hp, ac, inventory,
conditions, skills. This is a feature, not laziness — it means a new mechanic needs no
schema migration, and it travels through export/import for free. The established pattern
for adding a field is a **lazy default on the read path**: see `rules.ensure_skills()`,
called from `store.party()`, which gives characters created before skills existed their
class defaults on first load.

**`campaigns.history`** is the entire transcript as one JSON TEXT column. Every turn reads
it whole and writes it back whole. This is the main scaling liability in the data model and
is addressed in the [refactoring plan](docs/REFACTORING_PLAN.md).

### Identity

There are no user accounts. A signed cookie (`itsdangerous`, keyed by `SESSION_SECRET`)
carries an opaque `player_token`. That token is what owns a character row. Consequences:

- Clearing cookies orphans your character — which is why "pick an existing character"
  exists on the join screen (`store.claim_character`).
- Anyone with the campaign code can join and see all its media. This is stated in the
  player reference (docs/reference.md) because it matters before you upload a photo
  of a real person.
- `APP_PASSWORD` is a single shared door for the whole instance, not per-user auth.

### Language

Mechanical values are stored in **English** and translated at display: races, classes,
ability names, skill names. Prose written by people — character names, items the DM
invents, the story — is stored as typed. `game/i18n.py` holds the rule; `static/i18n.js`
holds the browser half.

> One consequence bites anyone touching inventory: starting gear is **localised at
> creation** (`rules.new_character` calls `i18n.gear(item, lang)`), so a Thai campaign's
> `inventory` list contains Thai display strings, not keys. See
> [Known defects](#known-defects).

---

## Module map

| File | Lines | Owns | Reaches storage through |
|---|---|---|---|
| `server.py` | 1154 | HTTP: auth, endpoints, media, import/export | `A`, the server's adapters |
| `game/services/turn.py` | 129 | the DM turn as a queued job; effect expiry | the adapters it is handed |
| `game/services/events.py` | 34 | publish (logged) vs broadcast (transient) | the adapters it is handed |
| `game/ports.py` | 223 | the four interfaces — nothing else | — |
| `game/adapters/lite.py` | 174 | in-process bus, locks, queue; SQLite repository | `store.py` |
| `game/providers.py` | 844 | 6 backends, format translation, failover, key storage | — |
| `game/store.py` | 651 | every SQLite statement; one connection per thread | SQLite |
| `game/dm.py` | 629 | system prompt, tool schemas, tool execution, prompt assembly | the `repo` the turn lends it |
| `game/claude_code.py` | 420 | Claude Pro/Max backend + its sign-in flow | the `call_tool` the DM lends it |
| `game/rules.py` | 526 | **dice, abilities, skills, AC, character gen — no I/O** | — |
| `game/media.py` | 286 | image validation, EXIF stripping, SSRF guards, file store | — |
| `game/lore.py` | 198 | encoding detection, HTML→text, substring search | — |
| `game/i18n.py` | 179 | server strings: gear, narration instruction, CLI | — |
| `static/app.js` | 1975 | the entire UI | — |
| `static/i18n.js` | 511 | browser strings, en + th | — |
| `dnd.py` / `play.py` | 322 / 272 | terminal client / tool CLI | none — no campaign |

**Dependency direction is strictly inward.** `rules.py` depends on nothing but `i18n`.
Nothing in `game/` imports `server.py`, and nothing above the ports imports what is below
them — `tests/test_boundaries.py` checks both. Keep it that way: it is what lets the test suite
drive the rules without a web server and the CLI share the same DM.

### The DM's tools

| Tool | Always on? | Does |
|---|---|---|
| `roll_dice` | yes | announces DC, then Python's RNG produces the number |
| `update_character` | yes | all hp/xp/gold/items/conditions, named to one character |
| `equip_armor` | yes | carried armour or a shield on or off; AC recomputed in Python |
| `set_effect` | yes | a named AC effect — bonus, unarmoured base, or floor — with a duration |
| `use_spell_slot` | yes | spends a slot from the SRD table; *refuses* when none is left |
| `long_rest` | yes | HP and slots restored, timed effects ended — one character or the party |
| `search_lore` | only with documents | substring search over the campaign library |
| `draw_scene` | only with an image provider | one slot per `DM_ART_EVERY_TURNS` turns |

`dm.tools_for(cid)` assembles the list per campaign. **A tool with nothing behind it is
worse than no tool** — the model reaches for it anyway and gets an error, which spends a
round and confuses the narration. Conditional registration is deliberate.

---

## Target architecture

> **Status:** the ports and the `lite` adapters are built (Phase 2). The `prod` adapters
> are Phase 8, and should wait until a measurement says one process is not enough.

The goal is to make horizontal scaling *possible* without making the single-host install
*worse*. Those two requirements are in tension, and the resolution is a port/adapter
boundary with two implementations of each port.

```mermaid
graph TB
    subgraph Core["Core — knows nothing about infrastructure"]
        RULES["rules.py<br/>pure mechanics"]
        DMC["dm.py<br/>prompt + tools"]
        SVC["services/<br/>turn orchestration"]
    end

    subgraph Ports["Ports (ABCs)"]
        P1["EventBus"]
        P2["LockManager"]
        P3["Repository"]
        P4["TaskQueue"]
    end

    subgraph Lite["Mode: lite — zero dependencies"]
        L1["asyncio.Queue fan-out"]
        L2["asyncio.Lock"]
        L3["SQLite + WAL"]
        L4["asyncio.create_task<br/>+ tasks table"]
    end

    subgraph Prod["Mode: prod — multi-process"]
        R1["Redis Pub/Sub"]
        R2["Postgres advisory lock"]
        R3["Postgres / LibSQL"]
        R4["ARQ on Redis Streams"]
    end

    SVC --> P1 & P2 & P3 & P4
    SVC --> DMC --> RULES
    P1 --> L1 & R1
    P2 --> L2 & R2
    P3 --> L3 & R3
    P4 --> L4 & R4

    style Lite fill:#2d4a2d,color:#fff
    style Prod fill:#2d3d5a,color:#fff
```

**Mode is chosen once at startup** from `DND_MODE` (`lite` | `prod`), defaulting to
`lite`. There is no per-call branching — `services/` receives injected adapters and never
asks which mode it is in. Any `if REDIS_URL:` inside business logic is a bug.

```mermaid
graph LR
    subgraph prod["prod mode, 3 instances"]
        I1["uvicorn 1"] & I2["uvicorn 2"] & I3["uvicorn 3"]
        W1["worker 1"] & W2["worker 2"]
    end
    LB["load balancer"] --> I1 & I2 & I3
    I1 & I2 & I3 <--> RD["Redis<br/>pub/sub + streams"]
    I1 & I2 & I3 --> PG[("Postgres<br/>rows + advisory locks")]
    RD --> W1 & W2
    W1 & W2 --> PG
    W1 & W2 --> S3[("object storage")]
```

Why **Postgres advisory locks rather than a Redis lock** for turn serialisation: it is one
fewer system in the critical path, and `pg_advisory_xact_lock` is released automatically
when the transaction ends — including when the worker holding it crashes. A Redis
`SETNX`+TTL lock needs a fencing token, a Lua compare-and-delete release, and a TTL tuned
longer than the slowest LLM turn, which is unbounded. Redis stays for pub/sub and the task
queue, where it is the right tool.

The real safety net is not the lock. It is the **append-only event log with a monotonic
`seq`** plus optimistic concurrency on the character row. The lock is an optimisation that
keeps two simultaneous turns from wasting tokens; correctness comes from the log.

---

## The four ports

The interfaces are in [`game/ports.py`](game/ports.py); read that file rather than a copy
of it here. What follows is what each one promises, and where the real thing differs from
the first sketch of it.

| Port | Promises | `lite` | Departed from the sketch |
|---|---|---|---|
| `EventBus` | `publish(cid, event)`; `async with subscribe(cid) as sub` registered on entry; `sub.next(timeout)` returns `None` on a quiet line | an `asyncio.Queue` per browser | Best effort, by contract: the event log is the guarantee, the bus the fast path. Dropping frames for a stalled subscriber is allowed. |
| `LockManager` | `async with hold(key)` — a critical section; `claim(key, ttl)` / `release(key)` — a lease | `asyncio.Lock`s, refcounted; a dict of expiries | **Gained `claim`.** Pressing Begin must stay claimed for as long as the opening scene takes to write, which outlives the request — a scoped lock cannot express that. And `hold` has **no timeout**: turns queue behind each other, as they always have. A timeout would have turned "wait your turn" into an error. |
| `TaskQueue` | `register(name, job)` once at start-up; `enqueue(name, **kwargs)` without waiting | `create_task`, references kept, failures logged | Jobs travel **by name** with plain-data arguments, because a coroutine cannot be sent to another process. A job receives the adapters it should use as its first argument. |
| `Repository` | every read and write of campaign state, async | delegates to `game/store.py` | 36 methods, mirroring the store. Async even though SQLite is not, so a Postgres adapter changes no caller. |

### Two things the port boundary is not

**It is not a guarantee against races by itself.** In `lite` mode a repository call never
actually suspends — `await` on a coroutine with no real I/O inside runs straight through —
so a check followed by a write is atomic by accident. A networked database yields on every
call. Read-then-write sequences therefore hold a lock: joining a table (`roster:{cid}`:
seated already? table full? name taken?) and the image cap (`media:{cid}`).
`tests/test_ports_races.py` swaps in a repository that yields before every call and checks
that two people can still not both join as "Bram" — which, without the lock, they could.

**It is not optional.** `tests/test_boundaries.py` reads the syntax tree of everything
above the ports and fails if any of it imports `game.store`, an adapter, `sqlite3` or
`redis`, if a service reaches for the server's global adapters instead of the ones it was
handed, if anything calls a repository method the port does not declare, or if a job is
enqueued under a name nobody registered.

### Who gets which adapters

The server builds one `Adapters` at import (`server.fresh_adapters`) and registers its
jobs on it; the app's lifespan calls `start` and `aclose`. Endpoints use it as `A`.
Services and jobs never touch `A` — they are handed adapters, so the same job can run on a
worker process that built its own. The DM is lent `repo` by the turn, and lends Claude
Code a `call_tool` in turn, so neither reaches back for storage.

---

## Known defects

Verified against the code, not inferred. Fixed ones stay listed, with what fixed them,
because the reasoning is what stops them coming back.

### Fixed

**1. AC was frozen at creation.** `new_character` wrote `12 + max(0, DEX mod)` and nothing
ever touched it again: armour, shields and spells did nothing, `12` had no basis in 5e,
and the clamp threw away DEX penalties. A starting Fighter in chain mail and a shield
showed 13 where the rules give 18. AC is now derived — `rules.compute_ac` from worn
armour, DEX, a shield and named effects — and `ch["ac"]` is only a cache of it. Old saves
are put in their starting armour and recomputed on load (`ensure_equipment`, the same
lazy pattern as `ensure_skills`). Covered by `tests/test_armour.py`.

**2. The SSRF guard had a DNS-rebinding window.** The hostname was resolved and checked,
then handed to `httpx`, which resolved it again. A TTL-0 DNS server could answer the
check with a public address and the fetch with `169.254.169.254`. Now `resolve_public`
resolves once, and the request is sent to that address with the real name kept in `Host`
and the TLS SNI, so certificate verification still applies (checked against a live HTTPS
host, and that a wrong name is refused). Every redirect hop is resolved and pinned the
same way. Covered by `tests/test_media_security.py`, which fails against the old code.

**3. Response bodies were buffered before their size was checked.** `len(r.content)` had
already read the whole body. Bodies now stream with a running count and stop at the cap;
a declared `Content-Length` over the cap is refused unread; a non-image `Content-Type` is
refused before Pillow sees it.

**4. Slow image work ran on the event loop.** `media.fetch` — blocking network I/O, up to
30 s a hop over four hops — and `media.process` — Pillow decoding up to 40 megapixels —
were called straight from `async` endpoints. While one player's pasted link trickled in,
narration froze for *every table on the server*. All five call sites now go through
`asyncio.to_thread`. Covered by `tests/test_event_loop.py`, which runs a heartbeat beside
the request: on the old code it managed one beat in 0.6 s.

**5. One SQLite connection was shared, with thread checks switched off.** An earlier
version of this document called that a live bug. It was not: every store call ran on the
event loop's thread, so the connection was in practice serialised. It was a latent one —
and #4's fix put threads in the process, which is exactly when it would have started to
bite. Connections are now one per thread with `check_same_thread` left on, so a
connection that wanders between threads raises instead of interleaving.
Covered by `tests/test_store_threads.py`.

**6. The schema was defined in two places.** `characters.portrait` existed only in
`_migrate`. It is in `SCHEMA` now; `_migrate` remains for databases created before it.

**7. `inventory` held localised display strings.** A Thai campaign stored `"ดาบสั้น"`,
not `"shortsword"`, and a count lived inside the text: five rations were the string
`"rations (5)"`. Items are now records — `{name, key, qty}` in `ch["items"]` — with the
name as the sheet spells it and an English rules key where the rules know the item.
`inventory` survives as a derived display list, rewritten from the records after every
change and never written to directly. Old saves are converted on load by splitting the
count off and mapping the name back through the translation table; anything unrecognised
is kept as itself. Checked against every character in the live database: nothing lost.
Covered by `tests/test_items.py`.

### Open

**Effect durations count table turns, not combat rounds.** `set_effect`'s `turns` ticks
once per player action, because there is no initiative or round structure yet. With one
player that is close to a round; with five it runs out five times faster. Phase 4 adds
rounds.

---

## What this project deliberately does not do

The most useful section for a contributor or an agent. These are decisions, not gaps —
"fixing" them is a regression unless the tradeoff is revisited on purpose.

| Not done | Why |
|---|---|
| **No build step** | `Play.cmd` must work on a machine with Python and nothing else. No npm, no bundler, no `node_modules`. This is load-bearing for how the app is distributed. |
| **No user accounts** | A signed cookie plus a 6-character code is the whole identity model. Accounts would need email, resets, and a privacy surface for a game six friends play. |
| **No saving throws, spell slots, or initiative** (yet) | Modelled in the user's own campaign notes and reachable via `search_lore`. Adding them to code is real work with no UI asking for it — until Phase 2 of the plan. |
| **No OAuth against Claude/GPT/Gemini** | There is no OAuth scope that lets a web app spend somebody else's subscription. Anything claiming otherwise impersonates a browser session, which violates provider terms. The subscription backend drives locally-installed Claude Code instead, and its sign-in endpoints are **loopback-only** — whoever completes that sign-in decides which account pays for every turn. |
| **No word-based lore search** | Thai is written without spaces between words. Anything that splits on whitespace finds nothing in a Thai document. Plain substring matching is a correctness requirement, not laziness. |
| **No images in campaign history** | An attached image is sent on the turn it appears, then dropped. Left in, it is re-sent every turn and multiplies the bill silently. |
| **No serverless hosting** | See [Current architecture](#current-architecture). Read-only filesystem, ephemeral `/tmp`, and no shared memory for SSE subscribers. |
| **No CPU-waster to defeat Oracle's idle reclaim** | `tools/oracle-setup.sh` deliberately ships without one. Upgrading to Pay As You Go is the honest fix; burning power to lie to your host is not. |

---

## Extension points

| To add… | Touch | Notes |
|---|---|---|
| A **class or race** | `rules.CLASSES` / `RACES`, `CLASS_SKILLS`, `i18n.NAMES` | hit die, primary stat, gear, 3 skills |
| An **AI provider** | subclass `providers.Backend`, add to `_build()` | needs `available()` and a `stream()` yielding text deltas then one message in Anthropic block format |
| A **DM tool** | `dm.TOOLS` (or a conditional like `LORE_TOOL`), then `dm.run_tool` | execution must be in Python; emit an event so the table sees it happen |
| A **language** | `i18n.LANGUAGES`, `NARRATION_INSTRUCTION`, `GEAR`, `NAMES`, `CLI`; a `STRINGS` block in `static/i18n.js`; a `:root[data-lang="xx"]` font block in `style.css` | nothing else knows about languages |
| A **character field** | `rules.new_character` + a lazy default on the read path | follow `ensure_skills`; no migration needed, rides in `data` |
| The **DM's personality** | `dm.SYSTEM` | this one string is the whole voice, pacing, and house rules |

### Testing

```bash
pip install -r requirements-dev.txt
python -m pytest          # 89 tests, no API key, no model call
```

Every backend in the suite is a stub: a DM that says exactly what the test scripted, an
artist that returns four pixels of PNG. Tests run against a throwaway database and media
folder and never touch `campaign.db`. The stub records what it was handed, which is how a
test can assert that a player's own notes and the right party state genuinely **arrived in
the prompt** rather than merely being saved somewhere.

When adding a mechanic, the test that matters is the one asserting the number the DM
received — not that the function returns the right value in isolation.

---

## See also

- [README.md](README.md) — how to start playing ([ไทย](README.th.md))
- [docs/reference.md](docs/reference.md) — every feature, hosting, keys, for players
  ([ไทย](docs/reference.th.md))
- [docs/REFACTORING_PLAN.md](docs/REFACTORING_PLAN.md) — phased plan with code
