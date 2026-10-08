/* A player's standing notes for the DM. Part of the front end - see js/main.js. */

import { api } from "./core/api.js";
import { $, toast } from "./core/dom.js";
import { S } from "./core/state.js";
import { t } from "./i18n/index.js";

/* ── notes for the DM: this player's own standing details ───────────── */

/* The cap belongs to the server — it trims on save whatever the browser let you type —
   so the box is told that number rather than carrying a second one of its own. */
export function fillNotes() {
  const box = $("notes");
  box.maxLength = S.notesMax;
  box.value = S.notes;
  renderNotesCount();
}

export function renderNotesCount() {
  $("notes-count").textContent = t("notes_count", $("notes").value.length, S.notesMax);
}

$("notes").addEventListener("input", renderNotesCount);

$("btn-notes-save").onclick = async () => {
  const btn = $("btn-notes-save");
  btn.disabled = true;
  try {
    const r = await api(`/api/campaigns/${S.campaign.id}/notes`, {
      method: "POST", body: { notes: $("notes").value },
    });
    S.notes = r.notes;
    $("notes").value = r.notes;      // show what was actually kept, trimming included
    renderNotesCount();
    toast(t("notes_saved"));
  } catch (e) {
    toast(e.message);
  } finally {
    btn.disabled = false;
  }
};
