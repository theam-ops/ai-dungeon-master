"""Fetch the D&D 5e System Reference Document and turn it into the game's rulebook.

    python tools/fetch_srd.py                 download SRD 5.1 and convert it
    python tools/fetch_srd.py --pdf FILE      convert a PDF you already have

Writes `data/srd/srd-5.1.md` (one markdown section per heading, which is what
`game/rulebook.py` searches) and `data/srd/ATTRIBUTION.md`. Needs `pypdf`, used only here:

    pip install pypdf

Why 5.1 and not the newer 5.2.1: the game's own mechanics follow 5.1 - its spell-slot
tables, for one, give a level-1 ranger no slots, where 5.2.1 gives two. A 5.2.1 rulebook
would have the DM reading rules the dice do not follow.

The SRD 5.1 is published by Wizards of the Coast under CC-BY-4.0, which permits copying it
with the attribution below. The PDF is cached in tools/bin/ (git-ignored); only the
converted text and its attribution are meant to be committed.
"""

import argparse
import collections
import hashlib
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from game.rulebook import ATTRIBUTION  # noqa: E402

URL = "https://media.dndbeyond.com/compendium-images/srd/5.1/SRD_CC_v5.1.pdf"
CACHE = os.path.join(HERE, "bin", "SRD_CC_v5.1.pdf")
OUT_DIR = os.path.join(ROOT, "data", "srd")
MAX_BYTES = 20 * 1024 * 1024        # the real file is ~3 MB; anything far bigger is wrong


def download(url=URL, target=CACHE):
    os.makedirs(os.path.dirname(target), exist_ok=True)
    print(f"Downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "ai-dungeon-master/fetch_srd"})
    with urllib.request.urlopen(req, timeout=120) as r:
        if "pdf" not in (r.headers.get("Content-Type") or ""):
            raise SystemExit(f"Expected a PDF, got {r.headers.get('Content-Type')!r}")
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES or not data.startswith(b"%PDF"):
        raise SystemExit("That download is not the SRD PDF it should be.")
    with open(target, "wb") as f:
        f.write(data)
    print(f"  {len(data):,} bytes, sha256 {hashlib.sha256(data).hexdigest()}")
    return target


def runs(pdf_path):
    """Every run of text in the PDF with the size it is printed at, in reading order."""
    try:
        from pypdf import PdfReader
    except ImportError:
        raise SystemExit("This needs pypdf:  pip install pypdf")
    reader = PdfReader(pdf_path)
    out = []
    for page_no, page in enumerate(reader.pages, start=1):
        def visit(text, cm, tm, font_dict, font_size):
            # the size a run is drawn at is its font size scaled by the text matrix
            scale = abs(tm[3]) or abs(tm[0]) or 1
            size = round(font_size * scale, 1)
            if text:
                out.append((page_no, text, size))
        page.extract_text(visitor_text=visit)
    return out


FOOTER_RE = re.compile(r"^System Reference Document 5\.1(?: \d{1,3})?$")
MIN_TIER_CHARS = 200     # a "size" with less text than this is a stray, not a heading tier


def to_markdown(chunks):
    """Lines printed bigger than the body text become headings, ranked by size.

    The SRD prints headings at five sizes. The smallest - Exhaustion, Casting in Armor -
    is only ten percent bigger than the body text, so the threshold is close to it; the
    page footer happens to share that size, and is dropped along with the page number
    that follows it every time.
    """
    kept, after_footer = [], False
    for page, text, size in chunks:
        flat = " ".join(text.split())        # the footer is spaced with tabs and NBSPs
        if FOOTER_RE.match(flat):             # sometimes with its page number fused on
            after_footer = True
            continue
        if after_footer and flat.isdigit():
            continue                          # its page number
        after_footer = False if flat else after_footer
        kept.append((page, text, size))
    chunks = kept

    weight = collections.Counter()
    for _, text, size in chunks:
        weight[size] += len(text.strip())
    body = weight.most_common(1)[0][0]
    heading_sizes = sorted({s for s in weight
                            if s > body * 1.05 and weight[s] >= MIN_TIER_CHARS},
                           reverse=True)[:5]
    level = {size: i + 1 for i, size in enumerate(heading_sizes)}

    lines, current, current_size = [], [], None

    def flush():
        text = re.sub(r"\s+", " ", "".join(current)).strip()
        if FOOTER_RE.match(text):        # a footer split across runs, joined back up here
            return
        if text:
            if current_size in level:
                lines.append("")
                lines.append("#" * level[current_size] + " " + text)
                lines.append("")
            else:
                lines.append(text)

    for _, text, size in chunks:
        if size != current_size and current:
            flush()
            current = []
        current_size = size
        current.append(text)
    flush()
    return "\n".join(lines), body, heading_sizes


def main():
    ap = argparse.ArgumentParser(description="Fetch and convert the SRD 5.1.")
    ap.add_argument("--pdf", help="convert this PDF instead of downloading")
    args = ap.parse_args()

    pdf = args.pdf or (CACHE if os.path.exists(CACHE) else download())
    chunks = runs(pdf)
    markdown, body, headings = to_markdown(chunks)
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "srd-5.1.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(markdown.strip() + "\n")
    with open(os.path.join(OUT_DIR, "ATTRIBUTION.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("# Attribution\n\n" + ATTRIBUTION + "\n")
    count = sum(1 for line in markdown.splitlines() if line.startswith("#"))
    print(f"  body text {body}pt; heading sizes {headings}")
    print(f"  {count:,} sections, {len(markdown):,} characters -> {OUT_DIR}")


if __name__ == "__main__":
    main()
