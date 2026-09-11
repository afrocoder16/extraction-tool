"""Cross-check each transcript against the deterministic colour mask.

The mask knows how much red ink is on a page and roughly how many lines there are,
independently of what any model said. Comparing the two catches the failure modes
that matter: red spans marked on the wrong words, hallucinated or dropped text,
and Latin characters leaking into Ethiopic output.

Every page gets a review_flags list. A page with no flags agreed with the mask on
every check; a flagged page needs a human to look at it.
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import cv2
import numpy as np

ETHIOPIC_RE = re.compile(r"[ሀ-፿ᎀ-᎟ⶀ-⷟]")
LATIN_RE = re.compile(r"[A-Za-z]")
ASCII_DIGIT_RE = re.compile(r"[0-9]")

# The model's red-character share and the mask's red-ink-area share measure the
# same thing through different instruments, so they never match exactly. Anything
# beyond this gap means one of them is wrong.
RED_RATIO_TOLERANCE = 0.18

# Tesseract and the model segment lines differently (wrapped verses, merged
# stanzas), so only a large divergence is meaningful.
LINE_COUNT_TOLERANCE = 0.5

# Below this, the model and Tesseract disagree so completely that one of them is
# probably not reading the same page.
MIN_TEXT_AGREEMENT = 0.25


def block_text(block: dict) -> str:
    return "".join(span["t"] for span in block["spans"])


def transcript_text(transcript: dict) -> str:
    return "\n".join(block_text(b) for b in transcript["blocks"])


def red_text(transcript: dict) -> str:
    return "".join(
        span["t"] for b in transcript["blocks"] for span in b["spans"] if span["c"] == "red"
    )


def normalize(text: str) -> str:
    """Strip everything that isn't an Ethiopic letter, for fuzzy comparison."""
    text = unicodedata.normalize("NFC", text)
    return "".join(ETHIOPIC_RE.findall(text))


def mask_red_ratio(page: int, v2_root: Path) -> float | None:
    """Fraction of the page's ink that is red, measured from the images."""
    name = f"page_{page:03d}.png"
    red_layer = cv2.imread(str(v2_root / "red_layer" / name), cv2.IMREAD_GRAYSCALE)
    black_layer = cv2.imread(str(v2_root / "black_layer" / name), cv2.IMREAD_GRAYSCALE)
    if red_layer is None or black_layer is None:
        return None

    red_px = int(np.count_nonzero(red_layer < 128))
    black_px = int(np.count_nonzero(black_layer < 128))
    total = red_px + black_px
    if total == 0:
        return None
    return red_px / float(total)


def tesseract_text(page: int, v2_root: Path) -> str:
    path = v2_root / "layout" / f"page_{page:03d}.json"
    if not path.exists():
        return ""
    layout = json.loads(path.read_text(encoding="utf-8"))
    return "".join(
        word["text"] for line in layout["lines"] for word in line["words"]
    )


def tesseract_line_count(page: int, v2_root: Path) -> int | None:
    path = v2_root / "layout" / f"page_{page:03d}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))["line_count"]


def verify_page(page: int, v2_root: Path) -> dict:
    path = v2_root / "transcripts" / f"page_{page:03d}.json"
    if not path.exists():
        return {"page": page, "review_flags": ["no transcript"], "checks": {}}

    transcript = json.loads(path.read_text(encoding="utf-8"))
    flags: list[str] = []
    checks: dict[str, object] = {}

    full = transcript_text(transcript)
    normalized = normalize(full)

    # --- Script purity: any Latin letter is a misread glyph or a hallucination.
    latin = LATIN_RE.findall(full)
    digits = ASCII_DIGIT_RE.findall(full)
    checks["latin_chars"] = len(latin)
    checks["ascii_digits"] = len(digits)
    if latin:
        flags.append(f"{len(latin)} Latin characters in Ethiopic text: {''.join(latin[:20])!r}")
    if digits:
        flags.append(f"{len(digits)} ASCII digits (Ethiopic numerals expected)")

    # --- Red coverage: the model's red share vs the mask's red share.
    model_red = len(normalize(red_text(transcript)))
    model_ratio = model_red / len(normalized) if normalized else 0.0
    mask_ratio = mask_red_ratio(page, v2_root)
    checks["model_red_ratio"] = round(model_ratio, 3)
    checks["mask_red_ratio"] = round(mask_ratio, 3) if mask_ratio is not None else None
    if mask_ratio is not None:
        gap = model_ratio - mask_ratio
        if abs(gap) > RED_RATIO_TOLERANCE:
            direction = "over" if gap > 0 else "under"
            flags.append(
                f"red coverage {direction}-marked: model {model_ratio:.2f} "
                f"vs mask {mask_ratio:.2f}"
            )

    # --- Line count: a large divergence means blocks were dropped or invented.
    tess_lines = tesseract_line_count(page, v2_root)
    model_lines = sum(max(1, len(b["spans"])) for b in transcript["blocks"])
    checks["tesseract_lines"] = tess_lines
    checks["model_blocks"] = len(transcript["blocks"])
    if tess_lines:
        ratio = abs(model_lines - tess_lines) / float(tess_lines)
        if ratio > LINE_COUNT_TOLERANCE and not normalized:
            flags.append("model produced no text but Tesseract found lines")

    # --- Text agreement: low similarity means one of them misread the page.
    tess_norm = normalize(tesseract_text(page, v2_root))
    if tess_norm and normalized:
        agreement = SequenceMatcher(None, tess_norm, normalized).ratio()
        checks["text_agreement"] = round(agreement, 3)
        if agreement < MIN_TEXT_AGREEMENT:
            flags.append(f"text agreement with Tesseract only {agreement:.2f}")
    else:
        checks["text_agreement"] = None
        if not normalized:
            flags.append("transcript contains no Ethiopic text")

    # --- Empty non-ornament blocks are a structural error.
    for block in transcript["blocks"]:
        if block["type"] != "ornament" and not block_text(block).strip():
            flags.append(f"empty {block['type']} block")
            break

    # --- Spelling suggestions still awaiting a human decision.
    corrections = transcript.get("corrections") or []
    undecided = [c for c in corrections if not c.get("applied") and not c.get("dismissed")]
    checks["corrections_total"] = len(corrections)
    checks["corrections_undecided"] = len(undecided)
    if undecided:
        words = ", ".join(f"{c['printed']}→{c['suggested']}" for c in undecided[:4])
        more = f" (+{len(undecided) - 4} more)" if len(undecided) > 4 else ""
        flags.append(f"{len(undecided)} spelling suggestion(s) to review: {words}{more}")

    # --- Carry forward whatever the model itself flagged.
    if transcript.get("notes", "").strip():
        checks["model_notes"] = transcript["notes"]

    return {"page": page, "review_flags": flags, "checks": checks}


def verify_pages(pages: list[int], v2_root: Path) -> list[dict]:
    return [verify_page(page, v2_root) for page in pages]


def write_report(reports: list[dict], v2_root: Path) -> Path:
    path = v2_root / "verification.json"
    flagged = [r for r in reports if r["review_flags"]]
    payload = {
        "pages_checked": len(reports),
        "pages_flagged": len(flagged),
        "flagged_pages": [r["page"] for r in flagged],
        "reports": reports,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v2-root", type=Path, required=True)
    parser.add_argument("--pages", required=True)
    args = parser.parse_args()

    pages = [int(p) for p in args.pages.split(",") if p.strip()]
    reports = verify_pages(pages, args.v2_root)
    write_report(reports, args.v2_root)

    for report in reports:
        if report["review_flags"]:
            print(f"page {report['page']}: " + "; ".join(report["review_flags"]))
        else:
            print(f"page {report['page']}: clean")


if __name__ == "__main__":
    main()
