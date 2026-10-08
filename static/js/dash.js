/* The dashboard: cards, play views, layout. Part of the front end - see js/main.js. */

import { input } from "./composer.js";
import { $, el, icon } from "./core/dom.js";
import { S } from "./core/state.js";
import { t, tClass, tRace, tSkill, tStat } from "./i18n/index.js";
import { mediaUrl, openLightbox } from "./images.js";
import { renderHud, renderParty } from "./party.js";
import { scrollFeed } from "./stream.js";

/* ── the dashboard ──────────────────────────────────────────────────────
   One set of card builders, two shells. On a wide screen they fill a panel beside the
   story; on a phone they fill the same box behind tabs, because there is no room for
   both. Every builder returns a node and reads only from S.party, so it does not care
   which shell it lands in. */

export const SKILL_ABILITY = {
  Athletics: "STR",
  Acrobatics: "DEX", "Sleight of Hand": "DEX", Stealth: "DEX",
  Arcana: "INT", History: "INT", Investigation: "INT", Nature: "INT", Religion: "INT",
  "Animal Handling": "WIS", Insight: "WIS", Medicine: "WIS",
  Perception: "WIS", Survival: "WIS",
  Deception: "CHA", Intimidation: "CHA", Performance: "CHA", Persuasion: "CHA",
};

export const DESKTOP = window.matchMedia("(min-width: 1024px)");
export const signed = (n) => (n >= 0 ? "+" : "") + n;
export const abilityMod = (c, a) => Math.floor((c.abilities[a] - 10) / 2);

/* Mirrors rules.proficiency_bonus: +2 at level 1-4, a point every four after. */
export const proficiencyBonus = (level) => 2 + Math.floor(Math.max(0, (level || 1) - 1) / 4);

export function skillTotal(c, skill) {
  const base = abilityMod(c, SKILL_ABILITY[skill]);
  return base + ((c.skills || []).includes(skill) ? proficiencyBonus(c.level) : 0);
}

export function card(titleKey, iconName) {
  const box = el("section", "card");
  if (titleKey) {
    const h = el("h3", "card-title");
    if (iconName) h.append(icon(iconName));
    h.append(el("span", "", t(titleKey)));
    box.append(h);
  }
  return box;
}

/* The working behind an AC, in the player's language: "chain mail 16 · DEX +0 · shield +2".
   Item names arrive as the sheet spells them, so a Thai campaign's read in Thai. */
export function acWorking(c) {
  const signed = (v) => (v >= 0 ? "+" : "\u2212") + Math.abs(v);
  return (c.ac_parts || []).map((p) => {
    switch (p.k) {
      case "armor": return `${p.item} ${p.v}`;
      case "base":  return `${p.item || t("ac_unarmoured")} ${p.v}`;
      case "dex":   return `${tStat("DEX")} ${signed(p.v)}` + (p.capped ? ` ${t("ac_capped")}` : "");
      case "floor": return t("ac_floor", p.item, p.v);
      default:      return `${p.item} ${signed(p.v)}`;
    }
  }).join(" \u00b7 ");
}

export function cardVitals(c) {
  const box = card();
  const head = el("div", "who-row");
  if (c.portrait) {
    const face = el("img", "who-face");
    face.src = mediaUrl(c.portrait);
    face.alt = c.name;
    face.onclick = () => openLightbox(face.src, c.name);
    head.append(face);
  }
  const names = el("div");
  names.append(el("div", "who-name", c.name));
  names.append(el("div", "who-sub", t("level_line", c.level, tRace(c.race), tClass(c.class))));
  head.append(names);
  box.append(head);

  const frac = c.max_hp ? c.hp / c.max_hp : 0;
  const hp = el("div", "dash-hp" + (frac < 0.34 ? " low" : ""));
  const hpLabel = el("div", "dash-hpnum");
  hpLabel.append(icon("hp"), el("span", "", t("hp") + " " + c.hp + "/" + c.max_hp));
  const track = el("div", "dash-track");
  const fill = el("div", "dash-fill");
  fill.style.width = Math.max(0, Math.min(100, Math.round(frac * 100))) + "%";
  track.append(fill);
  hp.append(hpLabel, track);
  box.append(hp);

  const vitals = el("div", "sheet-vitals");
  [["ac", t("ac"), c.ac], ["xp", t("xp"), c.xp], ["gp", t("gp"), c.gold]]
    .forEach((row) => {
      const cell = el("div", "vital");
      const k = el("div", "k");
      k.append(icon(row[0]), el("span", "", row[1]));
      cell.append(k, el("div", "v", String(row[2])));
      vitals.append(cell);
    });
  box.append(vitals);

  // AC is worked out from what they wear, so say how - on a phone there is no hover
  const working = acWorking(c);
  if (working) {
    const line = el("div", "ac-from");
    line.append(icon("ac"), el("span", "", working));
    box.append(line);
  }

  return box;
}

export function cardItems(c) {
  const box = card("carrying", "scroll");
  const list = el("div", "item-list");
  if (!c.inventory.length) {
    box.append(el("p", "hint", t("nothing")));
    return box;
  }
  c.inventory.forEach((i) => list.append(el("span", "item", i)));
  box.append(list);

  // weight against capacity, under this table's rules - worked out on the server
  if (c.load) {
    const l = c.load;
    const status = { "over capacity": "load_over", "heavily encumbered": "load_heavy",
                     "encumbered": "load_enc" }[l.status];
    const parts = [t("load", l.carried, l.capacity)];
    if (l.unweighed) parts.push(t("load_unweighed", l.unweighed));
    const line = el("div", "load-line" + (status ? " strained" : ""));
    line.append(el("span", "", parts.join(" \u00b7 ")));
    if (status) line.append(el("span", "load-status", t(status)));
    box.append(line);
  }
  return box;
}

/* The initiative order: who acts, in what order, and whose turn it is now. Shown to
   everyone, because the order is the table's, not the DM's secret. */
export function cardCombat() {
  const c = S.combat;
  if (!c || !c.order || !c.order.length) return null;
  const box = card("initiative", "dice");
  box.classList.add("combat-card");
  box.append(el("div", "combat-round", t("combat_round", c.round)));
  const list = el("ol", "combat-order");
  const me = S.campaign && S.campaign.you;
  c.order.forEach((e, i) => {
    const now = i === c.turn;
    const row = el("li", "combatant" + (now ? " now" : "") + (e.pc ? "" : " npc")
                         + (e.name === me ? " me" : ""));
    if (now) row.setAttribute("aria-current", "step");
    row.append(el("span", "combatant-total", String(e.total)),
               el("span", "combatant-name", e.name));
    if (!e.pc) row.append(el("span", "combatant-tag", t("combat_npc")));
    if (now) row.append(el("span", "combatant-now", t("combat_now")));
    list.append(row);
  });
  box.append(list);
  return box;
}

/* The composer says whose turn it is - without stopping anyone. Acting out of turn is
   allowed; the DM is told and fits it in as a reaction or on their turn. */
export function renderTurnHint() {
  const c = S.combat;
  const acting = c && c.order && c.order.length ? c.order[c.turn % c.order.length] : null;
  const me = S.campaign && S.campaign.you;
  input.placeholder = acting && acting.pc && acting.name !== me
    ? t("not_your_turn", acting.name) : t("what_do");
}

/* What a caster has left, one row of pips per spell level. Nothing for anyone else -
   including a level-1 ranger, who casts nothing yet. */
export function cardSlots(c) {
  const levels = Object.keys(c.slots || {});
  if (!levels.length) return null;
  const box = card("spell_slots", "skill");
  levels.forEach((lvl) => {
    const [left, max] = c.slots[lvl];
    const row = el("div", "slot-row");
    const pips = el("span", "slot-pips");
    pips.setAttribute("role", "img");
    pips.setAttribute("aria-label", t("slots_left", left, max));
    for (let i = 0; i < max; i++) pips.append(el("span", "pip" + (i < left ? " on" : "")));
    row.append(el("span", "slot-lvl", t("slot_level", lvl)), pips,
               el("span", "slot-count", `${left}/${max}`));
    box.append(row);
  });
  return box;
}

export function cardAbilities(c) {
  const box = card("abilities", "skill");
  const stats = el("div", "stats");
  Object.keys(c.abilities).forEach((a) => {
    const cell = el("div", "stat");
    const k = el("div", "k");
    k.append(icon(a, "ab-" + a), el("span", "", tStat(a)));
    cell.append(k, el("div", "v", String(c.abilities[a])),
                el("div", "m", signed(abilityMod(c, a))));
    stats.append(cell);
  });
  box.append(stats);
  return box;
}

export function cardSkills(c) {
  // "skills" is the skill-name vocabulary map, not a string - t() would hand back the
  // whole object and render "[object Object]"
  const box = card("view_skills", "skill");
  const prof = new Set(c.skills || []);
  box.append(el("p", "hint", t("proficiency_is", signed(proficiencyBonus(c.level)))));

  const list = el("div", "skill-list");
  // yours first. Eighteen rows in alphabetical order buries the three you are actually
  // trained in, which is the only part of this list you go looking for mid-scene.
  Object.keys(SKILL_ABILITY)
    .sort((a, b) => (prof.has(b) - prof.has(a)) || tSkill(a).localeCompare(tSkill(b)))
    .forEach((skill, i) => {
      if (i === prof.size && prof.size) list.append(el("div", "skill-split"));
      const ability = SKILL_ABILITY[skill];
      const row = el("div", "skill-row" + (prof.has(skill) ? " prof" : ""));
      const name = el("div", "name");
      name.append(icon(ability, "ab-" + ability), el("span", "", tSkill(skill)));
      row.append(name);
      if (prof.has(skill)) {
        const mark = el("span", "prof-dot");
        mark.title = t("proficient");
        mark.setAttribute("aria-label", t("proficient"));
        row.append(mark);
      }
      row.append(el("div", "num", signed(skillTotal(c, skill))));
      list.append(row);
    });
  box.append(list);
  return box;
}

export function cardConditions(c) {
  if (!c.conditions || !c.conditions.length) return null;
  const box = card("conditions", "cond");
  const chips = el("div", "cond-chips");
  // verbatim, including duplicates in two languages: the DM writes these as free text,
  // and a chip that shows the truth beats one that shows a filtered lie
  c.conditions.forEach((x) => chips.append(el("span", "hud-cond", x)));
  box.append(chips);
  return box;
}

export function cardParty() {
  const box = card("party", "party");
  const list = el("div", "party-list");
  S.party.forEach((p) => {
    const row = el("div", "party-row" + (p.name === S.campaign.you ? " you" : ""));
    if (p.portrait) {
      const face = el("img", "party-face");
      face.src = mediaUrl(p.portrait);
      face.alt = "";
      row.append(face);
    }
    const who = el("div", "party-who");
    who.append(el("div", "party-name", p.name));
    who.append(el("div", "party-sub",
                  t("level_line", p.level, tRace(p.race), tClass(p.class))));
    const frac = p.max_hp ? p.hp / p.max_hp : 0;
    const track = el("div", "dash-track");
    const fill = el("div", "dash-fill");
    fill.style.width = Math.max(0, Math.round(frac * 100)) + "%";
    if (frac < 0.34) fill.style.background = "var(--red)";
    track.append(fill);
    who.append(track);
    row.append(who, el("div", "party-hp", p.hp + "/" + p.max_hp));
    list.append(row);
  });
  box.append(list);
  return box;
}

export function cardRolls() {
  if (!S.rolls.length) return null;
  const box = card("recent_rolls", "dice");
  const list = el("div", "roll-list");
  S.rolls.slice().reverse().forEach((r) => {
    const row = el("div", "roll-row" + (r.crit ? " crit-" + r.crit : ""));
    row.append(el("span", "why", r.reason || t("roll")));
    row.append(el("b", "", String(r.total)));
    list.append(row);
  });
  box.append(list);
  return box;
}

/* Which shell, and what goes in it. */
/* Status, items and skills are what you check mid-scene, so they share one page.
   Abilities and the roll log are reference, and go behind their own tab. */
export const VIEWS = ["story", "character", "detail", "party"];
export const VIEW_ICONS = { story: "story", character: "scroll", detail: "skill", party: "party" };

/* Not persisted, deliberately. The HUD's collapsed state is remembered because it is a
   strip; a remembered view tab means closing the app on "Skills" and reopening it to no
   story at all. */
export let view = "story";

export function renderDash() {
  const box = $("dash-body");
  if (!box) return;
  const c = S.party.find((p) => p.name === (S.campaign && S.campaign.you));
  box.innerHTML = "";
  const wide = DESKTOP.matches;

  if (c) {
    if (wide) {
      // all of it at once; the topbar strip already carries the party on a wide screen
      [cardCombat(), cardVitals(c), cardConditions(c), cardSlots(c), cardItems(c),
       cardSkills(c), cardAbilities(c), cardRolls()].forEach((n) => n && box.append(n));
    } else if (view === "character") {
      [cardCombat(), cardVitals(c), cardConditions(c), cardSlots(c), cardItems(c),
       cardSkills(c)]
        .forEach((n) => n && box.append(n));
    } else if (view === "detail") {
      [cardAbilities(c), cardRolls()].forEach((n) => n && box.append(n));
    } else if (view === "party") {
      [cardCombat(), cardParty()].forEach((n) => n && box.append(n));
    }
  }

  // on a phone the panel replaces the story, so the tabs are the only way back to it
  $("dash").classList.toggle("hidden", wide ? false : view === "story");
  $("feed").classList.toggle("hidden", !wide && view !== "story");
  $("view-tabs").classList.toggle("hidden", wide);
  renderViewTabs();
}

export function renderViewTabs() {
  const bar = $("view-tabs");
  bar.innerHTML = "";
  if (DESKTOP.matches) return;
  VIEWS.forEach((name) => {
    const b = el("button", "gtab" + (name === view ? " on" : ""));
    b.type = "button";
    b.append(icon(VIEW_ICONS[name]), el("span", "", t("view_" + name)));
    b.onclick = () => {
      view = name;
      renderDash();
      if (name === "story") scrollFeed(true);
    };
    bar.append(b);
  });
}

/* Anything that changes a character changes all three of these. */
export function renderLive() {
  renderParty();
  renderHud();
  renderDash();
  renderTurnHint();
}

/* Re-lay-out when the screen changes shape - a rotated phone, a dragged window.
   `matchMedia` change is the tidier event but does not fire under every emulated
   viewport, and being wrong here strands someone with a hidden panel and no tabs.
   Caching the last known width to skip renders looked like an optimisation and was a
   bug: the cached flag and what the render actually read could disagree, leaving the
   layout stuck mid-flip. So: always re-render, once per frame. */
export let dashTimer = 0;
export function onViewportChange() {
  // a timer, not requestAnimationFrame: rAF is paused while the page is not
  // compositing - a background tab, a hidden window - so a resize there would never
  // be answered, and the layout would still be wrong when it came back
  clearTimeout(dashTimer);
  dashTimer = setTimeout(renderDash, 100);
}
DESKTOP.addEventListener("change", onViewportChange);
window.addEventListener("resize", onViewportChange);

/* Putting the panel away. Only meaningful on a wide screen - on a phone the tabs
   already decide what is showing - so the buttons are CSS-hidden below the breakpoint
   rather than conditionally built. Remembered, like the HUD's own collapsed state. */
export function showDash(on) {
  $("screen-game").classList.toggle("dash-off", !on);
  localStorage.setItem("dash", on ? "1" : "0");
}
$("dash-toggle").onclick = () => showDash(false);
$("dash-show").onclick = () => showDash(true);


/* `view` is assigned from another module too; imports are read-only. */
export function setView(value) { view = value; }
