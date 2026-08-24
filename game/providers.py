"""Which AI is running the table.

Claude is the default DM. When its credit runs out (or it rate-limits, or the key is
missing), the turn retries on the next available backend instead of the game stalling.

Every backend speaks the same shape to the rest of the app: an async generator that
yields text deltas and finishes with a message in Anthropic's content-block format.
Campaign history is therefore always stored in one format, and switching AI mid-campaign
needs no migration - the OpenRouter backend translates on the way in and out.

Environment:
    ANTHROPIC_API_KEY    Claude (the default DM)
    OPENROUTER_API_KEY   one key, many models - https://openrouter.ai/keys
    OPENROUTER_MODELS    comma-separated model ids to offer (see DEFAULT_OR_MODELS)
    DM_BACKEND           id of the backend to start new campaigns on
"""

import base64
import json
import os
import re
import socket
import time
from urllib.parse import urlparse

import anthropic
import httpx

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Google and Groq both expose OpenAI-compatible endpoints, so one backend class covers
# all three. Both hand out a key on a free tier with no card, which makes them the
# easiest way to get this running without paying anything.
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Verified against a live free-tier key: these answer and call tools. The 2.x flash
# models are listed by the API but 404 for keys created now ("no longer available to
# new users"), and `gemini-flash-latest` floats onto whatever is current - handy, but
# it was returning 503 when this was checked, so it isn't the default.
DEFAULT_GEMINI_MODELS = [
    ("gemini-3.6-flash", "Gemini 3.6 Flash (free)"),
    ("gemini-3.5-flash", "Gemini 3.5 Flash (free)"),
]

DEFAULT_GROQ_MODELS = [
    ("llama-3.3-70b-versatile", "Llama 3.3 70B (free)"),
]

# Drawing is a different model family and, unlike the text tiers, is not free anywhere.
GEMINI_IMAGE_URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
                    "{model}:generateContent")
GEMINI_IMAGE_MODEL = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image")

# Ollama runs a model on this machine: no key, no account, nothing to run out of.
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")

# Small enough for 8GB of RAM without a dedicated GPU, and both support tool calling -
# which this game requires, since every die is rolled through a tool.
DEFAULT_OLLAMA_MODELS = [
    ("llama3.2:3b", "Llama 3.2 3B (local)"),
    ("qwen2.5:3b", "Qwen 2.5 3B (local)"),
]

# How much campaign history the local model can see. Ollama defaults to a very small
# window; without this the DM silently forgets earlier scenes.
OLLAMA_CONTEXT = int(os.environ.get("OLLAMA_CONTEXT", "8192"))

# Models must support tool calling - the DM rolls dice through a tool, and a model
# without tool support would have to invent its own results, which is the one thing
# this whole design exists to prevent.
DEFAULT_OR_MODELS = [
    ("openai/gpt-4o-mini", "GPT-4o mini"),
    ("google/gemini-2.0-flash-001", "Gemini 2.0 Flash"),
    ("deepseek/deepseek-chat", "DeepSeek"),
    ("meta-llama/llama-3.3-70b-instruct", "Llama 3.3 70B"),
]


# Keys pasted into the app are kept here so they survive a restart. Same exposure as
# an environment variable - anyone who can read the file can read the key - so it sits
# beside the database and is git-ignored.
KEY_FILE = os.environ.get("DND_KEYS", os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".keys.json"))

KEY_RE = re.compile(r"^[A-Za-z0-9_\-.:~+/=]{8,400}$")


def load_saved_keys():
    """Put previously saved keys into the environment, without overriding real ones."""
    try:
        with open(KEY_FILE, encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return {}
    for name, value in saved.items():
        if value and not os.environ.get(name):
            os.environ[name] = value
    return saved


def key_env_names():
    """Every environment variable a key may legitimately be stored in."""
    names = set()
    for cls in (AnthropicBackend, OpenRouterBackend, GeminiBackend, GroqBackend):
        env = cls.key_env
        names.update([env] if isinstance(env, str) else env)
    return names


def save_key(name, value):
    """Store one API key. Returns nothing; raises ValueError if it looks wrong."""
    if name not in key_env_names():
        raise ValueError(f"unknown key name: {name}")
    value = (value or "").strip()
    if not KEY_RE.match(value):
        raise ValueError("that doesn't look like an API key")

    try:
        with open(KEY_FILE, encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        saved = {}
    saved[name] = value

    with open(KEY_FILE, "w", encoding="utf-8") as f:
        json.dump(saved, f, indent=2)
    try:
        os.chmod(KEY_FILE, 0o600)
    except OSError:
        pass

    os.environ[name] = value


def forget_key(name):
    if name not in key_env_names():
        raise ValueError(f"unknown key name: {name}")
    try:
        with open(KEY_FILE, encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        saved = {}
    saved.pop(name, None)
    try:
        with open(KEY_FILE, "w", encoding="utf-8") as f:
            json.dump(saved, f, indent=2)
    except OSError:
        pass
    os.environ.pop(name, None)


class ProviderExhausted(Exception):
    """Out of credit, rate limited, or missing a key - try the next backend."""


class ProviderFailed(Exception):
    """The backend broke in a way retrying elsewhere might fix."""


# --------------------------------------------------------------------------- #
# backends
# --------------------------------------------------------------------------- #

class Backend:
    kind = "?"
    vision = False      # can it look at an image the player attached?
    draws = False       # can it produce one?
    key_url = ""        # where a person goes to get a key
    key_env = ""        # the environment variable to put it in
    free = False        # costs nothing to use

    def __init__(self, id, label, model):
        self.id = id
        self.label = label
        self.model = model

    def available(self):
        return False

    def env_name(self):
        return self.key_env if isinstance(self.key_env, str) else self.key_env[0]

    async def verify(self):
        """Cheap authenticated call, so a pasted key can be checked immediately.
        Returns (ok, message)."""
        return self.available(), ""

    def describe(self):
        return {"id": self.id, "label": self.label, "model": self.model,
                "kind": self.kind, "available": self.available(),
                "key_url": self.key_url, "key_env": self.env_name(), "free": self.free,
                "vision": self.vision, "draws": self.draws}


class AnthropicBackend(Backend):
    kind = "anthropic"
    vision = True
    key_url = "https://console.anthropic.com/settings/keys"
    key_env = "ANTHROPIC_API_KEY"

    def __init__(self, id, label, model):
        super().__init__(id, label, model)
        self._client = None

    def available(self):
        return bool(os.environ.get("ANTHROPIC_API_KEY")
                    or os.environ.get("ANTHROPIC_AUTH_TOKEN"))

    def client(self):
        if self._client is None:
            self._client = anthropic.AsyncAnthropic()
        return self._client

    async def verify(self):
        if not self.available():
            return False, "no key set"
        try:
            # cheapest authenticated call there is - no tokens generated
            await anthropic.AsyncAnthropic().models.list(limit=1)
            return True, ""
        except anthropic.APIStatusError as e:
            if e.status_code in (401, 403):
                return False, "the key was rejected"
            return False, f"HTTP {e.status_code}"
        except anthropic.APIError as e:
            return False, type(e).__name__

    async def stream(self, system_blocks, messages, tools, images=None):
        try:
            async with self.client().beta.messages.stream(
                model=self.model,
                max_tokens=8000,
                system=system_blocks,
                messages=attach_images(strip_private(messages), images, "anthropic"),
                tools=tools,
                output_config={"effort": "medium"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as stream:
                async for event in stream:
                    if (event.type == "content_block_delta"
                            and event.delta.type == "text_delta"):
                        yield {"type": "delta", "text": event.delta.text}
                msg = await stream.get_final_message()
        except anthropic.APIStatusError as e:
            # 401 bad key, 402 out of credit, 429 rate limited
            if e.status_code in (401, 402, 429):
                raise ProviderExhausted(f"Claude: HTTP {e.status_code}") from e
            raise ProviderFailed(f"Claude: HTTP {e.status_code}") from e
        except anthropic.APIError as e:
            raise ProviderFailed(f"Claude: {type(e).__name__}") from e

        yield {
            "type": "message",
            "content": [b.model_dump(exclude_none=True) for b in msg.content],
            "stop_reason": msg.stop_reason,
        }


class OpenAICompatBackend(Backend):
    """Any OpenAI-compatible chat-completions endpoint, translated to and from
    Anthropic blocks. Covers OpenRouter, Google Gemini and Groq."""

    kind = "openai-compat"
    url = ""
    key_env = ""
    vision = True
    replay_extra = False    # send provider-specific tool-call metadata back verbatim

    def __init__(self, id, label, model, url=None, key_env=None, kind=None):
        super().__init__(id, label, model)
        if url:
            self.url = url
        if key_env:
            self.key_env = key_env
        if kind:
            self.kind = kind

    def key(self):
        for name in ([self.key_env] if isinstance(self.key_env, str) else self.key_env):
            if os.environ.get(name):
                return os.environ[name]
        return None

    def available(self):
        return bool(self.key())

    def models_url(self):
        return self.url.replace("/chat/completions", "/models")

    async def draw(self, prompt):
        """Generate an image. Returns (bytes, mime). Gemini only, for now."""
        if self.kind != "gemini":
            raise ProviderFailed(f"{self.label} can't draw")
        key = self.key()
        if not key:
            raise ProviderExhausted(f"{self.label}: no API key set")

        url = GEMINI_IMAGE_URL.format(model=GEMINI_IMAGE_MODEL)
        body = {"contents": [{"parts": [{"text": prompt}]}]}
        try:
            async with httpx.AsyncClient(timeout=180) as http:
                r = await http.post(url, json=body, headers={"x-goog-api-key": key})
        except httpx.HTTPError as e:
            raise ProviderFailed(f"{self.label}: {type(e).__name__}")

        if r.status_code in (401, 403):
            raise ProviderExhausted(f"{self.label}: the key was rejected for images")
        if r.status_code == 429:
            raise ProviderExhausted(
                "image generation is out of quota - Google's free tier doesn't include "
                "drawing, so this needs billing enabled on the key")
        if r.status_code >= 400:
            raise ProviderFailed(f"{self.label}: HTTP {r.status_code} "
                                 f"{r.text[:160]}")

        try:
            parts = r.json()["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, ValueError):
            raise ProviderFailed(f"{self.label}: no image came back")
        for part in parts:
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return base64.b64decode(inline["data"]), inline.get(
                    "mimeType", inline.get("mime_type", "image/png"))
        raise ProviderFailed(f"{self.label}: the model replied with text, not a picture")

    async def verify(self):
        key = self.key()
        if not key:
            return False, "no key set"
        try:
            async with httpx.AsyncClient(timeout=15) as http:
                r = await http.get(self.models_url(),
                                   headers={"Authorization": f"Bearer {key}"})
            if r.status_code in (401, 403):
                return False, "the key was rejected"
            if r.status_code >= 400:
                return False, f"HTTP {r.status_code}"
            return True, ""
        except httpx.HTTPError as e:
            return False, type(e).__name__

    async def stream(self, system_blocks, messages, tools, images=None):
        key = self.key()
        if not key:
            raise ProviderExhausted(f"{self.label}: no API key set")

        body = {
            "model": self.model,
            "messages": attach_images(
                to_openai_messages(system_blocks, messages,
                                   keep_extra=self.replay_extra),
                images, "openai"),
            "tools": to_openai_tools(tools),
            "max_tokens": 8000,
            "stream": True,
        }
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "X-Title": "AI Dungeon Master",
        }

        text_parts = []
        calls = {}          # index -> {id, name, args}
        finish = None

        try:
            async with httpx.AsyncClient(timeout=180) as http:
                async with http.stream("POST", self.url, json=body,
                                       headers=headers) as resp:
                    if resp.status_code in (401, 402, 429):
                        await resp.aread()
                        raise ProviderExhausted(
                            f"{self.label}: HTTP {resp.status_code}")
                    if resp.status_code >= 400:
                        detail = (await resp.aread()).decode("utf-8", "replace")[:200]
                        raise ProviderFailed(
                            f"{self.label}: HTTP {resp.status_code} {detail}")

                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        payload = line[6:].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                        except json.JSONDecodeError:
                            continue

                        # OpenRouter reports upstream problems inside a 200 stream
                        if chunk.get("error"):
                            msg = str(chunk["error"].get("message", ""))[:200]
                            code = chunk["error"].get("code")
                            if code in (401, 402, 429) or "credit" in msg.lower():
                                raise ProviderExhausted(f"{self.label}: {msg}")
                            raise ProviderFailed(f"{self.label}: {msg}")

                        choice = (chunk.get("choices") or [{}])[0]
                        finish = choice.get("finish_reason") or finish
                        delta = choice.get("delta") or {}

                        piece = delta.get("content")
                        if piece:
                            text_parts.append(piece)
                            yield {"type": "delta", "text": piece}

                        for tc in delta.get("tool_calls") or []:
                            slot = calls.setdefault(tc.get("index", 0),
                                                    {"id": "", "name": "", "args": "",
                                                     "extra": None})
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            # Gemini 3.x returns a thought_signature here and rejects
                            # the next request unless it comes back verbatim
                            if tc.get("extra_content"):
                                slot["extra"] = tc["extra_content"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["name"] = fn["name"]
                            if fn.get("arguments"):
                                slot["args"] += fn["arguments"]
        except (ProviderExhausted, ProviderFailed):
            raise
        except httpx.HTTPError as e:
            raise ProviderFailed(f"{self.label}: {type(e).__name__}") from e

        content = []
        text = "".join(text_parts)
        if text.strip():
            content.append({"type": "text", "text": text})
        for i, slot in sorted(calls.items()):
            try:
                args = json.loads(slot["args"] or "{}")
            except json.JSONDecodeError:
                args = {}
            block = {"type": "tool_use", "id": slot["id"] or f"call_{i}",
                     "name": slot["name"], "input": args}
            if slot.get("extra"):
                block["_extra"] = slot["extra"]
            content.append(block)

        yield {
            "type": "message",
            "content": content,
            "stop_reason": "tool_use" if calls else (
                "max_tokens" if finish == "length" else "end_turn"),
        }


class OpenRouterBackend(OpenAICompatBackend):
    kind = "openrouter"
    url = OPENROUTER_URL
    key_env = "OPENROUTER_API_KEY"
    key_url = "https://openrouter.ai/keys"


class GeminiBackend(OpenAICompatBackend):
    """Google AI Studio. Free tier, no card required."""
    kind = "gemini"
    url = GEMINI_URL
    key_env = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
    key_url = "https://aistudio.google.com/apikey"
    free = True
    draws = True
    # Gemini 3.x refuses a follow-up request unless each function call carries back the
    # thought_signature it issued ("Function call is missing a thought_signature")
    replay_extra = True


class GroqBackend(OpenAICompatBackend):
    """Groq. Free tier, no card required, very fast."""
    kind = "groq"
    url = GROQ_URL
    key_env = "GROQ_API_KEY"
    key_url = "https://console.groq.com/keys"
    free = True


class OllamaBackend(Backend):
    """A model running on this machine. No key, no cost, no quota."""

    kind = "ollama"
    vision = False      # depends entirely on the pulled model, so assume not
    key_url = "https://ollama.com/download"
    key_env = ""          # none - it needs no key, just the app installed
    free = True
    _probe_cache = (0.0, False)

    def available(self):
        """Is Ollama actually running? Cached briefly so the UI can ask freely."""
        cls = OllamaBackend
        now = time.monotonic()
        when, ok = cls._probe_cache
        if now - when < 10:
            return ok
        parsed = urlparse(OLLAMA_HOST)
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 11434)
        try:
            with socket.create_connection((host, port), timeout=0.4):
                ok = True
        except OSError:
            ok = False
        cls._probe_cache = (now, ok)
        return ok

    async def verify(self):
        if not self.available():
            return False, "Ollama isn't running"
        try:
            async with httpx.AsyncClient(timeout=10) as http:
                r = await http.get(f"{OLLAMA_HOST}/api/tags")
            names = [m.get("name", "") for m in (r.json().get("models") or [])]
            if any(n == self.model or n.startswith(self.model + ":") for n in names):
                return True, ""
            return False, f"run `ollama pull {self.model}` first"
        except (httpx.HTTPError, ValueError) as e:
            return False, type(e).__name__

    async def stream(self, system_blocks, messages, tools, images=None):
        body = {
            "model": self.model,
            "messages": attach_images(
                to_ollama_messages(system_blocks, messages), images, "ollama"),
            "tools": to_openai_tools(tools),
            "stream": True,
            "options": {"num_ctx": OLLAMA_CONTEXT},
        }

        text_parts = []
        calls = []

        try:
            # a small model on a CPU is slow; give it room rather than cutting a turn off
            async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=5)) as http:
                async with http.stream("POST", f"{OLLAMA_HOST}/api/chat",
                                       json=body) as resp:
                    if resp.status_code == 404:
                        detail = (await resp.aread()).decode("utf-8", "replace")[:200]
                        raise ProviderFailed(
                            f"{self.label}: model not pulled yet — run "
                            f"`ollama pull {self.model}` ({detail})")
                    if resp.status_code >= 400:
                        detail = (await resp.aread()).decode("utf-8", "replace")[:200]
                        raise ProviderFailed(f"{self.label}: HTTP {resp.status_code} {detail}")

                    # Ollama streams newline-delimited JSON, not SSE
                    async for line in resp.aiter_lines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            chunk = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        if chunk.get("error"):
                            raise ProviderFailed(f"{self.label}: {chunk['error']}")

                        msg = chunk.get("message") or {}
                        piece = msg.get("content")
                        if piece:
                            text_parts.append(piece)
                            yield {"type": "delta", "text": piece}

                        for tc in msg.get("tool_calls") or []:
                            fn = tc.get("function") or {}
                            calls.append((fn.get("name", ""), fn.get("arguments")))

                        if chunk.get("done"):
                            break
        except (ProviderExhausted, ProviderFailed):
            raise
        except httpx.ConnectError as e:
            raise ProviderFailed(
                f"{self.label}: can't reach Ollama at {OLLAMA_HOST} — is it running?") from e
        except httpx.HTTPError as e:
            raise ProviderFailed(f"{self.label}: {type(e).__name__}") from e

        content = []
        text = "".join(text_parts)
        if text.strip():
            content.append({"type": "text", "text": text})
        for i, (fname, args) in enumerate(calls):
            # Ollama sends arguments already decoded, but some builds send a string
            if isinstance(args, str):
                try:
                    args = json.loads(args or "{}")
                except json.JSONDecodeError:
                    args = {}
            content.append({"type": "tool_use", "id": f"ollama_{i}",
                            "name": fname, "input": args or {}})

        return_reason = "tool_use" if calls else "end_turn"
        yield {"type": "message", "content": content, "stop_reason": return_reason}


# --------------------------------------------------------------------------- #
# format translation
# --------------------------------------------------------------------------- #

def to_openai_tools(tools):
    out = []
    for t in tools:
        out.append({"type": "function", "function": {
            "name": t["name"],
            "description": t["description"],
            "parameters": t["input_schema"],
        }})
    return out


def attach_images(messages, images, kind):
    """Put images onto the last user message, in the shape this provider expects.

    `images` is a list of (bytes, mime). Only the newest turn carries them: an image
    kept in history would be re-sent every turn and quietly inflate the bill.
    """
    if not images or not messages:
        return messages

    out = list(messages)
    for i in range(len(out) - 1, -1, -1):
        if out[i]["role"] != "user":
            continue
        text = out[i]["content"]
        if not isinstance(text, str):
            break                                    # a tool-result turn; leave it alone

        if kind == "anthropic":
            blocks = [{"type": "image", "source": {
                "type": "base64", "media_type": mime,
                "data": base64.b64encode(raw).decode()}} for raw, mime in images]
            blocks.append({"type": "text", "text": text})
            out[i] = {"role": "user", "content": blocks}
        elif kind == "ollama":
            out[i] = {"role": "user", "content": text,
                      "images": [base64.b64encode(raw).decode() for raw, mime in images]}
        else:                                        # OpenAI-compatible
            parts = [{"type": "image_url", "image_url": {
                "url": f"data:{mime};base64,{base64.b64encode(raw).decode()}"}}
                for raw, mime in images]
            parts.append({"type": "text", "text": text})
            out[i] = {"role": "user", "content": parts}
        break
    return out


def strip_private(messages):
    """Drop the underscore-prefixed fields we stash on blocks for one provider's sake,
    so another provider never sees a field it will reject."""
    out = []
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            out.append(m)
            continue
        cleaned = [{k: v for k, v in b.items() if not k.startswith("_")}
                   if isinstance(b, dict) else b for b in content]
        out.append({**m, "content": cleaned})
    return out


def to_ollama_messages(system_blocks, messages):
    """Like the OpenAI shape, but Ollama has no tool_call_id and takes arguments as
    an object rather than a JSON string."""
    system_text = "\n\n".join(b["text"] for b in system_blocks if b.get("text"))
    out = [{"role": "system", "content": system_text}]

    for m in messages:
        content = m["content"]
        role = m["role"]

        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        if role == "assistant":
            text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
            calls = [{"function": {"name": b["name"], "arguments": b.get("input") or {}}}
                     for b in content if b.get("type") == "tool_use"]
            msg = {"role": "assistant", "content": text}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
            continue

        for b in content:
            if b.get("type") == "tool_result":
                out.append({"role": "tool", "content": str(b.get("content", ""))})
            elif b.get("type") == "text":
                out.append({"role": "user", "content": b["text"]})

    return out


def to_openai_messages(system_blocks, messages, keep_extra=False):
    """Anthropic content blocks -> OpenAI chat messages.

    `keep_extra` replays provider-specific metadata stored on a tool_use block. Only
    the provider that issued it wants it back; everyone else would reject it."""
    system_text = "\n\n".join(b["text"] for b in system_blocks if b.get("text"))
    out = [{"role": "system", "content": system_text}]

    for m in messages:
        content = m["content"]
        role = m["role"]

        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        if role == "assistant":
            text = "".join(b.get("text", "") for b in content
                           if b.get("type") == "text")
            calls = []
            for b in content:
                if b.get("type") != "tool_use":
                    continue
                call = {"id": b["id"], "type": "function",
                        "function": {"name": b["name"],
                                     "arguments": json.dumps(b.get("input") or {},
                                                             ensure_ascii=False)}}
                if keep_extra and b.get("_extra"):
                    call["extra_content"] = b["_extra"]
                calls.append(call)
            msg = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
            continue

        # user turn: either plain text blocks or the results of the tools just run
        for b in content:
            if b.get("type") == "tool_result":
                out.append({"role": "tool", "tool_call_id": b["tool_use_id"],
                            "content": str(b.get("content", ""))})
            elif b.get("type") == "text":
                out.append({"role": "user", "content": b["text"]})

    return out


# --------------------------------------------------------------------------- #
# the registry
# --------------------------------------------------------------------------- #

def _build():
    backends = [
        AnthropicBackend("claude-opus-5", "Claude Opus 5", "claude-opus-5"),
        AnthropicBackend("claude-sonnet-5", "Claude Sonnet 5", "claude-sonnet-5"),
    ]

    def listed(env, fallback, suffix=""):
        raw = os.environ.get(env, "").strip()
        if not raw:
            return fallback
        return [(m.strip(), m.strip().split("/")[-1] + suffix)
                for m in raw.split(",") if m.strip()]

    # free tiers first among the non-Claude options - these are the ones someone with
    # no budget can actually turn on today
    for model, label in listed("GEMINI_MODELS", DEFAULT_GEMINI_MODELS, " (free)"):
        backends.append(GeminiBackend(f"gemini:{model}", label, model))

    for model, label in listed("GROQ_MODELS", DEFAULT_GROQ_MODELS, " (free)"):
        backends.append(GroqBackend(f"groq:{model}", label, model))

    for model, label in listed("OPENROUTER_MODELS", DEFAULT_OR_MODELS):
        backends.append(OpenRouterBackend(f"openrouter:{model}", label, model))

    local = os.environ.get("OLLAMA_MODELS", "").strip()
    local_models = ([(m.strip(), m.strip() + " (local)") for m in local.split(",") if m.strip()]
                    if local else DEFAULT_OLLAMA_MODELS)
    for model, label in local_models:
        backends.append(OllamaBackend(f"ollama:{model}", label, model))

    return backends


load_saved_keys()

BACKENDS = _build()
BY_ID = {b.id: b for b in BACKENDS}

DEFAULT_ID = os.environ.get("DM_BACKEND") or "claude-opus-5"


def get(backend_id):
    return BY_ID.get(backend_id or "")


def default_id():
    """The backend new campaigns start on: the configured one if it's usable."""
    b = get(DEFAULT_ID)
    if b and b.available():
        return b.id
    for b in BACKENDS:
        if b.available():
            return b.id
    return DEFAULT_ID


def any_available():
    return any(b.available() for b in BACKENDS)


def failover_order(current_id):
    """Who to try, in order, starting with the campaign's own choice.

    Claude first (it's the best DM), then everything else that has a key. Only
    configured backends are included, so a missing key is skipped rather than failed.
    """
    seen, order = set(), []
    for b in [get(current_id)] + BACKENDS:
        if b and b.available() and b.id not in seen:
            seen.add(b.id)
            order.append(b)
    return order


def image_backend():
    """The first configured backend that can draw, or None."""
    for b in BACKENDS:
        if b.draws and b.available():
            return b
    return None


def catalogue():
    return [b.describe() for b in BACKENDS]
