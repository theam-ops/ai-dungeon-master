/* The password screen. Part of the front end - see js/main.js. */

import { boot } from "./boot.js";
import { api } from "./core/api.js";
import { $ } from "./core/dom.js";

/* ── login ──────────────────────────────────────────────────────────── */

$("login-form").onsubmit = async (e) => {
  e.preventDefault();
  $("login-err").textContent = "";
  try {
    await api("/api/login", { method: "POST", body: { password: $("login-password").value } });
    boot();
  } catch (err) {
    $("login-err").textContent = err.message;
  }
};
