/* The drawer and its tabs. Part of the front end - see js/main.js. */

import { renderAI } from "./ai.js";
import { api } from "./core/api.js";
import { $, el, icon, toast } from "./core/dom.js";
import { S } from "./core/state.js";
import { renderGallery } from "./gallery.js";
import { openGuide } from "./guide.js";
import { t } from "./i18n/index.js";
import { renderPortrait } from "./images.js";
import { renderLore } from "./library.js";
import { fillNotes } from "./notes.js";
import { renderHud } from "./party.js";

/* What is left in the drawer once the sheet moved to the dashboard: your own things,
   the campaign's art, and the table's settings. Remembered between opens - unlike the
   play tabs, the drawer is modal and you always chose to open it. */
export const DRAWER_TABS = ["you", "art", "table"];
export const DRAWER_ICONS = { you: "scroll", art: "art", table: "gear" };
export let drawerTab = localStorage.getItem("dtab") || "you";
if (!DRAWER_TABS.includes(drawerTab)) drawerTab = "you";

/* The table's optional rules. Server keys on the left, string suffixes on the right. */
export const HOUSE_KEYS = { variant_encumbrance: "variant_enc" };

/* The campaign's synopsis, as the DM is given it. Text only - it came from a model. */
export function renderRecap() {
  const box = $("recap");
  if (!box) return;
  const mem = S.campaign && S.campaign.memory;
  box.textContent = mem && mem.synopsis ? mem.synopsis : "";
  box.classList.toggle("hidden", !(mem && mem.synopsis));
  $("recap-meta").textContent = mem && mem.synopsis
    ? t("story_so_far_meta", mem.turns) : t("story_so_far_none");
}

/* The SRD's licence asks for its attribution wherever its text is used. */
export function renderRulebookCredit() {
  const credit = S.campaign && S.campaign.rulebook;
  $("rulebook-credit").classList.toggle("hidden", !credit);
  $("rulebook-attribution").textContent = credit || "";
}

export function renderHouse() {
  const box = $("house-variant-enc");
  if (box && S.campaign) box.checked = !!(S.campaign.house || {}).variant_encumbrance;
}

$("house-variant-enc").onchange = async (e) => {
  const on = e.target.checked;
  try {
    const r = await api(`/api/campaigns/${S.campaign.id}/house`,
                        { method: "POST", body: { variant_encumbrance: on } });
    S.campaign.house = r.house;
  } catch (err) {
    e.target.checked = !on;           // the server said no; show what is actually set
    toast(err.message);
  }
};

export function showDrawerTab(name) {
  drawerTab = name;
  localStorage.setItem("dtab", name);
  document.querySelectorAll(".dtab-page")
    .forEach((p) => p.classList.toggle("hidden", p.dataset.tab !== name));
  const bar = $("drawer-tabs");
  bar.innerHTML = "";
  DRAWER_TABS.forEach((key) => {
    const b = el("button", "gtab" + (key === name ? " on" : ""));
    b.type = "button";
    b.append(icon(DRAWER_ICONS[key]), el("span", "", t("drawer_" + key)));
    b.onclick = () => showDrawerTab(key);
    bar.append(b);
  });
}

export function openDrawer(open) {
  $("drawer").classList.toggle("hidden", !open);
  $("scrim").classList.toggle("hidden", !open);
  if (open) { showDrawerTab(drawerTab);
              renderAI(); renderPortrait(); renderLore(); renderGallery(); fillNotes();
              $("library-note").textContent = t("library_hint"); }
  else $("ai-list").classList.add("hidden");
}
$("btn-sheet").onclick = () => openDrawer(true);
$("hud-toggle").onclick = () => {
  S.hudOpen = !S.hudOpen;
  localStorage.setItem("hud", S.hudOpen ? "1" : "0");
  renderHud();
};
$("scrim").onclick = () => openDrawer(false);

$("btn-private-roll").onclick = async () => {
  const notation = $("private-roll").value.trim() || "1d20";
  try {
    const r = await api("/api/roll", { method: "POST", body: { notation } });
    $("private-result").textContent = r.detail;
  } catch (e) {
    $("private-result").textContent = e.message;
  }
};

document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!$("lightbox").classList.contains("hidden")) {
    $("lightbox").classList.add("hidden");
    return;
  }
  if (!$("guide").classList.contains("hidden")) {
    openGuide(false);
    return;
  }
  openDrawer(false);
});
