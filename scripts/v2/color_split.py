"""Separate red ink from black ink on scanned Wudase Mariam pages.

Red in this book is semantic: it marks divine names and congregational refrains.
The scans carry two kinds of contamination that must not be mistaken for red ink:

1. Bleed-through from the reverse page - pale pink mirrored text and grey ghosting.
   Rejected by requiring high saturation together with a bounded value range.
2. Decorative red ornament borders (e.g. page 147 header).
   Rejected by connected-component shape analysis: ornaments are far wider than any
   glyph and have a much higher fill ratio.

Outputs per page:
    red_mask/page_NNN.png     binary mask of true red ink (white = red ink)
    black_layer/page_NNN.png  greyscale, red glyphs erased to paper white
    red_layer/page_NNN.png    red glyphs rendered black-on-white for OCR
    debug/page_NNN.png        original with the accepted mask tinted, for eyeballing
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, asdict
from pathlib import Path

import cv2
import numpy as np


# --- Tuning ---------------------------------------------------------------
# Hue is OpenCV's 0-179 range. Red straddles the wrap point, so two windows.
HUE_LOW_MAX = 12
HUE_HIGH_MIN = 168

# True red ink on this paper sits well above this saturation; bleed-through and
# the pink watermark sit below it.
MIN_SATURATION = 90

# Reject near-black pixels that merely lean warm (dark ghosting, ink shadows).
MIN_VALUE = 70

# Red must actually dominate the other channels, not just tie them.
MIN_CHANNEL_MARGIN = 28

# Components smaller than this are scanner speckle / JPEG ringing.
MIN_COMPONENT_AREA = 28

# Ornament rejection. A glyph at 300 DPI in this book is roughly 40-90px tall and
# never spans a large fraction of the text column.
MAX_GLYPH_WIDTH_RATIO = 0.55      # of page width
MAX_GLYPH_HEIGHT_RATIO = 0.10     # of page height
ORNAMENT_FILL_RATIO = 0.55        # solid rules and borders fill their bbox densely
ORNAMENT_MIN_ASPECT = 12.0        # very wide and short => a rule or border

# 87 of the book's 218 scans are blank verso sheets carrying nothing but a faint
# website watermark. A real text page covers well over 1% of its area in ink;
# a watermark-only page covers a small fraction of that.
BLANK_INK_RATIO = 0.01

# Two pages are full-colour devotional icons (the cover, and a plate mid-book).
# The red-ink mask is meaningless on them - skin tones and warm robes read as red -
# and there is no prayer text to transcribe. Ink is dark and paper is unsaturated,
# so only a picture covers a large area in colour that is both saturated and bright.
ILLUSTRATION_COLOUR_RATIO = 0.15
ILLUSTRATION_MIN_SAT = 0.35
ILLUSTRATION_MIN_VAL = 0.35


@dataclass
class PageColorStats:
    """Per-page numbers used later by verify.py to sanity-check the transcription."""

    page: int
    width: int
    height: int
    red_ink_pixels: int
    black_ink_pixels: int
    red_components_kept: int
    red_components_dropped_small: int
    red_components_dropped_ornament: int
    red_ink_ratio: float
    ink_coverage: float
    colour_area: float
    is_blank: bool
    is_illustration: bool


def _red_candidate_mask(bgr: np.ndarray) -> np.ndarray:
    """Pixels that are plausibly red ink, before any shape filtering."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

    hue_ok = (hue <= HUE_LOW_MAX) | (hue >= HUE_HIGH_MIN)
    sat_ok = sat >= MIN_SATURATION
    val_ok = val >= MIN_VALUE

    # Red channel must lead green and blue by a real margin. Using int16 to avoid
    # the uint8 wraparound that would make dark pixels look falsely red.
    blue = bgr[:, :, 0].astype(np.int16)
    green = bgr[:, :, 1].astype(np.int16)
    red = bgr[:, :, 2].astype(np.int16)
    margin_ok = (red - np.maximum(green, blue)) >= MIN_CHANNEL_MARGIN

    mask = (hue_ok & sat_ok & val_ok & margin_ok).astype(np.uint8) * 255

    # Close single-pixel gaps inside strokes, then drop isolated noise.
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    return mask


def _is_ornament(w: int, h: int, area: int, page_w: int, page_h: int) -> bool:
    """True if a component looks like a decorative rule or border, not a glyph."""
    if w > page_w * MAX_GLYPH_WIDTH_RATIO:
        return True
    if h > page_h * MAX_GLYPH_HEIGHT_RATIO:
        return True

    fill = area / float(max(1, w * h))
    aspect = w / float(max(1, h))
    if aspect >= ORNAMENT_MIN_ASPECT and fill >= ORNAMENT_FILL_RATIO:
        return True
    return False


def _filter_components(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray, int, int, int]:
    """Split the candidate mask into accepted glyph ink and rejected ornament ink."""
    page_h, page_w = mask.shape[:2]
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)

    kept = np.zeros_like(mask)
    ornaments = np.zeros_like(mask)
    n_small = n_ornament = n_kept = 0

    for label in range(1, count):  # 0 is background
        x, y, w, h, area = stats[label]
        if area < MIN_COMPONENT_AREA:
            n_small += 1
            continue
        component = labels == label
        if _is_ornament(w, h, int(area), page_w, page_h):
            ornaments[component] = 255
            n_ornament += 1
            continue
        kept[component] = 255
        n_kept += 1

    return kept, ornaments, n_kept, n_small, n_ornament


def _colour_area_ratio(bgr: np.ndarray) -> float:
    """Fraction of the page that is both saturated and bright — i.e. a picture."""
    small = cv2.resize(bgr, (bgr.shape[1] // 6, bgr.shape[0] // 6))
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1].astype(np.float32) / 255.0
    val = hsv[:, :, 2].astype(np.float32) / 255.0
    coloured = (sat > ILLUSTRATION_MIN_SAT) & (val > ILLUSTRATION_MIN_VAL)
    return float(np.mean(coloured))


def _ink_mask(bgr: np.ndarray) -> np.ndarray:
    """All ink (any colour) versus paper, with bleed-through suppressed.

    Bleed-through is markedly lighter than true ink, so a global Otsu threshold on
    an illumination-normalised image separates them better than adaptive
    thresholding, which happily promotes faint ghost text to solid black.
    """
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    # Estimate the paper background with a large median blur and divide it out, so
    # uneven scan lighting does not shift the threshold across the page.
    background = cv2.medianBlur(gray, 51)
    normalised = cv2.divide(gray, background, scale=255)

    _, binary = cv2.threshold(normalised, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return binary


def split_page(src_path: Path, dirs: dict[str, Path], page: int) -> PageColorStats:
    bgr = cv2.imread(str(src_path), cv2.IMREAD_COLOR)
    if bgr is None:
        raise FileNotFoundError(f"could not read page image: {src_path}")

    page_h, page_w = bgr.shape[:2]

    candidate = _red_candidate_mask(bgr)
    red_mask, ornament_mask, n_kept, n_small, n_ornament = _filter_components(candidate)

    ink = _ink_mask(bgr)

    # Black ink is all ink minus anything the colour pass claimed (glyphs or
    # ornaments) - ornaments must not leak into the black layer either.
    claimed_red = cv2.bitwise_or(red_mask, ornament_mask)
    # Dilate slightly so anti-aliased red stroke edges are removed too, otherwise
    # they survive as thin outlines and confuse the black-layer OCR.
    edge_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    claimed_red_dilated = cv2.dilate(claimed_red, edge_kernel)
    black_mask = cv2.bitwise_and(ink, cv2.bitwise_not(claimed_red_dilated))

    # Render both layers as black-on-white, which is what Tesseract expects.
    black_layer = np.full((page_h, page_w), 255, dtype=np.uint8)
    black_layer[black_mask > 0] = 0

    red_layer = np.full((page_h, page_w), 255, dtype=np.uint8)
    red_layer[red_mask > 0] = 0

    debug = bgr.copy()
    debug[red_mask > 0] = (0, 255, 0)        # accepted red ink -> green
    debug[ornament_mask > 0] = (255, 0, 255)  # rejected ornaments -> magenta

    name = f"page_{page:03d}.png"
    cv2.imwrite(str(dirs["red_mask"] / name), red_mask)
    cv2.imwrite(str(dirs["black_layer"] / name), black_layer)
    cv2.imwrite(str(dirs["red_layer"] / name), red_layer)
    cv2.imwrite(str(dirs["debug"] / name), debug)

    red_px = int(np.count_nonzero(red_mask))
    black_px = int(np.count_nonzero(black_mask))
    coverage = int(np.count_nonzero(ink)) / float(page_w * page_h)
    colour_area = _colour_area_ratio(bgr)
    return PageColorStats(
        page=page,
        width=page_w,
        height=page_h,
        red_ink_pixels=red_px,
        black_ink_pixels=black_px,
        red_components_kept=n_kept,
        red_components_dropped_small=n_small,
        red_components_dropped_ornament=n_ornament,
        red_ink_ratio=round(red_px / float(max(1, red_px + black_px)), 4),
        ink_coverage=round(coverage, 5),
        colour_area=round(colour_area, 4),
        is_blank=coverage < BLANK_INK_RATIO,
        is_illustration=colour_area > ILLUSTRATION_COLOUR_RATIO,
    )


def ensure_dirs(out_root: Path) -> dict[str, Path]:
    dirs = {
        name: out_root / name
        for name in ("red_mask", "black_layer", "red_layer", "debug")
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def stats_path(out_root: Path) -> Path:
    return out_root / "page_stats.json"


def load_stats(out_root: Path) -> dict[int, dict]:
    path = stats_path(out_root)
    if not path.exists():
        return {}
    return {int(k): v for k, v in json.loads(path.read_text(encoding="utf-8")).items()}


def blank_pages(out_root: Path, pages: list[int]) -> set[int]:
    """Pages carrying no transcribable prayer text: blank scans and colour plates.

    Both are excluded from OCR and from the final book for the same reason — there
    is no text on them — even though they look nothing alike. Pages not yet split
    are treated as ordinary text pages.
    """
    stats = load_stats(out_root)
    return {
        p for p in pages
        if stats.get(p, {}).get("is_blank") or stats.get(p, {}).get("is_illustration")
    }


def split_pages(
    page_image_dir: Path, out_root: Path, pages: list[int]
) -> list[PageColorStats]:
    dirs = ensure_dirs(out_root)
    stats = []
    for page in pages:
        src = page_image_dir / f"page_{page:03d}.png"
        stats.append(split_page(src, dirs, page))

    # Merge into the persisted table so later stages can skip blank scans without
    # re-analysing, and so a partial run doesn't discard earlier pages.
    merged = load_stats(out_root)
    for stat in stats:
        merged[stat.page] = asdict(stat)
    stats_path(out_root).write_text(
        json.dumps({str(k): merged[k] for k in sorted(merged)}, indent=2),
        encoding="utf-8",
    )
    return stats


# A prayer page in this book always carries some red. Zero is far more likely to
# mean the mask failed than that the page is genuinely all black.
SUSPICIOUS_NO_RED = 0.005
# Above this, the "red" is probably bleed-through or a page-wide colour cast
# being admitted as ink.
SUSPICIOUS_TOO_MUCH_RED = 0.70


def write_audit(out_root: Path) -> Path:
    """Report red-ink coverage per page and list the pages worth eyeballing.

    Reads the stats split_pages already recorded, so it costs nothing to re-run
    and never disagrees with what was actually written to the mask files.
    """
    stats = load_stats(out_root)
    pages = sorted(stats)
    content = [p for p in pages
               if not stats[p]["is_blank"] and not stats[p].get("is_illustration")]

    no_red, too_much, ornaments = [], [], []
    for page in content:
        s = stats[page]
        if s["red_ink_ratio"] < SUSPICIOUS_NO_RED:
            no_red.append(page)
        elif s["red_ink_ratio"] > SUSPICIOUS_TOO_MUCH_RED:
            too_much.append(page)
        if s["red_components_dropped_ornament"]:
            ornaments.append(page)

    lines = [
        "# Red-ink coverage audit",
        "",
        f"- Pages analysed: {len(pages)}",
        f"- Content pages: {len(content)}",
        f"- Blank scans excluded: {sum(1 for p in pages if stats[p]['is_blank'])}",
        f"- Colour illustrations excluded: {sum(1 for p in pages if stats[p].get('is_illustration'))}",
        "",
        "## Pages to check by eye",
        "",
        "Open each in the review UI and hold `O`, or use the Split view. Green must sit",
        "on the red glyphs and nowhere else.",
        "",
    ]

    def section(title: str, items: list[int], why: str) -> None:
        lines.append(f"### {title} — {len(items)} page(s)")
        lines.append("")
        lines.append(why)
        lines.append("")
        lines.append(", ".join(f"p.{p}" for p in items) if items else "_none_")
        lines.append("")

    section("No red detected", no_red,
            "A content page with no red at all usually means the mask missed it.")
    section("Red over 70% of ink", too_much,
            "Possible bleed-through or a colour cast being counted as ink.")
    section("Ornament dropped", ornaments,
            "Confirm the dropped component was decoration, not text. Magenta in Split view.")

    lines += ["## Every content page", "",
              "| Page | Red % of ink | Glyph groups | Ornaments dropped | Speckle dropped |",
              "| ---: | ---: | ---: | ---: | ---: |"]
    for page in content:
        s = stats[page]
        lines.append(
            f"| {page} | {s['red_ink_ratio'] * 100:.1f}% | {s['red_components_kept']} "
            f"| {s['red_components_dropped_ornament']} | {s['red_components_dropped_small']} |"
        )

    path = out_root / "red_audit.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--page-images", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--pages", required=True, help="comma-separated page numbers")
    args = parser.parse_args()

    pages = [int(p) for p in args.pages.split(",") if p.strip()]
    for stat in split_pages(args.page_images, args.out, pages):
        print(asdict(stat))


if __name__ == "__main__":
    main()
