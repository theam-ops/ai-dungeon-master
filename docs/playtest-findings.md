# Playtest findings

Scripted playtest, 2026-08-25, against `main` at e21ef81.

**No model was called.** This machine has no API key and no Claude Code sign-in, so the
DM is `tests/stub_backend.StubBackend` — an implementation of exactly the contract
`game/providers.Backend.stream` describes: an async generator that yields
`{"type": "delta", "text": ...}` chunks and finishes with one `{"type": "message",
"content": [...anthropic blocks...], "stop_reason": ...}`. Everything downstream of that
seam is real: `dm.take_turn`, the tool loop, `dm.run_tool`, `rules.py`'s dice, the SQLite
event log, the SSE fan-out, media, lore, export and import. Only the choice of words is
faked.

The scripted session in `tests/test_session.py` is one campaign end to end: create it,
open the scene, creep along a gantry and roll for it, take damage, earn XP and gold, pick
up a Thai-named item, level up, have a second player join mid-story with a Thai character
name, import the players' own notes, have the DM search them, then export the whole thing
and import it back and keep playing.

```
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest
```

35 tests, all passing.

## Verdict first

The game is in better shape than this list makes it look. **Thai survives every hop** —
upload, storage, prompt, narration, export, zip, re-import, search — and I went looking
hard for a place it didn't. The dice-honesty guarantee holds under test: the number in the
feed is the number `random` produced, and it is what gets fed back to the model. Every
security guard I poked held: SVG refused, EXIF stripped by re-encode, key-setting refused
from off-machine, the Claude sign-in refused from off-loopback, media scoped per campaign,
the 12-round tool-loop backstop cutting a runaway model off.

What follows is ranked by how much it would spoil an actual evening of play.

---

## Fixed in this PR

### 1. A campaign over ~500 events opened on the wrong scene, permanently

**Severity: high.** This is the one that would have ended a session.

`store.events_since(cid, since, limit=500)` returned the *oldest* 500 events after
`since`. The browser sets `S.lastSeq` from whatever it receives
(`static/app.js`, `handle()`), and `enterCampaign` starts from `since=0`. So opening a
campaign that had run for a few sessions replayed the first 500 events, left `lastSeq` at
seq 500 — and nothing ever asked for 501 onwards. The player sat looking at session one's
opening scene while the table talked past them, and **no reconnect healed it**, because
every reconnect asked from the same stale `lastSeq`. The same hole opened for a phone that
slept through more than 500 events.

A turn produces roughly 5–10 events (player line, two or three dice, a sheet change, the
narration), so 500 events is 50–100 turns. A campaign playing across two or three evenings
reaches it.

Reproduce (before the fix):

```python
c = store.create_campaign("long", "en", "")
for i in range(1200):
    store.append_event(c["id"], "player", {"character": "V", "text": f"turn {i}"})
evs = store.events_since(c["id"], 0)
# len(evs) == 500, evs[-1]["seq"] == 500, and 501..1200 are unreachable forever
```

**Fix** (`game/store.py`): when more than `limit` events are waiting, return the *newest*
window instead of the oldest. That is the right answer for both cases — opening a campaign
should show the current scene, and a client that fell too far behind should jump forward
rather than stall forever. No client change needed. Export still gets everything
(its limit is 100,000).

Tests: `test_a_long_campaign_replays_its_newest_events`,
`test_a_client_that_was_away_too_long_still_catches_up`,
`test_export_still_carries_the_whole_event_log`.

### 2. Thai notes saved by Windows imported as silent mojibake

**Severity: high for this project specifically.**

`server.import_library` did `raw.decode("utf-8", errors="replace")`. Thai `.txt` files are
very often **CP874** (the Thai Windows ANSI codepage, which Notepad wrote by default for
years) or **UTF-16** (which is what "Unicode" means in Notepad's Save As dialog). Both
decoded to replacement characters. The upload *succeeded*, the document appeared in the
Campaign library with a plausible character count, and `search_lore` then found nothing in
it — ever — with nothing anywhere explaining why. The DM would keep inventing NPCs the
notes already defined, and the player would have no way to tell the notes were the problem.

Reproduce (before the fix):

```python
lore.to_text("notes.txt", "อรุณจมไปกับระฆัง".encode("cp874").decode("utf-8", "replace"))
# -> '��ส�...'  - imports fine, matches nothing
```

**Fix** (`game/lore.py`): a `decode()` that checks for a BOM (UTF-16, UTF-8-sig), then
tries strict UTF-8 so a correct modern file is never second-guessed, then CP874 — but
**only if the result contains a run of three or more consecutive Thai characters**. CP874
maps essentially every high byte into the Thai block, so accepting it on a single hit would
turn a CP1252 file with one `é` in it into confident Thai nonsense; real Thai prose comes in
long unbroken runs. Last resort is UTF-8 with replacement, which is exactly what happened
before, so nothing that used to import now fails.

Tests: `test_thai_notes_survive_whichever_encoding_notepad_used` (four encodings),
`test_a_western_legacy_file_is_not_turned_into_thai`, `test_utf8_is_never_second_guessed`.

### 3. A malformed export file was an HTTP 500 with a stack trace

**Severity: medium.** An export is a file off somebody's disk, so every field in it is
attacker-shaped — and this app *invites* people to hand it one, on the free-tier deploy
path where the database is wiped on redeploy.

`store.import_campaign` assumed shapes. `blob["campaign"]` being a string meant
`meta.get` → `AttributeError`, which is not in any caller's `except` tuple, so it became a
500. Five of six malformed shapes I tried did this:

| blob | before |
|---|---|
| `{"campaign": "nope"}` | `AttributeError: 'str' object has no attribute 'get'` → 500 |
| `{"campaign": []}` | `AttributeError: 'list' object has no attribute 'get'` → 500 |
| `{"characters": ["Vess"]}` | `AttributeError: 'str' object has no attribute 'pop'` → 500 |
| `{"events": ["boom"]}` | `AttributeError: 'str' object has no attribute 'items'` → 500 |
| `{"lore": ["boom"]}` | `AttributeError: 'str' object has no attribute 'get'` → 500 |

**Fix**: `_records()` validates each list is a list of dicts and raises `ValueError` naming
what was wrong; `_text`/`_int` coerce fields headed for TEXT/INTEGER columns rather than
letting sqlite raise an `InterfaceError` three frames down; `AttributeError` added to the
`except` tuples on all four import endpoints as a backstop. All eight malformed shapes now
give a 400 saying "that file isn't a campaign export".

A second bug fell out of this: the character loop did `ch.pop("_token")` and
`ch.pop("portrait")` **on the caller's dict**, so importing the same in-memory blob twice
lost the portrait on the second pass. Now it copies first.

Tests: `test_a_malformed_export_is_a_400_not_a_500` (8 cases),
`test_import_does_not_mutate_the_blob_it_was_given`.

### 4. Two players could take the same name, and one became invulnerable

**Severity: medium.** Nothing stopped a second player calling themselves Vess.
`dm.run_tool` finds a character by name via `dm._find`, which returns the **first** match —
so from that moment on, every point of damage the DM aimed at *either* Vess landed on the
one created first, and the second was immortal. Their HP bar never moved. From the table it
looks exactly like the DM cheating, which is the one thing this design exists to rule out.

Reproduce (before the fix): join a campaign twice with the same name, then have the DM deal
damage to that name — both times it lands on character one.

**Fix** (`server.add_or_claim`): reject a name already at the table, case-insensitively,
with a message saying so. Existing campaigns that already have a collision are unaffected;
this only stops new ones.

Test: `test_two_characters_cannot_share_a_name`.

### 5. Two players tapping "Begin" together opened the campaign twice

**Severity: medium.** `begin` refused a second call by checking `store.get_history(cid)` —
which is only written when the turn *ends*, in `run_dm_turn`'s `finally`. Two requests in
the same moment both passed the check, both got 200, and the campaign opened twice, in two
different places, with two openings interleaved in the feed. It also spends two turns'
worth of tokens.

Reproduce (before the fix): two threads both POST `/api/campaigns/{id}/begin` → `[200, 200]`
and two `narration` events.

**Fix**: a module-level `beginning` set claimed before the task is spawned and discarded
when it finishes. There is no `await` between the check and the claim, so it is atomic
against the other request on the same event loop. Now `[200, 400]` and one opening.

Test: `test_the_campaign_only_opens_once`.

### 6. The provider's raw error body was shown to every player at the table

**Severity: medium.** When a turn failed everywhere, `dm.take_turn` composed
`f"Every AI turned the turn away ({last_error})"` and that went to `publish`, i.e. to every
player — including friends who joined over a Cloudflare tunnel. `last_error` carries up to
200 characters of the provider's response body verbatim. What actually appeared in the feed:

> Every AI turned the turn away (Gemini 3.6 Flash (free): HTTP 400 {"error":{"message":"API key not valid. Please pass a valid API key.","details":[{"@type":"type.googleapis.com/google.rpc.ErrorInfo","reason":"API_KEY_INVALID"}]}}).

That is the host's billing state, in JSON, in the story feed. The `switch` event carried the
same thing in its `reason`. No key leaked in any body I could construct, but the shape is
wrong: this is written for whoever holds the key, not for six friends reading a story.

**Fix** (`server.player_safe`): keep the readable head — "Claude Opus 5: HTTP 429" — and cut
at the first `{`, `[`, `<`, or anything that looks like a key prefix; balance any dangling
paren; cap at 160 characters. The full text goes to `logging.getLogger("dnd").warning`,
where the person who can act on it is looking.

Test: `test_a_provider_error_body_is_not_shown_to_the_table`.

### 7. The 40-document limit counted one upload, not the campaign

**Severity: low.** `documents[:MAX_LORE_DOCS]` capped each *request*. Uploading a folder of
40 three times gave a campaign 120 documents — 120 names in the system prompt's manifest on
every single turn, and a `search_lore` that reads all of them.

Reproduce (before the fix): three 40-file library imports → 120 rows.

**Fix**: count what the campaign already holds. Re-uploading a document that is already
there still replaces it and costs no slot, which is what `store.add_lore` already promised.

Test: `test_the_document_limit_is_for_the_campaign_not_the_upload`.

### 8. Background turns could be garbage-collected mid-narration

**Severity: low, but nasty when it bites.** `act` and `begin` called
`asyncio.create_task(...)` and kept no reference. asyncio holds only a weak reference to a
running task, so it may be collected while it is still running — the documented footgun.
The symptom would be a turn that simply stops, with no error anywhere.

**Fix**: a `spawn()` helper holding the task in a module-level set until it completes.
Not directly testable without forcing a GC at the wrong moment; the fix is the standard one.

### 9. Two small tidy-ups

- `server.export_campaign` computed `utf8_name` twice, the first with a hard-coded `.json`
  that was immediately overwritten. Dead line, removed.
- `httpx` is imported directly by `game/providers.py` and `game/media.py` but was only
  present transitively via `anthropic`. Added to `requirements.txt`.

---

## Found, not fixed

Each of these has a test that pins the current behaviour, so a change to it is deliberate.

### A. Campaign history grows without limit, and every turn re-sends all of it

**Severity: high. Structural — this is the one worth doing next.**

`store.get_history` returns the whole campaign and `dm.take_turn` hands all of it to the
backend on every turn, with nothing truncating or summarising. Cost per turn therefore grows
linearly and cost per *session* quadratically; eventually the request exceeds the model's
context and the campaign simply stops working, with no warning beforehand and no way to
recover except editing an export by hand.

`claude_code.render_transcript` caps at 40 turns (`CLAUDE_CODE_TRANSCRIPT_TURNS`). Every
other backend — Anthropic, Gemini, Groq, OpenRouter, Ollama — sends everything. Ollama is the
worst placed: `OLLAMA_CONTEXT` defaults to 8192 tokens, which a campaign passes in an hour,
after which the local model silently forgets the beginning with no signal that it has.

Not fixed because doing it properly is a design decision, not a patch: a rolling window loses
continuity, which is the thing the README sells; a summarisation pass costs a model call and
needs somewhere to live in the schema; and either way the right cap depends on the backend.
Half-doing it would be worse than the honest status quo.

Test: `test_known_history_grows_without_limit` — asserts each request is strictly larger than
the last and that nothing is dropped.

### B. Import bypasses the media and document caps

Upload enforces 60 images (`media.MAX_PER_CAMPAIGN`) and now 40 documents. `store.import_campaign`
enforces neither, so a hand-edited export seeds a campaign with 200 of each, and re-importing
the same campaign repeatedly multiplies rows. Confirmed: 200 media rows and 200 lore rows from
one import.

Not fixed because the right behaviour is a product call — refuse the whole import? truncate,
and if so which ones? — and silently dropping a player's material during a restore is worse
than the current permissiveness.

Test: `test_known_import_bypasses_the_media_and_lore_caps`.

### C. `dm._find` can put damage on the wrong character

`game/dm.py`'s `_find` falls back to a substring match after an exact one fails. With Vess and
Vandar at the table, `_find("V")` returns Vess and `_find("a")` returns Vandar — a
one-character `character_name` lands on whoever matches first. The tolerance is there for a
good reason (the DM using a first name), but it has no minimum length and no
ambiguity check, so a truncated or hallucinated name silently hits a real character.

Not fixed: `game/dm.py` is being changed in parallel by another agent, and a tighter match
belongs with that work. The shape of the fix is small — require ≥3 characters for the fuzzy
pass and refuse when more than one character matches, returning the same
`ERROR: no character named ...` the exact-match miss already returns.

Test: `test_known_the_dm_can_hit_the_wrong_character_with_a_short_name`.

### D. The SSE queue drops frames silently when a client stalls

`server.broadcast` does `q.put_nowait` and swallows `asyncio.QueueFull` — "a stalled client
drops frames rather than blocking the table", which is the right call. But the client has no
way to know it happened: it receives seq 1..1000 then seq 1005, sets `lastSeq = 1005`, and
1001–1004 are gone for good. The hole is invisible.

The cheap fix is client-side: `handle()` already tracks `lastSeq`, so noticing
`ev.seq > lastSeq + 1` and re-fetching `/api/campaigns/{id}/events?since={lastSeq}` would
heal it. Left alone because it touches the delta/narration rendering path in `app.js` and
wants a browser to test in, which this pass did not have.

### E. Two module-level defaultdicts never shrink

`server.subscribers` and `server.locks` gain an entry per campaign id ever touched and lose
none — including for campaigns that are subsequently deleted. Each entry is tiny, but this is
a server meant to stay up for months. `broadcast` also creates a `subscribers[cid]` entry for
any id it is handed. Left alone: fixing it correctly means knowing when the last subscriber
has gone *and* no turn is in flight, which is more coordination than the leak is worth today.

Test: `test_known_locks_and_subscribers_are_never_cleaned_up`.

### F. The rate limit is per table, so one player can lock everyone out

`MAX_TURNS_PER_MIN` (default 12) counts `player` events for the whole campaign. One impatient
player uses the whole table's budget and everyone else gets the same 429 — "the table is
moving too fast", which reads as though *they* did something wrong. `begin` is not counted at
all. Per-character counting would be a better spend control and a much better message.

Test: `test_known_the_rate_limit_is_per_table_not_per_player`.

### G. More than four attached images vanish without a word

`act` does `(body.get("media") or [])[:4]`. Attach six, and four appear in the feed and two do
not — no error, no note, nothing. The cap itself is right (images are expensive context); the
silence is not. A line in the feed, or a 400, would both be better.

Test: `test_known_more_than_four_attached_images_vanish_silently`.

### H. Error and switch messages are always English, even in a Thai campaign

The DM narrates in Thai, the interface is in Thai, and then the feed says "Every AI turned the
turn away" or "The DM got stuck in a loop and the turn was cut short." Those strings are built
in `game/dm.py` and `server.py` with no language argument. `game/i18n.py`'s `CLI` dict is the
obvious home for them, and `static/i18n.js` would need matching entries — three places, per the
README's own "Adding a language" note. Left alone as it overlaps `game/dm.py`.

### I. `media.fetch` re-resolves DNS after checking it

`media._is_public` calls `getaddrinfo` and rejects private addresses, then httpx resolves the
hostname again to make the request. Between the two, a hostname the attacker controls can
change answer — classic DNS rebinding — so a "public" URL reaches a private one. The redirect
loop re-checks every hop, which handles the easy version of this; the rebinding version needs
connecting to the checked IP with the original `Host` header, which is a real piece of work.
Worth knowing about before exposing the paste-a-link feature to strangers.

### J. Smaller annoyances

- **SSE payloads escape Thai.** The stream does `json.dumps(event)` with the default
  `ensure_ascii=True`, so every Thai character becomes `\uXXXX` — 129 bytes where 49 would do,
  2.6× the traffic on a Thai campaign, all of it on someone's phone data. `ensure_ascii=False`
  is safe here (the response is UTF-8) but touches the streaming path, so it is listed rather
  than done.
- **No maximum level.** `rules.level_up` has no ceiling; 60 `level_up` calls give a level-61
  character with 222 max HP. A confused DM can inflate a sheet arbitrarily.
- **Anyone with a campaign code sees the party roster** before joining, via
  `/api/campaigns/join`, with no rate limit on guessing codes. 31⁶ ≈ 887M codes makes this
  theoretical, but there is no backstop at all.
- **Importing a campaign hands you its first character**, which on a shared export is somebody
  else's. Deliberate, per the code comment, and probably right — but surprising.

---

## What could not be verified without credentials

Named explicitly, because "the tests pass" should not be read as covering these:

- **No real model was ever called.** Nothing here says whether Claude, Gemini, Groq,
  OpenRouter or Ollama actually behave as `providers.py` translates them — in particular the
  Gemini 3.x `thought_signature` replay (`replay_extra`), which is the sort of thing that only
  fails against the live API.
- **Failover between real providers.** The stub can be made to raise `ProviderExhausted` and
  the loop does the right thing, but whether a real 402 or a 429 arrives in the shape
  `AnthropicBackend.stream` expects is untested here.
- **The Claude Code backend.** `game/claude_code.py` needs an installed, signed-in Claude Code
  and the Agent SDK. `auth_status()` on this machine reports installed and logged in, but no
  turn was run through it, and `LoginFlow` — which scrapes a URL out of `claude auth login`'s
  stdout — is entirely unexercised.
- **Image generation.** `OpenAICompatBackend.draw` needs a billed Gemini key.
- **Anything in a browser.** The SSE reconnect logic, the delta rendering, the visibility-change
  reconnect and the Thai font stack in `static/style.css` were read, not run. Finding D in
  particular is a client-side fix that nobody has watched happen.
- **Vision.** No provider was given a real image to look at.
