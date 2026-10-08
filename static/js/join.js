/* Joining a campaign by its code. Part of the front end - see js/main.js. */

import { enterCampaign } from "./campaign.js";
import { api } from "./core/api.js";
import { $, el, show } from "./core/dom.js";
import { S } from "./core/state.js";
import { openCreate } from "./create.js";
import { t, tClass, tRace } from "./i18n/index.js";

/* ── joining ────────────────────────────────────────────────────────── */

export async function joinByCode() {
  const code = $("join-code").value.trim().toUpperCase();
  $("lobby-err").textContent = "";
  if (code.length < 4) { $("lobby-err").textContent = t("enter_code"); return; }
  try {
    const info = await api("/api/campaigns/join", { method: "POST", body: { code } });
    if (info.already_in) return enterCampaign(info.id);
    S.pending = info;
    renderPick(info);
    show("pick");
  } catch (e) {
    $("lobby-err").textContent = e.message;
  }
}

$("btn-join").onclick = joinByCode;
$("join-code").onkeydown = (e) => { if (e.key === "Enter") joinByCode(); };

export function renderPick(info) {
  $("pick-title").textContent = info.name;
  $("pick-sub").textContent = info.party.length ? t("pick_claim") : t("pick_empty");
  const list = $("pick-list");
  list.innerHTML = "";
  info.party.forEach((c) => {
    const row = el("button", "campaign-row");
    const left = el("div");
    left.append(el("div", "cname", c.name));
    left.append(el("div", "cmeta", t("char_line", c.level, tRace(c.race), tClass(c.class))));
    row.append(left, el("div", "ccode", c.claimed ? t("in_play") : t("free")));
    row.onclick = async () => {
      try {
        await api(`/api/campaigns/${info.id}/characters`, {
          method: "POST", body: { claim_id: c.id },
        });
        S.pending = null;
        await enterCampaign(info.id);
      } catch (e) { $("pick-err").textContent = e.message; }
    };
    list.append(row);
  });
}

$("btn-pick-new").onclick = () => openCreate({ newCampaign: false });
