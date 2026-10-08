/* Start-up, the lobby, and pasting a key. Part of the front end - see js/main.js. */

import { loadProviders, renderAI } from "./ai.js";
import { enterCampaign } from "./campaign.js";
import { input } from "./composer.js";
import { api } from "./core/api.js";
import { $, el, show, toast } from "./core/dom.js";
import { S } from "./core/state.js";
import { openGuide } from "./guide.js";
import { applyI18n, setLang, t } from "./i18n/index.js";
import { joinByCode } from "./join.js";
import { refreshUI, renderLangBars } from "./lang.js";

/* ── boot ───────────────────────────────────────────────────────────── */

export async function boot() {
  setLang(localStorage.getItem("lang") || (navigator.language || "en").slice(0, 2));
  applyI18n();
  renderLangBars();

  let me;
  try {
    me = await api("/api/me");
  } catch (e) {
    document.body.innerHTML =
      '<section class="screen center"><div class="panel narrow">' +
      `<h1 class="title">${t("offline")}</h1><p class="sub">${t("no_answer")}</p>` +
      "</div></section>";
    return;
  }

  if (!me.authed) { show("login"); $("login-password").focus(); return; }

  $("dm-warning").classList.toggle("hidden", me.dm_ready);
  if (!me.dm_ready) { renderGetKeyLinks(); renderKeyForm("key-form"); }
  renderLobby(me.campaigns);

  // Whether to explain the game, decided before the branching below: the person who
  // most needs it is the friend opening a ?c= link, and they never see the lobby.
  const firstVisit = !localStorage.getItem("guide_seen");

  // deep link: ?c=CODE or a campaign we were mid-game in
  const codeParam = new URLSearchParams(location.search).get("c");
  const resume = localStorage.getItem("campaign_id");
  if (codeParam) {
    $("join-code").value = codeParam.toUpperCase();
    history.replaceState({}, "", location.pathname);
    await joinByCode();
    if (firstVisit) openGuide(true);
    return;
  }
  if (resume) {
    try {
      await enterCampaign(resume);
      if (firstVisit) openGuide(true);
      return;
    } catch (_) { localStorage.removeItem("campaign_id"); }
  }
  show("lobby");
  if (firstVisit) openGuide(true);
}

export function renderLobby(campaigns) {
  const list = $("campaign-list");
  list.innerHTML = "";
  $("continue-block").classList.toggle("hidden", !campaigns || !campaigns.length);
  (campaigns || []).forEach((c) => {
    // the row is a button, so the delete control is its sibling rather than nested
    // inside it - a button within a button is invalid and swallows the click
    const item = el("div", "campaign-item");
    const row = el("button", "campaign-row");
    const left = el("div");
    left.append(el("div", "cname", c.name));
    left.append(el("div", "cmeta", t("playing_as", c.character_name)));
    row.append(left, el("div", "ccode", c.code));
    row.onclick = () => enterCampaign(c.id).catch((e) => ($("lobby-err").textContent = e.message));

    const drop = el("button", "campaign-del", "×");
    drop.type = "button";
    drop.title = t("delete_campaign");
    drop.setAttribute("aria-label", t("delete_campaign"));
    drop.onclick = () => confirmDelete(item, c);

    item.append(row, drop);
    list.append(item);
  });
}

/* Deleting takes the story and its pictures with it and there is no undo, so it asks
   first - in the row itself rather than through a browser dialog, which is easy to
   dismiss without reading and which some browsers suppress outright. */
export function confirmDelete(item, c) {
  const saved = [...item.childNodes];        // put back on cancel
  const ask = el("div", "campaign-confirm");
  ask.append(el("div", "warn", t("delete_sure", c.name)));

  const yes = el("button", "danger", t("delete_yes"));
  yes.type = "button";
  yes.onclick = async () => {
    yes.disabled = true;
    try {
      await api(`/api/campaigns/${c.id}`, { method: "DELETE" });
      if (localStorage.getItem("campaign_id") === c.id) localStorage.removeItem("campaign_id");
      await refreshUI();
      toast(t("deleted", c.name));
    } catch (e) {
      $("lobby-err").textContent = e.message;
      yes.disabled = false;
    }
  };

  const no = el("button", "ghost", t("cancel"));
  no.type = "button";
  no.onclick = () => { ask.replaceWith(...saved); };

  const buttons = el("div", "row");
  buttons.append(yes, no);
  ask.append(buttons);
  item.replaceChildren(ask);
  yes.focus();
}

/* Paste an API key straight into the running server — no setx, no restart.
   The server only accepts this from localhost, or from someone who has passed the
   app password, so a public instance can't have keys set by a stranger. */
export async function renderKeyForm(id) {
  const box = $(id);
  if (!box) return;
  await loadProviders();

  const needy = S.providers.filter((p) => !p.available && p.key_env);
  const envs = [...new Map(needy.map((p) => [p.key_env, p])).values()];

  if (!S.canSetKeys || !envs.length) { box.classList.add("hidden"); return; }

  box.innerHTML = "";
  box.classList.remove("hidden");
  box.append(el("div", "keyform-title", t("paste_key")));

  const form = el("form", "keyform-row");

  const pick = el("select", "keyselect");
  pick.title = t("key_which");
  envs.forEach((p) => {
    const o = el("option", "", providerFamily(p) + " — " + p.key_env);
    o.value = p.key_env;
    pick.append(o);
  });

  const input = el("input", "keyinput");
  input.type = "password";
  input.placeholder = t("key_ph");
  input.autocomplete = "off";
  input.spellcheck = false;

  const save = el("button", "ghost");
  save.type = "submit";
  save.textContent = t("key_save");

  form.append(pick, input, save);
  box.append(form);

  const note = el("p", "hint", t("key_stored"));
  box.append(note);

  form.onsubmit = async (e) => {
    e.preventDefault();
    const key = input.value.trim();
    if (!key) return;
    save.disabled = true;
    save.textContent = t("key_saving");
    note.className = "hint";
    note.textContent = t("key_saving");
    try {
      const r = await api("/api/keys", {
        method: "POST", body: { env: pick.value, key },
      });
      S.providers = r.providers;
      input.value = "";
      if (r.ok) {
        const ready = S.providers.find((p) => p.key_env === pick.value && p.available);
        note.className = "hint good";
        note.textContent = t("key_ok", ready ? ready.label : pick.value);
        toast(t("key_ok", ready ? ready.label : pick.value));
        setTimeout(() => boot(), 900);         // the lobby can now start a campaign
      } else {
        note.className = "err";
        note.textContent = t("key_bad", r.message || "");
      }
    } catch (err) {
      note.className = "err";
      note.textContent = t("key_bad", err.message);
    } finally {
      save.disabled = false;
      save.textContent = t("key_save");
      renderAI();
    }
  };
}

export function providerFamily(p) {
  return { anthropic: "Claude", gemini: "Google Gemini", groq: "Groq",
           openrouter: "OpenRouter" }[p.kind] || p.label;
}

/* Nothing is configured yet: show where to get a key, one click away. */
export async function renderGetKeyLinks() {
  const box = $("get-key-links");
  box.innerHTML = "";
  await loadProviders();

  const seen = new Set();
  S.providers
    .filter((p) => p.key_url && p.free && !seen.has(p.kind) && seen.add(p.kind))
    .forEach((p) => {
      const a = el("a", "getkey", p.kind === "ollama"
        ? t("get_ollama") : t("get_key_from", p.kind === "gemini" ? "Google Gemini" : "Groq"));
      a.href = p.key_url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      box.append(a);
    });
  box.classList.remove("hidden");
}
