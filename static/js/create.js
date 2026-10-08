/* Rolling up a character. Part of the front end - see js/main.js. */

import { enterCampaign } from "./campaign.js";
import { api } from "./core/api.js";
import { $, el, icon, show } from "./core/dom.js";
import { S } from "./core/state.js";
import { getLang, t, tClass, tRace, tStat } from "./i18n/index.js";
import { LANGS } from "./lang.js";

/* ── character creation ─────────────────────────────────────────────── */

export async function loadOptions() {
  if (!S.options || S.optionsLang !== getLang()) {
    S.options = await api("/api/options?lang=" + getLang());
    S.optionsLang = getLang();
  }
  return S.options;
}

export async function openCreate({ newCampaign }) {
  await loadOptions();
  $("campaign-name-block").classList.toggle("hidden", !newCampaign);
  $("create-title").textContent = newCampaign ? t("roll_character") : t("join_party");
  $("create-err").textContent = "";
  $("btn-create-go").textContent = newCampaign ? t("begin") : t("join");
  $("create-back").dataset.to = newCampaign ? "lobby" : "pick";
  $("dm-lang-note").textContent = `${t("dm_language")} ${LANGS[getLang()]}`;

  S.picked = { race: S.options.races[0], class: Object.keys(S.options.classes)[0], scores: null };
  renderChips();
  await reroll();
  show("create");
}

/* Chips carry the English key in dataset.key and show a translated label. */
export function renderChips() {
  const o = S.options;
  const races = $("race-chips");
  races.innerHTML = "";
  o.races.forEach((r) => {
    const c = el("button", "chip" + (r === S.picked.race ? " on" : ""), tRace(r));
    c.type = "button";
    c.dataset.key = r;
    c.onclick = () => { S.picked.race = r; renderChips(); };
    races.append(c);
  });

  const classes = $("class-chips");
  classes.innerHTML = "";
  Object.keys(o.classes).forEach((k) => {
    const c = el("button", "chip" + (k === S.picked.class ? " on" : ""), tClass(k));
    c.type = "button";
    c.dataset.key = k;
    c.onclick = async () => { S.picked.class = k; renderChips(); await reroll(); };
    classes.append(c);
  });

  const info = o.classes[S.picked.class];
  $("gear-hint").textContent =
    t("gear_hint", info.hit_die, tStat(info.primary), info.gear.join(", "));
  renderStats();
}

export async function reroll() {
  const r = await api("/api/roll-stats", { method: "POST", body: { klass: S.picked.class } });
  S.picked.scores = r.scores;
  renderStats();
}

export function renderStats() {
  const box = $("stats");
  box.innerHTML = "";
  if (!S.picked.scores) return;
  const primary = S.options.classes[S.picked.class].primary;
  S.options.abilities.forEach((a) => {
    const v = S.picked.scores[a];
    const mod = Math.floor((v - 10) / 2);
    const cell = el("div", "stat" + (a === primary ? " primary" : ""));
    const key = el("div", "k");
    key.append(icon(a, "ab-" + a), el("span", "", tStat(a)));
    cell.append(key, el("div", "v", String(v)),
                el("div", "m", (mod >= 0 ? "+" : "") + mod));
    box.append(cell);
  });
}

$("btn-reroll").onclick = () => reroll();
$("btn-new").onclick = () => { S.pending = null; openCreate({ newCampaign: true }); };
$("create-back").onclick = () => show($("create-back").dataset.to || "lobby");
$("pick-back").onclick = () => { S.pending = null; show("lobby"); };

$("btn-create-go").onclick = async () => {
  const name = $("char-name").value.trim();
  $("create-err").textContent = "";
  if (!name) { $("create-err").textContent = t("need_name"); return; }

  const character = { name, race: S.picked.race, class: S.picked.class, scores: S.picked.scores };
  $("btn-create-go").disabled = true;
  try {
    if (S.pending) {
      await api(`/api/campaigns/${S.pending.id}/characters`, { method: "POST", body: { character } });
      const id = S.pending.id;
      S.pending = null;
      await enterCampaign(id);
    } else {
      const c = await api("/api/campaigns", {
        method: "POST",
        body: { name: $("campaign-name").value.trim(), character, lang: getLang() },
      });
      await enterCampaign(c.id);
      await api(`/api/campaigns/${c.id}/begin`, { method: "POST" });
    }
  } catch (e) {
    $("create-err").textContent = e.message;
  } finally {
    $("btn-create-go").disabled = false;
  }
};
