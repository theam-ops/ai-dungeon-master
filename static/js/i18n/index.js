/* Interface strings. `t(key, ...args)` fills {0}, {1}, ... in order.

   Race, class and ability names are stored in English in the database and translated
   here for display, so one campaign stays playable whatever language each player has
   their own interface set to. */

import en from "./en.js";
import th from "./th.js";

export const STRINGS = { en, th };

export let LANG = "en";

export function setLang(lang) {
  LANG = STRINGS[lang] ? lang : "en";
  localStorage.setItem("lang", LANG);
  document.documentElement.lang = LANG;
  document.documentElement.dataset.lang = LANG;
  return LANG;
}

export function getLang() { return LANG; }

export function t(key, ...args) {
  const s = (STRINGS[LANG] && STRINGS[LANG][key]) ?? STRINGS.en[key] ?? key;
  return typeof s === "string" ? s.replace(/\{(\d+)\}/g, (_, i) => args[i] ?? "") : s;
}

/* Race / class / ability names: English keys in, display names out. */
export const tRace  = (k) => (STRINGS[LANG].races  || {})[k] || k;
export const tClass = (k) => (STRINGS[LANG].classes || {})[k] || k;
export const tStat  = (k) => (STRINGS[LANG].stats  || {})[k] || k;
export const tSkill = (k) => (STRINGS[LANG].skills || {})[k] || k;

/* Apply translations to everything marked up in index.html. */
export function applyI18n(root = document) {
  root.querySelectorAll("[data-i18n]").forEach((n) => {
    n.textContent = t(n.dataset.i18n);
  });
  root.querySelectorAll("[data-i18n-ph]").forEach((n) => {
    n.placeholder = t(n.dataset.i18nPh);
  });
  root.querySelectorAll("[data-i18n-title]").forEach((n) => {
    n.title = t(n.dataset.i18nTitle);
  });
}

/* One sheet change ("HP 8 → 5/8", "+ rusty key") rendered in the player's language. */
export function renderChange(c) {
  switch (c.t) {
    case "hp":     return t("ch_hp", c.from, c.to, c.max);
    case "down":   return t("ch_down");
    case "xp":     return t("ch_xp", c.gain);
    case "gold":   return c.delta >= 0 ? t("ch_gold_gain", c.delta)
                                       : t("ch_gold_spend", Math.abs(c.delta));
    case "item+":  return t("ch_item_add", c.item);
    case "item-":  return c.left ? t("ch_item_rm_left", c.item, c.left) : t("ch_item_rm", c.item);
    case "cond+":  return t("ch_cond_add", c.cond);
    case "cond-":  return t("ch_cond_rm", c.cond);
    case "level":  return t("ch_level", c.level, c.max);
    case "ac":     return t("ch_ac", c.from, c.to);
    case "wear":   return t("ch_wear", c.item);
    case "unwear": return t("ch_unwear", c.item);
    case "fx+":    return c.turns ? t("ch_fx_add_for", c.name, c.turns) : t("ch_fx_add", c.name);
    case "fx-":    return t("ch_fx_rm", c.name);
    case "slot":   return c.spell ? t("ch_slot_spell", c.spell, c.level, c.left, c.max)
                                  : t("ch_slot", c.level, c.left, c.max);
    case "slot-none": return t("ch_slot_none", c.level);
    case "rest":   return t("ch_rest");
    default:       return "";
  }
}
