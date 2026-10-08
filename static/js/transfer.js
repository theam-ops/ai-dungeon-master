/* Import and export. Part of the front end - see js/main.js. */

import { enterCampaign } from "./campaign.js";
import { api } from "./core/api.js";
import { $, toast } from "./core/dom.js";
import { S } from "./core/state.js";
import { t } from "./i18n/index.js";

/* ── import / export ────────────────────────────────────────────────── */

$("import-file").onchange = async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  $("lobby-err").textContent = "";
  try {
    // a campaign with images exports as a zip (campaign.json plus the image files);
    // one without exports as plain json. Either is a valid thing to hand back - and
    // the contents decide, not the name, since a downloaded file often gets renamed.
    const c = await isZip(file) ? await importArchive(file)
                                : await api("/api/import", { method: "POST",
                                              body: JSON.parse(await file.text()) });
    toast(t("imported"));
    await enterCampaign(c.id);
  } catch (err) {
    $("lobby-err").textContent = t("import_fail", err.message);
  }
  e.target.value = "";
};

/* An export that has been unzipped: the folder holds campaign.json and a media/ folder.
   The browser hands over every file in it, so find the manifest, then send it back with
   only the images it actually names. */
$("import-dir").onchange = async (e) => {
  const files = [...e.target.files];
  e.target.value = "";
  if (!files.length) return;
  $("lobby-err").textContent = "";
  try {
    const manifest = findManifest(files);
    if (!manifest) throw new Error(t("no_campaign_json"));
    const blob = JSON.parse(await manifest.text());
    const wanted = new Set((blob.media || []).map((m) => m.file));

    const form = new FormData();
    form.append("campaign", JSON.stringify(blob));
    files.filter((f) => wanted.has(baseName(f)))
         .forEach((f) => form.append("files", f, baseName(f)));

    const c = await postForm("/api/import/folder", form);
    toast(t("imported"));
    await enterCampaign(c.id);
  } catch (err) {
    $("lobby-err").textContent = t("import_fail", err.message);
  }
};

export const baseName = (f) => (f.webkitRelativePath || f.name).split("/").pop();

/* campaign.json if it's there; otherwise a lone .json, since people rename exports. */
export function findManifest(files) {
  const exact = files.find((f) => baseName(f) === "campaign.json");
  if (exact) return exact;
  const jsons = files.filter((f) => /\.json$/i.test(baseName(f)));
  return jsons.length === 1 ? jsons[0] : null;
}

/* "PK" - every zip starts with it, whatever the file ended up being called. */
export async function isZip(file) {
  const head = new Uint8Array(await file.slice(0, 2).arrayBuffer());
  return head[0] === 0x50 && head[1] === 0x4b;
}

/* Zipped exports go up as multipart - `api()` always sends JSON, so it can't carry one. */
export function importArchive(file) {
  const form = new FormData();
  form.append("file", file);
  return postForm("/api/import/archive", form);
}

export async function postForm(path, form) {
  const res = await fetch(path, { method: "POST", body: form, credentials: "same-origin" });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return res.json();
}

$("btn-export").onclick = () => {
  window.location = `/api/campaigns/${S.campaign.id}/export`;
};

$("btn-share").onclick = async () => {
  const url = `${location.origin}/?c=${S.campaign.code}`;
  const text = t("share_text", S.campaign.name, S.campaign.code);
  if (navigator.share) {
    try { await navigator.share({ title: t("app_title").replace("\n", " "), text, url }); return; }
    catch (_) {}
  }
  try { await navigator.clipboard.writeText(url); toast(t("copied")); }
  catch (_) { toast(t("code_is", S.campaign.code)); }
};
