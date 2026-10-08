/* The message box. Part of the front end - see js/main.js. */

import { api } from "./core/api.js";
import { $, toast } from "./core/dom.js";
import { S } from "./core/state.js";
import { renderAttachments } from "./images.js";
import { setPinned } from "./stream.js";

/* ── acting ─────────────────────────────────────────────────────────── */

export const input = $("input");

input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 140) + "px";
});

// plain Enter sends on a keyboard; on touch it makes a newline and you tap send
export const touch = window.matchMedia("(pointer: coarse)").matches;
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && (!touch || e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    $("composer").requestSubmit();
  }
});

$("composer").onsubmit = async (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text || !S.campaign) return;
  input.value = "";
  input.style.height = "auto";
  setPinned(true);
  const media = S.attached.map((m) => m.id);
  S.attached = [];
  renderAttachments();
  try {
    await api(`/api/campaigns/${S.campaign.id}/act`,
              { method: "POST", body: { text, media } });
  } catch (err) {
    toast(err.message);
    input.value = text;
  }
};
