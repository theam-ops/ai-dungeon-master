# "Login with Claude / GPT / Gemini" — what is actually possible

Researched 2026-08-25. Everything below is desk research against vendor documentation and
public issue trackers. **Nothing here was executed**: this machine has no Claude Code
sign-in, no ChatGPT account, no Google account, and no API keys of any kind. Where a claim
could only be settled by running the tool, it is marked *unverified* rather than guessed.

## The question, stated honestly

The ask was three buttons — *Login with Claude*, *Login with GPT*, *Login with Gemini* —
that let a player pay for their own turns out of a subscription they already have.

**That product does not exist and cannot be built.** Not for any of the three. There is no
OAuth scope, on any of these vendors, that a third-party web application can request in
order to spend a stranger's Claude Pro, ChatGPT Plus, or Google AI Pro subscription. The
subscription buys a seat in *the vendor's own first-party clients*; the OAuth clients that
back those clients are the vendor's, and asking for a token as if you were one of them is
impersonating a first-party client. It breaks, it gets accounts suspended, and it is
against every one of these vendors' terms.

What *is* legitimate is the pattern this repo already implements for Claude, in
`game/claude_code.py`: **drive the vendor's own CLI, already installed and already signed
in, on the machine running the server, on that machine owner's subscription.** One person —
whoever is sitting at the host machine — decides which account pays, and everyone at the
table is served by that host's DM. `require_host` in `server.py:301` restricts the sign-in
endpoints to loopback for exactly this reason.

So the real question is not "can we add a login button" but: **can `gemini` and `codex` be
driven the way `claude` is?** That is what the rest of this document answers.

## The bar a backend has to clear for this game

This game is unusual in what it needs from a backend, and the bar rules things out quickly:

| # | Requirement | Why it is non-negotiable |
|---|---|---|
| 1 | **Tool / function calling** | Every die is rolled in `game/rules.py` through the `roll_dice` tool. A backend that cannot call tools cannot be a DM — it would have to invent results, which is the one thing this design exists to prevent. |
| 2 | **Our tools, not its own** | The tools must be `roll_dice` / `update_character` / `search_lore` bound to *this campaign's* character list. A generic coding agent's `shell` and `read_file` are worse than useless here. |
| 3 | **Tool calls observable to us** | `dm.run_tool` returns an event that becomes a dice chip in the feed. If the harness cannot see which tools were called with what arguments, the feed goes silent. |
| 4 | **Full system-prompt replacement** | `dm.SYSTEM` is the DM's entire personality. A coding agent's built-in prompt would fight it. |
| 5 | **Subscription-backed auth** | Otherwise there is no point — `providers.py` already has API-key paths for both vendors (`GeminiBackend`, `OpenRouterBackend`). |
| 6 | Streaming text deltas | Nice to have, not fatal. `dm._run` yields `{"kind": "delta"}` so prose appears as it is written; without it the table stares at a spinner for 30s. `claude_code.py` gets these via `include_partial_messages`. |
| 7 | Vision | Already sacrificed for Claude Code (`vision = False`), so not a blocker. |

## Google `gemini` CLI

**Verdict: technically viable, with real caveats. The one of the three worth building.**

### Tool calling — yes

Gemini CLI is a full agent with a tool loop, and it consumes external MCP servers over
stdio or HTTP, configured under `mcpServers` in `settings.json`
([MCP servers with Gemini CLI](https://geminicli.com/docs/tools/mcp-server/)). Each entry
takes `command`, `args`, `env`, `cwd`, `trust`, `includeTools`, `excludeTools`.

Requirement 1 and 2 are met, and the containment story is good:

- `tools.core: []` restricts the built-in toolset by allowlist — this is how you take the
  shell and the filesystem away from it.
- `mcp.allowed: ["dnd"]` connects to our server and nothing else.
- `trust: true` on our server bypasses the confirmation dialog, which headless mode needs.

([Gemini CLI configuration reference](https://geminicli.com/docs/reference/configuration/))

Settings merge across System > Workspace > User, and `.gemini/settings.json` in the working
directory is the workspace level — so the server can write a throwaway directory per turn
with exactly the config it wants, rather than mutating the host's global settings.
([enterprise / settings precedence](https://geminicli.com/docs/cli/enterprise/))

### Observability — yes, and better than Codex

`--output-format stream-json` emits newline-delimited JSON with `init`, `message` (with
`delta: true` on incremental chunks), `tool_use` (name, id, arguments), `tool_result`,
`error`, and `result` events
([headless mode reference](https://geminicli.com/docs/cli/headless/); landed in
[PR #10883](https://github.com/google-gemini/gemini-cli/pull/10883), originally
[issue #8203](https://github.com/google-gemini/gemini-cli/issues/8203)).

That satisfies requirement 3 **and** requirement 6 — this is the only one of the two CLIs
that gives us streaming text deltas, which matters a lot for a game where a turn is
200 words of prose.

### System prompt — yes, cleanly

`GEMINI_SYSTEM_MD=/path/to/SYSTEM.md` replaces the built-in system prompt entirely — a full
replacement, not a merge ([system prompt override](https://geminicli.com/docs/cli/system-prompt/)).
`dm.SYSTEM` plus the language block plus the lore manifest would be written to that file per
turn. Requirement 4 met.

### Authentication — yes for the subscription, but the login cannot be a button

"Sign in with Google" covers individual Google accounts including the free Gemini Code
Assist tier and paid **Google AI Pro / Ultra**; most individual accounts need no Cloud
project ([authentication](https://geminicli.com/docs/get-started/authentication/)). Free tier
is documented at 60 req/min and 1,000 req/day
([quotas and pricing](https://geminicli.com/docs/resources/quota-and-pricing/)). For a
D&D session at one request per player turn plus a few tool rounds, 1,000/day is generous.

**But the sign-in cannot be driven from the web page the way Claude's is.** The Claude flow
works because `claude auth login --claudeai` prints the authorisation URL to stdout and then
blocks reading a code from stdin — see `claude_code.LoginFlow`, which scrapes the URL with a
regex and writes the code back. Gemini CLI has no equivalent contract:

- The docs state plainly there is **no no-browser option**; it launches a browser and
  expects a loopback callback on the same machine.
- Headless login is a live, unresolved sore spot:
  [#1696](https://github.com/google-gemini/gemini-cli/issues/1696),
  [#13853](https://github.com/google-gemini/gemini-cli/issues/13853) (v0.18.0 regression:
  hangs with no URL fallback),
  [#27300](https://github.com/google-gemini/gemini-cli/issues/27300).

Since our flow is host-only anyway, and the host *does* have a browser, this is survivable —
but the honest UX is **"run `gemini` once in a terminal and sign in"**, then the app detects
the cached credential and lights the row up. Not a *Sign in with Google* button in the app.
Building the button would mean depending on undocumented stdout of a flow that has already
regressed once.

### Cost of implementing it

Roughly a day, and structurally more work than Claude Code was, because the SDK convenience
is missing:

1. **A standalone MCP server module** (~80 lines). Claude Code got `create_sdk_mcp_server`,
   an *in-process* server whose handlers close over `characters` and `cid` directly.
   Gemini needs a real subprocess speaking stdio JSON-RPC. So: `game/dnd_mcp.py`, launched
   as `python -m game.dnd_mcp`, which must reach the campaign state *somehow* — either by
   opening `store.py` against the same SQLite file (simplest; the DB is already the source
   of truth) or over a loopback HTTP callback into the running server. The SQLite route is
   preferable but means `dm.run_tool`'s in-memory `characters` list and the child process's
   view have to be reconciled at the end of the turn.
2. **A per-turn scratch directory** with `.gemini/settings.json` and `SYSTEM.md`, plus
   `--skip`-equivalent care that the CLI does not wander into the repo. Gemini CLI takes its
   workspace from cwd, so point it at a temp dir, not at the game folder.
3. **A `run_turn` implementation** on a new backend class, parsing the JSONL stream and
   yielding the same `{"kind": "delta"|"narration"|"dice"|"sheet"}` events `dm._run` does —
   `claude_code.ClaudeCodeBackend.run_turn` is the template.
4. **Availability detection** that distinguishes *installed* from *signed in* from *signed
   in with an API key rather than a subscription* — the same three-way trap
   `claude_code.available()` documents. Unverified whether `gemini` exposes an equivalent of
   `claude auth status --json`; if it does not, this has to be inferred from the credential
   cache file, which is fragile.

### What could not be verified without credentials

- Whether `--output-format stream-json` actually emits `tool_use` for **MCP** tools
  specifically, as opposed to only built-in tools. The docs describe the event generically.
  This is the single load-bearing unknown; if it is false, the whole thing collapses to
  requirement 3.
- Whether `tools.core: []` is accepted as "no built-in tools" rather than treated as unset.
- Whether the process spawn cost per turn (Node startup + MCP handshake) is acceptable —
  guessing 1–3s, on top of model latency.
- Whether the free tier's request accounting counts tool rounds as separate requests. If it
  does, 1,000/day is closer to ~100 turns than 1,000.
- Whether a subscription-vs-API-key distinction is even detectable, which matters because
  `GEMINI_API_KEY` being set in the server's environment would silently make the
  "subscription" backend bill the key — exactly the trap `claude_code.py` guards against,
  and this server *is* likely to have `GEMINI_API_KEY` set, since that is the README's
  recommended free path.

That last point is worth dwelling on. This is the one project where the API-key confusion is
near-certain rather than hypothetical.

## OpenAI `codex` CLI

**Verdict: not worth building. Tool calling works; the pieces around it do not.**

### Tool calling — yes

Codex consumes MCP servers from `[mcp_servers]` in `~/.codex/config.toml`, stdio or
streamable HTTP, and `codex mcp add <NAME> -- <COMMAND> [ARGS...]` registers one
([Codex MCP docs](https://developers.openai.com/codex/mcp)). Per-invocation overrides are
available via dotted `-c` keys (e.g. `-c 'mcp_servers.dnd.enabled=false'`), and
`CODEX_HOME` relocates the config directory — so a per-turn config is achievable without
trampling the host's.

### Observability — partial, and the missing part is the one that matters

`codex exec --json` emits JSONL with `thread.started`, `turn.started`, `turn.completed`,
`turn.failed`, `error`, and `item.*` events. Item types include `agent_message`,
`reasoning`, `command_execution`, `file_change`, **`mcp_tool_call`**, `web_search`,
`todo_list` ([non-interactive mode](https://developers.openai.com/codex/noninteractive),
[event cheatsheet](https://takopi.dev/reference/runners/codex/exec-json-cheatsheet/)).

So requirement 3 is met — `mcp_tool_call` is right there.

**Requirement 6 is not.** `agent_message` arrives **only as `item.completed`**, carrying the
whole text at once. There is no delta stream. In this game that means the DM's 200-word
narration lands as a single block after the entire turn finishes — no typing effect, just a
long silence and then a wall of text. Every other backend in `providers.py` streams. This is
not fatal but it is a visibly worse table.

There is also a documented bug where `--json` and `--output-schema` are "silently ignored
when tools/MCP servers are active"
([openai/codex#15451](https://github.com/openai/codex/issues/15451)) — which, if it affects
the event stream and not just schema-constrained output, would take requirement 3 with it.
Unverified.

### System prompt — this is where it falls down

Codex has no documented equivalent of `GEMINI_SYSTEM_MD` or the Agent SDK's
`system_prompt=`. Instructions come from `AGENTS.md` files discovered on disk and from the
prompt itself, layered *on top of* Codex's own coding-agent system prompt. There is no
supported "you are not a coding assistant, you are a Dungeon Master, forget everything else"
switch.

That is requirement 4 unmet, and it is not cosmetic. The whole `dm.SYSTEM` block —
"120-250 words per turn", "never narrate another player's character", "call roll_dice for
EVERY die" — would be competing with a system prompt that is telling the model it is a
software engineer working in a git repository. `codex exec` even refuses to run outside a
git repo by default (`--skip-git-repo-check` exists for a reason).

### Authentication — the terms say no

`codex login` signs in with a ChatGPT account across ChatGPT plans, storing tokens at
`~/.codex/auth.json`. `codex login --device-auth` covers machines without a browser, though
it requires "device code authorization" to be enabled in ChatGPT security settings and, on a
workspace account, an admin to enable it
([#9253](https://github.com/openai/codex/issues/9253),
[#3820](https://github.com/openai/codex/issues/3820)).

But the [authentication docs](https://developers.openai.com/codex/auth) are explicit about
the automation case, and it points the other way:

> Use API key authentication for programmatic Codex CLI workflows, such as CI/CD jobs.
> Don't expose Codex execution in untrusted or public environments.

A game server that accepts turns from friends over a Cloudflare tunnel and turns them into
`codex exec` invocations is *precisely* "exposing Codex execution in an untrusted
environment", and it is programmatic use, for which the vendor's own instruction is to use
an API key — which is the thing this exercise was trying to avoid. The docs also warn that
`auth.json` should be treated like a password and never shared.

Note that this is a genuine difference from Claude Code, not the same situation with
different wording. Anthropic's documented position, reflected in this repo's README, is that
driving your own installed Claude Code on your own machine is ordinary use of the
subscription. OpenAI's documented position for the equivalent scenario is "use an API key".

### Verdict

Even setting the terms aside, the engineering ledger is: no system-prompt replacement, no
streaming deltas, a git-repo requirement, a coding-agent persona fighting the DM prompt, and
a known `--json`-with-MCP bug. Against that, the only thing gained over the existing
`OpenRouterBackend` (which already reaches GPT-class models, streams properly, and takes a
clean system prompt) is not paying per token.

**Not worth building.** If someone wants GPT to run the table, `OPENROUTER_API_KEY` already
does it, better.

## Anthropic `claude` — already done, and the reasoning still holds

`game/claude_code.py` is the reference implementation and does not need changing. Worth
recording *why* it is the strongest of the three, so the comparison is not just assertion:

- **In-process MCP.** `create_sdk_mcp_server` lets the tool handlers close over the live
  `characters` list — no subprocess, no second view of the database, no reconciliation.
  Neither `gemini` nor `codex` offers this; both would need a real child process.
- **`system_prompt=` on the options object.** Full replacement, no file on disk, no
  competing built-in persona.
- **`tools=[]`** removes every built-in tool, so the model genuinely has only this game's
  tools. `setting_sources=None` keeps the host's `CLAUDE.md` and MCP servers out.
- **`include_partial_messages=True`** gives real text deltas.
- **`claude auth status --json`** answers "installed / signed in / signed in *how*" without
  spending a turn, which is what lets the AI picker mark a row **sign in needed** honestly
  instead of failing on the first turn.
- **A scriptable login.** `claude auth login --claudeai` prints the URL and reads the code
  from stdin, which is the only reason the in-app sign-in button can exist at all.

## Summary

| | Tool calling | Our tools only | Tool calls visible | System prompt replaceable | Text deltas | Subscription auth | In-app login | Build it? |
|---|---|---|---|---|---|---|---|---|
| **Claude Code** | yes | yes (in-process MCP) | yes | yes | yes | yes | yes | **done** |
| **`gemini` CLI** | yes (MCP, stdio) | yes (`tools.core: []`) | likely* | yes (`GEMINI_SYSTEM_MD`) | yes (`stream-json`) | yes (AI Pro / free tier) | **no** — run `gemini` once by hand | maybe, ~1 day |
| **`codex` CLI** | yes (MCP) | yes | yes (`mcp_tool_call`)† | **no** | **no** | yes, but docs say use an API key for this | device-auth only | **no** |

\* unverified that MCP tool calls specifically surface in the stream, as opposed to built-ins.
† subject to [#15451](https://github.com/openai/codex/issues/15451).

**And for the third time, plainly: there is no "Login with Gemini" or "Login with GPT"
button that spends a remote player's subscription. There is no such OAuth scope. The only
honest shape is the one already shipped — the host's own installed CLI, on the host's own
subscription, restricted to the host's own machine.**

## Recommendation

1. **Do not build a Codex backend.** `OpenRouterBackend` already covers GPT better.
2. **Gemini CLI is defensible but not urgent.** The gap it closes is narrow: this server
   very likely already has `GEMINI_API_KEY` set, because that is the README's headline free
   path, and the free API tier already costs nothing. The subscription backend would buy a
   higher daily quota (1,000 vs 250 requests/day) and nothing else — and it introduces the
   API-key-masquerading-as-subscription trap in a project where `GEMINI_API_KEY` is
   *expected* to be present.
3. **Before writing any of it**, verify the one load-bearing unknown on a machine with a
   signed-in `gemini`: does `--output-format stream-json` emit `tool_use` / `tool_result`
   events for an **MCP** tool? Ten minutes with a hello-world MCP server settles it. If the
   answer is no, stop.
4. **Fix the README's framing either way.** It currently reads as though Claude is special
   because Anthropic is generous. Claude is special because Claude Code ships an SDK with
   in-process MCP, full system-prompt replacement, partial-message streaming, and a
   machine-readable `auth status`. That is an engineering fact, and it is the reason the
   other two are harder — worth saying, so the next person does not assume it is a licensing
   accident.

## Sources

- [Gemini CLI — headless mode reference](https://geminicli.com/docs/cli/headless/)
- [Gemini CLI — MCP servers](https://geminicli.com/docs/tools/mcp-server/)
- [Gemini CLI — configuration reference](https://geminicli.com/docs/reference/configuration/)
- [Gemini CLI — system prompt override](https://geminicli.com/docs/cli/system-prompt/)
- [Gemini CLI — authentication](https://geminicli.com/docs/get-started/authentication/)
- [Gemini CLI — quotas and pricing](https://geminicli.com/docs/resources/quota-and-pricing/)
- [Gemini CLI — enterprise / settings precedence](https://geminicli.com/docs/cli/enterprise/)
- [google-gemini/gemini-cli PR #10883 — add `stream-json` output format](https://github.com/google-gemini/gemini-cli/pull/10883)
- [google-gemini/gemini-cli issue #8203 — add `stream-json` output format](https://github.com/google-gemini/gemini-cli/issues/8203)
- [google-gemini/gemini-cli issue #1696 — auth fails on headless](https://github.com/google-gemini/gemini-cli/issues/1696)
- [google-gemini/gemini-cli issue #13853 — v0.18.0 login hangs headless](https://github.com/google-gemini/gemini-cli/issues/13853)
- [google-gemini/gemini-cli issue #27300 — OAuth fails in SSH/headless](https://github.com/google-gemini/gemini-cli/issues/27300)
- [Codex — non-interactive mode (`codex exec`)](https://developers.openai.com/codex/noninteractive)
- [Codex — Model Context Protocol](https://developers.openai.com/codex/mcp)
- [Codex — authentication](https://developers.openai.com/codex/auth)
- [openai/codex issue #15451 — `--json` ignored when MCP servers active](https://github.com/openai/codex/issues/15451)
- [openai/codex issue #9253 — headless login needs device code enabled by admin](https://github.com/openai/codex/issues/9253)
- [openai/codex issue #3820 — headless/CLI auth for ChatGPT plans](https://github.com/openai/codex/issues/3820)
- [codex exec `--json` event cheatsheet](https://takopi.dev/reference/runners/codex/exec-json-cheatsheet/)
