/* Switching the interface language. Part of the front end - see js/main.js. */

import { renderAI } from "./ai.js";
import { renderLobby } from "./boot.js";
import { api } from "./core/api.js";
import { $, el } from "./core/dom.js";
import { S } from "./core/state.js";
import { loadOptions, renderChips } from "./create.js";
import { renderLive } from "./dash.js";
import { renderRecap } from "./drawer.js";
import { renderGuide } from "./guide.js";
import { applyI18n, getLang, setLang, t } from "./i18n/index.js";
import { renderPick } from "./join.js";
import { renderNotesCount } from "./notes.js";

/* ── language ───────────────────────────────────────────────────────── */

export const LANGS = { en: "English", th: "ไทย" };

export function renderLangBars() {
  ["lang-login", "lang-lobby", "lang-game"].forEach((id) => {
    const bar = $(id);
    if (!bar) return;
    bar.innerHTML = "";
    Object.entries(LANGS).forEach(([code, label]) => {
      const b = el("button", "chip" + (code === getLang() ? " on" : ""), label);
      b.type = "button";
      b.onclick = () => switchLang(code);
      bar.append(b);
    });
  });
}

export async function switchLang(code) {
  if (code === getLang()) return;
  setLang(code);
  S.options = null;                 // gear labels are localised server-side
  await refreshUI();
}

/* Re-render every visible piece of text in the new language. */
export async function refreshUI() {
  applyI18n();
  renderLangBars();
  $("create-title").textContent = S.pending ? t("join_party") : t("roll_character");
  $("btn-create-go").textContent = S.pending ? t("join") : t("begin");
  $("dm-lang-note").textContent = `${t("dm_language")} ${LANGS[getLang()]}`;
  if (!$("screen-create").classList.contains("hidden")) {
    await loadOptions();
    renderChips();
  }
  if (S.pending) renderPick(S.pending);
  // the steps are built in JS, so applyI18n above does not reach them
  if (!$("guide").classList.contains("hidden")) renderGuide();
  if (S.campaign) {
    renderLive();
    renderRecap();
    // the count only — re-filling the box would throw away notes being typed right now
    if (!$("drawer").classList.contains("hidden")) {
      renderAI(); renderNotesCount();
    }
  }
  if (!$("screen-lobby").classList.contains("hidden")) {
    try { renderLobby((await api("/api/me")).campaigns); } catch (_) {}
  }
}
