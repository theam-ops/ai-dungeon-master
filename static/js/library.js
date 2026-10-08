/* The campaign library. Part of the front end - see js/main.js. */

import { api } from "./core/api.js";
import { $, el } from "./core/dom.js";
import { S } from "./core/state.js";
import { renderGallery } from "./gallery.js";
import { t } from "./i18n/index.js";
import { baseName, postForm } from "./transfer.js";

/* ── campaign library: the player's own art and notes ───────────────── */

export const LORE_EXTENSIONS = [".md", ".markdown", ".txt", ".html", ".htm"];
// 51MB of portraits in one request is a bad idea on any connection - send it in pieces,
// which also gives us something honest to show a progress line from
export const BATCH_BYTES = 12 * 1024 * 1024;

$("btn-library").onclick = () => $("library-dir").click();

$("library-dir").onchange = async (e) => {
  const picked = [...e.target.files];
  e.target.value = "";
  const usable = picked.filter(isLibraryFile);
  if (!usable.length) return void ($("library-note").textContent = t("library_nothing"));

  // anything filtered out here never leaves the browser, but it still gets counted -
  // picking a folder and being told "added 2" with no word about the other 30 is a lie
  const totals = { images: 0, documents: 0, skipped: picked.length - usable.length };
  let done = 0;
  try {
    for (const batch of batches(usable)) {
      $("library-note").textContent = t("library_working", done, usable.length);
      const form = new FormData();
      // send the path within the chosen folder, not just the name: NPC/ and Place/ are
      // how the server knows which shelf each picture belongs on
      batch.forEach((f) => form.append("files", f, f.webkitRelativePath || f.name));
      const r = await postForm(`/api/campaigns/${S.campaign.id}/library`, form);
      totals.images += r.images.length;
      totals.documents += r.documents.length;
      totals.skipped += r.skipped.length;
      done += batch.length;
    }
  } catch (err) {
    $("library-note").textContent = t("image_failed", err.message);
    return;
  }

  const summary = t("library_done", totals.images, totals.documents);
  $("library-note").textContent =
    totals.skipped ? `${summary} ${t("library_skipped", totals.skipped)}` : summary;
  renderLore();
  renderGallery();
};

export function isLibraryFile(f) {
  const name = baseName(f).toLowerCase();
  return (f.type || "").startsWith("image/")
      || LORE_EXTENSIONS.some((ext) => name.endsWith(ext));
}

/* Group files so no single request is enormous; an oversized file still gets its own
   batch rather than being dropped here — the server decides whether it's too big. */
export function* batches(files) {
  let batch = [], size = 0;
  for (const f of files) {
    if (batch.length && size + f.size > BATCH_BYTES) {
      yield batch;
      batch = []; size = 0;
    }
    batch.push(f);
    size += f.size;
  }
  if (batch.length) yield batch;
}

export async function renderLore() {
  const box = $("lore-list");
  box.innerHTML = "";
  let docs;
  try {
    docs = (await api(`/api/campaigns/${S.campaign.id}/lore`)).documents;
  } catch (_) { return; }
  docs.forEach((d) => {
    const row = el("div", "lore-row");
    const left = el("div");
    left.append(el("div", "lore-name", d.name));
    left.append(el("div", "lore-size", t("lore_chars", d.chars.toLocaleString())));
    const drop = el("button", "ghost tiny", t("lore_remove"));
    drop.onclick = async () => {
      await api(`/api/campaigns/${S.campaign.id}/lore/${d.id}`, { method: "DELETE" });
      renderLore();
    };
    row.append(left, drop);
    box.append(row);
  });
}
