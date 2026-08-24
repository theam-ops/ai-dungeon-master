"""Server-side localisation.

Only two things need translating on this side: the starting gear written onto a new
character sheet, and the instruction that tells the DM which language to narrate in.
Everything else the player reads is either translated in the browser (static/i18n.js)
or written by the DM itself.

Mechanical keys - race, class, ability names - stay English in the database and are
translated for display. That keeps one campaign readable no matter which language each
player has their interface set to.
"""

LANGUAGES = {
    "en": {"name": "English", "native": "English"},
    "th": {"name": "Thai", "native": "ไทย"},
}

DEFAULT_LANG = "en"


def normalise(lang):
    return lang if lang in LANGUAGES else DEFAULT_LANG


# The DM's narration language. Appended to the system prompt.
NARRATION_INSTRUCTION = {
    "en": "",
    "th": """
ภาษา / LANGUAGE
- Narrate entirely in Thai (ภาษาไทย). Every word the players read - description, NPC
  dialogue, the reasons you give for dice rolls - must be Thai.
- Use natural, modern written Thai. Do not transliterate English sentences.
- Keep game mechanics readable: dice notation stays in digits (1d20+3), and DC is written
  as "DC 14". Ability names may be written in Thai (พละ, ความคล่องแคล่ว, ความอดทน,
  สติปัญญา, ปัญญาญาณ, เสน่ห์) or kept as STR/DEX/CON/INT/WIS/CHA - be consistent.
- Character, item and place names you invent should be Thai.
- The `reason` you pass to roll_dice is shown to the players, so write it in Thai.
- The `character_name` you pass to update_character must match the sheet exactly, as
  written - never translate a character's name.
- If a player writes in English, keep narrating in Thai unless they ask you to switch.
""",
}

# Starting gear, localised when the sheet is created so a Thai campaign's inventory
# reads as Thai from the first turn.
GEAR = {
    "th": {
        "longsword": "ดาบยาว",
        "shield": "โล่",
        "chain mail": "เกราะโซ่",
        "explorer's pack": "ถุงยังชีพนักสำรวจ",
        "quarterstaff": "ไม้เท้ายาว",
        "spellbook": "ตำราเวทมนตร์",
        "component pouch": "ถุงส่วนผสมเวท",
        "scholar's pack": "ถุงยังชีพนักปราชญ์",
        "shortsword": "ดาบสั้น",
        "shortbow": "ธนูสั้น",
        "thieves' tools": "ชุดเครื่องมือโจร",
        "leather armor": "เกราะหนัง",
        "mace": "กระบอง",
        "chain shirt": "เสื้อเกราะโซ่",
        "holy symbol": "สัญลักษณ์ศักดิ์สิทธิ์",
        "longbow": "ธนูยาว",
        "two shortswords": "ดาบสั้นสองเล่ม",
        "hunting trap": "กับดักล่าสัตว์",
        "rapier": "ดาบเรเปียร์",
        "lute": "พิณลูท",
        "diplomat's pack": "ถุงยังชีพนักการทูต",
        "rations (5)": "เสบียง (5)",
        "torch": "คบไฟ",
        "waterskin": "ถุงน้ำ",
    },
}


def gear(item, lang):
    return GEAR.get(lang, {}).get(item, item)


# Race and class names, for the prompt the DM sees. The UI translates these itself.
NAMES = {
    "th": {
        "Human": "มนุษย์", "Elf": "เอลฟ์", "Dwarf": "คนแคระ", "Halfling": "ฮาล์ฟลิง",
        "Half-Orc": "ครึ่งออร์ค", "Tiefling": "ทีฟลิง", "Dragonborn": "ดราก้อนบอร์น",
        "Gnome": "โนม",
        "Fighter": "นักรบ", "Wizard": "นักเวทมนตร์", "Rogue": "นักโจรกรรม",
        "Cleric": "นักบวช", "Ranger": "นักล่า", "Bard": "นักดนตรี",
    },
}


def name(key, lang):
    return NAMES.get(lang, {}).get(key, key)


# Terminal client strings (dnd.py). The web UI has its own set in static/i18n.js.
CLI = {
    "en": {
        "tagline": "real dice, persistent world",
        "web_hint": "(want a UI, or to play from your phone? run:  python -m uvicorn server:app)",
        "existing": "Existing campaigns:",
        "load_prompt": "Load one (number), or press Enter for a new character:",
        "loaded": "Loaded {0}.",
        "new_character": "NEW CHARACTER",
        "ask_name": "Your character's name?",
        "races": "Races:",
        "classes": "Classes:",
        "ask_race": "Race (name or number)?",
        "ask_class": "Class (name or number)?",
        "keep_stats": "Keep these stats? (y/n)",
        "rerolling": "rerolling...",
        "pick_one": "pick one of: {0}",
        "level_line": "{0} - level {1} {2} {3}",
        "vitals": "HP {0}/{1}   AC {2}   XP {3}   GP {4}",
        "inventory": "Inventory:",
        "conditions": "Conditions:",
        "empty": "(empty)",
        "saved": "Saved to {0}",
        "farewell": "Saved to {0}. Farewell, {1}.",
        "unknown_cmd": "unknown command {0} - try /help",
        "down": "You are down. Every action is now a death save.",
        "no_key": "No AI key found in the environment.",
        "ai_list": "Available AI (> is the one running this game):",
        "ai_now": "The DM is now {0}.",
        "ai_unknown": "no AI called {0} - run /ai to see the list",
        "help": """Type what your character does, in plain language. Some commands:
  /sheet          show the character sheet
  /roll 2d6+1     roll dice yourself (the DM doesn't see it)
  /ai             list the available AI, or /ai <name> to switch
  /save           save the campaign
  /recap          have the DM summarize the story so far
  /help           this list
  /quit           save and exit""",
    },
    "th": {
        "tagline": "ลูกเต๋าทอยจริง โลกที่จดจำทุกอย่าง",
        "web_hint": "(อยากได้หน้าจอ หรือเล่นจากมือถือ? รัน:  python -m uvicorn server:app)",
        "existing": "แคมเปญที่มีอยู่:",
        "load_prompt": "เลือกหมายเลขเพื่อเล่นต่อ หรือกด Enter เพื่อสร้างตัวละครใหม่:",
        "loaded": "โหลด {0} แล้ว",
        "new_character": "ตัวละครใหม่",
        "ask_name": "ตัวละครของคุณชื่ออะไร?",
        "races": "เผ่าพันธุ์:",
        "classes": "อาชีพ:",
        "ask_race": "เผ่าพันธุ์ (ชื่อหรือหมายเลข)?",
        "ask_class": "อาชีพ (ชื่อหรือหมายเลข)?",
        "keep_stats": "ใช้ค่าเหล่านี้ไหม? (y/n)",
        "rerolling": "ทอยใหม่...",
        "pick_one": "เลือกอย่างใดอย่างหนึ่ง: {0}",
        "level_line": "{0} - เลเวล {1} {2}{3}",
        "vitals": "พลังชีวิต {0}/{1}   เกราะ {2}   EXP {3}   ทอง {4}",
        "inventory": "ของที่พกติดตัว:",
        "conditions": "สถานะ:",
        "empty": "(ไม่มีอะไร)",
        "saved": "บันทึกไว้ที่ {0}",
        "farewell": "บันทึกไว้ที่ {0} ลาก่อน {1}",
        "unknown_cmd": "ไม่รู้จักคำสั่ง {0} - ลอง /help",
        "down": "คุณล้มลงแล้ว ทุกการกระทำนับเป็นการทอยเอาชีวิตรอด",
        "no_key": "ไม่พบคีย์ AI ในระบบ",
        "ai_list": "AI ที่ใช้ได้ (> คือตัวที่กำลังคุมเกมนี้):",
        "ai_now": "ตอนนี้ผู้คุมเกมคือ {0}",
        "ai_unknown": "ไม่มี AI ชื่อ {0} - พิมพ์ /ai เพื่อดูรายการ",
        "help": """พิมพ์สิ่งที่ตัวละครของคุณทำเป็นภาษาปกติ คำสั่งที่ใช้ได้:
  /sheet          ดูใบตัวละคร
  /roll 2d6+1     ทอยลูกเต๋าเอง (ผู้คุมเกมไม่เห็น)
  /ai             ดูรายการ AI หรือ /ai <ชื่อ> เพื่อสลับ
  /save           บันทึกแคมเปญ
  /recap          ให้ผู้คุมเกมสรุปเรื่องที่ผ่านมา
  /help           รายการนี้
  /quit           บันทึกแล้วออก""",
    },
}


def cli(key, lang, *args):
    s = CLI.get(lang, CLI["en"]).get(key) or CLI["en"].get(key, key)
    for i, a in enumerate(args):
        s = s.replace("{%d}" % i, str(a))
    return s
