"""Extract page geometry with Tesseract, and flag red words from the colour mask.

Tesseract is used here for *geometry, not text*: its Amharic recognition is not
reliable enough for this scan (verified: it renders ለምኝልን as ሰምኘነልን), but its word
and line segmentation is solid. The actual transcription comes from Claude in
transcribe.py.

The critical design point: a word's `is_red` flag is decided by measuring how much
of the word's ink is red in the colour mask produced by color_split.py - never by
what Tesseract recognised. Red assignment is therefore deterministic and cannot be
corrupted by OCR errors.

Output per page: layout/page_NNN.json
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import subprocess
from pathlib import Path

import cv2
import numpy as np

# Fraction of a word box's ink that must be red for the word to count as red.
# Well separated in practice: true red words score >0.85, black words <0.05.
RED_WORD_THRESHOLD = 0.5

# Tesseract drops boxes it is not confident are text at all.
MIN_WORD_CONFIDENCE = 20.0

# Lines whose centre sits within this fraction of the page centre, and which are
# short, are probably titles rather than body text.
TITLE_CENTRE_TOLERANCE = 0.06
TITLE_MAX_WIDTH_RATIO = 0.75


def run_tesseract_tsv(
    image_path: Path, tesseract: Path, tessdata: Path, lang: str, psm: int
) -> list[dict]:
    """Run Tesseract in TSV mode and return its word-level rows."""
    cmd = [
        str(tesseract),
        str(image_path),
        "stdout",
        "--tessdata-dir", str(tessdata),
        "-l", lang,
        "--psm", str(psm),
        "tsv",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(f"tesseract failed on {image_path}: {proc.stderr[:400]}")

    rows = []
    reader = csv.DictReader(io.StringIO(proc.stdout), delimiter="\t", quoting=csv.QUOTE_NONE)
    for row in reader:
        if row.get("level") != "5":  # level 5 == word
            continue
        text = (row.get("text") or "").strip()
        if not text:
            continue
        try:
            conf = float(row.get("conf", -1))
        except ValueError:
            continue
        if conf < MIN_WORD_CONFIDENCE:
            continue
        rows.append(
            {
                "text": text,
                "conf": round(conf, 2),
                "left": int(row["left"]),
                "top": int(row["top"]),
                "width": int(row["width"]),
                "height": int(row["height"]),
                "block": int(row["block_num"]),
                "par": int(row["par_num"]),
                "line": int(row["line_num"]),
                "word": int(row["word_num"]),
            }
        )
    return rows


def measure_redness(word: dict, red_mask: np.ndarray, ink_mask: np.ndarray) -> float:
    """Fraction of the word box's ink pixels that are red ink."""
    x, y = word["left"], word["top"]
    x2, y2 = x + word["width"], y + word["height"]
    h, w = red_mask.shape[:2]
    x, y = max(0, x), max(0, y)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 <= x or y2 <= y:
        return 0.0

    red_crop = red_mask[y:y2, x:x2]
    ink_crop = ink_mask[y:y2, x:x2]
    ink_px = int(np.count_nonzero(ink_crop))
    if ink_px == 0:
        return 0.0
    return float(np.count_nonzero(red_crop)) / ink_px


def group_lines(words: list[dict], page_w: int) -> list[dict]:
    """Group words into reading-order lines and derive layout hints."""
    buckets: dict[tuple[int, int, int], list[dict]] = {}
    for word in words:
        buckets.setdefault((word["block"], word["par"], word["line"]), []).append(word)

    lines = []
    for key, group in buckets.items():
        group.sort(key=lambda item: item["left"])
        left = min(item["left"] for item in group)
        top = min(item["top"] for item in group)
        right = max(item["left"] + item["width"] for item in group)
        bottom = max(item["top"] + item["height"] for item in group)

        red_words = sum(1 for item in group if item["is_red"])
        centre = (left + right) / 2.0
        width_ratio = (right - left) / float(page_w)
        centred = abs(centre - page_w / 2.0) / page_w <= TITLE_CENTRE_TOLERANCE

        lines.append(
            {
                "bbox": [left, top, right, bottom],
                "words": [
                    {
                        "text": item["text"],
                        "bbox": [
                            item["left"],
                            item["top"],
                            item["left"] + item["width"],
                            item["top"] + item["height"],
                        ],
                        "is_red": item["is_red"],
                        "redness": item["redness"],
                        "conf": item["conf"],
                    }
                    for item in group
                ],
                "red_word_count": red_words,
                "all_red": red_words == len(group) and bool(group),
                # A short, centred, fully-red line is almost certainly a section title.
                "looks_like_title": (
                    centred
                    and width_ratio <= TITLE_MAX_WIDTH_RATIO
                    and red_words == len(group)
                    and len(group) <= 6
                ),
                "_sort": (top, left),
            }
        )

    lines.sort(key=lambda item: item["_sort"])
    for order, line in enumerate(lines, start=1):
        line["order"] = order
        del line["_sort"]
    return lines


def analyse_page(
    page: int,
    v2_root: Path,
    tesseract: Path,
    tessdata: Path,
    lang: str,
) -> dict:
    name = f"page_{page:03d}.png"
    red_mask = cv2.imread(str(v2_root / "red_mask" / name), cv2.IMREAD_GRAYSCALE)
    black_layer = cv2.imread(str(v2_root / "black_layer" / name), cv2.IMREAD_GRAYSCALE)
    red_layer = cv2.imread(str(v2_root / "red_layer" / name), cv2.IMREAD_GRAYSCALE)
    if red_mask is None or black_layer is None or red_layer is None:
        raise FileNotFoundError(f"run color_split first; missing layers for page {page}")

    page_h, page_w = red_mask.shape[:2]

    # Ink across both layers, used as the denominator when measuring redness.
    black_ink = (black_layer < 128).astype(np.uint8) * 255
    red_ink = (red_layer < 128).astype(np.uint8) * 255
    all_ink = cv2.bitwise_or(black_ink, red_ink)

    # OCR each layer separately. Words found on the red layer are red by
    # construction; words on the black layer are measured against the mask so that
    # any red glyph the erase step missed is still caught.
    words: list[dict] = []
    for layer_path, forced_red in (
        (v2_root / "black_layer" / name, False),
        (v2_root / "red_layer" / name, True),
    ):
        for word in run_tesseract_tsv(layer_path, tesseract, tessdata, lang, psm=6):
            redness = measure_redness(word, red_mask, all_ink)
            word["redness"] = round(redness, 3)
            word["is_red"] = True if forced_red else redness >= RED_WORD_THRESHOLD
            words.append(word)

    # The two layers are OCR'd independently, so their block/par/line numbering
    # collides. Offset the red layer's blocks to keep line grouping separate.
    black_blocks = [w["block"] for w in words if not w["is_red"]]
    offset = (max(black_blocks) + 100) if black_blocks else 100
    for word in words:
        if word["is_red"]:
            word["block"] += offset

    lines = group_lines(words, page_w)

    return {
        "page": page,
        "width": page_w,
        "height": page_h,
        "line_count": len(lines),
        "word_count": len(words),
        "red_word_count": sum(1 for w in words if w["is_red"]),
        "lines": lines,
    }


def analyse_pages(
    pages: list[int], v2_root: Path, tesseract: Path, tessdata: Path, lang: str
) -> list[dict]:
    out_dir = v2_root / "layout"
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for page in pages:
        result = analyse_page(page, v2_root, tesseract, tessdata, lang)
        (out_dir / f"page_{page:03d}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        results.append(result)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v2-root", type=Path, required=True)
    parser.add_argument("--tesseract", type=Path, default=Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"))
    parser.add_argument("--tessdata", type=Path, required=True)
    parser.add_argument("--lang", default="amh")
    parser.add_argument("--pages", required=True)
    args = parser.parse_args()

    pages = [int(p) for p in args.pages.split(",") if p.strip()]
    for result in analyse_pages(pages, args.v2_root, args.tesseract, args.tessdata, args.lang):
        print(
            f"page {result['page']}: {result['line_count']} lines, "
            f"{result['word_count']} words, {result['red_word_count']} red"
        )


if __name__ == "__main__":
    main()
