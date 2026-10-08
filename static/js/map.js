/* The battle map. Part of the front end - see js/main.js.

   Drawn on a canvas from `S.map`, which is only ever what the server says the party
   can see: unseen cells arrive as "?" and nothing standing in them arrives at all. So
   there is no fog to draw over a hidden map here - there is no hidden map here.

   Your own token moves two ways: drag it with a mouse, or tap it and then tap where it
   should go - a drag on a phone is a scroll. The server decides whether the move
   stands, and the map redraws from what it sends back. */

import { api } from "./core/api.js";
import { el, icon, toast } from "./core/dom.js";
import { S } from "./core/state.js";
import { t } from "./i18n/index.js";

const FILL = {
  ".": "#2b251e", "#": "#4b4137", "+": "#7a5a30", "~": "#22414f", ":": "#3a3222", "?": "#0c0b0a",
};
const GRID = "rgba(232, 220, 200, 0.07)";
const PC = "#c9a227";
const NPC = "#d1603d";
const MAX_CELL = 34;

let selected = false;        // your token, tapped and waiting for a destination
let pressed = null;          // where a mouse press on your token began

export function mapOn() {
  return !!(S.campaign && (S.campaign.house || {}).battle_map && S.map);
}

function mine() {
  return S.map && S.map.tokens.find((tk) => tk.name === (S.campaign && S.campaign.you));
}

export function cardMap() {
  if (!mapOn()) return null;
  const box = el("section", "card map-card");
  const h = el("h3", "card-title");
  h.append(icon("map"), el("span", "", t("map")));
  box.append(h);
  const canvas = el("canvas", "map-canvas");
  canvas.setAttribute("role", "img");
  canvas.setAttribute("aria-label", t("map_label", S.map.w, S.map.h));
  box.append(canvas);
  box.append(el("p", "hint", mine() ? t("map_hint") : t("map_hint_off")));
  // drawn once it is in the page and has a width to fit. A timer, not
  // requestAnimationFrame: rAF is paused while the window is not being painted, and the
  // map would stay blank until it was (see onViewportChange in dash.js)
  setTimeout(() => draw(canvas), 0);
  canvas.addEventListener("pointerdown", (e) => onDown(canvas, e));
  canvas.addEventListener("pointerup", (e) => onUp(canvas, e));
  return box;
}

/* Whole pixels per square, from the width the card actually leaves - measured with the
   canvas at full width, so padding is already taken off and nothing is scaled after. */
function cellSize(canvas) {
  canvas.style.width = "100%";
  const width = canvas.clientWidth || 300;
  return Math.max(8, Math.min(MAX_CELL, Math.floor(width / S.map.w)));
}

export function draw(canvas) {
  const m = S.map;
  if (!m || !canvas.isConnected) return;
  const size = cellSize(canvas);
  const ratio = window.devicePixelRatio || 1;
  canvas.style.width = `${size * m.w}px`;
  canvas.style.height = `${size * m.h}px`;
  canvas.width = Math.round(size * m.w * ratio);
  canvas.height = Math.round(size * m.h * ratio);
  const g = canvas.getContext("2d");
  g.setTransform(ratio, 0, 0, ratio, 0, 0);

  for (let y = 0; y < m.h; y++) {
    for (let x = 0; x < m.w; x++) {
      const c = m.cells[y * m.w + x];
      const px = x * size, py = y * size;
      g.fillStyle = FILL[c] || FILL["."];
      g.fillRect(px, py, size, size);
      if (c === ":") {                       // difficult ground: scattered stones
        g.fillStyle = "rgba(232, 220, 200, 0.18)";
        g.fillRect(px + size * 0.25, py + size * 0.3, 2, 2);
        g.fillRect(px + size * 0.65, py + size * 0.6, 2, 2);
      } else if (c === "~") {                // water: a ripple
        g.strokeStyle = "rgba(160, 200, 220, 0.25)";
        g.beginPath();
        g.moveTo(px + size * 0.2, py + size * 0.55);
        g.quadraticCurveTo(px + size * 0.5, py + size * 0.35, px + size * 0.8, py + size * 0.55);
        g.stroke();
      } else if (c === "+") {                // a door: a bar across the opening
        g.fillStyle = "#c8a46a";
        g.fillRect(px + size * 0.15, py + size * 0.42, size * 0.7, size * 0.16);
      }
      if (c !== "?" && c !== "#") {
        g.strokeStyle = GRID;
        g.strokeRect(px + 0.5, py + 0.5, size - 1, size - 1);
      }
    }
  }

  const acting = S.combat && S.combat.order.length
    ? S.combat.order[S.combat.turn % S.combat.order.length].name : null;
  m.tokens.forEach((tk) => {
    const cx = tk.x * size + size / 2, cy = tk.y * size + size / 2;
    const r = size * 0.38;
    const own = tk.name === (S.campaign && S.campaign.you);
    if (tk.name === acting) {                // whose turn it is, ringed
      g.strokeStyle = "#e8dcc8";
      g.lineWidth = 2;
      g.beginPath();
      g.arc(cx, cy, r + 3, 0, Math.PI * 2);
      g.stroke();
    }
    g.fillStyle = tk.pc ? PC : NPC;
    g.globalAlpha = own && selected ? 0.6 : 1;
    g.beginPath();
    g.arc(cx, cy, r, 0, Math.PI * 2);
    g.fill();
    g.globalAlpha = 1;
    if (own) {
      g.strokeStyle = "#fff4d6";
      g.lineWidth = 2;
      g.stroke();
    }
    if (size >= 16) {
      g.fillStyle = "#12100e";
      g.font = `600 ${Math.round(size * 0.36)}px system-ui, sans-serif`;
      g.textAlign = "center";
      g.textBaseline = "middle";
      g.fillText(initials(tk.name), cx, cy + 1);
    }
  });
  g.lineWidth = 1;
}

function initials(name) {
  const words = name.trim().split(/\s+/);
  const last = words[words.length - 1];
  // "Goblin 2" reads better as G2 than as GO
  if (words.length > 1 && /^\d+$/.test(last)) return words[0][0] + last;
  return Array.from(words[0]).slice(0, 2).join("");
}

function cellAt(canvas, e) {
  const box = canvas.getBoundingClientRect();
  const size = box.width / S.map.w;
  return { x: Math.floor((e.clientX - box.left) / size), y: Math.floor((e.clientY - box.top) / size) };
}

function onDown(canvas, e) {
  const me = mine();
  if (!me) return;
  const at = cellAt(canvas, e);
  pressed = at.x === me.x && at.y === me.y && e.pointerType === "mouse" ? at : null;
}

function onUp(canvas, e) {
  const me = mine();
  if (!me) return;
  const at = cellAt(canvas, e);
  const onMe = at.x === me.x && at.y === me.y;
  if (pressed && !onMe) {                    // a mouse drag off your own token
    pressed = null;
    moveTo(at);
    return;
  }
  pressed = null;
  if (onMe) {                                // tap your token to pick it up, again to drop
    selected = !selected;
    draw(canvas);
  } else if (selected) {
    selected = false;
    moveTo(at);
  }
}

async function moveTo(at) {
  try {
    await api(`/api/campaigns/${S.campaign.id}/map/move`, { method: "POST", body: at });
    // the map redraws when the server's event arrives - that, not this, is the truth
  } catch (err) {
    toast(err.code ? t("map_" + err.code) : err.message);
    redrawAll();
  }
}

/* Redraw every map on the page in place - cheaper than rebuilding the dashboard, and
   it keeps a tap-selected token selected while events arrive. */
export function redrawAll() {
  document.querySelectorAll(".map-canvas").forEach(draw);
}

export function resetMap() {
  selected = false;
  pressed = null;
}
