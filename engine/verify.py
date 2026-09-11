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

from .console import configure_utf8_stdio

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

# --- Unusable-page gate ---------------------------------------------------
# Two batches of transcription came back looking well-formed - correct schema,
# plausible red spans, honest-looking flags - but the Amharic itself was noise
# shaped like prose ("ለተጠማ ያጠጣ ከመዓር" transcribed as "ስተጠም የጠጣ ከመጠጥ"). Every
# individual check passed; only reading a page against its scan caught it.
#
# Two signals separate that failure from ordinary difficulty, and they agree:
# agreement with Tesseract collapses (0.04-0.24, against 0.49-0.92 on sound
# pages), and the transcriber floods `uncertain` because it knows it is guessing
# (4 entries a page, against well under 1). Either alone is noisy; together they
# are decisive. A page that trips this is not "needs review" - it is not a
# transcription, and sending it to a human wastes the one scarce resource here.
REJECT_TEXT_AGREEMENT = 0.35
REJECT_UNCERTAIN_PER_PAGE = 4


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


def mask_red_ratio(page: int, work: Path) -> float | None:
    """Fraction of the page's ink that is red, measured from the images."""
    name = f"page_{page:03d}.png"
    red_layer = cv2.imread(str(work / "red_layer" / name), cv2.IMREAD_GRAYSCALE)
    black_layer = cv2.imread(str(work / "black_layer" / name), cv2.IMREAD_GRAYSCALE)
    if red_layer is None or black_layer is None:
        return None

    red_px = int(np.count_nonzero(red_layer < 128))
    black_px = int(np.count_nonzero(black_layer < 128))
    total = red_px + black_px
    if total == 0:
        return None
    return red_px / float(total)


def tesseract_text(page: int, work: Path) -> str:
    path = work / "layout" / f"page_{page:03d}.json"
    if not path.exists():
        return ""
    layout = json.loads(path.read_text(encoding="utf-8"))
    return "".join(
        word["text"] for line in layout["lines"] for word in line["words"]
    )


def tesseract_line_count(page: int, work: Path) -> int | None:
    path = work / "layout" / f"page_{page:03d}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))["line_count"]


def verify_page(page: int, work: Path) -> dict:
    path = work / "transcripts" / f"page_{page:03d}.json"
    if not path.exists():
        return {"page": page, "review_flags": ["no transcript"], "checks": {}}

    transcript = json.loads(path.read_text(encoding="utf-8"))
    flags: list[str] = []
    checks: dict[str, object] = {}

    # Older/manual batches predate these required review fields. Treating a
    # missing field as an empty list would falsely claim the page was checked and
    # found certain/unclipped, so make the gap visible to the reviewer.
    missing_review_fields = [
        field for field in ("uncertain", "clipped") if field not in transcript
    ]
    checks["missing_review_fields"] = missing_review_fields
    if missing_review_fields:
        flags.append(
            "legacy transcript missing review field(s): "
            + ", ".join(missing_review_fields)
        )

    source = str(transcript.get("_source", ""))
    is_draft = "draft" in source.lower() or "tesseract on cleaned layers" in source.lower()
    checks["draft_transcript"] = is_draft
    if is_draft:
        flags.append("DRAFT transcript — every word still needs checking against the scan")

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
    mask_ratio = mask_red_ratio(page, work)
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
    tess_lines = tesseract_line_count(page, work)
    model_lines = sum(max(1, len(b["spans"])) for b in transcript["blocks"])
    checks["tesseract_lines"] = tess_lines
    checks["model_blocks"] = len(transcript["blocks"])
    if tess_lines:
        ratio = abs(model_lines - tess_lines) / float(tess_lines)
        if ratio > LINE_COUNT_TOLERANCE and not normalized:
            flags.append("model produced no text but Tesseract found lines")

    # --- Text agreement: low similarity means one of them misread the page.
    tess_norm = normalize(tesseract_text(page, work))
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

    # --- Text the transcriber could not read confidently.
    uncertain = transcript.get("uncertain") or []
    checks["uncertain_words"] = len(uncertain)
    if uncertain:
        words = ", ".join(u.get("text", "?") for u in uncertain[:4])
        more = f" (+{len(uncertain) - 4} more)" if len(uncertain) > 4 else ""
        flags.append(f"{len(uncertain)} uncertain word(s) to check: {words}{more}")

    # --- Lines the scan cut off. These are missing text, not just a warning:
    # the words exist in the physical book and are absent from this transcript.
    clipped = transcript.get("clipped") or []
    checks["clipped_lines"] = len(clipped)
    if clipped:
        edges = ", ".join(c.get("edge", "?") for c in clipped)
        flags.append(f"TEXT CUT OFF at {edges} of page — {len(clipped)} line(s) missing")

    # --- Spelling suggestions still awaiting a human decision.
    corrections = transcript.get("corrections") or []
    undecided = [c for c in corrections if not c.get("applied") and not c.get("dismissed")]
    checks["corrections_total"] = len(corrections)
    checks["corrections_undecided"] = len(undecided)
    if undecided:
        words = ", ".join(f"{c['printed']}→{c['suggested']}" for c in undecided[:4])
        more = f" (+{len(undecided) - 4} more)" if len(undecided) > 4 else ""
        flags.append(f"{len(undecided)} spelling suggestion(s) to review: {words}{more}")

    # --- Unusable-page gate: low agreement AND heavy self-doubt together.
    agreement = checks.get("text_agreement")
    unusable = (
        agreement is not None
        and agreement < REJECT_TEXT_AGREEMENT
        and len(uncertain) >= REJECT_UNCERTAIN_PER_PAGE
    )
    checks["unusable"] = unusable
    if unusable:
        flags.insert(
            0,
            f"REJECT — likely not a real transcription "
            f"(agreement {agreement:.2f}, {len(uncertain)} uncertain). "
            f"Check this page against its scan before reviewing it.",
        )

    # --- Carry forward whatever the model itself flagged.
    if transcript.get("notes", "").strip():
        checks["model_notes"] = transcript["notes"]

    return {"page": page, "review_flags": flags, "checks": checks}


def verify_pages(pages: list[int], work: Path) -> list[dict]:
    return [verify_page(page, work) for page in pages]


def write_report(reports: list[dict], work: Path) -> Path:
    """Merge this run's results into the report, keeping pages not checked now.

    Verifying a subset must not erase the rest. The review UI reads this file to
    decide which pages carry a flag, so replacing it wholesale after a one-page
    run would silently blank every other page's flags in the sidebar.
    """
    path = work / "verification.json"

    merged: dict[int, dict] = {}
    if path.exists():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
            merged = {r["page"]: r for r in previous.get("reports", [])}
        except (json.JSONDecodeError, KeyError):
            merged = {}

    for report in reports:
        merged[report["page"]] = report

    ordered = [merged[p] for p in sorted(merged)]
    flagged = [r for r in ordered if r["review_flags"]]
    payload = {
        "pages_checked": len(ordered),
        "pages_flagged": len(flagged),
        "flagged_pages": [r["page"] for r in flagged],
        "reports": ordered,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> None:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--work", "--v2-root", dest="work", type=Path, required=True,
        help="book work directory (legacy name: --v2-root)",
    )
    parser.add_argument("--pages", required=True)
    args = parser.parse_args()

    pages = [int(p) for p in args.pages.split(",") if p.strip()]
    reports = verify_pages(pages, args.work)
    write_report(reports, args.work)

    for report in reports:
        if report["review_flags"]:
            print(f"page {report['page']}: " + "; ".join(report["review_flags"]))
        else:
            print(f"page {report['page']}: clean")


if __name__ == "__main__":
    main()
