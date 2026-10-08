/* Which AI runs the game, and signing Claude Code in. Part of the front end - see js/main.js. */

import { renderKeyForm } from "./boot.js";
import { api } from "./core/api.js";
import { $, el, toast } from "./core/dom.js";
import { S } from "./core/state.js";
import { t } from "./i18n/index.js";

/* ── which AI runs the game ─────────────────────────────────────────── */

export async function loadProviders() {
  if (!S.providers.length) {
    try {
      const r = await api("/api/providers");
      S.providers = r.providers;
      S.canSetKeys = !!r.can_set_keys;
    } catch (_) { S.providers = []; }
  }
  return S.providers;
}

export function backendLabel(id) {
  const p = S.providers.find((x) => x.id === id);
  return p ? p.label : id;
}

export async function renderAI() {
  await loadProviders();
  const usable = S.providers.filter((p) => p.available);

  $("ai-current").textContent = backendLabel(S.backend);
  $("ai-note").textContent = usable.length > 1 ? t("ai_note") : t("ai_note_alone");

  const list = $("ai-list");
  list.innerHTML = "";
  S.providers.forEach((p) => {
    const left = el("div");
    left.append(el("div", "ai-name", p.label));
    left.append(el("div", "ai-model", p.model));

    if (p.available) {
      const row = el("button", "ai-row" + (p.id === S.backend ? " on" : ""));
      row.type = "button";
      row.append(left);
      if (p.free) row.append(el("span", "ai-tag free",
                                p.kind === "ollama" ? t("ai_local") : t("ai_free")));
      // Claude Code being installed doesn't mean it's signed in, and the difference only
      // shows up when a turn fails. Ask it, and say so here instead.
      if (p.kind === "claude-code") checkSignIn(row, p);
      row.onclick = () => chooseAI(p);
      list.append(row);
      return;
    }

    // Not usable yet. Rather than a dead grey row, offer the way to fix it: a link
    // straight to the page that hands out the key (or installs Ollama).
    const row = el("div", "ai-row off");
    row.append(left);

    // Signed out is the state where the sign-in button is most wanted, and it is also
    // the state where this backend reports itself unavailable - so ask here too, not
    // only on the available path.
    if (p.kind === "claude-code") {
      checkSignIn(row, p);
      list.append(row);
      return;
    }

    if (p.key_url) {
      // Ollama is installed, not keyed - don't send people looking for a key page
      // that doesn't exist
      const get = el("a", "ai-get" + (p.free ? " free" : ""),
                     p.kind === "ollama" ? t("ai_install") : t("ai_get_key"));
      get.href = p.key_url;
      get.target = "_blank";
      get.rel = "noopener noreferrer";
      get.title = p.key_env ? t("ai_set_env", p.key_env) : p.key_url;
      row.append(get);
    } else {
      row.append(el("span", "ai-tag", t("ai_no_key")));
    }
    list.append(row);
  });
}

export async function checkSignIn(row, p) {
  const tag = el("span", "ai-tag", t("ai_checking"));
  row.append(tag);
  let state;
  try {
    state = await api("/api/claude/status");
  } catch (_) {
    tag.remove();               // couldn't ask; don't claim either way
    return;
  }
  if (!state.installed) {
    // nothing to sign into: this machine has no Claude Code
    tag.remove();
    const get = el("a", "ai-get", t("ai_install_claude"));
    get.href = p.key_url || "https://claude.com/download";
    get.target = "_blank";
    get.rel = "noopener noreferrer";
    row.append(get);
    $("claude-login").classList.add("hidden");
    return;
  }

  tag.className = "ai-tag " + (state.logged_in ? "free" : "warn");
  tag.textContent = state.logged_in ? t("ai_signed_in") : t("ai_sign_in");

  // the sign-in runs on the machine hosting the game, so only that machine is offered it
  const box = $("claude-login");
  box.classList.toggle("hidden", state.logged_in);
  if (state.logged_in) return;
  $("btn-claude-login").classList.toggle("hidden", !state.can_sign_in);
  $("claude-login-note").textContent = state.can_sign_in ? "" : t("claude_signin_remote");
}

$("btn-claude-login").onclick = async () => {
  const note = $("claude-login-note");
  note.textContent = t("claude_signin_busy");
  try {
    const { url } = await api("/api/claude/login", { method: "POST" });
    const link = $("claude-login-url");
    link.href = url;
    $("claude-login-step").classList.remove("hidden");
    note.textContent = "";
    window.open(url, "_blank", "noopener");
    $("claude-login-code").focus();
    // Claude Code opens a browser too, and if you are already signed in to claude.ai the
    // flow finishes on its own without a code ever being shown. Watch for that rather
    // than leaving someone staring at a box they don't need to fill in.
    watchSignIn();
  } catch (err) {
    note.textContent = err.message;
  }
};

export async function watchSignIn(tries = 45) {
  for (let i = 0; i < tries; i++) {
    await new Promise((r) => setTimeout(r, 2000));
    if ($("claude-login-step").classList.contains("hidden")) return;   // done by hand
    let state;
    try { state = await api("/api/claude/status"); } catch (_) { return; }
    if (state.logged_in) {
      $("claude-login-step").classList.add("hidden");
      $("claude-login-note").textContent = t("claude_signin_ok");
      await renderAI();
      return;
    }
  }
}

$("btn-claude-code").onclick = async () => {
  const code = $("claude-login-code").value.trim();
  if (!code) return;
  const note = $("claude-login-note");
  note.textContent = t("claude_signin_busy");
  try {
    await api("/api/claude/login/code", { method: "POST", body: { code } });
    $("claude-login-code").value = "";
    $("claude-login-step").classList.add("hidden");
    note.textContent = t("claude_signin_ok");
    await renderAI();           // the row should go green now
  } catch (err) {
    note.textContent = err.message;
  }
};

export async function chooseAI(p) {
  $("ai-list").classList.add("hidden");
  if (p.id === S.backend) return;
  try {
    await api(`/api/campaigns/${S.campaign.id}/provider`, {
      method: "POST", body: { backend: p.id },
    });
    S.backend = p.id;
    renderAI();
  } catch (e) {
    toast(e.message);
  }
}

$("btn-ai").onclick = async () => {
  await renderAI();
  const open = $("ai-list").classList.toggle("hidden");
  if (!open) renderKeyForm("key-form-game");
  else $("key-form-game").classList.add("hidden");
};
