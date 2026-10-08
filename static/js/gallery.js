/* The gallery. Part of the front end - see js/main.js. */

import { api } from "./core/api.js";
import { $, el } from "./core/dom.js";
import { S } from "./core/state.js";
import { t } from "./i18n/index.js";
import { mediaUrl, openLightbox } from "./images.js";

/* ── gallery: every picture this campaign holds ─────────────────────── */

/* Art arrives from four places - uploaded, linked, drawn by the DM, or brought in by a
   folder import - and only the shared ones ever scroll past in the feed. A campaign that
   imported two dozen NPC portraits had nowhere at all to look at them. */
export const GALLERY_TABS = ["npc", "scene", "map", "handout"];
export let galleryTab = "all";

export async function renderGallery() {
  const box = $("gallery");
  const note = $("gallery-note");
  box.innerHTML = "";
  let items;
  try {
    items = (await api(`/api/campaigns/${S.campaign.id}/media`)).media;
  } catch (_) {
    note.textContent = "";
    $("gallery-tabs").innerHTML = "";
    return;
  }
  if (!items.length) {
    note.textContent = t("gallery_empty");
    $("gallery-tabs").innerHTML = "";
    return;
  }

  // only offer a shelf that has something on it - empty tabs are just noise
  const counts = {};
  items.forEach((m) => { counts[m.kind] = (counts[m.kind] || 0) + 1; });
  const shelves = GALLERY_TABS.filter((k) => counts[k]);
  if (!shelves.includes(galleryTab)) galleryTab = "all";

  const tabs = $("gallery-tabs");
  tabs.innerHTML = "";
  if (shelves.length > 1) {
    [["all", items.length]].concat(shelves.map((k) => [k, counts[k]]))
      .forEach(([key, n]) => {
        const b = el("button", "gtab" + (key === galleryTab ? " on" : ""),
                     `${key === "all" ? t("gallery_all") : t("kind_" + key)} ${n}`);
        b.type = "button";
        b.onclick = () => { galleryTab = key; renderGallery(); };
        tabs.append(b);
      });
  }

  const shown = galleryTab === "all" ? items : items.filter((m) => m.kind === galleryTab);
  note.textContent = t("gallery_count", shown.length);

  shown.forEach((m) => {
    const cell = el("figure", "shot");
    const img = el("img");
    img.src = mediaUrl(m.id);
    img.alt = m.caption || "";
    img.loading = "lazy";                    // two dozen of them at once, on a phone
    img.onclick = () => openLightbox(img.src, m.caption);
    cell.append(img);

    // the description is the point of the shelf: "Kentuckai Father" is a file name,
    // "his brother, who signed the register" is what you actually want to read later
    const cap = el("figcaption", "shot-cap", m.caption || t("gallery_describe"));
    if (!m.caption) cap.classList.add("empty");
    cap.title = t("gallery_describe");
    cap.tabIndex = 0;
    const edit = () => describeShot(cell, cap, m);
    cap.onclick = edit;
    cap.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); edit(); } };
    cell.append(cap);

    const drop = el("button", "shot-del", "\u00d7");
    drop.type = "button";
    drop.title = t("gallery_remove");
    drop.setAttribute("aria-label", t("gallery_remove"));
    drop.onclick = async (e) => {
      e.stopPropagation();
      drop.disabled = true;
      try {
        await api(`/api/campaigns/${S.campaign.id}/media/${m.id}`, { method: "DELETE" });
        renderGallery();
      } catch (err) {
        note.textContent = err.message;
        drop.disabled = false;
      }
    };
    cell.append(drop);
    box.append(cell);
  });
}

/* Editing happens in place under the picture, because the thing you are describing has
   to stay in front of you while you write about it. */
export function describeShot(cell, cap, m) {
  const open = $("gallery").querySelector(".shot-edit");
  if (open) open.remove();                   // one at a time
  const box = el("div", "shot-edit");

  const text = el("textarea");
  text.value = m.caption || "";
  text.rows = 3;
  text.maxLength = 400;
  text.placeholder = t("gallery_describe");

  const where = el("select");
  GALLERY_TABS.forEach((k) => {
    const o = el("option", "", t("kind_" + k));
    o.value = k;
    if (k === m.kind) o.selected = true;
    where.append(o);
  });

  const save = el("button", "ghost tiny", t("save"));
  save.type = "button";
  save.onclick = async () => {
    save.disabled = true;
    try {
      const updated = await api(`/api/campaigns/${S.campaign.id}/media/${m.id}`,
                                { method: "POST",
                                  body: { caption: text.value, kind: where.value } });
      m.caption = updated.caption;
      m.kind = updated.kind;
      renderGallery();
    } catch (err) {
      $("gallery-note").textContent = err.message;
      save.disabled = false;
    }
  };

  const stop = el("button", "ghost tiny", t("cancel_edit"));
  stop.type = "button";
  stop.onclick = () => box.remove();

  const row = el("div", "row");
  row.append(where, save, stop);
  box.append(text, row);
  // a grid child, not a child of the cell: inside an 84px thumbnail there is nowhere
  // to write. As a sibling it spans the whole shelf, right under the picture's row.
  cell.after(box);
  text.focus();
}
