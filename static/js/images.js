/* Images: portraits, attachments, the lightbox. Part of the front end - see js/main.js. */

import { api } from "./core/api.js";
import { $, el, toast } from "./core/dom.js";
import { S } from "./core/state.js";
import { renderLive } from "./dash.js";
import { t } from "./i18n/index.js";

/* ── images ─────────────────────────────────────────────────────────── */

export const mediaUrl = (id) => `/api/campaigns/${S.campaign.id}/media/${id}`;

export async function uploadImage(file, kind, opts = {}) {
  const form = new FormData();
  form.append("file", file);
  form.append("kind", kind);
  form.append("caption", opts.caption || "");
  form.append("share", opts.share === false ? "0" : "1");
  const res = await fetch(`/api/campaigns/${S.campaign.id}/media`, {
    method: "POST", body: form, credentials: "same-origin",
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return res.json();
}

export const imageFromUrl = (url, kind, share) =>
  api(`/api/campaigns/${S.campaign.id}/media/url`,
      { method: "POST", body: { url, kind, share: share === false ? "0" : "1" } });

export const drawImage = (prompt, kind, share) =>
  api(`/api/campaigns/${S.campaign.id}/media/generate`,
      { method: "POST", body: { prompt, kind, share: share === false ? "0" : "1" } });

export function openLightbox(src, caption) {
  $("lightbox-img").src = src;
  $("lightbox-caption").textContent = caption || "";
  $("lightbox").classList.remove("hidden");
}
$("lightbox").onclick = () => $("lightbox").classList.add("hidden");

/* staging images alongside the next action */
export const MAX_ATTACH = 4;
export const IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp", "image/gif"];

$("btn-attach").onclick = () => $("attach-file").click();
$("btn-attach-dir").onclick = () => $("attach-dir").click();

export async function attachFiles(files) {
  for (const file of files.slice(0, MAX_ATTACH)) {
    try {
      // share:false — the player's own action puts it in the feed, not the upload
      const m = await uploadImage(file, "handout", { share: false });
      S.attached.push(m);
    } catch (err) {
      toast(t("image_failed", err.message));
    }
  }
  renderAttachments();
}

$("attach-file").onchange = async (e) => {
  await attachFiles([...e.target.files]);
  e.target.value = "";
};

/* A folder holds whatever it holds — filter to images, and say so when there were more
   than one turn can carry rather than silently dropping them. */
$("attach-dir").onchange = async (e) => {
  const images = [...e.target.files].filter((f) => IMAGE_TYPES.includes(f.type));
  e.target.value = "";
  if (!images.length) return toast(t("no_images_here"));
  if (images.length > MAX_ATTACH) toast(t("used_first_n", MAX_ATTACH, images.length));
  await attachFiles(images);
};

export function renderAttachments() {
  const box = $("attachments");
  box.innerHTML = "";
  box.classList.toggle("hidden", !S.attached.length);
  S.attached.forEach((m, i) => {
    const wrap = el("div", "attachment");
    const img = el("img");
    img.src = mediaUrl(m.id);
    img.alt = m.caption || "";
    img.onclick = () => openLightbox(img.src, m.caption);
    const x = el("button", "", "\u00d7");
    x.type = "button";
    x.onclick = () => { S.attached.splice(i, 1); renderAttachments(); };
    wrap.append(img, x);
    box.append(wrap);
  });
}

/* portrait */
export function renderPortrait() {
  const me = S.party.find((p) => p.name === S.campaign.you);
  const slot = $("portrait-slot");
  slot.innerHTML = "";
  $("portrait-note").textContent = t("portrait_hint");
  if (me && me.portrait) {
    const img = el("img");
    img.src = mediaUrl(me.portrait);
    img.alt = me.name;
    img.onclick = () => openLightbox(img.src, me.name);
    slot.append(img);
  } else {
    slot.textContent = "\u2687";
  }
}

$("btn-portrait-upload").onclick = () => $("portrait-file").click();

$("portrait-file").onchange = async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  try {
    await uploadImage(file, "portrait");
    await refreshParty();
    toast(t("image_added"));
  } catch (err) { toast(t("image_failed", err.message)); }
};

$("btn-portrait-link").onclick = async () => {
  const url = prompt(t("ask_url"));
  if (!url) return;
  try {
    await imageFromUrl(url, "portrait");
    await refreshParty();
    toast(t("image_added"));
  } catch (err) { toast(t("image_failed", err.message)); }
};

$("btn-portrait-draw").onclick = async () => {
  const me = S.party.find((p) => p.name === S.campaign.you);
  const suggestion = me ? `${me.race} ${me.class} named ${me.name}` : "";
  const want = prompt(t("ask_portrait_prompt"), suggestion);
  if (!want) return;
  const btn = $("btn-portrait-draw");
  btn.disabled = true;
  btn.textContent = t("illustrating");
  try {
    await drawImage(`Fantasy character portrait, head and shoulders, painterly: ${want}`,
                    "portrait");
    await refreshParty();
    toast(t("image_added"));
  } catch (err) {
    toast(err.status === 503 ? t("no_artist") : t("image_failed", err.message));
  } finally {
    btn.disabled = false;
    btn.textContent = t("draw_it");
  }
};

$("btn-portrait-clear").onclick = async () => {
  const me = S.party.find((p) => p.name === S.campaign.you);
  if (!me || !me.portrait) return;
  try {
    await api(`/api/campaigns/${S.campaign.id}/media/${me.portrait}`, { method: "DELETE" });
    await refreshParty();
  } catch (err) { toast(t("image_failed", err.message)); }
};

$("btn-illustrate").onclick = async () => {
  if (!S.lastScene) { toast(t("no_scene_yet")); return; }
  const btn = $("btn-illustrate");
  btn.disabled = true;
  btn.textContent = t("illustrating");
  try {
    await drawImage(
      "Atmospheric fantasy illustration of this tabletop RPG scene, no text or words: "
      + S.lastScene.slice(0, 600), "scene");
  } catch (err) {
    toast(err.status === 503 ? t("no_artist") : t("image_failed", err.message));
  } finally {
    btn.disabled = false;
    btn.textContent = t("illustrate");
  }
};

export async function refreshParty() {
  const info = await api(`/api/campaigns/${S.campaign.id}`);
  S.party = info.party;
  renderLive();
  renderPortrait();
}
