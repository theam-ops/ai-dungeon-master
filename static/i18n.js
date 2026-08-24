/* Interface strings. `t(key, ...args)` fills {0}, {1}, ... in order.

   Race, class and ability names are stored in English in the database and translated
   here for display, so one campaign stays playable whatever language each player has
   their own interface set to. */

const STRINGS = {
  en: {
    app_title: "AI Dungeon\nMaster",
    locked: "This table is locked.",
    password: "Password",
    enter: "Enter",
    tagline: "Claude runs the table. The dice are real.",
    no_key: "No AI available — a free Gemini key (aistudio.google.com) or Ollama gets you playing.",
    language: "Language",

    continue: "Continue",
    start_new: "Start something new",
    new_campaign: "New campaign",
    join_code: "Join code",
    join: "Join",
    import: "Import a saved campaign",
    import_folder: "…or an unzipped folder",
    no_campaign_json: "that folder has no campaign.json in it",
    playing_as: "as {0}",
    enter_code: "Enter the 6-character code.",
    import_fail: "Couldn't import that file: {0}",

    back: "← Back",
    roll_character: "Roll a character",
    join_party: "Join the party",
    campaign_name: "Campaign name",
    campaign_ph: "The Salt Road",
    character_name: "Character name",
    character_ph: "Vess",
    race: "Race",
    klass: "Class",
    abilities: "Ability scores",
    reroll: "Reroll 4d6",
    begin: "Begin",
    need_name: "Your character needs a name.",
    gear_hint: "d{0} hit die · {1} focus · starts with {2}",
    dm_language: "The DM narrates in",

    pick_claim: "Claim a character to continue on this device, or make a new one.",
    pick_empty: "Nobody has rolled a character yet.",
    make_new: "Make a new character",
    in_play: "in play",
    free: "free",
    char_line: "level {0} {1} {2}",

    back_lobby: "Back to lobby",
    char_sheet: "Character sheet",
    thinking: "the DM is thinking",
    what_do: "What do you do?",
    act: "Act",
    joins: "{0} joins the party.",

    level_line: "Level {0} {1} {2}",
    hp: "HP", ac: "AC", xp: "XP", gp: "GP",
    roll: "roll",
    hud_collapse: "Hide the roll log",
    hud_expand: "Show the roll log",
    carrying: "Carrying",
    nothing: "nothing",
    conditions: "Conditions",
    campaign_code: "Campaign code {0}",
    roll_privately: "Roll privately",
    share_code: "Share code",
    export: "Export",
    copied: "Invite link copied",
    code_is: "Code: {0}",
    imported: "Campaign imported",
    share_text: 'Join my D&D campaign "{0}" — code {1}',

    offline: "Offline",
    no_answer: "The server isn't answering.",

    attach: "Attach an image",
    library: "Campaign library",
    ai_checking: "checking…",
    claude_signin: "Sign in with Claude",
    claude_signin_step: "Open the link, approve it, then paste the code Anthropic shows you.",
    claude_signin_open: "Open the Claude sign-in page ↗",
    claude_signin_code: "Paste the code",
    claude_signin_finish: "Finish",
    claude_signin_ok: "Signed in. Claude is ready to run the table.",
    claude_signin_busy: "Starting…",
    claude_signin_remote: "Sign in on the computer running the game — this page can't do it from here.",
    ai_signed_in: "signed in",
    ai_sign_in: "sign in needed",
    ai_sign_in_how: "Claude Code is installed but not signed in. Open a terminal, run `claude`, and sign in with your Claude account — then reopen this list.",
    looked_up: "checked the notes for “{0}”",
    library_import: "Import a folder of your own material",
    library_hint: "Art and notes from a campaign you already run. Images become handouts; .md, .txt and .html files become notes the DM can search.",
    library_working: "Uploading… {0} of {1}",
    library_done: "Added {0} images and {1} documents.",
    library_skipped: "Skipped {0} file(s).",
    library_nothing: "Nothing in that folder could be used.",
    lore_chars: "{0} characters",
    lore_remove: "Remove",
    attach_folder: "Attach a folder of images",
    no_images_here: "No images in that folder.",
    used_first_n: "Attached the first {0} of {1} images.",
    portrait: "Portrait",
    upload: "Upload",
    from_link: "From a link",
    draw_it: "Draw it",
    remove: "Remove",
    scene_art: "Scene art",
    illustrate: "Illustrate this scene",
    illustrating: "Drawing…",
    ask_url: "Paste the address of an image",
    ask_portrait_prompt: "Describe your character for the artist",
    no_scene_yet: "Nothing has happened yet to illustrate.",
    image_failed: "That image didn't work: {0}",
    image_added: "Image added",
    no_artist: "No AI here can draw. That needs a Gemini key with billing enabled.",
    shows_image: "{0} shows an image",
    portrait_hint: "Shown on your sheet and beside your name at the table.",
    which_ai: "Which AI runs the game",
    ai_switched: "{0} is running the game now.",
    ai_took_over: "{0} ran out — {1} has taken over the table.",
    ai_no_key: "no key",
    ai_offline: "not running",
    ai_local: "on this PC",
    ai_free: "free tier",
    ai_get_key: "Get a key ↗",
    ai_install: "Install Ollama ↗",
    ai_set_env: "Then set {0} on the server and restart it",
    get_key_from: "Get a free key from {0} ↗",
    get_ollama: "Or install Ollama — free and offline ↗",
    paste_key: "…or paste a key here",
    key_ph: "Paste your API key",
    key_save: "Save",
    key_saving: "Checking…",
    key_ok: "{0} is ready. You can start a campaign now.",
    key_bad: "That key didn't work: {0}",
    key_which: "Which key is this?",
    key_stored: "Stored on this server, not in your browser.",
    ai_note: "If this one runs out of credit mid-turn, the next one available takes over automatically.",
    ai_note_alone: "Only one AI is available. Add a free Gemini or Groq key, or install Ollama, for a backup.",

    ch_hp: "HP {0} → {1}/{2}",
    ch_down: "unconscious at 0 HP",
    ch_xp: "+{0} XP",
    ch_gold_gain: "+{0} gp",
    ch_gold_spend: "−{0} gp",
    ch_item_add: "+ {0}",
    ch_item_rm: "− {0}",
    ch_cond_add: "{0}",
    ch_cond_rm: "cured: {0}",
    ch_level: "Level {0}! max HP {1}",

    races: {}, classes: {}, stats: {},
  },

  th: {
    app_title: "เอไอ\nดันเจียนมาสเตอร์",
    locked: "โต๊ะนี้ถูกล็อกไว้",
    password: "รหัสผ่าน",
    enter: "เข้าสู่ระบบ",
    tagline: "Claude เป็นผู้คุมเกม และลูกเต๋าทอยจริง",
    no_key: "ยังไม่มี AI ที่ใช้ได้ — ใช้คีย์ Gemini ฟรี (aistudio.google.com) หรือ Ollama ก็เริ่มเล่นได้",
    language: "ภาษา",

    continue: "เล่นต่อ",
    start_new: "เริ่มเรื่องใหม่",
    new_campaign: "แคมเปญใหม่",
    join_code: "รหัสเข้าร่วม",
    join: "เข้าร่วม",
    import: "นำเข้าแคมเปญที่บันทึกไว้",
    import_folder: "…หรือโฟลเดอร์ที่แตกไฟล์แล้ว",
    no_campaign_json: "ไม่พบไฟล์ campaign.json ในโฟลเดอร์นั้น",
    playing_as: "เล่นเป็น {0}",
    enter_code: "กรอกรหัส 6 ตัวอักษร",
    import_fail: "นำเข้าไฟล์นี้ไม่ได้: {0}",

    back: "← ย้อนกลับ",
    roll_character: "สร้างตัวละคร",
    join_party: "เข้าร่วมปาร์ตี้",
    campaign_name: "ชื่อแคมเปญ",
    campaign_ph: "เส้นทางเกลือ",
    character_name: "ชื่อตัวละคร",
    character_ph: "เวส",
    race: "เผ่าพันธุ์",
    klass: "อาชีพ",
    abilities: "ค่าความสามารถ",
    reroll: "ทอยใหม่ 4d6",
    begin: "เริ่มการผจญภัย",
    need_name: "ตัวละครต้องมีชื่อ",
    gear_hint: "ลูกเต๋าพลังชีวิต d{0} · เน้น{1} · เริ่มด้วย {2}",
    dm_language: "ผู้คุมเกมจะเล่าเรื่องเป็น",

    pick_claim: "เลือกตัวละครเดิมเพื่อเล่นต่อบนเครื่องนี้ หรือสร้างตัวใหม่",
    pick_empty: "ยังไม่มีใครสร้างตัวละคร",
    make_new: "สร้างตัวละครใหม่",
    in_play: "มีผู้เล่นแล้ว",
    free: "ว่าง",
    char_line: "เลเวล {0} {1}{2}",

    back_lobby: "กลับหน้าหลัก",
    char_sheet: "ใบตัวละคร",
    thinking: "ผู้คุมเกมกำลังคิด",
    what_do: "คุณจะทำอะไร?",
    act: "ลงมือ",
    joins: "{0} เข้าร่วมปาร์ตี้",

    level_line: "เลเวล {0} {1}{2}",
    hp: "พลัง", ac: "เกราะ", xp: "EXP", gp: "ทอง",
    roll: "ทอย",
    hud_collapse: "ซ่อนบันทึกการทอย",
    hud_expand: "แสดงบันทึกการทอย",
    carrying: "ของที่พกติดตัว",
    nothing: "ไม่มีอะไร",
    conditions: "สถานะ",
    campaign_code: "รหัสแคมเปญ {0}",
    roll_privately: "ทอยส่วนตัว",
    share_code: "แชร์รหัส",
    export: "ส่งออก",
    copied: "คัดลอกลิงก์เชิญแล้ว",
    code_is: "รหัส: {0}",
    imported: "นำเข้าแคมเปญแล้ว",
    share_text: 'มาเล่น D&D แคมเปญ "{0}" ด้วยกัน — รหัส {1}',

    offline: "ออฟไลน์",
    no_answer: "เซิร์ฟเวอร์ไม่ตอบสนอง",

    attach: "แนบรูปภาพ",
    library: "คลังแคมเปญ",
    ai_checking: "กำลังตรวจสอบ…",
    claude_signin: "เข้าสู่ระบบด้วย Claude",
    claude_signin_step: "เปิดลิงก์ อนุมัติ แล้ววางรหัสที่ Anthropic แสดงให้",
    claude_signin_open: "เปิดหน้าเข้าสู่ระบบ Claude ↗",
    claude_signin_code: "วางรหัสที่นี่",
    claude_signin_finish: "เสร็จสิ้น",
    claude_signin_ok: "เข้าสู่ระบบแล้ว Claude พร้อมคุมโต๊ะ",
    claude_signin_busy: "กำลังเริ่ม…",
    claude_signin_remote: "ต้องเข้าสู่ระบบบนเครื่องที่รันเกม หน้านี้ทำให้ไม่ได้",
    ai_signed_in: "เข้าสู่ระบบแล้ว",
    ai_sign_in: "ต้องเข้าสู่ระบบ",
    ai_sign_in_how: "ติดตั้ง Claude Code แล้วแต่ยังไม่ได้เข้าสู่ระบบ เปิดเทอร์มินัล พิมพ์ `claude` แล้วเข้าสู่ระบบด้วยบัญชี Claude ของคุณ จากนั้นเปิดรายการนี้ใหม่",
    looked_up: "เปิดดูบันทึกเรื่อง “{0}”",
    library_import: "นำเข้าโฟลเดอร์ของคุณเอง",
    library_hint: "ภาพและบันทึกจากแคมเปญที่คุณเล่นอยู่แล้ว รูปภาพจะกลายเป็นภาพประกอบ ส่วนไฟล์ .md .txt และ .html จะกลายเป็นบันทึกที่ DM ค้นได้",
    library_working: "กำลังอัปโหลด… {0} จาก {1}",
    library_done: "เพิ่มรูป {0} ภาพ และเอกสาร {1} ไฟล์",
    library_skipped: "ข้ามไป {0} ไฟล์",
    library_nothing: "ไม่มีไฟล์ในโฟลเดอร์นั้นที่ใช้ได้",
    lore_chars: "{0} ตัวอักษร",
    lore_remove: "ลบ",
    attach_folder: "แนบทั้งโฟลเดอร์รูปภาพ",
    no_images_here: "ไม่มีรูปภาพในโฟลเดอร์นั้น",
    used_first_n: "แนบรูปแรก {0} จาก {1} รูป",
    portrait: "รูปตัวละคร",
    upload: "อัปโหลด",
    from_link: "จากลิงก์",
    draw_it: "ให้ AI วาด",
    remove: "ลบออก",
    scene_art: "ภาพประกอบฉาก",
    illustrate: "วาดภาพฉากนี้",
    illustrating: "กำลังวาด…",
    ask_url: "วางที่อยู่ของรูปภาพ",
    ask_portrait_prompt: "อธิบายตัวละครของคุณให้ผู้วาดฟัง",
    no_scene_yet: "ยังไม่มีเหตุการณ์ให้วาด",
    image_failed: "ใช้รูปนี้ไม่ได้: {0}",
    image_added: "เพิ่มรูปแล้ว",
    no_artist: "ยังไม่มี AI ที่วาดได้ ต้องใช้คีย์ Gemini ที่เปิดการเรียกเก็บเงินแล้ว",
    shows_image: "{0} แสดงรูปภาพ",
    portrait_hint: "แสดงบนใบตัวละครและข้างชื่อของคุณที่โต๊ะ",
    which_ai: "AI ที่คุมเกมนี้",
    ai_switched: "ตอนนี้ {0} เป็นผู้คุมเกม",
    ai_took_over: "{0} หมดโควตา — {1} เข้ามาคุมเกมแทนแล้ว",
    ai_no_key: "ไม่มีคีย์",
    ai_offline: "ยังไม่ได้เปิด",
    ai_local: "บนเครื่องนี้",
    ai_free: "ใช้ฟรี",
    ai_get_key: "ขอคีย์ ↗",
    ai_install: "ติดตั้ง Ollama ↗",
    ai_set_env: "จากนั้นตั้งค่า {0} บนเซิร์ฟเวอร์แล้วรีสตาร์ท",
    get_key_from: "ขอคีย์ฟรีจาก {0} ↗",
    get_ollama: "หรือติดตั้ง Ollama — ฟรีและใช้ออฟไลน์ได้ ↗",
    paste_key: "…หรือวางคีย์ที่นี่",
    key_ph: "วางคีย์ API ของคุณ",
    key_save: "บันทึก",
    key_saving: "กำลังตรวจสอบ…",
    key_ok: "{0} พร้อมใช้งานแล้ว เริ่มแคมเปญได้เลย",
    key_bad: "คีย์นี้ใช้ไม่ได้: {0}",
    key_which: "นี่คือคีย์ของอะไร?",
    key_stored: "เก็บไว้บนเซิร์ฟเวอร์ ไม่ได้เก็บในเบราว์เซอร์",
    ai_note: "ถ้าตัวนี้หมดโควตากลางเกม ตัวถัดไปที่ใช้ได้จะเข้ามาแทนอัตโนมัติ",
    ai_note_alone: "มี AI ที่ใช้ได้ตัวเดียว เพิ่มคีย์ Gemini หรือ Groq แบบฟรี หรือติดตั้ง Ollama เพื่อมีตัวสำรอง",

    ch_hp: "พลังชีวิต {0} → {1}/{2}",
    ch_down: "หมดสติที่ 0 พลังชีวิต",
    ch_xp: "+{0} EXP",
    ch_gold_gain: "+{0} ทอง",
    ch_gold_spend: "−{0} ทอง",
    ch_item_add: "+ {0}",
    ch_item_rm: "− {0}",
    ch_cond_add: "{0}",
    ch_cond_rm: "หายจาก: {0}",
    ch_level: "เลเวล {0}! พลังชีวิตสูงสุด {1}",

    races: {
      Human: "มนุษย์", Elf: "เอลฟ์", Dwarf: "คนแคระ", Halfling: "ฮาล์ฟลิง",
      "Half-Orc": "ครึ่งออร์ค", Tiefling: "ทีฟลิง", Dragonborn: "ดราก้อนบอร์น",
      Gnome: "โนม",
    },
    classes: {
      Fighter: "นักรบ", Wizard: "นักเวทมนตร์", Rogue: "นักโจรกรรม",
      Cleric: "นักบวช", Ranger: "นักล่า", Bard: "นักดนตรี",
    },
    stats: {
      STR: "พละ", DEX: "คล่องแคล่ว", CON: "อดทน",
      INT: "สติปัญญา", WIS: "ปัญญาญาณ", CHA: "เสน่ห์",
    },
  },
};

let LANG = "en";

function setLang(lang) {
  LANG = STRINGS[lang] ? lang : "en";
  localStorage.setItem("lang", LANG);
  document.documentElement.lang = LANG;
  document.documentElement.dataset.lang = LANG;
  return LANG;
}

function getLang() { return LANG; }

function t(key, ...args) {
  const s = (STRINGS[LANG] && STRINGS[LANG][key]) ?? STRINGS.en[key] ?? key;
  return typeof s === "string" ? s.replace(/\{(\d+)\}/g, (_, i) => args[i] ?? "") : s;
}

/* Race / class / ability names: English keys in, display names out. */
const tRace  = (k) => (STRINGS[LANG].races  || {})[k] || k;
const tClass = (k) => (STRINGS[LANG].classes || {})[k] || k;
const tStat  = (k) => (STRINGS[LANG].stats  || {})[k] || k;

/* Apply translations to everything marked up in index.html. */
function applyI18n(root = document) {
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
function renderChange(c) {
  switch (c.t) {
    case "hp":     return t("ch_hp", c.from, c.to, c.max);
    case "down":   return t("ch_down");
    case "xp":     return t("ch_xp", c.gain);
    case "gold":   return c.delta >= 0 ? t("ch_gold_gain", c.delta)
                                       : t("ch_gold_spend", Math.abs(c.delta));
    case "item+":  return t("ch_item_add", c.item);
    case "item-":  return t("ch_item_rm", c.item);
    case "cond+":  return t("ch_cond_add", c.cond);
    case "cond-":  return t("ch_cond_rm", c.cond);
    case "level":  return t("ch_level", c.level, c.max);
    default:       return "";
  }
}
