/* The party bar and the HUD. Part of the front end - see js/main.js. */

import { $, el } from "./core/dom.js";
import { S } from "./core/state.js";
import { openDrawer } from "./drawer.js";
import { t } from "./i18n/index.js";

/* ── party bar & sheet ──────────────────────────────────────────────── */

export function renderParty() {
  const bar = $("party-bar");
  bar.innerHTML = "";
  S.party.forEach((c) => {
    const box = el("div", "pc" + (c.name === S.campaign.you ? " you" : "") +
                             (c.hp === 0 ? " down" : ""));
    if (c.portrait) {
      const face = el("img", "face");
      face.src = `/api/campaigns/${S.campaign.id}/media/${c.portrait}`;
      face.alt = "";
      box.append(face);
    }
    box.append(el("div", "n", c.name));
    const track = el("div", "hpbar");
    const fill = el("div", "hpfill");
    fill.style.width = Math.max(0, Math.round((c.hp / c.max_hp) * 100)) + "%";
    if (c.hp / c.max_hp < 0.34) fill.style.background = "var(--red)";
    track.append(fill);
    box.append(track, el("div", "hptext", `${c.hp}/${c.max_hp}`));
    bar.append(box);
  });
}

export const HUD_ROLLS = 4;

/* The HUD is the character sheet's headline, kept on screen: whoever you are
   playing, their HP and AC, anything wrong with them, and the last few rolls.
   Everything it needs already arrives on the party and dice events. */
export function renderHud() {
  const hud = $("hud");
  const c = S.party.find((p) => p.name === (S.campaign && S.campaign.you));
  if (!c) { hud.classList.add("hidden"); return; }

  hud.classList.remove("hidden");
  hud.classList.toggle("collapsed", !S.hudOpen);
  $("hud-toggle").title = t(S.hudOpen ? "hud_collapse" : "hud_expand");

  const body = $("hud-body");
  body.innerHTML = "";

  // tapping the vitals opens the full sheet — the HUD is a summary, not a replacement
  const vitals = el("div", "hud-vitals");
  vitals.title = t("char_sheet");
  vitals.onclick = () => openDrawer(true);
  vitals.append(el("div", "hud-who", c.name));

  const frac = c.max_hp ? c.hp / c.max_hp : 0;
  const hp = el("div", "hud-hp" + (frac < 0.34 ? " low" : "") + (c.hp === 0 ? " down" : ""));
  const track = el("div", "hud-track");
  const fill = el("div", "hud-fill");
  fill.style.width = Math.max(0, Math.min(100, Math.round(frac * 100))) + "%";
  track.append(fill);
  hp.append(track, el("div", "hud-num", `${t("hp")} ${c.hp}/${c.max_hp}`));
  vitals.append(hp);

  const ac = el("div", "hud-ac");
  ac.append(document.createTextNode(t("ac") + " "), el("b", "", String(c.ac)));
  vitals.append(ac);
  body.append(vitals);

  if (c.conditions && c.conditions.length) {
    const conds = el("div", "hud-conds");
    c.conditions.forEach((x) => conds.append(el("span", "hud-cond", x)));
    body.append(conds);
  }

  const rolls = el("div", "hud-rolls");
  // newest first, so the roll that just landed is the one nearest the composer
  S.rolls.slice().reverse().forEach((r) => {
    const pill = el("div", "hud-roll" + (r.crit ? " crit-" + r.crit : ""));
    pill.title = r.reason || "";
    // The DM writes the reason for the feed, not for a pill: "Tamar: Athletics (STR)
    // to haul the mule back onto the trail vs DC 15" is a normal one. Drop the name -
    // this row is already yours - and let the rest truncate, so the total, which is the
    // whole point of the pill, never gets squeezed off the end. Full text is the title.
    const why = (r.reason || t("roll")).replace(/^[^:]{1,24}:\s*/, "");
    pill.append(el("span", "hud-why", why), el("b", "", String(r.total)));
    rolls.append(pill);
  });
  body.append(rolls);
}
