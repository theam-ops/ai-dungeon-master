/* Entering and leaving a campaign. Part of the front end - see js/main.js. */

import { boot } from "./boot.js";
import { api } from "./core/api.js";
import { $, show } from "./core/dom.js";
import { S } from "./core/state.js";
import { renderLive, setView } from "./dash.js";
import { renderHouse, renderRecap, renderRulebookCredit } from "./drawer.js";
import { renderAttachments } from "./images.js";
import { resetMap } from "./map.js";
import { stopSound } from "./sound.js";
import { connect } from "./stream.js";
import { hush } from "./voice.js";

/* ── entering a campaign ────────────────────────────────────────────── */

export async function enterCampaign(id) {
  const info = await api(`/api/campaigns/${id}`);
  S.campaign = info;
  S.combat = info.combat || null;
  renderHouse();
  renderRecap();
  renderRulebookCredit();
  S.party = info.party;
  S.backend = info.backend;
  S.notes = info.notes || "";
  S.notesMax = info.notes_max || S.notesMax;
  S.lastSeq = 0;
  S.seen.clear();
  S.live = null;
  S.attached = [];
  S.lastScene = "";
  S.rolls = [];        // rolls belong to the campaign you are in, not the browser
  S.map = null;        // rebuilt from the replay, as the feed is
  S.ambience = null;
  resetMap();
  stopSound();
  hush();
  renderAttachments();
  localStorage.setItem("campaign_id", id);

  $("feed").innerHTML = "";
  setView("story");            // never drop someone into a campaign on a stats tab
  renderLive();
  show("game");
  connect();
}

$("btn-leave").onclick = () => {
  if (S.es) { S.es.close(); S.es = null; }
  stopSound();
  hush();
  localStorage.removeItem("campaign_id");
  S.campaign = null;
  boot();
};
