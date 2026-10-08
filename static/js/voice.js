/* Reading the narration aloud. Part of the front end - see js/main.js.

   The browser's own speech: free, offline, nothing sent anywhere, and it speaks Thai
   wherever the device has a Thai voice. A paid voice would be better and would add a
   cost to every turn of every table, for a feature most players will leave off; this
   is per player, off by default, and costs nothing.

   Only narration that arrives live is read. Joining a campaign replays its whole
   story, and nobody wants an hour of it read out on arrival. */

import { S } from "./core/state.js";

const synth = window.speechSynthesis;
const LOCALES = { en: "en", th: "th" };
const CHUNK = 220;           // long utterances stall in some browsers; speak in pieces

export const voiceOn = () => localStorage.getItem("voice") === "1";
export const canSpeak = () => !!synth && typeof window.SpeechSynthesisUtterance === "function";

export function setVoiceOn(on) {
  localStorage.setItem("voice", on ? "1" : "0");
  if (!on) hush();
}

function campaignLang() {
  return LOCALES[(S.campaign && S.campaign.lang) || "en"] || "en";
}

/* A voice for this language, if the device has one. Voices load asynchronously, so
   this can be empty for a moment after the page opens. */
export function voiceFor(lang = campaignLang()) {
  if (!canSpeak()) return null;
  const voices = synth.getVoices();
  return voices.find((v) => v.lang.toLowerCase().startsWith(lang) && v.localService)
      || voices.find((v) => v.lang.toLowerCase().startsWith(lang)) || null;
}

/* The text in pieces of at most about CHUNK characters, broken between words - at the
   last sentence end that fits, where there is one. Thai has no spaces between words,
   only between phrases, so for Thai "words" here are phrases, which break just as well. */
const ENDS = /[.!?…]["'”’)]*$/;
export function pieces(text) {
  const words = text.replace(/[*_#>`]/g, "").split(/\s+/).filter(Boolean);
  const out = [];
  let cur = [];
  let len = 0;
  for (const w of words) {
    if (cur.length && len + w.length + 1 > CHUNK) {
      let cut = cur.length;
      for (let i = cur.length - 1; i > 0; i--) {
        if (ENDS.test(cur[i])) { cut = i + 1; break; }
      }
      out.push(cur.slice(0, cut).join(" "));
      cur = cur.slice(cut);
      len = cur.join(" ").length;
    }
    cur.push(w);
    len += w.length + 1;
  }
  if (cur.length) out.push(cur.join(" "));
  return out;
}

export function speak(text) {
  if (!voiceOn() || !canSpeak() || !text) return;
  const lang = campaignLang();
  const voice = voiceFor(lang);
  for (const piece of pieces(text)) {
    const u = new SpeechSynthesisUtterance(piece);
    u.lang = voice ? voice.lang : (lang === "th" ? "th-TH" : "en-US");
    if (voice) u.voice = voice;
    u.rate = 1;
    synth.speak(u);
  }
}

export function hush() {
  if (canSpeak()) synth.cancel();
}
