# AI Dungeon Master

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Claude](https://img.shields.io/badge/Claude-D97757?logo=anthropic&logoColor=white)](https://console.anthropic.com/)
[![Gemini](https://img.shields.io/badge/Gemini-free%20tier-4285F4?logo=googlegemini&logoColor=white)](https://aistudio.google.com/apikey)
[![Ollama](https://img.shields.io/badge/Ollama-runs%20offline-000000?logo=ollama&logoColor=white)](https://ollama.com/download)
[![Languages](https://img.shields.io/badge/languages-English%20%7C%20%E0%B9%84%E0%B8%97%E0%B8%A2-8a7220)](README.th.md)
[![Dice](https://img.shields.io/badge/dice-rolled%20in%20Python-7fb069)](game/rules.py)

> อ่านคู่มือภาษาไทยได้ที่ **[README.th.md](README.th.md)**

A D&D-style RPG where an AI runs the table. It narrates, voices every NPC and judges
the rules — but the dice, your HP and your XP run in Python, so nothing can be fudged.
Play solo or with friends, from a browser or your phone, in English or ไทย.

---

## 1. Start it

**Windows:** double-click **`Play.cmd`**. It installs anything missing the first time,
then opens your browser. Closing that window stops the game.

*Want it on your Desktop?* Open the `tools` folder and double-click
**`Create shortcut.cmd`** once. That puts a d20 icon named **AI Dungeon Master** on your
Desktop, and from then on you just double-click that. The shortcut remembers where this
folder is, so run it again if you ever move the folder.

**Anything else:**

```bash
pip install -r requirements.txt
python launch.py
```

## 2. Give it an AI

The game needs a model to be the DM. It will say so on the first screen and offer you
the links — you can paste a key straight into the app, no terminal needed.

| | How | Cost |
|---|---|---|
| **Gemini** | **aistudio.google.com/apikey** → *Create API key* → paste it in | Free, no card |
| **Claude Pro/Max** | Drawer → **Table** → *Sign in with Claude* | Included in your subscription |
| **Ollama** | Install from **ollama.com**, then `ollama pull llama3.2:3b` | Free, offline |

Claude is the best DM. Gemini is the quickest to start. Ollama never runs out.

If one runs out of credit mid-game, the next available one picks the turn up
automatically and says so in the story.

## 3. Play

Make a character, then **type what you do** in plain language:

```
I press my ear to the door and listen
"Who sent you?" — I keep my hand on the hilt
pour the oil across the floor and back away toward the stairs
```

The DM answers, rolls where the rules call for it, and updates your sheet. Every roll
is shown:

```
Vess: Stealth vs DC 14 · 1d20+3 → [7]+3 = 10
```

**Around the screen:**

- **★ top right** — the drawer: **You** (portrait, notes for the DM, private dice),
  **Art** (gallery, import your own material), **Table** (share code, export, which AI)
- **📎 beside the message box** — attach a picture; the DM can see it
- **HUD above the message box** — your HP, AC and last few rolls
- **☰ top left** — back to the lobby
- **How this works** in the lobby — a four-step guide, opens by itself the first time

## 4. Play with other people

Drawer → **Table** → **Share code**. Send the link; they pick a character and join the
same story. Up to six. Anyone can act at any time — no turn order.

**On your phone,** same wifi: open the address the launcher prints. From anywhere:

```bash
python launch.py --share --password "something-only-your-table-knows"
```

That gives you a public link for the evening. It refuses to run without a password —
a public link with none is an open tab on whatever is paying for the DM.

---

## Good to know

- **Your campaigns live on this PC** (`campaign.db`, `media/`) and are not in git. Use
  **Export** in the drawer to keep one — the `.zip` carries the pictures and notes too.
- **Already run a game by hand?** Drawer → **Art** → *Campaign library* — point it at
  your folder of notes and art and the DM can search them mid-game.
- **Terminal instead of a browser:** `python dnd.py`, or `/dm` inside Claude Code.

**More detail** — hosting it on the web, where keys are stored, how the DM is kept
honest, running the tests: **[docs/reference.md](docs/reference.md)**
([ไทย](docs/reference.th.md)).
