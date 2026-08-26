"""The player's own campaign documents, and how the DM looks things up in them.

A campaign someone has already been running by hand comes with notes: a character sheet,
an NPC roster, a ledger of what happened. Those are far too big to ride along in the
prompt - a single sheet can be 100KB - so they are stored whole and the DM is given a
tool to search them, pulling back only the passage it needs for the turn it is on.

Matching is plain substring, not word-based, which is deliberate: Thai is written without
spaces between words, so anything that tokenises on whitespace finds nothing in a Thai
document. Substring search works the same in both languages this game speaks.
"""

import html
import re

# One passage is big enough to carry a whole NPC entry or a scene description, and small
# enough that a handful of them don't crowd out the story so far.
WINDOW = 700
MAX_HITS = 6
MAX_REPLY = 6000

TAG_RE = re.compile(r"<[^>]+>")
STYLE_RE = re.compile(r"<style\b[^>]*>.*?</style>", re.S | re.I)
SCRIPT_RE = re.compile(r"<script\b[^>]*>(.*?)</script>", re.S | re.I)
DATA_URI_RE = re.compile(r"data:[a-z]+/[a-z0-9.+-]+;base64,[A-Za-z0-9+/=]+", re.I)
BREAK_RE = re.compile(r"<(br|/p|/div|/h\d|/li|/tr)\s*/?>", re.I)
BLANKS_RE = re.compile(r"\n{3,}")
SPACES_RE = re.compile(r"[ \t]+")

# A hand-built page keeps its content in a script; a framework bundle is just noise.
# Size is what separates the two, once the embedded images are gone.
MAX_SCRIPT = 200_000


# Notes people have actually been keeping are not all UTF-8. A Thai campaign's notes
# are very often plain .txt saved out of Windows Notepad, which until recently defaulted
# to the system ANSI codepage - CP874 for a Thai Windows - and "Unicode" in that Save As
# dialog means UTF-16, not UTF-8. Both decode as mojibake under `errors="replace"` and
# import silently: the document lands, `search_lore` finds nothing in it ever, and
# nothing tells anyone why.
BOMS = ((b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16"), (b"\xef\xbb\xbf", "utf-8-sig"))
# A run of three, not a single character: CP874 maps every high byte to something in the
# Thai block, so one hit proves nothing - "café" in CP1252 decodes to a Latin word with
# one Thai character in it. Actual Thai prose comes in long unbroken runs.
THAI_RUN_RE = re.compile(r"[฀-๿]{3,}")


def decode(raw):
    """Bytes off someone's disk as text, trying the encodings their notes really use.

    Order matters and is deliberate:

    1. A byte-order mark settles UTF-16 and UTF-8-with-BOM outright.
    2. Strict UTF-8, so a correct modern file is never second-guessed.
    3. CP874 - but only if the result actually contains Thai. CP874 decodes almost any
       byte string without complaint, so accepting it unconditionally would turn a
       legacy *Western* file into confident Thai gibberish. Requiring Thai in the output
       keeps this rescue narrow to the files it is for.
    4. UTF-8 with replacement, which is what this did before and so can only be an
       improvement - nothing that used to import now fails.
    """
    if isinstance(raw, str):
        return raw
    for bom, enc in BOMS:
        if raw.startswith(bom):
            try:
                return raw.decode(enc)
            except (UnicodeDecodeError, LookupError):
                break
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        thai = raw.decode("cp874")
    except (UnicodeDecodeError, LookupError):
        thai = ""
    if THAI_RUN_RE.search(thai):
        return thai
    return raw.decode("utf-8", errors="replace")


def to_text(name, raw):
    """A document as searchable plain text. HTML gets stripped; markdown is already text.

    Script contents are kept, not discarded. A single-file campaign ledger holds its
    whole roster inside one `<script>` as object literals, and dropping that would leave
    a 700KB document with four kilobytes of readable text in it. Embedded base64 images
    go first - they are most of the bytes and none of the meaning - and anything still
    enormous after that is a bundled library, so it is left out.

    `raw` may be bytes or text; bytes go through `decode` above.
    """
    raw = decode(raw)
    if name.lower().endswith((".html", ".htm")):
        raw = DATA_URI_RE.sub(" ", raw)
        raw = STYLE_RE.sub(" ", raw)
        raw = SCRIPT_RE.sub(
            lambda m: m.group(1) if len(m.group(1)) <= MAX_SCRIPT else " ", raw)
        raw = BREAK_RE.sub("\n", raw)
        raw = html.unescape(TAG_RE.sub("", raw))
    raw = SPACES_RE.sub(" ", raw.replace("\r\n", "\n").replace("\r", "\n"))
    return BLANKS_RE.sub("\n\n", raw).strip()


def _snippet(text, at, needle_len):
    """The passage around a hit, nudged out to line breaks so it doesn't cut mid-word.

    Thai has no spaces to break on, so lines are the only safe boundary; falling back to
    a hard slice is fine because the DM reads it as prose either way.
    """
    start = max(0, at - WINDOW // 2)
    end = min(len(text), at + needle_len + WINDOW // 2)
    nl = text.rfind("\n", start, at)
    if nl != -1:
        start = nl + 1
    nl = text.find("\n", end)
    if nl != -1 and nl - end < 200:
        end = nl
    lead = "…" if start > 0 else ""
    tail = "…" if end < len(text) else ""
    return f"{lead}{text[start:end].strip()}{tail}"


WORDY_RE = re.compile(r"[^\W_]", re.UNICODE)
MIN_DENSITY = 0.35


def _density(snippet):
    """Share of the passage that is letters or digits rather than punctuation.

    Stripping the base64 out of an HTML page leaves skeletons behind - a map of keys to
    empty strings, say. Those match and mean nothing, so a passage that is mostly quotes
    and commas is set aside while there are wordier ones to return.
    """
    if not snippet:
        return 0.0
    return len(WORDY_RE.findall(snippet)) / len(snippet)


def search(documents, query, document=None, max_hits=MAX_HITS):
    """Passages matching `query`, newest-irrelevant - document order, first hits first.

    `documents` is a list of (name, text). `document` narrows to one by name, which the
    DM can use once it knows from the manifest which file holds what.
    """
    query = (query or "").strip()
    if not query:
        return "ERROR: search_lore needs something to look for."

    wanted = (document or "").strip().lower()
    pool = [(n, t) for n, t in documents
            if not wanted or wanted in n.lower()]
    if not pool:
        known = ", ".join(n for n, _ in documents) or "none"
        return f"ERROR: no document called {document!r}. Available: {known}"

    needle = query.lower()
    hits, thin = [], []
    for name, text in pool:
        low = text.lower()
        at = low.find(needle)
        while at != -1 and len(hits) < max_hits:
            passage = _snippet(text, at, len(needle))
            line = f"[{name}]\n{passage}"
            (hits if _density(passage) >= MIN_DENSITY else thin).append(line)
            # jump past this passage so one dense page doesn't fill every slot
            at = low.find(needle, at + max(len(needle), WINDOW // 2))
        if len(hits) >= max_hits:
            break

    # a match in a sparse passage still beats telling the DM there is nothing there
    if not hits:
        hits = thin[:max_hits]

    if not hits:
        known = ", ".join(n for n, _ in pool)
        return (f"Nothing in the campaign documents matches {query!r}. "
                f"Searched: {known}. Try a shorter or differently spelled phrase.")

    reply = "\n\n---\n\n".join(hits)
    return reply[:MAX_REPLY] + ("\n\n[…more matches were cut]" if len(reply) > MAX_REPLY else "")


def manifest(documents):
    """A line per document for the system prompt, so the DM knows what it can search."""
    if not documents:
        return ""
    listed = "\n".join(f"- {d['name']} ({d['chars']:,} characters)" for d in documents)
    return (
        "CAMPAIGN DOCUMENTS\n"
        "The players have supplied their own notes for this campaign. These are the "
        "authority on anything they cover - names, backstory, what has already happened. "
        "You cannot see their contents from here; call search_lore to read a passage "
        "before inventing anything they might already settle, and whenever a player "
        "mentions a person, place or event you do not recognise.\n"
        f"{listed}"
    )
