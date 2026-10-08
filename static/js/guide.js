/* The first-run guide. Part of the front end - see js/main.js. */

import { $, el, icon } from "./core/dom.js";
import { t } from "./i18n/index.js";

/* ── the first-run guide ────────────────────────────────────────────────
   Four things a new player needs and cannot guess: that the dice are real, that one
   person hosts and the rest just open a link, where the character lives, and that the
   DM has to be told what it is. Opens once by itself; the button reopens it. */

export const GUIDE_STEPS = [
  ["dice",   "guide_dice"],
  ["party",  "guide_together"],
  ["scroll", "guide_sheet"],
  ["gear",   "guide_dm"],
];

export function renderGuide() {
  const box = $("guide-steps");
  box.innerHTML = "";
  GUIDE_STEPS.forEach(([ic, key]) => {
    const row = el("div", "guide-step");
    const badge = el("div", "guide-ic");   // the box is the wrapper's, not the svg's
    badge.append(icon(ic));
    row.append(badge);
    const body = el("div");
    body.append(el("div", "guide-h", t(key + "_h")));
    body.append(el("div", "guide-p", t(key + "_p")));
    row.append(body);
    box.append(row);
  });
}

export function openGuide(on) {
  if (on) renderGuide();
  $("guide").classList.toggle("hidden", !on);
  $("guide-scrim").classList.toggle("hidden", !on);
  if (!on) localStorage.setItem("guide_seen", "1");
  else $("guide-close").focus();
}

$("btn-guide").onclick = () => openGuide(true);
$("guide-close").onclick = () => openGuide(false);
$("guide-go").onclick = () => openGuide(false);
$("guide-scrim").onclick = () => openGuide(false);
