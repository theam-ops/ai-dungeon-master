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
import { setSoundOn, setVolume, soundOn, soundVolume } from "./sound.js";
import { canSpeak, setVoiceOn, speak, voiceFor, voiceOn } from "./voice.js";

/* What is left in the drawer once the sheet moved to the dashboard: your own things,
   the campaign's art, and the table's settings. Remembered between opens - unlike the
   play tabs, the drawer is modal and you always chose to open it. */
export const DRAWER_TABS = ["you", "art", "table"];
export const DRAWER_ICONS = { you: "scroll", art: "art", table: "gear" };
export let drawerTab = localStorage.getItem("dtab") || "you";
if (!DRAWER_TABS.includes(drawerTab)) drawerTab = "you";

/* The table's optional rules. Server keys on the left, string suffixes on the right. */
export const HOUSE_KEYS = { variant_encumbrance: "variant_enc", battle_map: "battle_map",
                            ambience: "ambience" };

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

const houseBox = (rule) => $("house-" + HOUSE_KEYS[rule].replace(/_/g, "-"));

export function renderHouse() {
  if (!S.campaign) return;
  Object.keys(HOUSE_KEYS).forEach((rule) => {
    const box = houseBox(rule);
    if (box) box.checked = !!(S.campaign.house || {})[rule];
  });
  renderSound();
}

Object.keys(HOUSE_KEYS).forEach((rule) => {
  houseBox(rule).onchange = async (e) => {
    const on = e.target.checked;
    try {
      const r = await api(`/api/campaigns/${S.campaign.id}/house`,
                          { method: "POST", body: { [rule]: on } });
      S.campaign.house = r.house;
    } catch (err) {
      e.target.checked = !on;         // the server said no; show what is actually set
      toast(err.message);
    }
  };
});

/* Sound and voice are this player's own choices, kept in this browser. The sound
   switch only appears when the table plays with ambience - there is nothing to hear
   otherwise - and the voice switch says plainly when the device has no voice for the
   campaign's language, rather than reading Thai in an English accent. */
export function renderSound() {
  const table = !!(S.campaign && (S.campaign.house || {}).ambience);
  $("sound-row").classList.toggle("hidden", !table);
  $("sound-on").checked = soundOn();
  $("sound-vol").value = String(soundVolume());
  $("sound-vol").disabled = !soundOn();
  $("voice-on").checked = voiceOn();
  $("voice-on").disabled = !canSpeak();
  const lang = (S.campaign && S.campaign.lang) || "en";
  $("voice-note").textContent = !canSpeak() ? t("voice_none")
    : voiceFor(lang) ? t("voice_hint") : t("voice_missing_" + lang);
}

$("sound-on").onchange = (e) => { setSoundOn(e.target.checked); renderSound(); };
$("sound-vol").oninput = (e) => setVolume(parseFloat(e.target.value));
$("voice-on").onchange = (e) => {
  setVoiceOn(e.target.checked);
  if (e.target.checked && S.lastScene) speak(S.lastScene);   // hear what it sounds like
};
if (canSpeak()) window.speechSynthesis.addEventListener("voiceschanged", () => {
  if (!$("drawer").classList.contains("hidden")) renderSound();
});

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
              renderSound();
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
