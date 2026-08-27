/* AI Dungeon Master — front end.
   No framework, no build step. Talks to the FastAPI server over fetch + SSE.
   Interface strings live in i18n.js. */

const $ = (id) => document.getElementById(id);
/* SVG needs its own namespace: document.createElement("svg") builds an inert
   HTMLUnknownElement that renders nothing, and `className` on an SVG element is a
   read-only SVGAnimatedString, so `el()` below cannot make one. */
const SVGNS = "http://www.w3.org/2000/svg";
function icon(name, cls) {
  const svg = document.createElementNS(SVGNS, "svg");
  svg.setAttribute("class", "ic " + (cls || ""));
  svg.setAttribute("aria-hidden", "true");     // always beside a label that carries it
  const use = document.createElementNS(SVGNS, "use");
  use.setAttribute("href", "#ic-" + name);
  svg.append(use);
  return svg;
}

const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

const S = {
  campaign: null,      // {id, code, name, lang, you}
  party: [],
  lastSeq: 0,
  seen: new Set(),
  es: null,            // EventSource
  live: null,          // narration element currently being streamed into
  pending: null,       // campaign being joined, awaiting character choice
  options: null,
  optionsLang: null,
  providers: [],       // every AI the server could use
  canSetKeys: false,   // may this browser paste a key straight into the server?
  backend: null,       // the one running this campaign
  picked: { race: null, class: null, scores: null },
  notes: "",           // your own standing notes for the DM
  notesMax: 600,       // replaced by the server's real cap when a campaign is entered
  attached: [],        // images staged for the next action
  lastScene: "",       // newest narration, for "illustrate this scene"
  rolls: [],           // newest-last, for the HUD; only the last few are kept
  hudOpen: localStorage.getItem("hud") !== "0",
};

const SCREENS = ["login", "lobby", "create", "pick", "game"];
function show(name) {
  SCREENS.forEach((s) => $("screen-" + s).classList.toggle("hidden", s !== name));
}

let toastTimer;
function toast(msg) {
  const t2 = $("toast");
  t2.textContent = msg;
  t2.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t2.classList.add("hidden"), 2600);
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return res.status === 204 ? null : res.json();
}

/* ── language ───────────────────────────────────────────────────────── */

const LANGS = { en: "English", th: "ไทย" };

function renderLangBars() {
  ["lang-login", "lang-lobby", "lang-game"].forEach((id) => {
    const bar = $(id);
    if (!bar) return;
    bar.innerHTML = "";
    Object.entries(LANGS).forEach(([code, label]) => {
      const b = el("button", "chip" + (code === getLang() ? " on" : ""), label);
      b.type = "button";
      b.onclick = () => switchLang(code);
      bar.append(b);
    });
  });
}

async function switchLang(code) {
  if (code === getLang()) return;
  setLang(code);
  S.options = null;                 // gear labels are localised server-side
  await refreshUI();
}

/* Re-render every visible piece of text in the new language. */
async function refreshUI() {
  applyI18n();
  renderLangBars();
  $("create-title").textContent = S.pending ? t("join_party") : t("roll_character");
  $("btn-create-go").textContent = S.pending ? t("join") : t("begin");
  $("dm-lang-note").textContent = `${t("dm_language")} ${LANGS[getLang()]}`;
  if (!$("screen-create").classList.contains("hidden")) {
    await loadOptions();
    renderChips();
  }
  if (S.pending) renderPick(S.pending);
  if (S.campaign) {
    renderLive();
    // the count only — re-filling the box would throw away notes being typed right now
    if (!$("drawer").classList.contains("hidden")) {
      renderAI(); renderNotesCount();
    }
  }
  if (!$("screen-lobby").classList.contains("hidden")) {
    try { renderLobby((await api("/api/me")).campaigns); } catch (_) {}
  }
}

/* ── boot ───────────────────────────────────────────────────────────── */

async function boot() {
  setLang(localStorage.getItem("lang") || (navigator.language || "en").slice(0, 2));
  applyI18n();
  renderLangBars();

  let me;
  try {
    me = await api("/api/me");
  } catch (e) {
    document.body.innerHTML =
      '<section class="screen center"><div class="panel narrow">' +
      `<h1 class="title">${t("offline")}</h1><p class="sub">${t("no_answer")}</p>` +
      "</div></section>";
    return;
  }

  if (!me.authed) { show("login"); $("login-password").focus(); return; }

  $("dm-warning").classList.toggle("hidden", me.dm_ready);
  if (!me.dm_ready) { renderGetKeyLinks(); renderKeyForm("key-form"); }
  renderLobby(me.campaigns);

  // deep link: ?c=CODE or a campaign we were mid-game in
  const codeParam = new URLSearchParams(location.search).get("c");
  const resume = localStorage.getItem("campaign_id");
  if (codeParam) {
    $("join-code").value = codeParam.toUpperCase();
    history.replaceState({}, "", location.pathname);
    return joinByCode();
  }
  if (resume) {
    try { return await enterCampaign(resume); }
    catch (_) { localStorage.removeItem("campaign_id"); }
  }
  show("lobby");
}

function renderLobby(campaigns) {
  const list = $("campaign-list");
  list.innerHTML = "";
  $("continue-block").classList.toggle("hidden", !campaigns || !campaigns.length);
  (campaigns || []).forEach((c) => {
    // the row is a button, so the delete control is its sibling rather than nested
    // inside it - a button within a button is invalid and swallows the click
    const item = el("div", "campaign-item");
    const row = el("button", "campaign-row");
    const left = el("div");
    left.append(el("div", "cname", c.name));
    left.append(el("div", "cmeta", t("playing_as", c.character_name)));
    row.append(left, el("div", "ccode", c.code));
    row.onclick = () => enterCampaign(c.id).catch((e) => ($("lobby-err").textContent = e.message));

    const drop = el("button", "campaign-del", "×");
    drop.type = "button";
    drop.title = t("delete_campaign");
    drop.setAttribute("aria-label", t("delete_campaign"));
    drop.onclick = () => confirmDelete(item, c);

    item.append(row, drop);
    list.append(item);
  });
}

/* Deleting takes the story and its pictures with it and there is no undo, so it asks
   first - in the row itself rather than through a browser dialog, which is easy to
   dismiss without reading and which some browsers suppress outright. */
function confirmDelete(item, c) {
  const saved = [...item.childNodes];        // put back on cancel
  const ask = el("div", "campaign-confirm");
  ask.append(el("div", "warn", t("delete_sure", c.name)));

  const yes = el("button", "danger", t("delete_yes"));
  yes.type = "button";
  yes.onclick = async () => {
    yes.disabled = true;
    try {
      await api(`/api/campaigns/${c.id}`, { method: "DELETE" });
      if (localStorage.getItem("campaign_id") === c.id) localStorage.removeItem("campaign_id");
      await refreshUI();
      toast(t("deleted", c.name));
    } catch (e) {
      $("lobby-err").textContent = e.message;
      yes.disabled = false;
    }
  };

  const no = el("button", "ghost", t("cancel"));
  no.type = "button";
  no.onclick = () => { ask.replaceWith(...saved); };

  const buttons = el("div", "row");
  buttons.append(yes, no);
  ask.append(buttons);
  item.replaceChildren(ask);
  yes.focus();
}

/* Paste an API key straight into the running server — no setx, no restart.
   The server only accepts this from localhost, or from someone who has passed the
   app password, so a public instance can't have keys set by a stranger. */
async function renderKeyForm(id) {
  const box = $(id);
  if (!box) return;
  await loadProviders();

  const needy = S.providers.filter((p) => !p.available && p.key_env);
  const envs = [...new Map(needy.map((p) => [p.key_env, p])).values()];

  if (!S.canSetKeys || !envs.length) { box.classList.add("hidden"); return; }

  box.innerHTML = "";
  box.classList.remove("hidden");
  box.append(el("div", "keyform-title", t("paste_key")));

  const form = el("form", "keyform-row");

  const pick = el("select", "keyselect");
  pick.title = t("key_which");
  envs.forEach((p) => {
    const o = el("option", "", providerFamily(p) + " — " + p.key_env);
    o.value = p.key_env;
    pick.append(o);
  });

  const input = el("input", "keyinput");
  input.type = "password";
  input.placeholder = t("key_ph");
  input.autocomplete = "off";
  input.spellcheck = false;

  const save = el("button", "ghost");
  save.type = "submit";
  save.textContent = t("key_save");

  form.append(pick, input, save);
  box.append(form);

  const note = el("p", "hint", t("key_stored"));
  box.append(note);

  form.onsubmit = async (e) => {
    e.preventDefault();
    const key = input.value.trim();
    if (!key) return;
    save.disabled = true;
    save.textContent = t("key_saving");
    note.className = "hint";
    note.textContent = t("key_saving");
    try {
      const r = await api("/api/keys", {
        method: "POST", body: { env: pick.value, key },
      });
      S.providers = r.providers;
      input.value = "";
      if (r.ok) {
        const ready = S.providers.find((p) => p.key_env === pick.value && p.available);
        note.className = "hint good";
        note.textContent = t("key_ok", ready ? ready.label : pick.value);
        toast(t("key_ok", ready ? ready.label : pick.value));
        setTimeout(() => boot(), 900);         // the lobby can now start a campaign
      } else {
        note.className = "err";
        note.textContent = t("key_bad", r.message || "");
      }
    } catch (err) {
      note.className = "err";
      note.textContent = t("key_bad", err.message);
    } finally {
      save.disabled = false;
      save.textContent = t("key_save");
      renderAI();
    }
  };
}

function providerFamily(p) {
  return { anthropic: "Claude", gemini: "Google Gemini", groq: "Groq",
           openrouter: "OpenRouter" }[p.kind] || p.label;
}

/* Nothing is configured yet: show where to get a key, one click away. */
async function renderGetKeyLinks() {
  const box = $("get-key-links");
  box.innerHTML = "";
  await loadProviders();

  const seen = new Set();
  S.providers
    .filter((p) => p.key_url && p.free && !seen.has(p.kind) && seen.add(p.kind))
    .forEach((p) => {
      const a = el("a", "getkey", p.kind === "ollama"
        ? t("get_ollama") : t("get_key_from", p.kind === "gemini" ? "Google Gemini" : "Groq"));
      a.href = p.key_url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      box.append(a);
    });
  box.classList.remove("hidden");
}

/* ── login ──────────────────────────────────────────────────────────── */

$("login-form").onsubmit = async (e) => {
  e.preventDefault();
  $("login-err").textContent = "";
  try {
    await api("/api/login", { method: "POST", body: { password: $("login-password").value } });
    boot();
  } catch (err) {
    $("login-err").textContent = err.message;
  }
};

/* ── character creation ─────────────────────────────────────────────── */

async function loadOptions() {
  if (!S.options || S.optionsLang !== getLang()) {
    S.options = await api("/api/options?lang=" + getLang());
    S.optionsLang = getLang();
  }
  return S.options;
}

async function openCreate({ newCampaign }) {
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
function renderChips() {
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

async function reroll() {
  const r = await api("/api/roll-stats", { method: "POST", body: { klass: S.picked.class } });
  S.picked.scores = r.scores;
  renderStats();
}

function renderStats() {
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

/* ── joining ────────────────────────────────────────────────────────── */

async function joinByCode() {
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

function renderPick(info) {
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

const baseName = (f) => (f.webkitRelativePath || f.name).split("/").pop();

/* campaign.json if it's there; otherwise a lone .json, since people rename exports. */
function findManifest(files) {
  const exact = files.find((f) => baseName(f) === "campaign.json");
  if (exact) return exact;
  const jsons = files.filter((f) => /\.json$/i.test(baseName(f)));
  return jsons.length === 1 ? jsons[0] : null;
}

/* "PK" - every zip starts with it, whatever the file ended up being called. */
async function isZip(file) {
  const head = new Uint8Array(await file.slice(0, 2).arrayBuffer());
  return head[0] === 0x50 && head[1] === 0x4b;
}

/* Zipped exports go up as multipart - `api()` always sends JSON, so it can't carry one. */
function importArchive(file) {
  const form = new FormData();
  form.append("file", file);
  return postForm("/api/import/archive", form);
}

async function postForm(path, form) {
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

/* ── campaign library: the player's own art and notes ───────────────── */

const LORE_EXTENSIONS = [".md", ".markdown", ".txt", ".html", ".htm"];
// 51MB of portraits in one request is a bad idea on any connection - send it in pieces,
// which also gives us something honest to show a progress line from
const BATCH_BYTES = 12 * 1024 * 1024;

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

function isLibraryFile(f) {
  const name = baseName(f).toLowerCase();
  return (f.type || "").startsWith("image/")
      || LORE_EXTENSIONS.some((ext) => name.endsWith(ext));
}

/* Group files so no single request is enormous; an oversized file still gets its own
   batch rather than being dropped here — the server decides whether it's too big. */
function* batches(files) {
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

async function renderLore() {
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

/* ── notes for the DM: this player's own standing details ───────────── */

/* The cap belongs to the server — it trims on save whatever the browser let you type —
   so the box is told that number rather than carrying a second one of its own. */
function fillNotes() {
  const box = $("notes");
  box.maxLength = S.notesMax;
  box.value = S.notes;
  renderNotesCount();
}

function renderNotesCount() {
  $("notes-count").textContent = t("notes_count", $("notes").value.length, S.notesMax);
}

$("notes").addEventListener("input", renderNotesCount);

$("btn-notes-save").onclick = async () => {
  const btn = $("btn-notes-save");
  btn.disabled = true;
  try {
    const r = await api(`/api/campaigns/${S.campaign.id}/notes`, {
      method: "POST", body: { notes: $("notes").value },
    });
    S.notes = r.notes;
    $("notes").value = r.notes;      // show what was actually kept, trimming included
    renderNotesCount();
    toast(t("notes_saved"));
  } catch (e) {
    toast(e.message);
  } finally {
    btn.disabled = false;
  }
};

/* ── gallery: every picture this campaign holds ─────────────────────── */

/* Art arrives from four places - uploaded, linked, drawn by the DM, or brought in by a
   folder import - and only the shared ones ever scroll past in the feed. A campaign that
   imported two dozen NPC portraits had nowhere at all to look at them. */
const GALLERY_TABS = ["npc", "scene", "map", "handout"];
let galleryTab = "all";

async function renderGallery() {
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
function describeShot(cell, cap, m) {
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

/* ── which AI runs the game ─────────────────────────────────────────── */

async function loadProviders() {
  if (!S.providers.length) {
    try {
      const r = await api("/api/providers");
      S.providers = r.providers;
      S.canSetKeys = !!r.can_set_keys;
    } catch (_) { S.providers = []; }
  }
  return S.providers;
}

function backendLabel(id) {
  const p = S.providers.find((x) => x.id === id);
  return p ? p.label : id;
}

async function renderAI() {
  await loadProviders();
  const usable = S.providers.filter((p) => p.available);

  $("ai-current").textContent = backendLabel(S.backend);
  $("ai-note").textContent = usable.length > 1 ? t("ai_note") : t("ai_note_alone");

  const list = $("ai-list");
  list.innerHTML = "";
  S.providers.forEach((p) => {
    const left = el("div");
    left.append(el("div", "ai-name", p.label));
    left.append(el("div", "ai-model", p.model));

    if (p.available) {
      const row = el("button", "ai-row" + (p.id === S.backend ? " on" : ""));
      row.type = "button";
      row.append(left);
      if (p.free) row.append(el("span", "ai-tag free",
                                p.kind === "ollama" ? t("ai_local") : t("ai_free")));
      // Claude Code being installed doesn't mean it's signed in, and the difference only
      // shows up when a turn fails. Ask it, and say so here instead.
      if (p.kind === "claude-code") checkSignIn(row, p);
      row.onclick = () => chooseAI(p);
      list.append(row);
      return;
    }

    // Not usable yet. Rather than a dead grey row, offer the way to fix it: a link
    // straight to the page that hands out the key (or installs Ollama).
    const row = el("div", "ai-row off");
    row.append(left);
    if (p.key_url) {
      // Ollama and Claude Code are installed, not keyed - don't send people
      // looking for a key page that doesn't exist
      const installed = p.kind === "ollama" || p.kind === "claude-code";
      const get = el("a", "ai-get" + (p.free ? " free" : ""),
                     installed ? t("ai_install") : t("ai_get_key"));
      get.href = p.key_url;
      get.target = "_blank";
      get.rel = "noopener noreferrer";
      get.title = p.key_env ? t("ai_set_env", p.key_env) : p.key_url;
      row.append(get);
    } else {
      row.append(el("span", "ai-tag", t("ai_no_key")));
    }
    list.append(row);
  });
}

async function checkSignIn(row, p) {
  const tag = el("span", "ai-tag", t("ai_checking"));
  row.append(tag);
  let state;
  try {
    state = await api("/api/claude/status");
  } catch (_) {
    tag.remove();               // couldn't ask; don't claim either way
    return;
  }
  tag.className = "ai-tag " + (state.logged_in ? "free" : "warn");
  tag.textContent = state.logged_in ? t("ai_signed_in") : t("ai_sign_in");

  // the sign-in runs on the machine hosting the game, so only that machine is offered it
  const box = $("claude-login");
  box.classList.toggle("hidden", state.logged_in);
  if (state.logged_in) return;
  $("btn-claude-login").classList.toggle("hidden", !state.can_sign_in);
  $("claude-login-note").textContent = state.can_sign_in ? "" : t("claude_signin_remote");
}

$("btn-claude-login").onclick = async () => {
  const note = $("claude-login-note");
  note.textContent = t("claude_signin_busy");
  try {
    const { url } = await api("/api/claude/login", { method: "POST" });
    const link = $("claude-login-url");
    link.href = url;
    $("claude-login-step").classList.remove("hidden");
    note.textContent = "";
    window.open(url, "_blank", "noopener");
    $("claude-login-code").focus();
    // Claude Code opens a browser too, and if you are already signed in to claude.ai the
    // flow finishes on its own without a code ever being shown. Watch for that rather
    // than leaving someone staring at a box they don't need to fill in.
    watchSignIn();
  } catch (err) {
    note.textContent = err.message;
  }
};

async function watchSignIn(tries = 45) {
  for (let i = 0; i < tries; i++) {
    await new Promise((r) => setTimeout(r, 2000));
    if ($("claude-login-step").classList.contains("hidden")) return;   // done by hand
    let state;
    try { state = await api("/api/claude/status"); } catch (_) { return; }
    if (state.logged_in) {
      $("claude-login-step").classList.add("hidden");
      $("claude-login-note").textContent = t("claude_signin_ok");
      await renderAI();
      return;
    }
  }
}

$("btn-claude-code").onclick = async () => {
  const code = $("claude-login-code").value.trim();
  if (!code) return;
  const note = $("claude-login-note");
  note.textContent = t("claude_signin_busy");
  try {
    await api("/api/claude/login/code", { method: "POST", body: { code } });
    $("claude-login-code").value = "";
    $("claude-login-step").classList.add("hidden");
    note.textContent = t("claude_signin_ok");
    await renderAI();           // the row should go green now
  } catch (err) {
    note.textContent = err.message;
  }
};

async function chooseAI(p) {
  $("ai-list").classList.add("hidden");
  if (p.id === S.backend) return;
  try {
    await api(`/api/campaigns/${S.campaign.id}/provider`, {
      method: "POST", body: { backend: p.id },
    });
    S.backend = p.id;
    renderAI();
  } catch (e) {
    toast(e.message);
  }
}

$("btn-ai").onclick = async () => {
  await renderAI();
  const open = $("ai-list").classList.toggle("hidden");
  if (!open) renderKeyForm("key-form-game");
  else $("key-form-game").classList.add("hidden");
};

/* ── images ─────────────────────────────────────────────────────────── */

const mediaUrl = (id) => `/api/campaigns/${S.campaign.id}/media/${id}`;

async function uploadImage(file, kind, opts = {}) {
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

const imageFromUrl = (url, kind, share) =>
  api(`/api/campaigns/${S.campaign.id}/media/url`,
      { method: "POST", body: { url, kind, share: share === false ? "0" : "1" } });

const drawImage = (prompt, kind, share) =>
  api(`/api/campaigns/${S.campaign.id}/media/generate`,
      { method: "POST", body: { prompt, kind, share: share === false ? "0" : "1" } });

function openLightbox(src, caption) {
  $("lightbox-img").src = src;
  $("lightbox-caption").textContent = caption || "";
  $("lightbox").classList.remove("hidden");
}
$("lightbox").onclick = () => $("lightbox").classList.add("hidden");

/* staging images alongside the next action */
const MAX_ATTACH = 4;
const IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp", "image/gif"];

$("btn-attach").onclick = () => $("attach-file").click();
$("btn-attach-dir").onclick = () => $("attach-dir").click();

async function attachFiles(files) {
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

function renderAttachments() {
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
function renderPortrait() {
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

async function refreshParty() {
  const info = await api(`/api/campaigns/${S.campaign.id}`);
  S.party = info.party;
  renderLive();
  renderPortrait();
}

/* ── entering a campaign ────────────────────────────────────────────── */

async function enterCampaign(id) {
  const info = await api(`/api/campaigns/${id}`);
  S.campaign = info;
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
  renderAttachments();
  localStorage.setItem("campaign_id", id);

  $("feed").innerHTML = "";
  view = "story";              // never drop someone into a campaign on a stats tab
  renderLive();
  show("game");
  connect();
}

$("btn-leave").onclick = () => {
  if (S.es) { S.es.close(); S.es = null; }
  localStorage.removeItem("campaign_id");
  S.campaign = null;
  boot();
};

/* ── the live stream ────────────────────────────────────────────────── */

let retryDelay = 1500;

function connect() {
  if (S.es) S.es.close();
  const es = new EventSource(`/api/campaigns/${S.campaign.id}/stream?since=${S.lastSeq}`);
  S.es = es;

  es.onmessage = (e) => {
    retryDelay = 1500;                 // a live connection resets the backoff
    let ev;
    try { ev = JSON.parse(e.data); } catch (_) { return; }
    handle(ev);
  };

  // EventSource retries on its own, but it would replay from the original
  // `since`. Reconnect by hand so we resume from what we've actually seen —
  // backing off so a phone on a dead network isn't retrying every second.
  es.onerror = () => {
    es.close();
    if (S.es !== es || !S.campaign) return;
    const wait = retryDelay;
    retryDelay = Math.min(retryDelay * 2, 20000);
    setTimeout(() => { if (S.campaign) connect(); }, wait);
  };
}

document.addEventListener("visibilitychange", () => {
  // phones kill background connections; re-open on return, and try immediately
  // rather than waiting out whatever backoff had built up while away
  if (!document.hidden && S.campaign && (!S.es || S.es.readyState === 2)) {
    retryDelay = 1500;
    connect();
  }
});

function handle(ev) {
  if (ev.seq) {
    if (S.seen.has(ev.seq)) return;      // replay overlap after a reconnect
    S.seen.add(ev.seq);
    S.lastSeq = Math.max(S.lastSeq, ev.seq);
  }

  switch (ev.kind) {
    case "ready":
      scrollFeed(true);
      break;

    case "delta":
      if (!S.live) {
        S.live = el("p", "narration", "");
        $("feed").append(S.live);
      }
      S.live.textContent += ev.text;
      scrollFeed();
      break;

    case "narration": {
      // the streamed element becomes the authoritative one; on replay there is none
      const node = S.live || el("p", "narration", "");
      node.textContent = ev.text;
      S.lastScene = ev.text;
      if (!node.isConnected) $("feed").append(node);
      S.live = null;
      scrollFeed();
      break;
    }

    case "player": {
      const line = el("div", "player-line" + (ev.character === S.campaign.you ? " mine" : ""));
      line.append(el("span", "who", ev.character));
      line.append(document.createTextNode(ev.text));
      $("feed").append(line);
      scrollFeed();
      break;
    }

    case "dice": {
      const chip = el("div", "dice-chip" + (ev.crit ? " crit-" + ev.crit : ""));
      chip.append(el("span", "reason", ev.reason));
      chip.append(el("span", "", ev.detail.replace(/\s+\*\*.*\*\*$/, "")));
      if (ev.crit === "success") chip.append(el("span", "", "NAT 20"));
      if (ev.crit === "fail") chip.append(el("span", "", "NAT 1"));
      appendChip(chip);
      S.rolls.push({ reason: ev.reason, total: ev.total, crit: ev.crit });
      if (S.rolls.length > HUD_ROLLS) S.rolls.shift();
      renderHud();
      renderDash();
      break;
    }

    case "sheet": {
      // render from the structured changes so this reads in the player's language;
      // fall back to the server's English summary for events logged before that existed
      const body = (ev.changes && ev.changes.length)
        ? ev.changes.map(renderChange).filter(Boolean).join(" · ")
        : ev.summary;
      appendChip(el("div", "sheet-chip", `${ev.character}: ${body}`));
      break;
    }

    case "lore":
      // the DM consulted the players' own notes - worth showing, the same way a roll is
      appendChip(el("div", "lore-chip", t("looked_up", ev.query)));
      break;

    case "join":
      appendChip(el("div", "join-chip", ev.text || t("joins", ev.character)));
      break;

    case "image": {
      const box = el("div", "image-line");
      if (ev.source === "dm") {
        // nobody at the table asked for this one — say where it came from
        box.append(el("div", "who", t("dm_drew")));
      } else if (ev.character && ev.source !== "generated") {
        box.append(el("div", "who", t("shows_image", ev.character)));
      }
      const img = el("img");
      img.src = mediaUrl(ev.media);
      img.alt = ev.caption || "";
      img.loading = "lazy";
      if (ev.width && ev.height) {           // reserve space so the feed doesn't jump
        img.width = ev.width;
        img.height = ev.height;
        img.style.width = "auto";
        img.style.height = "auto";
      }
      img.onclick = () => openLightbox(img.src, ev.caption);
      box.append(img);
      if (ev.caption) box.append(el("div", "cap", ev.caption));
      $("feed").append(box);
      scrollFeed();
      break;
    }

    case "switch": {
      // an AI ran out mid-turn and another picked the game up, or someone
      // switched by hand — either way the table should be told who is narrating
      S.backend = ev.backend;
      // the server discards whatever the failed AI managed to write, so drop the
      // half-finished paragraph here too rather than letting the retry append to it
      if (S.live) { S.live.remove(); S.live = null; }
      const text = ev.manual ? t("ai_switched", ev.label)
                             : t("ai_took_over", ev.from, ev.label);
      appendChip(el("div", "ai-chip", text));
      if (!$("drawer").classList.contains("hidden")) renderAI();
      break;
    }

    case "backend-now":
      S.backend = ev.backend;
      if (!$("drawer").classList.contains("hidden")) renderAI();
      break;

    case "error":
      $("feed").append(el("div", "error-line", ev.text));
      scrollFeed();
      break;

    case "thinking":
      $("thinking").classList.toggle("hidden", !ev.on);
      if (ev.on) S.live = null;
      scrollFeed();
      break;

    case "party":
      S.party = ev.party;
      renderLive();
      if (!$("drawer").classList.contains("hidden")) renderPortrait();
      break;
  }
}

function appendChip(chip) {
  const feed = $("feed");
  let row = feed.lastElementChild;
  if (!row || !row.classList.contains("chip-line")) {
    row = el("div", "chip-line");
    feed.append(row);
  }
  row.append(chip);
  scrollFeed();
}

let pinned = true;
$("feed").addEventListener("scroll", () => {
  const f = $("feed");
  pinned = f.scrollHeight - f.scrollTop - f.clientHeight < 90;
});

function scrollFeed(force) {
  if (!pinned && !force) return;
  const f = $("feed");
  requestAnimationFrame(() => { f.scrollTop = f.scrollHeight; });
}

/* ── acting ─────────────────────────────────────────────────────────── */

const input = $("input");

input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 140) + "px";
});

// plain Enter sends on a keyboard; on touch it makes a newline and you tap send
const touch = window.matchMedia("(pointer: coarse)").matches;
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && (!touch || e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    $("composer").requestSubmit();
  }
});

$("composer").onsubmit = async (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text || !S.campaign) return;
  input.value = "";
  input.style.height = "auto";
  pinned = true;
  const media = S.attached.map((m) => m.id);
  S.attached = [];
  renderAttachments();
  try {
    await api(`/api/campaigns/${S.campaign.id}/act`,
              { method: "POST", body: { text, media } });
  } catch (err) {
    toast(err.message);
    input.value = text;
  }
};

/* ── party bar & sheet ──────────────────────────────────────────────── */

function renderParty() {
  const bar = $("party-bar");
  bar.innerHTML = "";
  S.party.forEach((c) => {
    const box = el("div", "pc" + (c.name === S.campaign.you ? " you" : "") +
                             (c.hp === 0 ? " down" : ""));
    if (c.portrait) {
      const face = el("img", "face");
      face.src = `/api/campaigns/${S.campaign.id}/media/${c.portrait}`;
      face.alt = "";
      box.append(face);
    }
    box.append(el("div", "n", c.name));
    const track = el("div", "hpbar");
    const fill = el("div", "hpfill");
    fill.style.width = Math.max(0, Math.round((c.hp / c.max_hp) * 100)) + "%";
    if (c.hp / c.max_hp < 0.34) fill.style.background = "var(--red)";
    track.append(fill);
    box.append(track, el("div", "hptext", `${c.hp}/${c.max_hp}`));
    bar.append(box);
  });
}

const HUD_ROLLS = 4;

/* The HUD is the character sheet's headline, kept on screen: whoever you are
   playing, their HP and AC, anything wrong with them, and the last few rolls.
   Everything it needs already arrives on the party and dice events. */
function renderHud() {
  const hud = $("hud");
  const c = S.party.find((p) => p.name === (S.campaign && S.campaign.you));
  if (!c) { hud.classList.add("hidden"); return; }

  hud.classList.remove("hidden");
  hud.classList.toggle("collapsed", !S.hudOpen);
  $("hud-toggle").title = t(S.hudOpen ? "hud_collapse" : "hud_expand");

  const body = $("hud-body");
  body.innerHTML = "";

  // tapping the vitals opens the full sheet — the HUD is a summary, not a replacement
  const vitals = el("div", "hud-vitals");
  vitals.title = t("char_sheet");
  vitals.onclick = () => openDrawer(true);
  vitals.append(el("div", "hud-who", c.name));

  const frac = c.max_hp ? c.hp / c.max_hp : 0;
  const hp = el("div", "hud-hp" + (frac < 0.34 ? " low" : "") + (c.hp === 0 ? " down" : ""));
  const track = el("div", "hud-track");
  const fill = el("div", "hud-fill");
  fill.style.width = Math.max(0, Math.min(100, Math.round(frac * 100))) + "%";
  track.append(fill);
  hp.append(track, el("div", "hud-num", `${t("hp")} ${c.hp}/${c.max_hp}`));
  vitals.append(hp);

  const ac = el("div", "hud-ac");
  ac.append(document.createTextNode(t("ac") + " "), el("b", "", String(c.ac)));
  vitals.append(ac);
  body.append(vitals);

  if (c.conditions && c.conditions.length) {
    const conds = el("div", "hud-conds");
    c.conditions.forEach((x) => conds.append(el("span", "hud-cond", x)));
    body.append(conds);
  }

  const rolls = el("div", "hud-rolls");
  // newest first, so the roll that just landed is the one nearest the composer
  S.rolls.slice().reverse().forEach((r) => {
    const pill = el("div", "hud-roll" + (r.crit ? " crit-" + r.crit : ""));
    pill.title = r.reason || "";
    // The DM writes the reason for the feed, not for a pill: "Tamar: Athletics (STR)
    // to haul the mule back onto the trail vs DC 15" is a normal one. Drop the name -
    // this row is already yours - and let the rest truncate, so the total, which is the
    // whole point of the pill, never gets squeezed off the end. Full text is the title.
    const why = (r.reason || t("roll")).replace(/^[^:]{1,24}:\s*/, "");
    pill.append(el("span", "hud-why", why), el("b", "", String(r.total)));
    rolls.append(pill);
  });
  body.append(rolls);
}

/* ── the dashboard ──────────────────────────────────────────────────────
   One set of card builders, two shells. On a wide screen they fill a panel beside the
   story; on a phone they fill the same box behind tabs, because there is no room for
   both. Every builder returns a node and reads only from S.party, so it does not care
   which shell it lands in. */

const SKILL_ABILITY = {
  Athletics: "STR",
  Acrobatics: "DEX", "Sleight of Hand": "DEX", Stealth: "DEX",
  Arcana: "INT", History: "INT", Investigation: "INT", Nature: "INT", Religion: "INT",
  "Animal Handling": "WIS", Insight: "WIS", Medicine: "WIS",
  Perception: "WIS", Survival: "WIS",
  Deception: "CHA", Intimidation: "CHA", Performance: "CHA", Persuasion: "CHA",
};

const DESKTOP = window.matchMedia("(min-width: 1024px)");
const signed = (n) => (n >= 0 ? "+" : "") + n;
const abilityMod = (c, a) => Math.floor((c.abilities[a] - 10) / 2);

/* Mirrors rules.proficiency_bonus: +2 at level 1-4, a point every four after. */
const proficiencyBonus = (level) => 2 + Math.floor(Math.max(0, (level || 1) - 1) / 4);

function skillTotal(c, skill) {
  const base = abilityMod(c, SKILL_ABILITY[skill]);
  return base + ((c.skills || []).includes(skill) ? proficiencyBonus(c.level) : 0);
}

function card(titleKey, iconName) {
  const box = el("section", "card");
  if (titleKey) {
    const h = el("h3", "card-title");
    if (iconName) h.append(icon(iconName));
    h.append(el("span", "", t(titleKey)));
    box.append(h);
  }
  return box;
}

function cardVitals(c) {
  const box = card();
  const head = el("div", "who-row");
  if (c.portrait) {
    const face = el("img", "who-face");
    face.src = mediaUrl(c.portrait);
    face.alt = c.name;
    face.onclick = () => openLightbox(face.src, c.name);
    head.append(face);
  }
  const names = el("div");
  names.append(el("div", "who-name", c.name));
  names.append(el("div", "who-sub", t("level_line", c.level, tRace(c.race), tClass(c.class))));
  head.append(names);
  box.append(head);

  const frac = c.max_hp ? c.hp / c.max_hp : 0;
  const hp = el("div", "dash-hp" + (frac < 0.34 ? " low" : ""));
  const hpLabel = el("div", "dash-hpnum");
  hpLabel.append(icon("hp"), el("span", "", t("hp") + " " + c.hp + "/" + c.max_hp));
  const track = el("div", "dash-track");
  const fill = el("div", "dash-fill");
  fill.style.width = Math.max(0, Math.min(100, Math.round(frac * 100))) + "%";
  track.append(fill);
  hp.append(hpLabel, track);
  box.append(hp);

  const vitals = el("div", "sheet-vitals");
  [["ac", t("ac"), c.ac], ["xp", t("xp"), c.xp], ["gp", t("gp"), c.gold]]
    .forEach((row) => {
      const cell = el("div", "vital");
      const k = el("div", "k");
      k.append(icon(row[0]), el("span", "", row[1]));
      cell.append(k, el("div", "v", String(row[2])));
      vitals.append(cell);
    });
  box.append(vitals);

  const inv = el("p", "sheet-list");
  inv.append(el("b", "", t("carrying")));
  inv.append(document.createTextNode(c.inventory.join(", ") || t("nothing")));
  box.append(inv);
  return box;
}

function cardAbilities(c) {
  const box = card("abilities", "skill");
  const stats = el("div", "stats");
  Object.keys(c.abilities).forEach((a) => {
    const cell = el("div", "stat");
    const k = el("div", "k");
    k.append(icon(a, "ab-" + a), el("span", "", tStat(a)));
    cell.append(k, el("div", "v", String(c.abilities[a])),
                el("div", "m", signed(abilityMod(c, a))));
    stats.append(cell);
  });
  box.append(stats);
  return box;
}

function cardSkills(c) {
  // "skills" is the skill-name vocabulary map, not a string - t() would hand back the
  // whole object and render "[object Object]"
  const box = card("view_skills", "skill");
  const prof = new Set(c.skills || []);
  box.append(el("p", "hint", t("proficiency_is", signed(proficiencyBonus(c.level)))));

  const list = el("div", "skill-list");
  Object.keys(SKILL_ABILITY)
    .sort((a, b) => tSkill(a).localeCompare(tSkill(b)))
    .forEach((skill) => {
      const ability = SKILL_ABILITY[skill];
      const row = el("div", "skill-row" + (prof.has(skill) ? " prof" : ""));
      const name = el("div", "name");
      name.append(icon(ability, "ab-" + ability), el("span", "", tSkill(skill)));
      row.append(name);
      if (prof.has(skill)) {
        const mark = el("span", "prof-dot");
        mark.title = t("proficient");
        mark.setAttribute("aria-label", t("proficient"));
        row.append(mark);
      }
      row.append(el("div", "num", signed(skillTotal(c, skill))));
      list.append(row);
    });
  box.append(list);
  return box;
}

function cardConditions(c) {
  if (!c.conditions || !c.conditions.length) return null;
  const box = card("conditions", "cond");
  const chips = el("div", "cond-chips");
  // verbatim, including duplicates in two languages: the DM writes these as free text,
  // and a chip that shows the truth beats one that shows a filtered lie
  c.conditions.forEach((x) => chips.append(el("span", "hud-cond", x)));
  box.append(chips);
  return box;
}

function cardParty() {
  const box = card("party", "party");
  const list = el("div", "party-list");
  S.party.forEach((p) => {
    const row = el("div", "party-row" + (p.name === S.campaign.you ? " you" : ""));
    if (p.portrait) {
      const face = el("img", "party-face");
      face.src = mediaUrl(p.portrait);
      face.alt = "";
      row.append(face);
    }
    const who = el("div", "party-who");
    who.append(el("div", "party-name", p.name));
    who.append(el("div", "party-sub",
                  t("level_line", p.level, tRace(p.race), tClass(p.class))));
    const frac = p.max_hp ? p.hp / p.max_hp : 0;
    const track = el("div", "dash-track");
    const fill = el("div", "dash-fill");
    fill.style.width = Math.max(0, Math.round(frac * 100)) + "%";
    if (frac < 0.34) fill.style.background = "var(--red)";
    track.append(fill);
    who.append(track);
    row.append(who, el("div", "party-hp", p.hp + "/" + p.max_hp));
    list.append(row);
  });
  box.append(list);
  return box;
}

function cardRolls() {
  if (!S.rolls.length) return null;
  const box = card("recent_rolls", "dice");
  const list = el("div", "roll-list");
  S.rolls.slice().reverse().forEach((r) => {
    const row = el("div", "roll-row" + (r.crit ? " crit-" + r.crit : ""));
    row.append(el("span", "why", r.reason || t("roll")));
    row.append(el("b", "", String(r.total)));
    list.append(row);
  });
  box.append(list);
  return box;
}

/* Which shell, and what goes in it. */
const VIEWS = ["story", "sheet", "skills", "party"];
const VIEW_ICONS = { story: "story", sheet: "scroll", skills: "skill", party: "party" };

/* Not persisted, deliberately. The HUD's collapsed state is remembered because it is a
   strip; a remembered view tab means closing the app on "Skills" and reopening it to no
   story at all. */
let view = "story";

function renderDash() {
  const box = $("dash-body");
  if (!box) return;
  const c = S.party.find((p) => p.name === (S.campaign && S.campaign.you));
  box.innerHTML = "";
  const wide = DESKTOP.matches;

  if (c) {
    if (wide) {
      // all of it at once; the topbar strip already carries the party on a wide screen
      [cardVitals(c), cardAbilities(c), cardConditions(c), cardRolls(), cardSkills(c)]
        .forEach((n) => n && box.append(n));
    } else if (view === "sheet") {
      [cardVitals(c), cardAbilities(c), cardConditions(c), cardRolls()]
        .forEach((n) => n && box.append(n));
    } else if (view === "skills") {
      box.append(cardSkills(c));
    } else if (view === "party") {
      box.append(cardParty());
    }
  }

  // on a phone the panel replaces the story, so the tabs are the only way back to it
  $("dash").classList.toggle("hidden", wide ? false : view === "story");
  $("feed").classList.toggle("hidden", !wide && view !== "story");
  $("view-tabs").classList.toggle("hidden", wide);
  renderViewTabs();
}

function renderViewTabs() {
  const bar = $("view-tabs");
  bar.innerHTML = "";
  if (DESKTOP.matches) return;
  VIEWS.forEach((name) => {
    const b = el("button", "gtab" + (name === view ? " on" : ""));
    b.type = "button";
    b.append(icon(VIEW_ICONS[name]), el("span", "", t("view_" + name)));
    b.onclick = () => {
      view = name;
      renderDash();
      if (name === "story") scrollFeed(true);
    };
    bar.append(b);
  });
}

/* Anything that changes a character changes all three of these. */
function renderLive() {
  renderParty();
  renderHud();
  renderDash();
}

/* Re-lay-out when the screen changes shape - a rotated phone, a dragged window.
   `matchMedia` change is the tidier event but does not fire under every emulated
   viewport, and being wrong here strands someone with a hidden panel and no tabs.
   Caching the last known width to skip renders looked like an optimisation and was a
   bug: the cached flag and what the render actually read could disagree, leaving the
   layout stuck mid-flip. So: always re-render, once per frame. */
let dashTimer = 0;
function onViewportChange() {
  // a timer, not requestAnimationFrame: rAF is paused while the page is not
  // compositing - a background tab, a hidden window - so a resize there would never
  // be answered, and the layout would still be wrong when it came back
  clearTimeout(dashTimer);
  dashTimer = setTimeout(renderDash, 100);
}
DESKTOP.addEventListener("change", onViewportChange);
window.addEventListener("resize", onViewportChange);

/* What is left in the drawer once the sheet moved to the dashboard: your own things,
   the campaign's art, and the table's settings. Remembered between opens - unlike the
   play tabs, the drawer is modal and you always chose to open it. */
const DRAWER_TABS = ["you", "art", "table"];
const DRAWER_ICONS = { you: "scroll", art: "art", table: "gear" };
let drawerTab = localStorage.getItem("dtab") || "you";
if (!DRAWER_TABS.includes(drawerTab)) drawerTab = "you";

function showDrawerTab(name) {
  drawerTab = name;
  localStorage.setItem("dtab", name);
  document.querySelectorAll(".dtab-page")
    .forEach((p) => p.classList.toggle("hidden", p.dataset.tab !== name));
  const bar = $("drawer-tabs");
  bar.innerHTML = "";
  DRAWER_TABS.forEach((key) => {
    const b = el("button", "gtab" + (key === name ? " on" : ""));
    b.type = "button";
    b.append(icon(DRAWER_ICONS[key]), el("span", "", t("drawer_" + key)));
    b.onclick = () => showDrawerTab(key);
    bar.append(b);
  });
}

function openDrawer(open) {
  $("drawer").classList.toggle("hidden", !open);
  $("scrim").classList.toggle("hidden", !open);
  if (open) { showDrawerTab(drawerTab);
              renderAI(); renderPortrait(); renderLore(); renderGallery(); fillNotes();
              $("library-note").textContent = t("library_hint"); }
  else $("ai-list").classList.add("hidden");
}
$("btn-sheet").onclick = () => openDrawer(true);
$("hud-toggle").onclick = () => {
  S.hudOpen = !S.hudOpen;
  localStorage.setItem("hud", S.hudOpen ? "1" : "0");
  renderHud();
};
$("scrim").onclick = () => openDrawer(false);

$("btn-private-roll").onclick = async () => {
  const notation = $("private-roll").value.trim() || "1d20";
  try {
    const r = await api("/api/roll", { method: "POST", body: { notation } });
    $("private-result").textContent = r.detail;
  } catch (e) {
    $("private-result").textContent = e.message;
  }
};

document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!$("lightbox").classList.contains("hidden")) {
    $("lightbox").classList.add("hidden");
    return;
  }
  openDrawer(false);
});

boot();
