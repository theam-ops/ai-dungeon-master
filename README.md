# AI Dungeon Master

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![SQLite](https://img.shields.io/badge/SQLite-003B57?logo=sqlite&logoColor=white)](https://sqlite.org/)
[![No build step](https://img.shields.io/badge/frontend-no%20build%20step-c9a227)](static/)

[![Claude](https://img.shields.io/badge/Claude-D97757?logo=anthropic&logoColor=white)](https://console.anthropic.com/)
[![Gemini](https://img.shields.io/badge/Gemini-free%20tier-4285F4?logo=googlegemini&logoColor=white)](https://aistudio.google.com/apikey)
[![Groq](https://img.shields.io/badge/Groq-free%20tier-F55036?logo=groq&logoColor=white)](https://console.groq.com/keys)
[![Ollama](https://img.shields.io/badge/Ollama-runs%20offline-000000?logo=ollama&logoColor=white)](https://ollama.com/download)

[![Languages](https://img.shields.io/badge/languages-English%20%7C%20%E0%B9%84%E0%B8%97%E0%B8%A2-8a7220)](#%E0%B8%A0%E0%B8%B2%E0%B8%A9%E0%B8%B2%E0%B9%84%E0%B8%97%E0%B8%A2--thai-support)
[![Dice](https://img.shields.io/badge/dice-rolled%20in%20Python-7fb069)](game/rules.py)
[![Play from](https://img.shields.io/badge/play%20from-browser%20%7C%20phone%20%7C%20terminal-6f9bb5)](#playing-from-your-phone)

A D&D-style RPG where Claude Opus 5 runs the table. It narrates, voices every NPC,
and adjudicates the rules. The parts a language model is bad at — dice, arithmetic,
remembering your HP — run in Python instead.

Play in a browser on any device, solo or with friends in the same campaign, in
**English or Thai (ภาษาไทย)**. If Claude's credit runs out mid-game, another AI picks
the turn up automatically. There's a terminal client too (`dnd.py`), sharing the same
DM and the same rules.

---

## Quick start — put it on your Desktop (Windows)

Open the `tools` folder and double-click **Create shortcut.cmd**. That puts a d20 icon
named *AI Dungeon Master* on your Desktop. Double-click it to play: it installs anything
missing the first time, starts the server, and opens your browser. Close the window it
leaves behind to stop the game.

If the folder ever moves, run **Create shortcut.cmd** again — the shortcut remembers an
absolute path.

To skip the shortcut and just start it, double-click **Play.cmd** in this folder, or:

```bash
python launch.py
```

`launch.py` takes `--port 9000` if 8000 is taken (it will find a free port by itself
anyway), and `--no-browser` if you'd rather open the tab yourself.

You still need an AI to run the table — the game will say so and point you at the free
options if none is set up. See **Playing free, with no API key** below, or use a Claude
Pro subscription via Claude Code.

## Quick start (any platform, by hand)

```bash
pip install -r requirements.txt
```

Set your Anthropic API key (from console.anthropic.com):

```bash
setx ANTHROPIC_API_KEY "sk-ant-..."
```

Reopen the terminal after `setx` — or set it for this session only with
`$env:ANTHROPIC_API_KEY = "sk-ant-..."` in PowerShell.

Then:

```bash
python -m uvicorn server:app --reload
```

Open **http://localhost:8000**, roll a character, and play.

## Playing from your phone

**Option A — a tunnel (free, instant, no signup).** Your PC keeps running the server
and gets a public https URL. Install [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/),
then with the server running:

```bash
cloudflared tunnel --url http://localhost:8000
```

It prints a `https://something.trycloudflare.com` URL. Open it on your phone. Set
`APP_PASSWORD` first (see below) — that URL is on the public internet.

**Option B — deploy it (works when your PC is off).** The repo has a `Dockerfile` and
a `render.yaml`, so any container host works — Render, Railway, Fly.io, Koyeb. On
Render: push this folder to GitHub, create a Blueprint from `render.yaml`, and set
`ANTHROPIC_API_KEY` and `APP_PASSWORD` in the dashboard.

> **Free tiers wipe the disk on redeploy.** `campaign.db` is not permanent there. Use
> **Export** in the app to download a campaign as JSON and **Import** to restore it, or
> attach a persistent disk and set `DND_DB=/data/campaign.db` (commented into `render.yaml`).

## Playing free, with no API key

Three ways. If you already pay for Claude, start with the third.

> **You never need to touch a terminal for this.** Open the app with nothing configured
> and it offers the links — **Get a free key from Google Gemini**, **from Groq**,
> **install Ollama** — and a box to paste the key straight in. The server checks the key
> against the provider before keeping it, tells you if it was rejected, and starts
> working immediately. No `setx`, no restart.
>
> The same box sits under the AI button in the character sheet drawer, for adding
> another provider later.

### Google Gemini — a free key, no credit card

1. Go to **aistudio.google.com/apikey** and sign in with a Google account.
2. Click **Create API key**. That's it — no card, no billing setup. It starts with `AIza`.
3. Set it and restart the server:

Paste it into the app (easiest), or set it as an environment variable:

```bash
setx GEMINI_API_KEY "AIza-paste-yours-here"
```

**Gemini 2.0 Flash (free)** appears in the AI button tagged *free tier*, and becomes the
default when no Claude key is set. The free tier has daily limits rather than a bill —
plenty for normal play, and if you hit them the game fails over to whatever else you
have configured.

**Groq** works the same way and is very fast: a free key from **console.groq.com/keys**,
no card, then `setx GROQ_API_KEY "gsk_..."`.

Either of these is easier than everything below. If you just want to play tonight, get
the Gemini key.

### Ollama — free forever, offline, no account at all

**Ollama** runs a model on your own PC — no key, no account, no tokens, nothing to run
out of, and it works offline.

1. Install it from **ollama.com/download** (normal Windows installer).
2. Pull a model sized for your machine:

```bash
ollama pull llama3.2:3b
```

3. Restart the game server. **Llama 3.2 3B (local)** appears in the AI button, tagged
   *on this PC*, and new campaigns use it automatically when no API key is set.

`qwen2.5:3b` is the other default and often writes slightly better prose — pull both and
switch between them with the AI button. Point at different models with `OLLAMA_MODELS`,
and set `OLLAMA_HOST` if Ollama runs on another machine.

**What to expect on a modest laptop** (8GB RAM, no dedicated GPU): a 3B model writes
roughly 20–40 seconds per turn, and the DM is noticeably weaker than Claude — shorter
descriptions, thinner NPCs, and a shakier memory of what happened several scenes ago.
Bigger models are better but need more RAM: 7–8B wants 16GB to be comfortable.

The important part holds either way: **dice and your character sheet still run in
Python**, so a weaker model can't fake a roll or lose track of your HP. If a small model
forgets to call the dice tool at all, you'll see it immediately — no dice chip appears
and nothing on your sheet changes. It degrades into storytelling without mechanics
rather than silently inventing results.

> Models must support tool calling. `llama3.2` and `qwen2.5` do. Many small models don't
> — check before swapping one in.

### Already have Claude Pro? Run the web app on your subscription

A Claude Pro or Max subscription covers claude.ai and **Claude Code**. It is not an API
key, and there is no OAuth scope a web app can ask for to spend somebody's subscription —
so the game cannot log players in with their Claude accounts. What it *can* do is drive
the copy of Claude Code already installed on the machine running the server, which is
ordinary use of what you already pay for.

Install Claude Code, sign in, and add the Agent SDK:

```bash
pip install claude-agent-sdk
```

Restart the server. **Claude Pro/Max (this machine)** appears in the AI list in the
character sheet drawer, and new campaigns start on it. The list checks whether Claude
Code is actually signed in and marks the row **sign in needed** if it isn't — being
installed and being signed in are different things, and the difference would otherwise
only show up when a turn failed.

If it isn't signed in, **Sign in with Claude** is right there in the same panel. It runs
Claude Code's own `auth login` on this machine and shows you the link it prints, which is
Anthropic's real authorisation page — the game never sees a password or a token. Usually
the flow finishes by itself in the browser it opens and the row turns green; if Anthropic
shows you a code instead, paste it into the box.

That button only works **from the computer running the game**. A player who joined over a
tunnel gets a 403 and never sees the link, because whoever completes that sign-in decides
which Claude account pays for every turn at the table. No key, no credit — turns are
billed to the subscription, and they count against its usage limits like any other
Claude Code work. When the limit is reached the turn fails over to the next configured
AI, the same as an API key running out.

Everyone at the table plays on it: friends who join over a tunnel are served by the
host's DM, exactly as they are for every other backend. The dice are unchanged — Claude
Code reaches `roll_dice` and `update_character` through an in-process MCP server that
calls the same `game/rules.py` every other backend uses.

Two things to know. Claude Code cannot see images players attach, so a campaign on this
backend gets the caption and not the picture. And each turn is sent with the story so
far folded into the prompt rather than resuming a Claude Code session, which keeps one
canonical campaign record — the one that gets exported, and the one another AI reads if
it takes over mid-campaign.

Configure it with `CLAUDE_CODE_MODEL` (default `claude-opus-5`), `CLAUDE_CODE_PATH` if
`claude` is not on `PATH`, and `CLAUDE_CODE_TRANSCRIPT_TURNS` for how much history rides
along (default 40).

### Or play in the terminal, inside Claude Code itself

Open a terminal in this folder and run `claude`, then:

```bash
/dm
```

That loads `.claude/commands/dm.md` and the session becomes your Dungeon Master. It
rolls real dice and keeps your sheet by calling `play.py`, which wraps the same
`game/rules.py` the web app uses — so the honesty guarantee is identical. You will see
every roll:

```
Stealth vs DC 14: 1d20+3 -> [14] +3 = 17
  -> SUCCESS against DC 14
```

Characters live in `saves/` and are shared with the terminal client. The DM records what
happened with `play.py note`, and reads it back with `play.py recap` next session, so the
campaign survives between sittings.

You can drive the same tools yourself, without any AI, if you want to be your own DM:

| Command | What it does |
|---|---|
| `python play.py new "Vess" Elf Rogue` | roll up a character (`--lang th` for Thai) |
| `python play.py roll 1d20+3 --dc 14` | roll dice, and say whether it beat the DC |
| `python play.py update Vess --hp -4 --xp 25` | apply damage, XP, gold, items, conditions |
| `python play.py sheet Vess` | show the sheet |
| `python play.py note Vess "..."` | record something that happened |
| `python play.py recap Vess` | read the campaign log back |

What you give up versus the web app: no browser UI, no phone (unless you use Claude Code
on the web), no party — it is one player at a table with Claude. What you gain: nothing
to install beyond Claude Code itself. If you want the browser, the phone and the party
on the same subscription, use the backend above instead.

## When the tokens run out

The character sheet drawer has a **Which AI runs the game** button. It lists every AI
the server can reach; tap one to hand the table over. The switch is per campaign and
takes effect on the next turn.

You mostly won't need it. If the AI running a campaign returns "out of credit", "rate
limited", or "bad key" mid-turn, the turn is retried on the next AI that's available —
including a local Ollama model, which never runs out — and a line appears in the feed:

> *Claude Opus 5 ran out — GPT-4o mini has taken over the table.*

Nothing is lost. Whatever the failed AI managed to write is discarded before the retry,
so you never get half a paragraph in one voice and half in another, and the campaign
stays on whichever AI actually worked.

Free keys count as backups too — a Gemini or Groq key costs nothing and gives the
failover somewhere to land. For a wider set of paid models on one balance, get an
[OpenRouter](https://openrouter.ai/keys) key:

```bash
setx OPENROUTER_API_KEY "sk-or-..."
```

Restart the server and GPT-4o mini, Gemini Flash, DeepSeek and Llama appear in the list.
Choose different models with `OPENROUTER_MODELS`:

```bash
setx OPENROUTER_MODELS "openai/gpt-4o-mini,qwen/qwen-2.5-72b-instruct"
```

> **The model must support tool calling.** The DM rolls dice through a tool; a model
> without tool support would have to invent its own results, which is the one thing this
> whole design exists to prevent. The four defaults all support it — check OpenRouter's
> model page before adding others.

`DM_BACKEND` sets which AI new campaigns start on; `GEMINI_MODELS`, `GROQ_MODELS`,
`OPENROUTER_MODELS` and `OLLAMA_MODELS` change which models each provider offers. In the
terminal client, `/ai` lists them all and `/ai DeepSeek` switches.

Claude is the better DM — it holds a scene together over a long campaign in a way the
cheaper and local models don't. Treat the others as the thing that keeps the game moving
at 1am when your balance hits zero, not as an equal swap.

**A note on Claude Pro:** a Claude Pro or Max subscription covers claude.ai and Claude
Code, not the API. There's no supported way to point this *server* at a Pro subscription,
and anything claiming to do it works by impersonating a browser session — against
Anthropic's terms and liable to break or get an account suspended. What the subscription
does cover is playing through Claude Code with `/dm`, described above.

## Where pasted keys live, and who may set them

A key pasted into the app is written to `.keys.json` beside the database — plain text,
same exposure as an environment variable, and git-ignored. A real environment variable
always wins over a saved one, so `setx` still overrides.

Setting a key is deliberately restricted, because it is the one thing here that spends
money:

- **`APP_PASSWORD` set** — anyone who has entered the password may set keys. That's how
  you'd do it on a phone against a deployed instance.
- **No password** — only requests coming from the machine the server runs on are
  accepted. A public instance with no password refuses key changes outright, and says so.
- **`ALLOW_KEY_SETUP=0`** — turns the feature off completely.

Keys are never sent back to the browser; the app only ever learns *whether* a provider
is configured. Deleting one removes it from both the file and the running process.

## Lock it down

The server holds your API key, so anyone who can open the URL can spend your credits.
Set a password and the whole app sits behind it:

```bash
setx APP_PASSWORD "some-long-phrase"
```

Leave it unset for localhost-only use. `MAX_TURNS_PER_MIN` (default 12) caps how fast a
single campaign can burn turns — worth keeping now that a campaign can fail over onto a
second billing account.

---

## Solo or party — your choice

Every campaign has a 6-character share code. A party of one is the normal case; nothing
extra is needed for solo play.

To bring someone in, tap **Share code** in the character sheet drawer and send them the
link. They open it, pick a name, race and class, and they're in the same scene. Up to
six characters per campaign.

Turns are free-form, the way a real table works — anyone can act whenever, and the DM
sees who did what. It won't make you wait for an initiative order.

**Same character on two devices:** open the app on your phone, enter the campaign code,
and pick your existing character from the list instead of making a new one. The
character moves to that device.

**Joining late is the normal case.** A campaign that has been running for hours takes a
new player the same way an empty one does: they get the whole story replayed into their
feed on the way in, and the DM is told at that point in the transcript that somebody has
arrived, so it writes them into the scene instead of finding a stranger already in the
party.

### Notes for the DM

In the character sheet drawer, under **Notes for the DM**, each player keeps standing
details about their own character — the tone they want, backstory they have decided on, a
thread they want picked up, something they would rather the game left alone:

> Vess has been afraid of fire since her village burned, and is looking for the brother
> who walked out on her. I'd rather chase a mystery than fight a war.

They ride along with every turn *that* character takes, and only that one — another
player's preferences are not in the DM's ear on a turn that isn't theirs. Only you can
edit yours, nobody else at the table is sent them, and they travel with **Export**.

They are capped at 600 characters, deliberately: every one of them is re-sent on every
turn, and six players' worth of preamble would push the actual scene down the prompt.
Write what should always be true, not what you are doing this turn.

## ภาษาไทย — Thai support

Tap **ไทย** in the language switcher (on the login screen, at the bottom of the lobby,
or inside the character sheet drawer). Everything follows:

- The DM narrates, voices NPCs, and writes its dice reasons in Thai.
- The interface — buttons, labels, races, classes, ability names, the character sheet.
- Starting gear is written onto the sheet in Thai (ดาบสั้น, เกราะหนัง, คบไฟ), so a Thai
  campaign reads as Thai from the first turn.
- Thai typography: the whole font stack swaps to Sarabun / Noto Sans Thai with more
  line height, because tone marks and vowels stack above and below the baseline and
  collide at Latin leading. Uppercase and letter-spacing are dropped — both are
  Latin-only devices that mangle Thai.

The language you pick when creating a campaign is the language its DM narrates in, and
it sticks to that campaign. Your *interface* language is a separate, per-device
preference — so a Thai player and an English player can sit at the same table, each
reading the buttons in their own language while the DM narrates to both in Thai.

Under the hood, races, classes and ability scores are stored in English and translated
for display, which is what makes that work. Text people write — character names, items
the DM invents, the story itself — is stored as typed, in whatever language it was
written.

Switching language mid-game re-renders the interface immediately. Narration already on
screen keeps the language it was written in; it's a record of what happened.

## Images

Three ways to get a picture into a campaign, all from the character sheet drawer or the
paperclip beside the message box:

- **Upload** a file, or take a photo on your phone
- **From a link** — paste an image address and the server fetches it
- **Draw it** — have an AI generate a portrait, or illustrate the current scene

There is a fourth, which nobody has to ask for: with a drawing AI configured, the DM
illustrates scenes on its own. See **The DM illustrating on its own** below.

**Portraits** appear on your sheet and as a small face beside your name in the party bar.
**Attachments** go into the feed for everyone at the table, and the DM sees them too —
"here's the map I found, what do I make of it?" — on any provider that supports vision
(Claude, Gemini, Groq; a local Ollama model usually can't, and falls back to knowing an
image was shown without seeing it).

An attached image is sent on the turn it appears and then drops out of the history. Left
in, it would be re-sent every single turn and quietly multiply the bill.

> **Generated art needs billing.** Google's free tier covers text but not drawing — the
> image models return HTTP 429 on a free key. The **Draw it** and **Illustrate this
> scene** buttons say so plainly rather than failing silently. Everything else — upload,
> links, portraits, vision — works on the free tier.

### The DM illustrating on its own

When an AI that can draw is configured, the DM gets a fourth tool, `draw_scene`, and
will use it at moments worth a picture — the first sight of somewhere, a creature
revealed, the end of a bad fight. Nobody has to press anything; the image arrives in
the feed a little after the narration, labelled *The DM illustrates the scene*.

Two things keep it from becoming expensive.

**A limit in code, not in the prompt.** The DM is asked to be sparing, but asking is
not a guarantee — a chatty model would illustrate every paragraph and bill you for it.
So the campaign holds a single illustration slot which refills once every
`DM_ART_EVERY_TURNS` player turns (default 6), claimed in SQLite before anything is
drawn. When the slot is empty the tool answers *not now*, the DM carries on narrating,
and the table never sees the refusal. A campaign's first picture is free — that's
usually the opening scene, which has more reason to be drawn than anything after it.

```bash
setx DM_ART_EVERY_TURNS 12      # rarer
setx DM_ART_EVERY_TURNS 999     # off in practice
```

Unset a drawing provider and the tool disappears entirely, which is the real off switch.

**It never holds up the turn.** Generating an image takes the better part of a minute,
and the turn loop is what streams narration to every browser at the table — waiting for
a picture there would freeze the scene mid-sentence for everyone. The drawing goes off
on its own and lands in the feed when it is ready, the way an upload does. Every way it
can fail — no artist, a 429 on a free key, a model that returns text instead of a
picture, a campaign already at its 60-image limit — leaves the turn exactly as it was,
with a line on the server's log and a table that never knew a picture was coming.

DM art is stored, capped, exported and imported like every other image: same
`media/<campaign>/<sha256>.<ext>` file store, same `MEDIA_MAX_PER_CAMPAIGN`, same trip
into the `.zip`.

### What happens to a file you upload

Every image is decoded with Pillow rather than trusted by its extension, then re-encoded
from raw pixels. That destroys **all EXIF, including GPS coordinates**, and anything
hidden inside the container. SVG is refused outright — it is a script container, not an
image. Files are stored content-addressed under `media/<campaign>/<sha256>.<ext>`, capped
at 8MB in, 2048px (512px for portraits), 60 images per campaign.

Pasting a link is the riskier door, so the server refuses any address that resolves to
localhost, a private LAN range, or a cloud metadata endpoint — and re-checks every
redirect hop, since a public URL can bounce to a private one.

Images are served through `/api/campaigns/{id}/media/{id}` behind the same membership
check as everything else, never from a static folder. **Anyone with the campaign code
sees them** — worth remembering before uploading a photo of a real person.

A campaign with images exports as a **`.zip`** (campaign.json plus the files) instead of
plain JSON. Import takes either — and, via **…or an unzipped folder**, a folder you have
already extracted: point it at the directory holding `campaign.json` and its `media/`
folder. Whichever you pick, the contents decide how it's read, not the file name, so a
renamed export still imports.

## Commands and controls

| Where | What |
|---|---|
| Message box | Type what your character does, in plain language |
| English / ไทย | Switch the interface language, any time |
| Which AI runs the game | Hand the table to a different AI |
| 📎 (beside the message box) | Attach an image to your next action |
| 📁 (beside 📎) | Attach a whole folder — it takes the images and ignores the rest |
| Campaign library | In the sheet drawer: import a folder of your own art and notes |
| Portrait / Scene art | In the sheet drawer: upload, link, or generate |
| Notes for the DM | In the sheet drawer: standing details about your own character |
| ★ (top right) | Character sheet, private dice, share code, export |
| ☰ (top left) | Back to the lobby |
| Party bar | Everyone's HP, live — red border means down at 0 |
| HUD (above the message box) | Your HP, AC, conditions and the last four rolls — tap it for the full sheet |
| ▾ (right of the HUD) | Collapse the HUD to just your name and HP; the choice sticks |
| Enter | Sends on a keyboard; on a phone it's a newline, use the ➤ button |

Private rolls in the drawer are yours alone — the DM never sees them.

---

## Bringing a campaign you already run

If you have been running a game by hand, the notes and art come with you. Open the
character sheet drawer, find **Campaign library**, and point **Import a folder of your
own material** at the folder you keep it in. It sorts the folder out itself:

- **Images** (`.png`, `.jpg`, `.webp`, `.gif`) become campaign handouts, captioned from
  their file names — `NPC_Aria_Venn.png` becomes *Aria Venn*. Up to 60 per campaign.
- **Documents** (`.md`, `.txt`, `.html`) become notes the DM can search. Up to 40, and
  4MB each.
- Everything else is ignored, and the count of what was skipped is reported rather than
  quietly dropped.

The documents are *not* pasted into the prompt — a single character sheet can be 100KB,
and a campaign's worth would crowd out the story. Instead the DM gets a third tool,
`search_lore`, and the list of documents it can reach. When a player names someone it
doesn't recognise, it looks them up and reads back the passage. You see it happen: the
feed shows *checked the notes for "…"* the same way it shows a die roll.

Matching is plain substring rather than word-based, deliberately — **Thai is written
without spaces between words**, so anything splitting on whitespace would find nothing in
a Thai document. Searching works the same in both languages the game speaks.

A self-contained HTML page keeps its content inside a `<script>`, so that is read too;
embedded base64 images are dropped first, since they are most of the bytes and none of
the meaning. Documents travel with **Export** and **Import**, so a campaign moves whole.

Remove a document any time from the same panel.

## How it works

The DM has two tools that run on every campaign, and both execute locally:

- **`roll_dice`** — the model never states a result it didn't roll. It sets the DC out
  loud first, then the number comes from Python's RNG, so it can't quietly decide you
  succeeded. Every roll shows in the feed: `Vess: Stealth vs DC 14 · 1d20+3 → [7]+3 = 10`.
- **`update_character`** — all damage, healing, XP, gold, items and conditions go through
  the sheet in code, named to a specific character. The result is fed back to the model,
  so it can't drift from your real HP. Level-ups roll a fresh hit die.

Two more appear only when there is something behind them — a tool with nothing behind
it is worse than no tool, because the model reaches for it anyway:

- **`search_lore`**, when a campaign has documents in its library. See **Bringing a
  campaign you already run** above.
- **`draw_scene`**, when an AI that can draw is configured. See **The DM illustrating
  on its own** above.

Everything that happens is appended to an event log with a sequence number, and the
browser tracks the last one it saw. When your phone sleeps, changes networks, or you
close the tab, it reconnects and replays only what it missed — the scene is never lost.

```
game/rules.py    dice, ability scores, character generation   (no I/O)
game/dm.py       system prompt, tools, the async turn loop
game/store.py    SQLite: campaigns, characters, event log
game/i18n.py     server-side strings: gear, DM language instruction, CLI
game/media.py    image validation, EXIF stripping, URL guards, the file store
game/providers.py  the AI backends, format translation, and failover
server.py        FastAPI: auth, campaigns, SSE stream, actions
static/i18n.js   interface strings for the browser
static/          the rest of the UI — no build step
dnd.py           terminal client, same DM
tests/           pytest, driven by a stub DM — no API key, no model call
```

## Running the tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

They need no API key and never reach a model: a stub backend stands in for the AI and
records what it was handed, which is how the tests can assert that a player's own notes
and the right party state genuinely arrived in the prompt rather than merely being saved
somewhere.

Sheet changes travel as structured data (`{"t": "hp", "from": 8, "to": 5}`) rather than
a pre-written English sentence, so each browser renders them in its own language.

## Tweaking it

- **`SYSTEM` in `game/dm.py`** — the DM's voice, pacing, and house rules. Want grimdark
  horror, or comedy, or much harsher DCs? This string is the whole personality.
- **`CLASSES` / `RACES` in `game/rules.py`** — add your own, with hit dice and gear.
- **Adding a language** — three places: `LANGUAGES`, `NARRATION_INSTRUCTION`, `GEAR`,
  `NAMES` and `CLI` in `game/i18n.py`; a block in `STRINGS` in `static/i18n.js`; and a
  `:root[data-lang="xx"]` font/leading block in `static/style.css` if the script needs
  one. Nothing else knows about languages.
- **`output_config={"effort": ...}` in `game/providers.py`** — `"low"` for faster,
  cheaper Claude turns; `"high"` for a sharper DM.
- **Adding an AI provider** — subclass `Backend` in `game/providers.py` with an
  `available()` and a `stream()` that yields text deltas then one message in Anthropic
  block format, and add it to `_build()`. Campaign history is stored in that one format,
  so nothing else needs to know.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Every backend in the suite is a stub — a DM that says exactly what the test scripted,
an artist that hands back four pixels of PNG — so the tests need no API key, spend
nothing, and give the same answer on a machine with keys and a machine without. They
run against a throwaway database and media folder, never your `campaign.db`.

## Cost

Free options first: **Gemini** and **Groq** free tiers cost nothing (daily limits), and
**Ollama** costs nothing at all, ever. Paid, roughly a cent or two per turn at Opus 5 pricing. The system prompt is cached, and the
campaign history grows over a long session, so a multi-hour campaign runs to a few
dollars. Switching the campaign to Claude Sonnet 5, or to one of the OpenRouter models,
cuts that substantially — the AI button is as much a spending control as a fallback.

## Terminal client

```bash
python dnd.py
```

Solo only, saved to `saves/<name>.json`, with `/sheet`, `/roll`, `/save`, `/recap` and
`/quit`. It asks for a language first and then runs entirely in it — prompts, character
sheet, help text and all. Same DM, same dice, same rules as the web app.
