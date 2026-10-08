/* The live event stream and what each event does. Part of the front end - see js/main.js. */

import { renderAI } from "./ai.js";
import { $, el } from "./core/dom.js";
import { S } from "./core/state.js";
import { renderDash, renderLive } from "./dash.js";
import { HOUSE_KEYS, renderHouse, renderRecap } from "./drawer.js";
import { renderChange, t } from "./i18n/index.js";
import { mediaUrl, openLightbox, renderPortrait } from "./images.js";
import { mapOn, redrawAll } from "./map.js";
import { HUD_ROLLS, renderHud } from "./party.js";
import { setMood, stopSound } from "./sound.js";
import { speak } from "./voice.js";

/* ── the live stream ────────────────────────────────────────────────── */

export let retryDelay = 1500;

export function connect() {
  if (S.es) S.es.close();
  const es = new EventSource(`/api/campaigns/${S.campaign.id}/stream?since=${S.lastSeq}`);
  S.es = es;
  S.replaying = true;

  es.onmessage = (e) => {
    retryDelay = 1500;                 // a live connection resets the backoff
    let ev;
    try { ev = JSON.parse(e.data); } catch (_) { return; }
    handle(ev);
  };

  // EventSource retries on its own, but it would replay from the original
  // `since`. Reconnect by hand so we resume from what we've actually seen —
  // backing off so a phone on a dead network isn't retrying every second.
  es.onerror = () => {
    es.close();
    if (S.es !== es || !S.campaign) return;
    const wait = retryDelay;
    retryDelay = Math.min(retryDelay * 2, 20000);
    setTimeout(() => { if (S.campaign) connect(); }, wait);
  };
}

document.addEventListener("visibilitychange", () => {
  // phones kill background connections; re-open on return, and try immediately
  // rather than waiting out whatever backoff had built up while away
  if (!document.hidden && S.campaign && (!S.es || S.es.readyState === 2)) {
    retryDelay = 1500;
    connect();
  }
});

export function handle(ev) {
  if (ev.seq) {
    if (S.seen.has(ev.seq)) return;      // replay overlap after a reconnect
    S.seen.add(ev.seq);
    S.lastSeq = Math.max(S.lastSeq, ev.seq);
  }

  switch (ev.kind) {
    case "ready":
      // the story so far has been replayed; what comes next is live
      S.replaying = false;
      if (S.ambience && (S.campaign.house || {}).ambience) setMood(S.ambience);
      scrollFeed(true);
      break;

    case "delta":
      if (!S.live) {
        S.live = el("p", "narration", "");
        $("feed").append(S.live);
      }
      S.live.textContent += ev.text;
      scrollFeed();
      break;

    case "narration": {
      // the streamed element becomes the authoritative one; on replay there is none
      const node = S.live || el("p", "narration", "");
      node.textContent = ev.text;
      S.lastScene = ev.text;
      if (!node.isConnected) $("feed").append(node);
      S.live = null;
      if (!S.replaying) speak(ev.text);
      scrollFeed();
      break;
    }

    case "player": {
      const line = el("div", "player-line" + (ev.character === S.campaign.you ? " mine" : ""));
      line.append(el("span", "who", ev.character));
      line.append(document.createTextNode(ev.text));
      $("feed").append(line);
      scrollFeed();
      break;
    }

    case "dice": {
      const chip = el("div", "dice-chip" + (ev.crit ? " crit-" + ev.crit : ""));
      chip.append(el("span", "reason", ev.reason));
      chip.append(el("span", "", ev.detail.replace(/\s+\*\*.*\*\*$/, "")));
      if (ev.crit === "success") chip.append(el("span", "", "NAT 20"));
      if (ev.crit === "fail") chip.append(el("span", "", "NAT 1"));
      appendChip(chip);
      S.rolls.push({ reason: ev.reason, total: ev.total, crit: ev.crit });
      if (S.rolls.length > HUD_ROLLS) S.rolls.shift();
      renderHud();
      renderDash();
      break;
    }

    case "sheet": {
      // render from the structured changes so this reads in the player's language;
      // fall back to the server's English summary for events logged before that existed
      const body = (ev.changes && ev.changes.length)
        ? ev.changes.map(renderChange).filter(Boolean).join(" · ")
        : ev.summary;
      appendChip(el("div", "sheet-chip", `${ev.character}: ${body}`));
      break;
    }

    case "lore":
      // the DM consulted the players' own notes - worth showing, the same way a roll is
      appendChip(el("div", "lore-chip", t("looked_up", ev.query)));
      break;

    case "join":
      appendChip(el("div", "join-chip", ev.text || t("joins", ev.character)));
      break;

    case "combat": {
      S.combat = ev.state || null;
      const lines = [];
      const rolled = (ev.rolled || []).map((e) => `${e.name} ${e.total}`).join(" \u00b7 ");
      if (ev.what === "start") lines.push(t("combat_start", rolled));
      if (ev.what === "join") lines.push(t("combat_join", rolled));
      if ((ev.removed || []).length) lines.push(t("combat_out", ev.removed.join(", ")));
      (ev.expired || []).forEach((x) => lines.push(t("combat_expired", x.character, x.name)));
      const acting = S.combat ? S.combat.order[S.combat.turn % S.combat.order.length] : null;
      if (ev.what === "round" && acting) lines.push(t("combat_round_turn", S.combat.round, acting.name));
      else if (ev.what === "turn" && acting) lines.push(t("combat_turn", acting.name));
      if (ev.what === "end") lines.push(t("combat_end"));
      lines.forEach((line) => appendChip(el("div", "combat-chip", line)));
      renderLive();
      break;
    }

    case "map": {
      // only ever what the party can see - the server never sends the rest
      const had = mapOn();
      S.map = ev.map || null;
      if (had !== mapOn()) renderDash();     // the map card or tab comes or goes
      else redrawAll();
      break;
    }

    case "ambience":
      // during the replay just remember it; the last one is played on "ready"
      S.ambience = ev.mood;
      if (!S.replaying && (S.campaign.house || {}).ambience) setMood(ev.mood);
      break;

    case "rule":
      // the DM looked something up in the rulebook - shown like a roll is
      appendChip(el("div", "lore-chip", t("checked_rules", (ev.found || []).join(", "))));
      break;

    case "memory":
      // the older turns were condensed in the background; nothing to show in the feed
      if (S.campaign) S.campaign.memory = { synopsis: ev.synopsis, turns: ev.turns };
      renderRecap();
      break;

    case "house":
      // a table rule changed - everyone sees who, and the toggle follows
      if (S.campaign) S.campaign.house = { ...(S.campaign.house || {}), ...ev.changed };
      Object.entries(ev.changed || {}).forEach(([rule, on]) => appendChip(el("div",
        "join-chip", t(on ? "house_on" : "house_off", ev.character, t("house_" + HOUSE_KEYS[rule])))));
      renderHouse();
      if ("ambience" in (ev.changed || {})) {
        if (ev.changed.ambience && S.ambience) setMood(S.ambience);
        else stopSound();
      }
      renderLive();                          // the map card follows its rule
      break;

    case "image": {
      const box = el("div", "image-line");
      if (ev.source === "dm") {
        // nobody at the table asked for this one — say where it came from
        box.append(el("div", "who", t("dm_drew")));
      } else if (ev.character && ev.source !== "generated") {
        box.append(el("div", "who", t("shows_image", ev.character)));
      }
      const img = el("img");
      img.src = mediaUrl(ev.media);
      img.alt = ev.caption || "";
      img.loading = "lazy";
      if (ev.width && ev.height) {           // reserve space so the feed doesn't jump
        img.width = ev.width;
        img.height = ev.height;
        img.style.width = "auto";
        img.style.height = "auto";
      }
      img.onclick = () => openLightbox(img.src, ev.caption);
      box.append(img);
      if (ev.caption) box.append(el("div", "cap", ev.caption));
      $("feed").append(box);
      scrollFeed();
      break;
    }

    case "switch": {
      // an AI ran out mid-turn and another picked the game up, or someone
      // switched by hand — either way the table should be told who is narrating
      S.backend = ev.backend;
      // the server discards whatever the failed AI managed to write, so drop the
      // half-finished paragraph here too rather than letting the retry append to it
      if (S.live) { S.live.remove(); S.live = null; }
      const text = ev.manual ? t("ai_switched", ev.label)
                             : t("ai_took_over", ev.from, ev.label);
      appendChip(el("div", "ai-chip", text));
      if (!$("drawer").classList.contains("hidden")) renderAI();
      break;
    }

    case "backend-now":
      S.backend = ev.backend;
      if (!$("drawer").classList.contains("hidden")) renderAI();
      break;

    case "error":
      $("feed").append(el("div", "error-line", ev.text));
      scrollFeed();
      break;

    case "thinking":
      $("thinking").classList.toggle("hidden", !ev.on);
      if (ev.on) S.live = null;
      scrollFeed();
      break;

    case "party":
      S.party = ev.party;
      renderLive();
      if (!$("drawer").classList.contains("hidden")) renderPortrait();
      break;
  }
}

export function appendChip(chip) {
  const feed = $("feed");
  let row = feed.lastElementChild;
  if (!row || !row.classList.contains("chip-line")) {
    row = el("div", "chip-line");
    feed.append(row);
  }
  row.append(chip);
  scrollFeed();
}

export let pinned = true;
$("feed").addEventListener("scroll", () => {
  const f = $("feed");
  pinned = f.scrollHeight - f.scrollTop - f.clientHeight < 90;
});

export function scrollFeed(force) {
  if (!pinned && !force) return;
  const f = $("feed");
  requestAnimationFrame(() => { f.scrollTop = f.scrollHeight; });
}


/* `pinned` is assigned from another module too; imports are read-only. */
export function setPinned(value) { pinned = value; }
