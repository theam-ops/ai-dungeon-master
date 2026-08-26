"""Running the table on a Claude Pro/Max subscription instead of API credit.

Every other backend in `providers.py` talks to a hosted endpoint with a key. This one
talks to the copy of Claude Code already installed on the machine running this server,
through the Claude Agent SDK. Claude Code authenticates the way it always does - the
person who installed it is signed in - so a Pro or Max subscription pays for the turn
and no API key is involved.

That is the only sanctioned route from a subscription to a model. There is no OAuth
scope a third-party web app can request to spend somebody's Claude subscription, so
this backend is deliberately host-only: it runs on the machine hosting the game, on
that machine owner's subscription. Friends who join over a tunnel play on the host's
DM, exactly as they already do for every other backend.

Because Claude Code drives its own tool loop, `roll_dice` and `update_character` are
exposed to it as an in-process MCP server whose handlers call straight into `dm.run_tool`.
The dice are still rolled in Python; nothing about the rules moves into the model.

Requires:
    pip install claude-agent-sdk      and Claude Code installed and signed in.
"""

import asyncio
import json
import os
import re
import shutil
import subprocess
import time

from . import providers

try:
    from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ResultMessage,
                                  StreamEvent, TextBlock, ToolUseBlock,
                                  create_sdk_mcp_server, query, tool)
    SDK_ERROR = ""
except ImportError as e:                                   # pragma: no cover
    SDK_ERROR = f"claude-agent-sdk is not installed ({e})"

MCP_SERVER = "dnd"
MAX_TRANSCRIPT_TURNS = int(os.environ.get("CLAUDE_CODE_TRANSCRIPT_TURNS", "40"))


def cli_path():
    """Where Claude Code is, or "" if this machine doesn't have it."""
    return os.environ.get("CLAUDE_CODE_PATH") or shutil.which("claude") or ""


# --------------------------------------------------------------------------- #
# signing in
#
# There is no way for a web app to sign somebody into Claude itself - the OAuth client
# belongs to Claude Code. What the app can do is drive Claude Code's own `auth login` on
# the machine it is running on and show the player the URL it prints, which is Anthropic's
# real authorisation page. Nothing here handles a password or a token; the code the player
# pastes back goes straight to the same process that asked for it.
# --------------------------------------------------------------------------- #

URL_RE = re.compile(r"https://\S*claude\.com/\S+")
LOGIN_TIMEOUT = 300


def auth_status():
    """What Claude Code says about its own sign-in. Cheap - no model call."""
    cli = cli_path()
    if not cli:
        return {"installed": False, "logged_in": False, "method": ""}
    try:
        out = subprocess.run([cli, "auth", "status", "--json"], capture_output=True,
                             text=True, timeout=20).stdout
        data = json.loads(out or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"installed": True, "logged_in": False, "method": ""}
    return {"installed": True, "logged_in": bool(data.get("loggedIn")),
            "method": data.get("authMethod") or ""}


# Claude Code will happily authenticate with an API key from the environment. That is a
# perfectly good way to run Claude Code - it just isn't what this backend is for.
API_KEY_METHODS = ("api_key", "apikey", "bedrock", "vertex")

# `claude auth status` answers "is there a session on disk", not "does it still work".
# An expired refresh token reports loggedIn:true right up until a turn tries to use it
# and gets a 401, so the only honest signal is a turn that actually failed that way.
# Remembered here so the picker stops advertising the backend after the first one.
AUTH_ERROR_MARKS = ("oauth", "401", "authenticat", "expired", "re-authenticate")
_token_rejected = False


def note_auth_failure(message):
    """Called when a turn fails in a way only re-signing-in can fix."""
    global _token_rejected
    low = (message or "").lower()
    if any(m in low for m in AUTH_ERROR_MARKS):
        _token_rejected = True
    return _token_rejected


def clear_auth_failure():
    """A fresh sign-in, or a turn that worked, means the session is good again."""
    global _token_rejected
    _token_rejected = False
    ClaudeCodeBackend._auth_cache = (0.0, False)


def is_subscription(state):
    """Signed in, and by a route a Pro/Max subscription actually pays for."""
    return bool(state.get("logged_in")) and state.get("method", "") not in API_KEY_METHODS


class LoginFlow:
    """One sign-in at a time: start it, hand back the URL, feed the code back in."""

    def __init__(self):
        self.process = None
        self.url = ""

    async def start(self):
        await self.cancel()
        cli = cli_path()
        if not cli:
            raise RuntimeError("Claude Code is not installed on this machine")

        self.process = await asyncio.create_subprocess_exec(
            cli, "auth", "login", "--claudeai",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT)

        # it prints a line or two, then blocks waiting for the code
        try:
            self.url = await asyncio.wait_for(self._read_url(), timeout=45)
        except asyncio.TimeoutError:
            await self.cancel()
            raise RuntimeError("Claude Code didn't offer a sign-in link")
        return self.url

    async def _read_url(self):
        seen = []
        while True:
            chunk = await self.process.stdout.read(400)
            if not chunk:
                raise RuntimeError("".join(seen)[-300:] or "sign-in ended early")
            seen.append(chunk.decode("utf-8", errors="replace"))
            found = URL_RE.search("".join(seen))
            if found:
                return found.group(0)

    async def submit(self, code):
        """Hand the pasted code to the waiting process and report how it went."""
        if not self.process or self.process.returncode is not None:
            return False, "that sign-in is no longer running - start it again"
        try:
            self.process.stdin.write((code.strip() + chr(10)).encode())
            await self.process.stdin.drain()
            rest = await asyncio.wait_for(self.process.stdout.read(), timeout=90)
        except (OSError, asyncio.TimeoutError) as e:
            await self.cancel()
            return False, f"the sign-in didn't finish ({type(e).__name__})"
        finally:
            self.url = ""

        await self.cancel()
        if auth_status()["logged_in"]:
            clear_auth_failure()
            return True, ""
        tail = rest.decode("utf-8", errors="replace").strip().splitlines()
        return False, (tail[-1] if tail else "Claude Code did not accept that code")

    async def cancel(self):
        if self.process and self.process.returncode is None:
            try:
                self.process.kill()
                await self.process.wait()
            except OSError:
                pass
        self.process, self.url = None, ""


LOGIN = LoginFlow()


def render_transcript(history, limit=MAX_TRANSCRIPT_TURNS):
    """The campaign so far, as plain text Claude Code can be handed in one prompt.

    Claude Code owns its own conversation state, but this game's canonical record is
    the `history` list - that is what gets exported, and what another backend reads if
    Claude runs out mid-campaign. So each turn is sent stateless, with the story so far
    folded into the prompt, rather than resuming a Claude Code session and letting the
    two records drift apart.
    """
    lines = []
    for msg in history[-limit:]:
        content = msg.get("content")
        if msg["role"] == "user":
            if isinstance(content, str):
                lines.append(content)
            else:
                for block in content or []:
                    if block.get("type") == "tool_result":
                        lines.append(f"[rules] {block.get('content', '')}")
            continue
        for block in content or []:
            if block.get("type") == "text" and block.get("text", "").strip():
                lines.append("DM: " + block["text"].strip())
            elif block.get("type") == "tool_use":
                lines.append(f"[DM called {block.get('name')} "
                             f"with {block.get('input')}]")
    return "\n\n".join(lines)


class ClaudeCodeBackend(providers.Backend):
    """Claude Opus 5, billed to the host's Claude subscription via Claude Code."""

    kind = "claude-code"
    vision = False          # images reach Claude Code as files, not inline blocks
    free = False            # not free - it spends a subscription rather than credit
    key_url = "https://claude.com/download"
    key_env = ""

    _auth_cache = (0.0, False)

    def available(self):
        """Installed, signed in, and signed in *on a subscription*.

        Three traps, all of which make this backend claim to be ready when it isn't:

        Not signed in - a Claude Code that was never signed in still looks installed, so
        this would advertise itself and then fail over on every single turn.

        Expired token - worse, because `auth status` cannot see it: a lapsed session
        still reports `loggedIn: true` with a real email and plan attached, and only a
        turn discovers the 401. `note_auth_failure` records that, and this stops
        advertising until someone signs in again.

        API-key auth - if ANTHROPIC_API_KEY is set in this server's environment, Claude
        Code reports `loggedIn: true` with `authMethod: api_key` and bills that key.
        This backend exists precisely so a subscription pays instead of credit, so
        claiming to be "Claude Pro/Max" while quietly spending an API key would be
        worse than being unavailable.

        `auth status` shells out, so the answer is cached briefly - the AI picker asks
        this of every backend it lists.
        """
        if not cli_path() or SDK_ERROR or _token_rejected:
            return False
        cls = ClaudeCodeBackend
        now = time.monotonic()
        when, ok = cls._auth_cache
        if now - when < 15:
            return ok
        ok = is_subscription(auth_status())
        cls._auth_cache = (now, ok)
        return ok

    def env_name(self):
        return ""

    async def verify(self):
        """Is this usable? Claude Code answers that itself, without spending a turn."""
        if SDK_ERROR:
            return False, "run: pip install claude-agent-sdk"
        state = auth_status()
        if not state["installed"]:
            return False, "Claude Code is not installed on this machine"
        if not state["logged_in"]:
            return False, "Claude Code is installed but not signed in"
        if _token_rejected:
            return False, ("Claude Code's sign-in has expired - a turn came back 401. "
                           "Sign in again to use it")
        if not is_subscription(state):
            return False, ("Claude Code is signed in with an API key, not a Claude "
                           "subscription - turns would be billed to that key. Unset "
                           "ANTHROPIC_API_KEY for this server, or pick a Claude "
                           "backend instead")
        return True, ""

    def _options(self, system, mcp, cid=None):
        """Claude Code stripped down to a DM: no file access, no shell, no settings.

        `tools=[]` removes every built-in tool, so the only things the model can call
        are this game's two. `setting_sources=None` keeps the host's CLAUDE.md, hooks
        and MCP servers out of the campaign.
        """
        from . import dm
        allowed = [f"mcp__{MCP_SERVER}__{spec['name']}" for spec in dm.tools_for(cid)]
        return ClaudeAgentOptions(
            model=self.model,
            system_prompt=system,
            tools=[],
            allowed_tools=allowed if mcp else [],
            mcp_servers={MCP_SERVER: mcp} if mcp else {},
            strict_mcp_config=True,
            setting_sources=None,
            permission_mode="bypassPermissions",
            include_partial_messages=True,
            max_turns=providers_max_turns(),
        )

    def _tool_server(self, characters, lang, events, cid=None):
        """This campaign's tools, wired to the same `dm.run_tool` every backend uses."""
        from . import dm

        def run(name):
            async def handler(args):
                out, event = dm.run_tool(name, dict(args or {}), characters, lang, cid)
                if event:
                    events.put_nowait(event)
                return {"content": [{"type": "text", "text": out}]}
            return handler

        built = [tool(spec["name"], spec["description"], spec["input_schema"])(run(spec["name"]))
                 for spec in dm.tools_for(cid)]
        return create_sdk_mcp_server(MCP_SERVER, "1.0.0", built)

    async def run_turn(self, system_blocks, history, characters, lang, images=None,
                       cid=None):
        """One DM turn, start to finish, yielding the same events as `dm._run`.

        Claude Code runs its own tool loop, so unlike `Backend.stream` this covers the
        whole turn rather than one round of it. `history` is appended to here so the
        campaign record stays in the same shape every other backend writes.
        """
        if SDK_ERROR:
            raise providers.ProviderFailed(f"Claude Code: {SDK_ERROR}")

        system = "\n\n".join(b["text"] for b in system_blocks if b.get("text"))
        prompt = render_transcript(history)
        events = asyncio.Queue()
        content, text_parts, said_anything = [], [], False

        failure = None
        try:
            # Never break out of the SDK's generator early: abandoning it mid-run leaves
            # it un-closable. Note the failure, keep reading, and raise once it is done.
            async for message in query(
                    prompt=prompt,
                    options=self._options(
                        system, self._tool_server(characters, lang, events, cid), cid)):
                while not events.empty():
                    yield events.get_nowait()

                if isinstance(message, StreamEvent):
                    ev = message.event or {}
                    if (ev.get("type") == "content_block_delta"
                            and (ev.get("delta") or {}).get("type") == "text_delta"):
                        yield {"kind": "delta", "text": ev["delta"]["text"]}

                elif isinstance(message, AssistantMessage):
                    if message.error:
                        # auth failures and usage limits arrive as a synthetic turn
                        # rather than an exception; the text is the only explanation
                        failure = failure or (_text_of(message) or str(message.error))
                        continue
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            if block.text.strip():
                                text_parts.append(block.text)
                                said_anything = True
                            content.append({"type": "text", "text": block.text})
                        elif isinstance(block, ToolUseBlock):
                            content.append({"type": "tool_use", "id": block.id,
                                            "name": block.name.split("__")[-1],
                                            "input": block.input})

                elif isinstance(message, ResultMessage) and message.is_error:
                    failure = failure or _why(message)
        except Exception as e:
            failure = failure or str(e) or type(e).__name__

        while not events.empty():
            yield events.get_nowait()

        # exhausted rather than failed: a subscription that has hit its limit is exactly
        # the exhausted case, and failover moves on to the next AI either way
        if failure:
            # a 401 is not a rate limit - it will fail identically until someone
            # signs in again, so stop offering this backend rather than failing
            # over on every turn from here on
            note_auth_failure(failure)
            raise providers.ProviderExhausted(f"Claude Code: {failure}")
        if not said_anything:
            raise providers.ProviderFailed("Claude Code: empty response")

        clear_auth_failure()          # a turn went through, so the session is good
        history.append({"role": "assistant", "content": content})
        yield {"kind": "narration", "text": "\n\n".join(text_parts)}


def _text_of(message):
    return " ".join(b.text.strip() for b in message.content
                    if isinstance(b, TextBlock) and b.text.strip())


def _why(result):
    """The most human-readable reason a run ended badly."""
    return (result.result or "").strip() or result.subtype or "failed"


def providers_max_turns():
    from . import dm
    return dm.MAX_TOOL_ROUNDS


def install():
    """Offer the subscription DM ahead of the API-key ones, when it's usable.

    Runs on import rather than being called from `providers`, so it works whichever
    of the two modules Python loads first - importing this one first would otherwise
    leave `providers` holding a half-built module.
    """
    if "claude-code" in providers.BY_ID:
        return providers.BY_ID["claude-code"]
    backend = ClaudeCodeBackend("claude-code", "Claude Pro/Max (this machine)",
                                os.environ.get("CLAUDE_CODE_MODEL", "claude-opus-5"))
    providers.BACKENDS.insert(0, backend)
    providers.BY_ID[backend.id] = backend
    return backend


install()
