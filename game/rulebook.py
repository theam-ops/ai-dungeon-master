"""The rulebook: the D&D 5e System Reference Document, for the DM to look rules up in.

The DM is a language model, and it half-remembers the rules. Asked how grappling works
it will produce something plausible - which is the failure this project exists to stop
for numbers, applied to procedure instead. So the SRD itself ships with the game, and
`lookup_rule` hands the DM the actual text before it rules.

The text lives in `data/srd/` as markdown, one section per heading, converted from the
official PDF by `tools/fetch_srd.py`. No rulebook installed means no `lookup_rule` tool:
a tool with nothing behind it is worse than none, because the model reaches for it anyway.

Search is by section, not by substring. Rules are written under headings - Grappled,
Cover, Falling - and a query for "grappled" should land on the section of that name, not
on whichever paragraph mentions the word most. The rulebook is English, so the DM is told
to query in English whatever language it is narrating in; that is a translation the model
does well, and it keeps this a plain keyword search rather than a vector index.
"""

import os
import re

RULEBOOK_DIR = os.environ.get("DND_RULEBOOK", os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "srd"))

# Wizards of the Coast's own wording - CC-BY-4.0 requires it wherever the text is used.
ATTRIBUTION = (
    'This work includes material taken from the System Reference Document 5.1 ("SRD 5.1") '
    "by Wizards of the Coast LLC and available at "
    "https://dnd.wizards.com/resources/systems-reference-document. The SRD 5.1 is licensed "
    "under the Creative Commons Attribution 4.0 International License available at "
    "https://creativecommons.org/licenses/by/4.0/legalcode.")

HEADING_RE = re.compile(r"^(#{1,5})\s+(.+?)\s*#*\s*$")
WORD_RE = re.compile(r"[a-z0-9]+")
STOP = {"the", "a", "an", "of", "to", "in", "on", "and", "or", "for", "how", "what", "does",
        "do", "is", "are", "can", "with", "when", "rule", "rules", "work", "works", "you",
        "your", "be", "by", "it", "if", "at", "as", "from", "into", "that", "this"}

MAX_SECTIONS = 3
RUNNER_UP = 0.5         # a second section must score at least half the best
MAX_CHARS = 1800          # per section returned
MAX_REPLY = 4500          # whole reply

_cache = {"dir": None, "sections": None}


def _stem(word):
    """Crude, but enough for rules prose: grapple, grappled, grappling all meet."""
    for suffix in ("ing", "ed", "es", "s", "e"):
        if len(word) > 4 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def _terms(text):
    return [_stem(w) for w in WORD_RE.findall(text.lower()) if w not in STOP]


def _parse(text, source):
    """Markdown into sections: each heading's own text, with its chapter as a breadcrumb -
    'Appendix PH-A: Conditions > Exhaustion'."""
    sections, trail, title, lines = [], [], None, []

    def close():
        body = "\n".join(lines).strip()
        if title and body:
            # chapter > section: the full trail can mislead - in the SRD, Exhaustion is
            # printed smaller than the other conditions, so it nests under Deafened
            path = f"{trail[0]} > {title}" if len(trail) > 1 else title
            sections.append({"title": title, "path": path, "text": body,
                             "source": source, "title_terms": set(_terms(title)),
                             "terms": _terms(body)})

    for line in text.splitlines():
        m = HEADING_RE.match(line)
        if m:
            close()
            level, title, lines = len(m.group(1)), m.group(2).strip(), []
            trail = trail[: level - 1] + [title]
        else:
            lines.append(line)
    close()
    return sections


def sections():
    """Every section of the installed rulebook, loaded once. Empty if none is installed."""
    if _cache["dir"] != RULEBOOK_DIR:
        found = []
        if os.path.isdir(RULEBOOK_DIR):
            for name in sorted(os.listdir(RULEBOOK_DIR)):
                if name.endswith(".md") and name.upper() != "ATTRIBUTION.MD":
                    with open(os.path.join(RULEBOOK_DIR, name), encoding="utf-8") as f:
                        found += _parse(f.read(), name)
        _cache.update(dir=RULEBOOK_DIR, sections=found)
    return _cache["sections"]


def installed():
    return bool(sections())


def _score(section, query_terms, query_words=()):
    wanted = set(query_terms)
    if not wanted:
        return 0
    title = section["title_terms"]
    body = section["terms"]
    present = {t for t in wanted if t in title or t in body}
    if not present:
        return 0
    score = 3 * len(present)                              # covers more of the question
    score += 8 * len(wanted & title)                      # named in the heading
    if title and title <= wanted:
        score += 25                                       # the heading *is* the question
    # stems make "grappled" and "grappling" the same word; the exact one should still win
    # - asking about the condition, you want the condition before the action
    score += 6 * len(set(query_words) & set(WORD_RE.findall(section["title"].lower())))
    score += sum(min(4, body.count(t)) for t in wanted)   # talked about, with a ceiling
    return score


def _excerpt(section, query_terms):
    text = section["text"]
    if len(text) <= MAX_CHARS:
        return text
    lower = text.lower()
    hits = [lower.find(t) for t in query_terms if lower.find(t) >= 0]
    start = max(0, min(hits) - 200) if hits else 0
    cut = text[start:start + MAX_CHARS]
    return ("…" if start else "") + cut.rstrip() + "…"


def search(query, limit=MAX_SECTIONS):
    """The best few sections for `query`, as (reply text, section titles found)."""
    terms = _terms(query or "")
    words = [w for w in WORD_RE.findall((query or "").lower()) if w not in STOP]
    if not terms:
        return "Say what rule to look up - a condition, an action, a hazard.", []
    ranked = sorted(((_score(s, terms, words), i, s) for i, s in enumerate(sections())),
                    key=lambda x: (-x[0], x[1]))
    top = ranked[0][0] if ranked else 0
    # runners-up only when they are nearly as good: "half cover" should not drag in
    # Half-Elf Traits, and every extra section is tokens the DM reads
    best = [s for score, _, s in ranked if score > 0 and score >= top * RUNNER_UP][:limit]
    if not best:
        return (f"Nothing in the rulebook matches {query!r}. Rule on it yourself, and say "
                "that it is a ruling.", [])
    parts, used = [], 0
    for s in best:
        block = f"## {s['path']}\n{_excerpt(s, terms)}"
        if used + len(block) > MAX_REPLY and parts:
            break
        parts.append(block)
        used += len(block)
    return "From the SRD 5.1:\n\n" + "\n\n".join(parts), [s["title"] for s in best]
