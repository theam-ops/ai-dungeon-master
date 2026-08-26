"""FastAPI server for the AI Dungeon Master.

    python -m uvicorn server:app --reload      # then open http://localhost:8000

Environment:
    ANTHROPIC_API_KEY   Claude, the default DM
    GEMINI_API_KEY      free tier, no card - aistudio.google.com/apikey
    GROQ_API_KEY        free tier, no card - console.groq.com/keys
    OPENROUTER_API_KEY  one balance, many paid models
    (none)              Ollama, if it is running locally - free, no key
    *_MODELS            optional - which models each provider offers
    DM_BACKEND          optional - which AI new campaigns start on
    APP_PASSWORD        optional - gate the whole app behind one password
    SESSION_SECRET      optional - cookie signing key (generated and cached if unset)
    ALLOW_KEY_SETUP     optional - set 0 to forbid pasting keys into the running app
    DND_KEYS            optional - where pasted keys are stored (default .keys.json)
    DND_DB              optional - path to the SQLite file
    MAX_TURNS_PER_MIN   optional - per-campaign spend backstop (default 12)
    DM_ART_EVERY_TURNS  optional - player turns between DM illustrations (default 6)

At least one AI must be reachable: any key above, or a running Ollama.
"""

import asyncio
import io
import json
import logging
import os
import re
import secrets
import sys
import zipfile
from collections import defaultdict
from urllib.parse import quote

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (FileResponse, JSONResponse, Response,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from game import claude_code, dm, i18n, lore, media, providers, rules, store

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")

log = logging.getLogger("dnd")

APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
MAX_TURNS_PER_MIN = int(os.environ.get("MAX_TURNS_PER_MIN", "12"))

DEFAULT_TITLE = {"en": "An untitled campaign", "th": "แคมเปญไร้ชื่อ"}

# Pasting a key into the running app is convenient but it spends money, so it can be
# turned off entirely on a shared deployment.
KEY_SETUP_ENABLED = os.environ.get("ALLOW_KEY_SETUP", "1") not in ("0", "false", "no")


def _session_secret():
    if os.environ.get("SESSION_SECRET"):
        return os.environ["SESSION_SECRET"]
    # cache one on disk so sessions survive a restart during local dev
    path = os.path.join(HERE, ".session_secret")
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip()
    value = secrets.token_hex(32)
    try:
        with open(path, "w") as f:
            f.write(value)
    except OSError:
        pass
    return value


app = FastAPI(title="AI Dungeon Master")
app.add_middleware(SessionMiddleware, secret_key=_session_secret(),
                   session_cookie="dnd_session", max_age=60 * 60 * 24 * 365,
                   same_site="lax", https_only=False)

# The interface has no build step and no hashed file names, so /app.js always means the
# newest /app.js. Without an explicit header a browser is free to guess how long it may
# keep the old one, which shows up as an interface that quietly stays a version behind.
# "no-cache" doesn't mean don't store it - it means ask first, and the ETag turns that
# into a 304 with no body.
SHELL_FILES = (".html", ".js", ".css", ".json", ".ico")


@app.middleware("http")
async def revalidate_shell(request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith(SHELL_FILES):
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


# in-memory fanout: campaign_id -> set of subscriber queues
subscribers = defaultdict(set)
# one DM turn at a time per campaign
locks = defaultdict(asyncio.Lock)
# campaigns whose opening scene has been asked for but not yet written. `begin` refuses
# a second call by looking at the saved history, which is only written when the turn
# ends - so two players tapping Begin at the same moment both passed the check and the
# campaign opened twice, in two different places.
beginning = set()
# asyncio keeps only a weak reference to a task, so a turn with nothing holding on to it
# can be collected mid-narration. Hold them until they finish.
running = set()


def spawn(coro):
    task = asyncio.create_task(coro)
    running.add(task)
    task.add_done_callback(running.discard)
    return task


# --------------------------------------------------------------------------- #
# session helpers
# --------------------------------------------------------------------------- #

def player_token(request):
    token = request.session.get("token")
    if not token:
        token = secrets.token_urlsafe(24)
        request.session["token"] = token
    return token


def require_auth(request):
    if APP_PASSWORD and not request.session.get("authed"):
        raise HTTPException(401, "password required")
    return player_token(request)


def require_client():
    if not providers.any_available():
        raise HTTPException(503, "No AI is configured - set a free GEMINI_API_KEY "
                                 "(aistudio.google.com/apikey), another provider key, "
                                 "or run Ollama, so the DM can think.")


def require_member(request, cid):
    """Caller must have a character in this campaign."""
    token = require_auth(request)
    campaign = store.get_campaign(cid)
    if not campaign:
        raise HTTPException(404, "no such campaign")
    me = store.character_for_token(cid, token)
    if not me:
        raise HTTPException(403, "you have no character in this campaign")
    return token, campaign, me


# --------------------------------------------------------------------------- #
# broadcast
# --------------------------------------------------------------------------- #

async def broadcast(cid, event):
    for q in list(subscribers[cid]):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass  # a stalled client drops frames rather than blocking the table


async def publish(cid, kind, payload):
    """Persist an event and push it to everyone watching."""
    event = store.append_event(cid, kind, payload)
    await broadcast(cid, event)
    return event


# --------------------------------------------------------------------------- #
# auth + options
# --------------------------------------------------------------------------- #

@app.get("/api/me")
async def me(request: Request):
    token = player_token(request)
    authed = (not APP_PASSWORD) or bool(request.session.get("authed"))
    return {
        "authed": authed,
        "needs_password": bool(APP_PASSWORD),
        "dm_ready": providers.any_available(),
        "campaigns": store.campaigns_for_token(token) if authed else [],
    }


@app.post("/api/login")
async def login(request: Request, password: str = Body(..., embed=True)):
    if not APP_PASSWORD:
        request.session["authed"] = True
        return {"ok": True}
    if not secrets.compare_digest(password, APP_PASSWORD):
        raise HTTPException(401, "wrong password")
    request.session["authed"] = True
    player_token(request)
    return {"ok": True}


@app.get("/api/options")
async def options(lang: str = "en"):
    lang = i18n.normalise(lang)
    return {
        "races": rules.RACES,
        "classes": {k: {"hit_die": v["hit_die"], "primary": v["primary"],
                        "gear": [i18n.gear(g, lang) for g in v["gear"]]}
                    for k, v in rules.CLASSES.items()},
        "abilities": rules.ABILITIES,
        "languages": i18n.LANGUAGES,
        "lang": lang,
    }


@app.post("/api/roll-stats")
async def roll_stats(request: Request, klass: str = Body(..., embed=True, alias="klass")):
    require_auth(request)
    if klass not in rules.CLASSES:
        raise HTTPException(400, "unknown class")
    scores = rules.roll_stats(klass)
    return {"scores": scores,
            "modifiers": {a: rules.modifier(s) for a, s in scores.items()}}


@app.get("/api/providers")
async def list_providers(request: Request):
    """Every AI this server could run the game on, and whether it has a key."""
    require_auth(request)
    return {"providers": providers.catalogue(), "default": providers.default_id(),
            "can_set_keys": KEY_SETUP_ENABLED}


def allow_key_setup(request):
    """Who may paste an API key into the running server.

    An API key is the one thing here that costs real money, so this is deliberately
    narrow: either the caller has passed the app password, or the request came from
    this machine. A public deployment with no password can't have keys set at all.
    """
    if not KEY_SETUP_ENABLED:
        raise HTTPException(403, "setting keys from the app is disabled on this server")
    if APP_PASSWORD:
        require_auth(request)          # 401s unless the password was given
        return
    host = (request.client.host if request.client else "") or ""
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(
            403, "keys can only be set from this machine, or set APP_PASSWORD first")


@app.post("/api/keys")
async def set_key(request: Request, body: dict = Body(...)):
    """Paste an API key straight into the running server, no restart needed."""
    allow_key_setup(request)

    name = (body.get("env") or "").strip()
    try:
        providers.save_key(name, body.get("key"))
    except ValueError as e:
        raise HTTPException(400, str(e))

    # tell the player right away whether the key actually works
    backend = next((b for b in providers.BACKENDS
                    if b.key_env and name in
                    ([b.key_env] if isinstance(b.key_env, str) else b.key_env)), None)
    ok, why = (True, "")
    if backend:
        try:
            ok, why = await backend.verify()
        except Exception as e:                       # never fail the save on a probe
            ok, why = False, type(e).__name__
        if not ok:
            providers.forget_key(name)               # don't keep a key that doesn't work

    return {"ok": ok, "message": why,
            "providers": providers.catalogue(), "default": providers.default_id()}


@app.delete("/api/keys/{name}")
async def delete_key(request: Request, name: str):
    allow_key_setup(request)
    try:
        providers.forget_key(name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "providers": providers.catalogue(),
            "default": providers.default_id()}


@app.post("/api/providers/{backend_id:path}/verify")
async def verify_provider(request: Request, backend_id: str):
    """Check an AI is actually reachable and usable."""
    require_auth(request)
    backend = providers.get(backend_id)
    if not backend:
        raise HTTPException(404, "no such AI")
    try:
        ok, why = await backend.verify()
    except Exception as e:
        ok, why = False, type(e).__name__
    return {"ok": ok, "message": why}


# --------------------------------------------------------------------------- #
# signing Claude Code in, from the web page
#
# This drives `claude auth login` on the machine running the server, so it is strictly
# for whoever is sitting at that machine. A player who joined over a tunnel must not be
# able to start it or see the link: the sign-in decides which account pays for every
# turn at this table, and the link would let them attach their own.
# --------------------------------------------------------------------------- #

LOOPBACK = ("127.0.0.1", "::1", "localhost")


def require_host(request):
    if (request.client.host if request.client else "") not in LOOPBACK:
        raise HTTPException(403, "signing in can only be done from the computer running "
                                 "the game")


@app.get("/api/claude/status")
async def claude_status(request: Request):
    require_auth(request)
    state = claude_code.auth_status()
    host = (request.client.host if request.client else "") in LOOPBACK
    return {**state, "can_sign_in": host and state["installed"]}


@app.post("/api/claude/login")
async def claude_login(request: Request):
    """Start the sign-in and hand back Anthropic's own authorisation link."""
    require_auth(request)
    require_host(request)
    try:
        url = await claude_code.LOGIN.start()
    except Exception as e:
        raise HTTPException(400, str(e) or type(e).__name__)
    return {"url": url}


@app.post("/api/claude/login/code")
async def claude_login_code(request: Request, code: str = Body(..., embed=True)):
    """Pass the code Anthropic showed the player back to the waiting process."""
    require_auth(request)
    require_host(request)
    ok, why = await claude_code.LOGIN.submit(code)
    if not ok:
        raise HTTPException(400, why)
    return {"ok": True, **claude_code.auth_status()}


@app.post("/api/claude/login/cancel")
async def claude_login_cancel(request: Request):
    require_auth(request)
    require_host(request)
    await claude_code.LOGIN.cancel()
    return {"ok": True}


@app.post("/api/campaigns/{cid}/provider")
async def set_provider(request: Request, cid: str, backend: str = Body(..., embed=True)):
    """Switch this campaign to another AI. Takes effect on the next turn."""
    require_member(request, cid)
    target = providers.get(backend)
    if not target:
        raise HTTPException(400, "no such AI")
    if not target.available():
        raise HTTPException(400, f"{target.label} has no API key configured on the server")

    store.set_campaign_backend(cid, target.id)
    await publish(cid, "switch", {"backend": target.id, "label": target.label,
                                  "manual": True})
    return {"ok": True, "backend": target.id, "label": target.label}


@app.post("/api/roll")
async def private_roll(request: Request, notation: str = Body("1d20", embed=True)):
    """A roll just for you - the DM never sees it."""
    require_auth(request)
    try:
        total, detail, crit = rules.roll_notation(notation)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"total": total, "detail": detail, "crit": crit}


# --------------------------------------------------------------------------- #
# campaigns
# --------------------------------------------------------------------------- #

def _make_character(spec, lang="en"):
    try:
        return rules.new_character(
            (spec.get("name") or "").strip()[:40] or "Nameless",
            spec.get("race"), spec.get("class"), spec.get("scores"), lang)
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise HTTPException(400, f"bad character: {e}")


@app.post("/api/campaigns")
async def create_campaign(request: Request, body: dict = Body(...)):
    token = require_auth(request)
    require_client()

    lang = i18n.normalise(body.get("lang"))
    name = (body.get("name") or "").strip()[:60] or DEFAULT_TITLE.get(lang, DEFAULT_TITLE["en"])
    char = _make_character(body.get("character") or {}, lang)

    backend = body.get("backend") if providers.get(body.get("backend")) else providers.default_id()
    campaign = store.create_campaign(name, lang, backend)
    store.add_character(campaign["id"], char, token)
    await publish(campaign["id"], "join", {"character": char["name"]})
    return campaign


@app.post("/api/campaigns/join")
async def join_lookup(request: Request, code: str = Body(..., embed=True)):
    """Look up a campaign by code so the player can create or claim a character."""
    require_auth(request)
    campaign = store.campaign_by_code(code)
    if not campaign:
        raise HTTPException(404, "no campaign with that code")
    token = player_token(request)
    mine = store.character_for_token(campaign["id"], token)
    return {
        "id": campaign["id"], "code": campaign["code"], "name": campaign["name"],
        "lang": campaign["lang"] or "en", "already_in": bool(mine),
        "party": [{"id": c["_id"], "name": c["name"], "race": c["race"],
                   "class": c["class"], "level": c["level"], "claimed": bool(c["_token"])}
                  for c in store.party(campaign["id"])],
    }


@app.post("/api/campaigns/{cid}/characters")
async def add_or_claim(request: Request, cid: str, body: dict = Body(...)):
    """Create a new character in this campaign, or claim an existing one for this device."""
    token = require_auth(request)
    if not store.get_campaign(cid):
        raise HTTPException(404, "no such campaign")

    if store.character_for_token(cid, token):
        raise HTTPException(400, "you already have a character here")

    claim_id = body.get("claim_id")
    if claim_id:
        target = next((c for c in store.party(cid) if c["_id"] == claim_id), None)
        if not target:
            raise HTTPException(404, "no such character")
        store.claim_character(claim_id, token)
        return {"ok": True, "character": {k: v for k, v in target.items()
                                          if not k.startswith("_")}}

    roster = store.party(cid)
    if len(roster) >= 6:
        raise HTTPException(400, "this party is full (6 characters)")

    char = _make_character(body.get("character") or {}, store.campaign_lang(cid))
    # The DM addresses a character by name, and `dm.run_tool` finds it by name, so two
    # people called Vess is not a cosmetic clash: every point of damage the DM aims at
    # either of them lands on whichever was created first, and the other is invulnerable.
    if any(c["name"].strip().lower() == char["name"].strip().lower() for c in roster):
        raise HTTPException(400, f"someone at this table is already called "
                                 f"{char['name']} - pick another name")
    store.add_character(cid, char, token)
    await publish(cid, "join", {"character": char["name"]})
    return {"ok": True, "character": char}


@app.get("/api/campaigns/{cid}")
async def campaign_detail(request: Request, cid: str):
    token, campaign, me = require_member(request, cid)
    return {
        "id": cid, "code": campaign["code"], "name": campaign["name"],
        "lang": campaign["lang"] or "en",
        "backend": campaign["backend"] or providers.default_id(),
        "you": me["name"],
        "party": [{k: v for k, v in c.items() if not k.startswith("_")}
                  for c in store.party(cid)],
        "last_seq": store.last_seq(cid),
        "started": bool(store.get_history(cid)),
    }


@app.get("/api/campaigns/{cid}/events")
async def campaign_events(request: Request, cid: str, since: int = 0):
    require_member(request, cid)
    return {"events": store.events_since(cid, since)}


@app.delete("/api/campaigns/{cid}")
async def remove_campaign(request: Request, cid: str):
    require_member(request, cid)
    store.delete_campaign(cid)
    media.drop_campaign(cid)
    return {"ok": True}


# --------------------------------------------------------------------------- #
# the live stream
# --------------------------------------------------------------------------- #

@app.get("/api/campaigns/{cid}/stream")
async def stream(request: Request, cid: str, since: int = 0):
    require_member(request, cid)

    queue = asyncio.Queue(maxsize=1000)
    subscribers[cid].add(queue)

    async def gen():
        try:
            # replay anything this client missed while it was away
            for event in store.events_since(cid, since):
                yield f"data: {json.dumps(event)}\n\n"
            yield f"data: {json.dumps({'kind': 'ready'})}\n\n"

            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"   # keeps proxies and phones from hanging up
                    continue
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            subscribers[cid].discard(queue)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    })


def party_payload(cid):
    return {"kind": "party", "party": [{k: v for k, v in c.items() if not k.startswith("_")}
                                       for c in store.party(cid)]}


def load_images(cid, media_ids):
    """Read attached images off disk for the DM to look at."""
    out = []
    for mid in (media_ids or [])[:4]:          # a hard cap: images are expensive context
        m = store.get_media(cid, mid)
        if not m:
            continue
        data = media.read(cid, m["file"])
        if data:
            out.append((data, m["mime"]))
    return out


SECRETISH_RE = re.compile(r"[{\[<]|(?:sk|gsk|AIza|sk-or|sk-ant)[-_A-Za-z0-9]{6,}")


def player_safe(text):
    """An upstream failure, trimmed to what a player at the table should be shown.

    A provider's error body is written for whoever holds the key, not for six friends
    on a tunnel: it arrives as raw JSON naming the model, the account state, sometimes
    request metadata. `dm.take_turn` folds it verbatim into the `error` and `switch`
    events, which go to everyone. Keep the readable head - "Claude Opus 5: HTTP 429" -
    and drop the body. The full text still goes to the server log, where the person
    who can act on it is looking.
    """
    text = " ".join(str(text or "").split())
    cut = SECRETISH_RE.search(text)
    if cut:
        text = text[:cut.start()].rstrip(" :,-")
        text += ")" * max(0, text.count("(") - text.count(")"))
    return text[:160] or "the AI did not say why"


async def run_dm_turn(cid, actor, action, images=None):
    """One DM turn, broadcast to the whole table. Serialized per campaign."""
    async with locks[cid]:
        characters = store.party(cid)
        history = store.get_history(cid)

        await broadcast(cid, {"kind": "thinking", "on": True})
        try:
            async for event in dm.take_turn(history, characters, actor, action,
                                            store.campaign_lang(cid),
                                            store.campaign_backend(cid) or providers.default_id(),
                                            images, cid):
                kind = event.pop("kind")
                if kind == "delta":
                    await broadcast(cid, {"kind": "delta", **event})
                    continue
                if kind == "backend":
                    store.set_campaign_backend(cid, event["backend"])
                    continue
                if kind == "draw":
                    # The DM asked for an illustration. Generating one takes the better
                    # part of a minute, and this loop is what feeds the narration to
                    # every browser at the table - awaiting it here would freeze the
                    # scene mid-sentence for everyone. It goes off on its own and lands
                    # in the feed when it is ready, the way a player's upload does.
                    asyncio.create_task(illustrate(cid, event["prompt"],
                                                   event.get("caption", "")))
                    continue
                if kind in ("error", "switch"):
                    field = "text" if kind == "error" else "reason"
                    if event.get(field):
                        log.warning("campaign %s %s: %s", cid, kind, event[field])
                        event[field] = player_safe(event[field])
                await publish(cid, kind, event)
                if kind == "sheet":
                    # persist and push straight away so HP bars move as damage lands
                    store.save_party(characters)
                    await broadcast(cid, party_payload(cid))
        except Exception as e:  # never leave the table hanging on an unexpected fault
            await publish(cid, "error", {"text": f"The DM stumbled: {type(e).__name__}."})
            raise
        finally:
            store.save_party(characters)
            store.save_history(cid, history)
            await broadcast(cid, party_payload(cid))
            await broadcast(cid, {"kind": "backend-now",
                                  "backend": store.campaign_backend(cid) or providers.default_id()})
            await broadcast(cid, {"kind": "thinking", "on": False})


@app.post("/api/campaigns/{cid}/act")
async def act(request: Request, cid: str, body: dict = Body(...)):
    token, campaign, me = require_member(request, cid)
    require_client()

    text = (body.get("text") or "").strip()[:2000]
    if not text:
        raise HTTPException(400, "say something")

    attached = (body.get("media") or [])[:4]
    images = load_images(cid, attached)

    if store.turns_in_last_minute(cid) >= MAX_TURNS_PER_MIN:
        raise HTTPException(429, "the table is moving too fast - give the DM a moment")

    await publish(cid, "player", {"character": me["name"], "text": text})

    # showing the table something means everyone sees it, not just the DM
    for mid in attached:
        m = store.get_media(cid, mid)
        if m:
            await publish(cid, "image", {
                "media": m["id"], "character": me["name"], "caption": m["caption"],
                "image_kind": m["kind"], "width": m["width"], "height": m["height"],
                "source": m["source"]})

    # the DM gets a nudge the players don't see, so a model that can't look at
    # pictures still knows one was produced
    dm_text = text + (f"\n\n[{me['name']} shows the table an image]" if images else "")
    spawn(run_dm_turn(cid, me["name"], dm_text, images))
    return {"ok": True}


@app.post("/api/campaigns/{cid}/begin")
async def begin(request: Request, cid: str):
    """Kick off the opening scene."""
    token, campaign, me = require_member(request, cid)
    require_client()

    # No `await` between the check and the claim, so this is atomic against the other
    # request that arrived in the same millisecond.
    if store.get_history(cid) or cid in beginning:
        raise HTTPException(400, "this campaign has already begun")
    beginning.add(cid)

    party = store.party(cid)
    roster = ", ".join(f"{c['name']} the level {c['level']} {c['race']} {c['class']}"
                       for c in party)
    prompt = (
        f"Begin the campaign. The party: {roster}. "
        "Invent a hook that puts them in immediate motion - no tavern, no scroll of exposition. "
        "Open in the middle of something happening. Establish where they are, what is wrong, "
        "and one detail that will matter later. "
        + ("Give each character a moment in the opening. " if len(party) > 1 else "")
        + "Then hand control to the players."
    )
    spawn(run_dm_turn(cid, None, prompt)).add_done_callback(
        lambda _t, cid=cid: beginning.discard(cid))
    return {"ok": True}


# --------------------------------------------------------------------------- #
# images
# --------------------------------------------------------------------------- #

VALID_KINDS = ("portrait", "scene", "handout", "map")


def _store_image(cid, token, data_tuple, kind, caption, source):
    clean, ext, mime, w, h = data_tuple
    if store.media_count(cid) >= media.MAX_PER_CAMPAIGN:
        raise HTTPException(400, f"this campaign already has "
                                 f"{media.MAX_PER_CAMPAIGN} images")
    name = media.put(cid, clean, ext)
    mid = store.add_media(cid, name, kind, mime, len(clean), w, h,
                          caption[:200], source, token)
    return store.get_media(cid, mid)


def _describe(m):
    return {"id": m["id"], "kind": m["kind"], "mime": m["mime"],
            "width": m["width"], "height": m["height"], "caption": m["caption"],
            "source": m["source"], "bytes": m["bytes"]}


@app.post("/api/campaigns/{cid}/media")
async def upload_media(request: Request, cid: str,
                       file: UploadFile = File(...),
                       kind: str = Form("handout"),
                       caption: str = Form(""),
                       share: str = Form("1")):
    """Upload an image from the player's device."""
    token, campaign, me = require_member(request, cid)
    if kind not in VALID_KINDS:
        raise HTTPException(400, "unknown image kind")

    raw = await file.read(media.MAX_UPLOAD_BYTES + 1)
    try:
        processed = media.process(raw, kind)
    except media.MediaError as e:
        raise HTTPException(400, str(e))

    m = _store_image(cid, token, processed, kind, caption, "upload")
    await _announce_image(cid, m, me, kind, share)
    return _describe(m)


LORE_EXTENSIONS = (".md", ".markdown", ".txt", ".html", ".htm")
MAX_LORE_BYTES = int(os.environ.get("LORE_MAX_BYTES", 4 * 1024 * 1024))
MAX_LORE_DOCS = int(os.environ.get("LORE_MAX_DOCS", 40))


@app.post("/api/campaigns/{cid}/library")
async def import_library(request: Request, cid: str,
                         files: list[UploadFile] = File(default=[])):
    """Bring a folder of the players' own campaign material into this campaign.

    Images become handouts captioned from their file names; text documents become lore
    the DM can search. Everything else is ignored, so pointing this at a working folder
    full of odds and ends does the sensible thing rather than failing.
    """
    token, campaign, me = require_member(request, cid)
    images, documents, skipped = [], [], []

    for f in files:
        name = os.path.basename(f.filename or "")
        if not name:
            continue
        lower = name.lower()
        if lower.endswith(LORE_EXTENSIONS):
            raw = await f.read(MAX_LORE_BYTES + 1)
            if len(raw) > MAX_LORE_BYTES:
                skipped.append({"name": name, "why": "document too large"})
                continue
            documents.append((name, raw))
        elif (f.content_type or "").startswith("image/"):
            images.append((name, await f.read(media.MAX_UPLOAD_BYTES + 1)))
        else:
            skipped.append({"name": name, "why": "not an image or a document"})

    added_images = []
    for name, raw in images:
        try:
            processed = media.process(raw, "handout")
        except media.MediaError as e:
            skipped.append({"name": name, "why": str(e)})
            continue
        m = _store_image(cid, token, processed, "handout", _caption_from(name), "upload")
        added_images.append(_describe(m))

    # The cap is on what the campaign *holds*, not on what one upload carries. Counting
    # only this request let someone import 40 documents as often as they liked.
    already = {d["name"] for d in store.lore_documents(cid)}
    room = max(0, MAX_LORE_DOCS - len(already))
    keep, over = [], []
    for name, raw in documents:
        if name in already or room > 0:              # replacing one costs no new slot
            if name not in already:
                room -= 1
                already.add(name)
            keep.append((name, raw))
        else:
            over.append(name)

    added_docs = []
    for name, raw in keep:
        text = lore.to_text(name, raw)          # lore.decode works out the encoding
        if not text.strip():
            skipped.append({"name": name, "why": "no readable text in it"})
            continue
        added_docs.append(store.add_lore(cid, name, text))
    for name in over:
        skipped.append({"name": name, "why": f"over the {MAX_LORE_DOCS}-document limit"})

    return {"images": added_images, "documents": added_docs, "skipped": skipped}


def _caption_from(filename):
    """'NPC_Aria_Venn.png' -> 'Aria Venn'. The file name is the only label there is."""
    stem = os.path.splitext(os.path.basename(filename))[0]
    stem = re.sub(r"^(NPC|PC|Scene|Place|Map|Img|Image)[_\-\s]+", "", stem, flags=re.I)
    return re.sub(r"[_\-]+", " ", stem).strip()[:200]


@app.get("/api/campaigns/{cid}/lore")
async def list_lore(request: Request, cid: str):
    require_member(request, cid)
    return {"documents": store.lore_documents(cid)}


@app.delete("/api/campaigns/{cid}/lore/{lid}")
async def remove_lore(request: Request, cid: str, lid: str):
    require_member(request, cid)
    store.delete_lore(cid, lid)
    return {"ok": True}


@app.post("/api/campaigns/{cid}/media/url")
async def media_from_url(request: Request, cid: str, body: dict = Body(...)):
    """Pull an image in from a link the player pasted."""
    token, campaign, me = require_member(request, cid)
    kind = body.get("kind", "handout")
    if kind not in VALID_KINDS:
        raise HTTPException(400, "unknown image kind")
    try:
        processed = media.fetch(body.get("url"), kind)
    except media.MediaError as e:
        raise HTTPException(400, str(e))

    m = _store_image(cid, token, processed, kind, body.get("caption", ""), "link")
    await _announce_image(cid, m, me, kind, body.get("share", "1"))
    return _describe(m)


@app.post("/api/campaigns/{cid}/media/generate")
async def generate_media(request: Request, cid: str, body: dict = Body(...)):
    """Have an AI draw something. Costs money on every provider that offers it."""
    token, campaign, me = require_member(request, cid)
    kind = body.get("kind", "scene")
    if kind not in VALID_KINDS:
        raise HTTPException(400, "unknown image kind")

    prompt = (body.get("prompt") or "").strip()[:800]
    if not prompt:
        raise HTTPException(400, "say what you want drawn")

    artist = providers.image_backend()
    if artist is None:
        raise HTTPException(503, "no AI here can draw - a Gemini key with billing "
                                 "enabled is needed for image generation")
    try:
        raw, _mime = await artist.draw(prompt)
    except providers.ProviderExhausted as e:
        raise HTTPException(402, str(e))
    except providers.ProviderFailed as e:
        raise HTTPException(502, str(e))

    try:
        processed = media.process(raw, kind)
    except media.MediaError as e:
        raise HTTPException(502, f"the AI returned something unusable: {e}")

    m = _store_image(cid, token, processed, kind, prompt, "generated")
    await _announce_image(cid, m, me, kind, body.get("share", "1"))
    return _describe(m)


async def illustrate(cid, prompt, caption):
    """Draw what the DM asked for, away from the turn that asked for it.

    Called as a bare task from `run_dm_turn`, so it outlives the turn and must never
    raise: the narration has already gone out and the picture is a bonus on top of it.
    No artist, a 429 from a free key, a refusal, a campaign already at its image limit -
    all of them end the same way, with a line on the server's log and a table that
    never knew a picture was coming. The rate-limit slot was already spent in
    `dm.run_tool`, so a provider that fails does not get retried a moment later.
    """
    artist = providers.image_backend()
    if artist is None:
        return
    try:
        raw, _mime = await artist.draw(prompt)
        processed = media.process(raw, "scene")
        # "dm" rather than "generated": the feed says who reached for the pencil, and
        # a picture nobody asked for should be labelled as such
        m = _store_image(cid, None, processed, "scene", caption or prompt, "dm")
    except (providers.ProviderExhausted, providers.ProviderFailed, media.MediaError,
            HTTPException) as e:
        print(f"the DM's illustration didn't happen: {e}", file=sys.stderr)
        return
    except Exception as e:                       # a background task, so nothing catches
        print(f"the DM's illustration broke: {type(e).__name__}: {e}", file=sys.stderr)
        return

    await _announce_image(cid, m, {"name": "", "_id": None}, "scene", "1")


async def _announce_image(cid, m, me, kind, share):
    """Put the image in the feed, unless it is a portrait (those live on the sheet)."""
    if kind == "portrait":
        store.set_portrait(me["_id"], m["id"])
        await broadcast(cid, party_payload(cid))
        return
    if str(share) in ("0", "false", "no"):
        return
    await publish(cid, "image", {"media": m["id"], "character": me["name"],
                                 "caption": m["caption"], "image_kind": kind,
                                 "width": m["width"], "height": m["height"],
                                 "source": m["source"]})


@app.get("/api/campaigns/{cid}/media/{mid}")
async def serve_media(request: Request, cid: str, mid: str):
    """Deliberately not a static mount: only members of the campaign may see these."""
    require_member(request, cid)
    m = store.get_media(cid, mid)
    if not m:
        raise HTTPException(404, "no such image")
    data = media.read(cid, m["file"])
    if data is None:
        raise HTTPException(404, "that image is missing from disk")
    return Response(content=data, media_type=m["mime"], headers={
        "Cache-Control": "private, max-age=86400",
        "Content-Disposition": "inline",
        "X-Content-Type-Options": "nosniff",
    })


@app.get("/api/campaigns/{cid}/media")
async def list_media(request: Request, cid: str):
    require_member(request, cid)
    return {"media": [_describe(m) for m in store.campaign_media(cid)]}


@app.delete("/api/campaigns/{cid}/media/{mid}")
async def remove_media(request: Request, cid: str, mid: str):
    token, campaign, me = require_member(request, cid)
    m = store.get_media(cid, mid)
    if not m:
        raise HTTPException(404, "no such image")
    store.delete_media(cid, mid)
    if not store.file_still_used(cid, m["file"]):     # content-addressed: may be shared
        media.remove(cid, m["file"])
    await broadcast(cid, party_payload(cid))
    return {"ok": True}


# --------------------------------------------------------------------------- #
# export / import
# --------------------------------------------------------------------------- #

@app.get("/api/campaigns/{cid}/export")
async def export_campaign(request: Request, cid: str):
    require_member(request, cid)
    blob = store.export_campaign(cid)
    title = blob["campaign"]["name"]
    files = blob.get("media") or []

    # HTTP headers are latin-1, so a Thai (or any non-ASCII) campaign name cannot go in
    # the plain filename. Send an ASCII fallback plus the real name via RFC 5987.
    ascii_name = "".join(c for c in title if c.isascii() and (c.isalnum() or c in " -_")).strip()
    # A campaign with images can't travel as JSON - base64 would bloat it absurdly -
    # so it becomes a zip holding campaign.json plus the image files.
    ext = "zip" if files else "json"
    utf8_name = quote(f"{title}.{ext}", safe="")
    disposition = (f'attachment; filename="{ascii_name or "campaign"}.{ext}"; '
                   f"filename*=UTF-8''{utf8_name}")

    if not files:
        return JSONResponse(blob, headers={"Content-Disposition": disposition})

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("campaign.json", json.dumps(blob, ensure_ascii=False, indent=2))
        for m in files:
            data = media.read(cid, m["file"])
            if data is not None:
                z.writestr(f"media/{m['file']}", data)
    return Response(content=buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": disposition})


def _restore_media_files(cid, blob, read_file):
    """Put the image files back on disk under the new campaign id.

    `read_file(name)` returns that image's bytes, or None if the export didn't carry it -
    a zip reads from the archive, a folder upload from what the browser sent. Names come
    out of the export, so they are checked before being joined to a path.
    """
    for m in blob.get("media") or []:
        name = m.get("file") or ""
        if not name or "/" in name or os.sep in name or ".." in name:
            continue
        data = read_file(name)
        if data is None:
            continue
        folder = media.campaign_dir(cid)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, name), "wb") as f:
            f.write(data)


def _zip_reader(zf):
    def read(name):
        try:
            return zf.read(f"media/{name}")
        except KeyError:
            return None
    return read


@app.post("/api/import/archive")
async def import_archive(request: Request, file: UploadFile = File(...)):
    """Import a .zip export - campaign.json plus its images."""
    token = require_auth(request)
    raw = await file.read(120 * 1024 * 1024)
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
        blob = json.loads(zf.read("campaign.json").decode("utf-8"))
    except (zipfile.BadZipFile, KeyError, ValueError, UnicodeDecodeError) as e:
        raise HTTPException(400, f"that isn't a campaign archive: {type(e).__name__}")

    try:
        campaign = store.import_campaign(blob)
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise HTTPException(400, f"that archive isn't a campaign export: {e}")

    _restore_media_files(campaign["id"], blob, _zip_reader(zf))
    roster = store.party(campaign["id"])
    if roster and not store.character_for_token(campaign["id"], token):
        store.claim_character(roster[0]["_id"], token)
    return campaign


@app.post("/api/import/folder")
async def import_folder(request: Request, campaign: str = Form(...),
                        files: list[UploadFile] = File(default=[])):
    """Import an unzipped export - the campaign.json text plus its image files.

    The browser can hand over a whole directory, but not a zip of one, so the client
    reads campaign.json itself and posts the images alongside. Image names in an export
    are content hashes, so matching on the base name is enough to pair them up.
    """
    token = require_auth(request)
    try:
        blob = json.loads(campaign)
    except ValueError as e:
        raise HTTPException(400, f"that folder's campaign.json is not readable: {e}")
    if not isinstance(blob, dict):
        raise HTTPException(400, "that folder's campaign.json is not a campaign export")

    try:
        record = store.import_campaign(blob)
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise HTTPException(400, f"that folder isn't a campaign export: {e}")

    images = {}
    for f in files:
        images[os.path.basename(f.filename or "")] = await f.read(30 * 1024 * 1024)
    _restore_media_files(record["id"], blob, images.get)

    roster = store.party(record["id"])
    if roster and not store.character_for_token(record["id"], token):
        store.claim_character(roster[0]["_id"], token)
    return record


@app.post("/api/import")
async def import_campaign(request: Request, body: dict = Body(...)):
    token = require_auth(request)
    try:
        campaign = store.import_campaign(body)
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise HTTPException(400, f"that file isn't a campaign export: {e}")
    # first character becomes yours unless it already belongs to someone
    roster = store.party(campaign["id"])
    if roster and not store.character_for_token(campaign["id"], token):
        store.claim_character(roster[0]["_id"], token)
    return campaign


# --------------------------------------------------------------------------- #
# static
# --------------------------------------------------------------------------- #

@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/healthz")
async def healthz():
    return {"ok": True, "dm_ready": providers.any_available()}


app.mount("/", StaticFiles(directory=STATIC), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
