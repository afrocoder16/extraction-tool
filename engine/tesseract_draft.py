"""Build draft transcripts from the Tesseract geometry pass — free, offline.

This exists because a bad OCR read and a fabricated sentence are not the same
kind of wrong. Tesseract misreads glyphs and says so with a low confidence score;
a language model asked to transcribe a page it cannot read returns fluent Amharic
that was never printed. On page 159 Tesseract produced `ቃል ኪዳን ትገባልኝ` (correct,
with glyph slips elsewhere) where a model produced `ቃል ከዚህ ተግባለን` (invented).
Slips are fixable by a reader; inventions are not detectable by one.

Everything here is deterministic. Nothing is generated:

    text          Tesseract, run earlier on the cleaned layers
    colour        the red-ink pixel mask, per word box
    verse numeral the Ethiopic numeral block (U+1369-U+137C) at a line start
    title         short, centred, fully red — geometry already measured
    clipped       first/last line sitting hard against the page edge
    uncertain     every word Tesseract scored below MIN_WORD_CONFIDENCE

The output is a *draft*: honest about what was read and loud about what was not.
It is meant to be corrected in the review UI, not shipped as-is.

    python -m engine.tesseract_draft books/wudase-mariam --pages 159 --preview
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from . import book as book_mod
from .console import configure_utf8_stdio

# Ethiopic numerals ፩ ፪ ፫ … ፼. Verse markers in this book are always from this
# block, so a line opening with one is a numbered stanza — no model needed.
ETHIOPIC_NUMERAL = re.compile(r"^[፩-፼]+$")

# Tesseract's own confidence. Below this the reading goes to the reviewer as
# `uncertain` rather than being presented as settled text.
MIN_WORD_CONFIDENCE = 70.0

# A line whose box touches the very top or bottom of the page was sliced by the
# scan, so words are missing rather than merely misread.
EDGE_FRACTION = 0.03


def _is_verse_numeral(text: str) -> bool:
    return bool(ETHIOPIC_NUMERAL.match(text.strip()))


def _detect_clipped(layout: dict) -> list[dict]:
    """Lines running off the top or bottom edge of the scan."""
    lines = layout.get("lines") or []
    if not lines:
        return []
    height = layout["height"]
    margin = height * EDGE_FRACTION
    clipped = []

    first, last = lines[0], lines[-1]
    if first["bbox"][1] <= margin:
        clipped.append({
            "edge": "top",
            "note": "First line sits against the top edge of the scan — it is "
                    "probably sliced and words may be missing.",
        })
    if last["bbox"][3] >= height - margin:
        clipped.append({
            "edge": "bottom",
            "note": "Last line runs into the bottom edge of the scan — it is "
                    "probably sliced and words may be missing.",
        })
    return clipped


def _spans_from_words(words: list[dict]) -> list[dict]:
    """Merge consecutive same-colour words into spans, space-separated."""
    spans: list[dict] = []
    for word in words:
        colour = "red" if word["is_red"] else "black"
        if spans and spans[-1]["c"] == colour:
            spans[-1]["t"] += " " + word["text"]
        else:
            spans.append({"t": word["text"], "c": colour})
    return [s for s in spans if s["t"].strip()]


def build_page(bk: book_mod.Book, page: int) -> dict:
    path = bk.dir("layout") / f"page_{page:03d}.json"
    if not path.exists():
        raise FileNotFoundError(f"no layout for page {page} — run --stage layout first")
    layout = json.loads(path.read_text(encoding="utf-8"))

    blocks: list[dict] = []
    current: dict | None = None
    low_conf: list[dict] = []

    for line in layout.get("lines", []):
        words = list(line.get("words") or [])
        if not words:
            continue

        for word in words:
            if word["conf"] < MIN_WORD_CONFIDENCE:
                low_conf.append(
                    {"text": word["text"],
                     "why": f"Tesseract confidence {word['conf']:.0f} — check against the scan"}
                )

        # A line opening with an Ethiopic numeral starts a new numbered stanza.
        numeral = ""
        if _is_verse_numeral(words[0]["text"]):
            numeral = words[0]["text"].strip()
            words = words[1:]
            current = None  # force a new block

        if line.get("looks_like_title"):
            blocks.append({"type": "title", "verse_num_am": "", "_words": list(words)})
            current = None
            continue

        if current is None:
            current = {
                "type": "verse" if numeral else "prayer",
                "verse_num_am": numeral,
                "_words": [],
            }
            blocks.append(current)
        current["_words"].extend(words)

    out_blocks = []
    for block in blocks:
        spans = _spans_from_words(block["_words"])
        if spans:
            out_blocks.append({
                "type": block["type"],
                "verse_num_am": block["verse_num_am"],
                "spans": spans,
            })

    total = layout.get("word_count", 0)
    return {
        "page": page,
        "notes": (
            "DRAFT — Tesseract reading of the cleaned page layers, not a checked "
            f"transcription. {len(low_conf)} of {total} words scored below "
            f"{MIN_WORD_CONFIDENCE:.0f} confidence and are listed as uncertain. "
            "Expect glyph-level slips throughout; every word needs reading against "
            "the scan. Colour and verse numerals are derived from pixels and are "
            "more reliable than the text."
        ),
        "_source": "tesseract on cleaned layers (draft, needs human correction)",
        "corrections": [],
        # Cap the list: a page where most words are low-confidence needs re-reading
        # wholesale, and 90 blue underlines helps nobody.
        "uncertain": low_conf[:30],
        "clipped": _detect_clipped(layout),
        "blocks": out_blocks,
    }


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book", type=Path)
    parser.add_argument("--pages", required=True, help="e.g. 159 or 121-162")
    parser.add_argument("--preview", action="store_true",
                        help="print instead of writing files")
    parser.add_argument("--force", action="store_true",
                        help="overwrite existing transcripts")
    args = parser.parse_args(argv)

    bk = book_mod.load(args.book)
    pages: list[int] = []
    for part in args.pages.split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            pages.extend(range(int(a), int(b) + 1))
        elif part.strip():
            pages.append(int(part))

    out_dir = bk.dir("transcripts")
    out_dir.mkdir(parents=True, exist_ok=True)
    written = skipped = 0

    for page in sorted(set(pages)):
        try:
            result = build_page(bk, page)
        except FileNotFoundError as err:
            print(f"page {page}: {err}", file=sys.stderr)
            continue

        if args.preview:
            print(f"\n=== page {page} — {len(result['blocks'])} blocks, "
                  f"{len(result['uncertain'])} uncertain, "
                  f"{len(result['clipped'])} clipped ===")
            for block in result["blocks"]:
                tag = f"[{block['type']} {block['verse_num_am'] or '-'}]"
                text = "".join(
                    (f"«{s['t']}»" if s["c"] == "red" else s["t"]) + " "
                    for s in block["spans"]
                )
                print(f"{tag} {text[:400]}")
            continue

        target = out_dir / f"page_{page:03d}.json"
        if target.exists() and not args.force:
            skipped += 1
            continue
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                          encoding="utf-8")
        written += 1

    if not args.preview:
        print(f"wrote {written} draft(s); skipped {skipped} existing "
              f"(use --force to overwrite)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
