/* Background sound. Part of the front end - see js/main.js.

   The DM picks a mood with set_ambience (a table rule, off by default); this plays it.
   Nothing is downloaded: every mood is made here, from noise, filters and a few short
   synthesised events - rain is filtered noise with drops in it, a forest is wind with
   birdsong, a crypt is a low drone with water dripping. No audio files means nothing
   to license and no URL the DM could be talked into naming.

   Off until this player turns it on, and remembered per browser. Browsers will not
   start sound without a click, so the switch itself is what wakes the audio up; a
   player who left it on last time hears it from their first click on the page. */

import { S } from "./core/state.js";

const FADE = 2.5;                 // seconds to cross from one mood to the next

let ctx = null;
let master = null;
let playing = null;               // { mood, gain, stop }
let pending = null;               // the mood to play once the browser lets us

export const soundOn = () => localStorage.getItem("sound") === "1";
export const soundVolume = () => {
  const v = parseFloat(localStorage.getItem("sound_vol"));
  return Number.isFinite(v) ? Math.min(1, Math.max(0, v)) : 0.5;
};

/* Called from a click: the one moment a browser allows sound to start. */
export function setSoundOn(on) {
  localStorage.setItem("sound", on ? "1" : "0");
  if (on) {
    wake();
    play((S.campaign && (S.campaign.house || {}).ambience && S.ambience) || "silence");
  } else {
    play("silence");
  }
}

export function setVolume(v) {
  localStorage.setItem("sound_vol", String(v));
  if (master) master.gain.setTargetAtTime(v, ctx.currentTime, 0.1);
}

/* The DM changed the mood, or a campaign was entered with one already set. */
export function setMood(mood) {
  S.ambience = mood;
  if (soundOn()) play(mood);
}

export function stopSound() {
  play("silence");
}

/* For the console: is the audio running, and what is it playing. */
export function soundState() {
  return { audio: ctx ? ctx.state : "not started", playing: playing ? playing.mood : null,
           waiting: pending };
}

/* For the console, and for checking the moods against each other: render one offline
   for a few seconds, silently, and report how loud it is. A peak above 1 would clip.
   The occasional events (drips, birds, thunder) run on real-time timers, so this
   measures the continuous part of each mood. */
export async function measureMood(mood, seconds = 2) {
  const Offline = window.OfflineAudioContext || window.webkitOfflineAudioContext;
  if (!Offline || !MOODS[mood]) return null;
  const live = ctx;
  const cache = noiseCache;
  ctx = new Offline(1, 44100 * seconds, 44100);
  noiseCache = {};
  let stop = null;
  try {
    const out = ctx.createGain();
    out.connect(ctx.destination);
    stop = MOODS[mood](out);
    const buf = await ctx.startRendering();
    const d = buf.getChannelData(0);
    let sum = 0, peak = 0;
    for (let i = 0; i < d.length; i++) {
      sum += d[i] * d[i];
      peak = Math.max(peak, Math.abs(d[i]));
    }
    return { mood, rms: +Math.sqrt(sum / d.length).toFixed(4), peak: +peak.toFixed(3) };
  } finally {
    if (stop) stop();
    ctx = live;
    noiseCache = cache;
  }
}

function wake() {
  if (!ctx) {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return false;
    ctx = new Ctx();
    master = ctx.createGain();
    master.gain.value = soundVolume();
    // a limiter last: thunder on top of a storm, or two moods crossing, must never clip
    const limit = ctx.createDynamicsCompressor();
    limit.threshold.value = -6;
    limit.ratio.value = 12;
    master.connect(limit).connect(ctx.destination);
  }
  if (ctx.state === "suspended") ctx.resume();
  return true;
}

// a player who left sound on: the first click anywhere is the browser's permission
["pointerdown", "keydown"].forEach((type) => document.addEventListener(type, () => {
  if (soundOn() && pending && wake()) {
    const mood = pending;
    pending = null;
    play(mood);
  }
}, { capture: true }));

function play(mood) {
  if (playing && playing.mood === mood) return;
  if (!ctx || ctx.state !== "running") {
    // not allowed to make a sound yet; remember what to start with
    pending = mood === "silence" ? null : mood;
    if (!ctx) return;
  }
  const now = ctx.currentTime;
  if (playing) {
    const old = playing;
    old.gain.gain.cancelScheduledValues(now);
    old.gain.gain.setValueAtTime(old.gain.gain.value, now);
    old.gain.gain.linearRampToValueAtTime(0, now + FADE);
    setTimeout(() => { old.stop(); old.gain.disconnect(); }, (FADE + 0.2) * 1000);
    playing = null;
  }
  const build = MOODS[mood];
  if (!build) return;
  const gain = ctx.createGain();
  gain.gain.setValueAtTime(0, now);
  gain.gain.linearRampToValueAtTime(1, now + FADE);
  gain.connect(master);
  playing = { mood, gain, stop: build(gain) };
}

/* ── building blocks ────────────────────────────────────────────────── */

let noiseCache = {};
function noiseBuffer(colour) {
  if (noiseCache[colour] && noiseCache[colour].sampleRate === ctx.sampleRate) return noiseCache[colour];
  const length = ctx.sampleRate * 4;
  const buf = ctx.createBuffer(1, length, ctx.sampleRate);
  const d = buf.getChannelData(0);
  let last = 0, b0 = 0, b1 = 0, b2 = 0;
  for (let i = 0; i < length; i++) {
    const white = Math.random() * 2 - 1;
    if (colour === "brown") {
      last = (last + 0.02 * white) / 1.02;
      d[i] = last * 3.5;
    } else if (colour === "pink") {
      b0 = 0.997 * b0 + white * 0.029591;
      b1 = 0.985 * b1 + white * 0.032534;
      b2 = 0.95 * b2 + white * 0.048056;
      d[i] = (b0 + b1 + b2 + white * 0.05) * 1.6;
    } else {
      d[i] = white;
    }
  }
  noiseCache[colour] = buf;
  return buf;
}

/* A looping noise bed through a filter, at a level - most moods start with one. */
function bed(out, colour, type, freq, level, q = 0.7) {
  const src = ctx.createBufferSource();
  src.buffer = noiseBuffer(colour);
  src.loop = true;
  const filter = ctx.createBiquadFilter();
  filter.type = type;
  filter.frequency.value = freq;
  filter.Q.value = q;
  const gain = ctx.createGain();
  gain.gain.value = level;
  src.connect(filter).connect(gain).connect(out);
  src.start(0, Math.random() * 3);
  return { src, filter, gain, stop: () => { try { src.stop(); } catch (_) {} } };
}

/* A slow wobble on a parameter: wind gusting, waves rolling in. */
function swell(param, rate, depth) {
  const lfo = ctx.createOscillator();
  lfo.frequency.value = rate;
  const amount = ctx.createGain();
  amount.gain.value = depth;
  lfo.connect(amount).connect(param);
  lfo.start();
  return () => { try { lfo.stop(); } catch (_) {} };
}

/* Something that happens now and then - a drip, a bird, a clink. */
function every(min, max, fn) {
  let timer = null;
  const next = () => {
    timer = setTimeout(() => { fn(); next(); }, (min + Math.random() * (max - min)) * 1000);
  };
  next();
  return () => clearTimeout(timer);
}

function tone(out, { type = "sine", from, to, at = 0, dur, level, attack = 0.005 }) {
  const t0 = ctx.currentTime + at;
  const osc = ctx.createOscillator();
  osc.type = type;
  osc.frequency.setValueAtTime(from, t0);
  if (to) osc.frequency.exponentialRampToValueAtTime(to, t0 + dur);
  const g = ctx.createGain();
  g.gain.setValueAtTime(0, t0);
  g.gain.linearRampToValueAtTime(level, t0 + attack);
  g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
  osc.connect(g).connect(out);
  osc.start(t0);
  osc.stop(t0 + dur + 0.05);
}

function burst(out, { colour = "white", type = "bandpass", freq, q = 1, dur, level, at = 0 }) {
  const t0 = ctx.currentTime + at;
  const src = ctx.createBufferSource();
  src.buffer = noiseBuffer(colour);
  const filter = ctx.createBiquadFilter();
  filter.type = type;
  filter.frequency.value = freq;
  filter.Q.value = q;
  const g = ctx.createGain();
  g.gain.setValueAtTime(level, t0);
  g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
  src.connect(filter).connect(g).connect(out);
  src.start(t0, Math.random() * 3);
  src.stop(t0 + dur + 0.05);
}

const rand = (a, b) => a + Math.random() * (b - a);

function together(...stops) {
  return () => stops.forEach((s) => s());
}

/* ── the moods ──────────────────────────────────────────────────────── */

function rain(out, heavy) {
  const hiss = bed(out, "pink", "highpass", 500, heavy ? 0.34 : 0.28);
  const body = bed(out, "brown", "lowpass", 900, heavy ? 0.22 : 0.18);
  const drops = every(0.04, heavy ? 0.12 : 0.25, () =>
    burst(out, { freq: rand(1800, 5000), q: 4, dur: 0.03, level: rand(0.05, 0.18) }));
  return together(hiss.stop, body.stop, drops);
}

function wind(out, level, freq = 500) {
  const w = bed(out, "pink", "lowpass", freq, level, 1.2);
  const gust = swell(w.filter.frequency, rand(0.05, 0.12), freq * 0.5);
  const breath = swell(w.gain.gain, rand(0.04, 0.09), level * 0.5);
  return together(w.stop, gust, breath);
}

function bird(out) {
  const base = rand(2400, 4200);
  const notes = 2 + Math.floor(Math.random() * 4);
  for (let i = 0; i < notes; i++) {
    tone(out, { from: base * rand(0.9, 1.15), to: base * rand(1.2, 1.6), at: i * rand(0.09, 0.16),
                dur: rand(0.06, 0.12), level: 0.05 });
  }
}

const MOODS = {
  silence: null,

  rain: (out) => rain(out, false),

  storm: (out) => together(
    rain(out, true),
    wind(out, 0.12, 350),
    every(9, 22, () => {
      // thunder: a crack, then a long low roll
      burst(out, { colour: "white", type: "lowpass", freq: 1200, dur: 0.4, level: 0.25 });
      burst(out, { colour: "brown", type: "lowpass", freq: 160, dur: rand(3, 6), level: 0.5, at: 0.15 });
    })),

  sea: (out) => {
    const surf = bed(out, "brown", "lowpass", 700, 0.35);
    const waves = swell(surf.gain.gain, 0.09, 0.25);
    const foam = bed(out, "pink", "highpass", 1500, 0.06);
    const foamSwell = swell(foam.gain.gain, 0.09, 0.05);
    const gulls = every(12, 30, () => {
      for (let i = 0; i < 2; i++) {
        tone(out, { type: "triangle", from: 1500, to: 900, at: i * 0.35, dur: 0.3, level: 0.03 });
      }
    });
    return together(surf.stop, waves, foam.stop, foamSwell, gulls);
  },

  forest: (out) => together(
    wind(out, 0.12, 900),
    every(1.5, 6, () => bird(out)),
    every(6, 15, () =>                             // a twig, somewhere
      burst(out, { freq: rand(700, 1600), q: 3, dur: 0.05, level: 0.08 }))),

  night: (out) => together(
    wind(out, 0.06, 400),
    every(0.6, 2.2, () => {                        // crickets: quick high pulses
      const f = rand(4200, 5200);
      for (let i = 0; i < 3; i++) tone(out, { from: f, at: i * 0.06, dur: 0.035, level: 0.02 });
    }),
    every(14, 35, () => {                          // an owl
      tone(out, { from: 420, to: 380, dur: 0.35, level: 0.06, attack: 0.05 });
      tone(out, { from: 400, to: 360, at: 0.5, dur: 0.6, level: 0.05, attack: 0.08 });
    })),

  dungeon: (out) => {
    const droneOut = ctx.createGain();
    droneOut.gain.value = 0.07;
    const low = ctx.createBiquadFilter();
    low.type = "lowpass";
    low.frequency.value = 220;
    droneOut.connect(low).connect(out);
    const oscs = [55, 55.6, 82.4].map((f) => {
      const o = ctx.createOscillator();
      o.type = "sawtooth";
      o.frequency.value = f;
      o.connect(droneOut);
      o.start();
      return o;
    });
    const rumble = bed(out, "brown", "lowpass", 120, 0.25);
    const echo = ctx.createDelay(1);
    echo.delayTime.value = 0.32;
    const tail = ctx.createGain();
    tail.gain.value = 0.35;
    echo.connect(tail).connect(echo);
    tail.connect(out);
    const drips = every(1.5, 5, () => {
      const f = rand(900, 1700);
      tone(out, { from: f, to: f * 0.5, dur: 0.12, level: 0.06 });
      tone(echo, { from: f, to: f * 0.5, dur: 0.12, level: 0.04 });
    });
    return together(() => oscs.forEach((o) => { try { o.stop(); } catch (_) {} }),
                    rumble.stop, drips, () => tail.disconnect());
  },

  campfire: (out) => {
    const roar = bed(out, "brown", "lowpass", 500, 0.35);
    const flicker = swell(roar.gain.gain, 0.3, 0.12);
    const crackle = every(0.05, 0.4, () =>
      burst(out, { type: "highpass", freq: rand(1500, 4000), dur: rand(0.01, 0.04),
                   level: rand(0.05, 0.3) }));
    const pop = every(3, 9, () =>
      burst(out, { freq: rand(500, 1200), q: 2, dur: 0.08, level: 0.35 }));
    return together(roar.stop, flicker, crackle, pop);
  },

  tavern: (out) => {
    // a room full of voices: bands of noise in the speech range, each rising and
    // falling at its own speaking pace, so no word ever forms
    const voices = [320, 520, 780, 1100, 1500].map((f) => {
      const v = bed(out, "pink", "bandpass", f, 0.12, 2.5);
      const talk = swell(v.gain.gain, rand(2.5, 5.5), 0.09);
      return together(v.stop, talk);
    });
    const hearth = bed(out, "brown", "lowpass", 300, 0.15);
    const clink = every(2, 7, () => {
      const f = rand(2200, 3400);
      [1, 2.76, 5.4].forEach((p, i) =>
        tone(out, { from: f * p, dur: 0.4 - i * 0.1, level: 0.03 / (i + 1) }));
    });
    const laugh = every(8, 20, () => {
      for (let i = 0; i < 4; i++) {
        burst(out, { colour: "pink", freq: rand(600, 900), q: 3, dur: 0.12, level: 0.2,
                     at: i * 0.16 });
      }
    });
    return together(...voices, hearth.stop, clink, laugh);
  },

  battle: (out) => {
    const tension = ctx.createOscillator();
    tension.type = "sawtooth";
    tension.frequency.value = 73.4;
    const tf = ctx.createBiquadFilter();
    tf.type = "lowpass";
    tf.frequency.value = 400;
    const tg = ctx.createGain();
    tg.gain.value = 0.05;
    tension.connect(tf).connect(tg).connect(out);
    tension.start();
    const sweep = swell(tf.frequency, 0.15, 200);
    let beat = 0;
    const drum = every(0.32, 0.38, () => {           // a war drum, louder on the one
      const strong = beat++ % 4 === 0;
      tone(out, { from: strong ? 110 : 90, to: 45, dur: 0.35, level: strong ? 0.5 : 0.28 });
      burst(out, { colour: "brown", type: "lowpass", freq: 300, dur: 0.12, level: strong ? 0.4 : 0.2 });
    });
    const clash = every(2.5, 7, () => {              // steel on steel, far off
      [1, 1.47, 2.31, 3.1].forEach((p, i) =>
        tone(out, { type: "triangle", from: rand(700, 900) * p, dur: 0.5 - i * 0.08,
                    level: 0.025 }));
    });
    return together(() => { try { tension.stop(); } catch (_) {} }, sweep, drum, clash);
  },
};

export const MOOD_NAMES = Object.keys(MOODS);
