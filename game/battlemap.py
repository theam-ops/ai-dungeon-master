"""The battle map: a grid, who stands where, and what the party has seen. No I/O.

A table rule, off by default. This game is a story that happens to roll dice, and a
tactical grid pulls it towards being a tactics game, so a table that wants one turns it
on - and the DM still narrates; the map only shows where everyone is.

One map per campaign, stored as plain data:

    {"w": 20, "h": 15,
     "cells": "####....+~~:...",      # w*h characters, row by row - see TERRAIN
     "fog": "<base64 bitfield>",      # one bit per cell, 1 = the party has seen it
     "tokens": [{"name": "Vess", "x": 4, "y": 7, "pc": true}]}

The fog is the server's. A browser is only ever sent `public_view`: cells nobody has seen
arrive as "?", and a monster standing in them does not arrive at all. A fog drawn in the
browser over a full map would be a lie any player could read around.

The party sees around itself: whenever a player character is placed or moves, the cells
within SIGHT of it that a wall does not hide are revealed. The DM can reveal more - a
lit hall, a map found in a desk - and draws the map in rectangles, which is a shape a
model can get right.
"""

import base64
from collections import deque

TERRAIN = {"floor": ".", "wall": "#", "door": "+", "water": "~", "difficult": ":"}
NAMES = {v: k for k, v in TERRAIN.items()}
BLOCKS_SIGHT = "#+"              # a closed door hides what is behind it, as a wall does
IMPASSABLE = "#"
MIN_SIDE, MAX_SIDE = 3, 40
MAX_TOKENS = 40
SIGHT = 6                         # cells, so 30 ft - what a party sees without help
FOG = "?"


# --------------------------------------------------------------------------- #
# the fog bitfield
# --------------------------------------------------------------------------- #

def _bits(m):
    raw = base64.b64decode(m.get("fog") or "") if m.get("fog") else b""
    n = m["w"] * m["h"]
    raw = raw.ljust((n + 7) // 8, b"\0")
    return bytearray(raw[:(n + 7) // 8])


def _store_bits(m, bits):
    m["fog"] = base64.b64encode(bytes(bits)).decode()


def seen(m, x, y, bits=None):
    bits = bits if bits is not None else _bits(m)
    i = y * m["w"] + x
    return bool(bits[i >> 3] & (1 << (i & 7)))


def _reveal_cell(m, bits, x, y):
    i = y * m["w"] + x
    bits[i >> 3] |= 1 << (i & 7)


# --------------------------------------------------------------------------- #
# reading a map
# --------------------------------------------------------------------------- #

def cell(m, x, y):
    return m["cells"][y * m["w"] + x]


def inside(m, x, y):
    return 0 <= x < m["w"] and 0 <= y < m["h"]


def _key(name):
    return " ".join(str(name or "").lower().split())


def token(m, name):
    return next((t for t in m["tokens"] if _key(t["name"]) == _key(name)), None)


def clean(m):
    """A stored or imported map, made safe to use - or None. Anything malformed is
    dropped rather than trusted: an import is a file off someone's disk."""
    if not isinstance(m, dict):
        return None
    try:
        w, h = int(m.get("w")), int(m.get("h"))
    except (TypeError, ValueError):
        return None
    if not (MIN_SIDE <= w <= MAX_SIDE and MIN_SIDE <= h <= MAX_SIDE):
        return None
    cells = m.get("cells")
    if not isinstance(cells, str) or len(cells) != w * h or any(c not in NAMES for c in cells):
        return None
    out = {"w": w, "h": h, "cells": cells, "fog": "", "tokens": []}
    try:
        _store_bits(out, _bits({"w": w, "h": h, "fog": str(m.get("fog") or "")}))
    except (ValueError, TypeError):
        _store_bits(out, bytearray((w * h + 7) // 8))
    for t in m.get("tokens") or []:
        if not isinstance(t, dict) or len(out["tokens"]) >= MAX_TOKENS:
            continue
        try:
            x, y = int(t.get("x")), int(t.get("y"))
        except (TypeError, ValueError):
            continue
        name = str(t.get("name") or "").strip()[:40]
        if name and inside(out, x, y) and not token(out, name):
            out["tokens"].append({"name": name, "x": x, "y": y, "pc": bool(t.get("pc"))})
    return out


def public_view(m):
    """What every browser at the table is sent: the seen cells, the tokens standing on
    them, and the party's own tokens wherever they are. Nothing else leaves the server."""
    if not m:
        return None
    bits = _bits(m)
    cells = "".join(c if seen(m, i % m["w"], i // m["w"], bits) else FOG
                    for i, c in enumerate(m["cells"]))
    tokens = [dict(t) for t in m["tokens"] if t["pc"] or seen(m, t["x"], t["y"], bits)]
    return {"w": m["w"], "h": m["h"], "cells": cells, "tokens": tokens}


# --------------------------------------------------------------------------- #
# changing a map
# --------------------------------------------------------------------------- #

def new_map(w, h, fill="floor"):
    w = max(MIN_SIDE, min(MAX_SIDE, int(w)))
    h = max(MIN_SIDE, min(MAX_SIDE, int(h)))
    m = {"w": w, "h": h, "cells": TERRAIN.get(fill, ".") * (w * h), "fog": "", "tokens": []}
    _store_bits(m, bytearray((w * h + 7) // 8))
    return m


def _rect(m, r):
    """A rectangle from the DM, as inclusive bounds clipped to the map - or None."""
    try:
        x1, y1, x2, y2 = (int(r[k]) for k in ("x1", "y1", "x2", "y2"))
    except (KeyError, TypeError, ValueError):
        return None
    x1, x2 = sorted((x1, x2))
    y1, y2 = sorted((y1, y2))
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(m["w"] - 1, x2), min(m["h"] - 1, y2)
    return (x1, y1, x2, y2) if x1 <= x2 and y1 <= y2 else None


def _paint(m, r, terrain):
    cells = list(m["cells"])
    x1, y1, x2, y2 = r
    for y in range(y1, y2 + 1):
        for x in range(x1, x2 + 1):
            if terrain == "room":
                edge = x in (x1, x2) or y in (y1, y2)
                cells[y * m["w"] + x] = "#" if edge else "."
            else:
                cells[y * m["w"] + x] = TERRAIN[terrain]
    m["cells"] = "".join(cells)


def _line(x0, y0, x1, y1):
    """The cells a straight line passes through, from the first to the last."""
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
    err = dx + dy
    while True:
        yield x0, y0
        if (x0, y0) == (x1, y1):
            return
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def look(m, x, y, radius=SIGHT):
    """Reveal what can be seen from (x, y): every cell within `radius` whose line of
    sight is not broken by a wall or a closed door. The blocking cell itself is seen -
    you see the wall, not what is behind it. Returns how many cells were new."""
    bits, new = _bits(m), 0
    for ty in range(max(0, y - radius), min(m["h"], y + radius + 1)):
        for tx in range(max(0, x - radius), min(m["w"], x + radius + 1)):
            if (tx - x) ** 2 + (ty - y) ** 2 > radius * radius:
                continue
            for cx, cy in _line(x, y, tx, ty):
                if not seen(m, cx, cy, bits):
                    _reveal_cell(m, bits, cx, cy)
                    new += 1
                if (cx, cy) != (x, y) and cell(m, cx, cy) in BLOCKS_SIGHT:
                    break
    _store_bits(m, bits)
    return new


def apply(m, args, party_names):
    """The DM's update_map call. Returns (map or None, notes for the DM).

    Applied in a fixed order - put away, start fresh, paint, place, remove, reveal -
    so one call can lay out a whole room. A mistake in one part is reported and the rest
    still happens: half a map is better than a retry that costs a whole round.
    """
    notes = []
    if args.get("clear_map"):
        return None, ["The map is put away."]
    w, h = int(args.get("new_width") or 0), int(args.get("new_height") or 0)
    if w > 0 and h > 0:
        m = new_map(w, h, args.get("fill") or "floor")
        notes.append(f"New {m['w']}x{m['h']} map, all of it unseen by the party.")
    elif m is None:
        return None, ["ERROR: there is no map yet - give new_width and new_height to start one."]
    else:
        m = clean(m)

    for p in args.get("paint") or []:
        r, terrain = _rect(m, p), p.get("terrain")
        if terrain not in TERRAIN and terrain != "room":
            notes.append(f"Skipped painting {terrain!r}: not a terrain.")
        elif r is None:
            notes.append(f"Skipped a {terrain} rectangle entirely off the map.")
        else:
            _paint(m, r, terrain)

    pcs = {_key(n): n for n in party_names}
    for t in args.get("tokens") or []:
        name = str(t.get("name") or "").strip()[:40]
        try:
            x, y = int(t.get("x")), int(t.get("y"))
        except (TypeError, ValueError):
            notes.append(f"Skipped {name}: no position.")
            continue
        pc = t.get("kind") == "pc" or _key(name) in pcs
        if pc:
            if _key(name) not in pcs:
                notes.append(f"Skipped {name}: no player character by that name.")
                continue
            name = pcs[_key(name)]
        if not name or not inside(m, x, y):
            notes.append(f"Skipped {name or 'a token'}: ({x},{y}) is off the map.")
            continue
        if cell(m, x, y) in IMPASSABLE:
            notes.append(f"Skipped {name}: ({x},{y}) is a wall.")
            continue
        existing = token(m, name)
        if existing is None and len(m["tokens"]) >= MAX_TOKENS:
            notes.append(f"Skipped {name}: the map holds at most {MAX_TOKENS} tokens.")
            continue
        if existing is None:
            m["tokens"].append({"name": name, "x": x, "y": y, "pc": pc})
        else:
            existing.update(x=x, y=y)

    gone = {_key(n) for n in args.get("remove_tokens") or []}
    m["tokens"] = [t for t in m["tokens"] if _key(t["name"]) not in gone]

    bits = _bits(m)
    if args.get("reveal_all"):
        bits = bytearray(b"\xff" * len(bits))
    else:
        for r in args.get("reveal") or []:
            r = _rect(m, r)
            if r:
                for y in range(r[1], r[3] + 1):
                    for x in range(r[0], r[2] + 1):
                        _reveal_cell(m, bits, x, y)
    _store_bits(m, bits)
    for t in m["tokens"]:
        if t["pc"]:
            look(m, t["x"], t["y"])
    return m, notes


# Why a move was refused, by code - the browser shows each in the player's language;
# the English is for the API and the logs.
REFUSALS = {
    "no_map": "There is no map.",
    "not_placed": "The DM has not put you on the map yet.",
    "off_map": "That is off the map.",
    "unseen": "Nobody has seen that far yet.",
    "wall": "That is a wall.",
    "occupied": "Someone is already standing there.",
    "no_path": "There is no way through to there.",
}


def move(m, name, x, y):
    """A player drags their own token. Returns (map, None) or (None, a REFUSALS code).

    Only to a cell the party has seen, that is not a wall or somebody else's square, and
    that can be walked to through seen cells - dragging a token is walking, not blinking
    through a wall. Distance is not limited, in a fight or out of one: the turn order
    in this game guides rather than polices (see rules.join_combat), and so does this.
    """
    m = clean(m)
    if m is None:
        return None, "no_map"
    me = token(m, name)
    if me is None:
        return None, "not_placed"
    if not inside(m, x, y):
        return None, "off_map"
    bits = _bits(m)
    if not seen(m, x, y, bits):
        return None, "unseen"
    if cell(m, x, y) in IMPASSABLE:
        return None, "wall"
    if any(t is not me and (t["x"], t["y"]) == (x, y) for t in m["tokens"]):
        return None, "occupied"
    if not _reachable(m, bits, (me["x"], me["y"]), (x, y)):
        return None, "no_path"
    me.update(x=x, y=y)
    look(m, x, y)
    return m, None


def _reachable(m, bits, start, goal):
    frontier, came = deque([start]), {start}
    while frontier:
        x, y = frontier.popleft()
        if (x, y) == goal:
            return True
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                nx, ny = x + dx, y + dy
                if ((nx, ny) not in came and inside(m, nx, ny) and seen(m, nx, ny, bits)
                        and cell(m, nx, ny) not in IMPASSABLE):
                    came.add((nx, ny))
                    frontier.append((nx, ny))
    return False


# --------------------------------------------------------------------------- #
# the DM's view
# --------------------------------------------------------------------------- #

GLYPHS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def describe(m):
    """The whole map as the DM reads it: everything, fog included, with the fog marked.

    Coordinates are spelled out for every token, because a model reading a grid of
    characters is far better at "Vess at 4,7" than at counting columns.
    """
    if not m:
        return ""
    w, h, bits = m["w"], m["h"], _bits(m)
    marks = {}
    legend = []
    for i, t in enumerate(m["tokens"]):
        g = GLYPHS[i % len(GLYPHS)]
        marks[(t["x"], t["y"])] = g
        hidden = "" if seen(m, t["x"], t["y"], bits) else ", unseen by the party"
        legend.append(f"{g} = {t['name']} ({'PC' if t['pc'] else 'NPC'}) at {t['x']},{t['y']}{hidden}")
    head = "    " + "".join(str(x % 10) for x in range(w))
    rows = [f"{y:>3} " + "".join(marks.get((x, y), cell(m, x, y)) for x in range(w))
            for y in range(h)]
    n_seen = sum(seen(m, i % w, i // w, bits) for i in range(w * h))
    lines = [f"{w}x{h} grid, x across 0-{w - 1}, y down 0-{h - 1}. "
             "# wall, + door, ~ water, : difficult ground, . floor.", head, *rows,
             "Tokens: " + ("; ".join(legend) if legend else "none")]
    if n_seen == 0:
        lines.append("The party has seen none of it yet.")
    elif n_seen == w * h:
        lines.append("The party has seen all of it.")
    else:
        lines.append("What the party has seen (o seen, - not yet):")
        lines += [f"{y:>3} " + "".join("o" if seen(m, x, y, bits) else "-" for x in range(w))
                  for y in range(h)]
    return "\n".join(lines)
