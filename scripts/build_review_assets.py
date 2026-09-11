"""Create compact WebP copies of the page images used by the hosted reviewer.

The 300-DPI PNG files remain local extraction artifacts.  Netlify receives a
smaller, still-readable set under ``netlify/review-assets``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2


ROOT = Path(__file__).resolve().parents[1]
BOOK_WORK = ROOT / "books" / "wudase-mariam" / "work"
ASSET_ROOT = ROOT / "netlify" / "review-assets" / "work"


def convert_folder(name: str, max_edge: int, quality: int, force: bool) -> tuple[int, int]:
    source_dir = BOOK_WORK / name
    target_dir = ASSET_ROOT / name
    target_dir.mkdir(parents=True, exist_ok=True)

    converted = skipped = 0
    for source in sorted(source_dir.glob("page_*.png")):
        target = target_dir / f"{source.stem}.webp"
        if not force and target.exists() and target.stat().st_mtime_ns >= source.stat().st_mtime_ns:
            skipped += 1
            continue

        image = cv2.imread(str(source), cv2.IMREAD_COLOR)
        if image is None:
            raise RuntimeError(f"Could not read {source}")

        height, width = image.shape[:2]
        scale = min(1.0, max_edge / max(width, height))
        if scale < 1.0:
            image = cv2.resize(
                image,
                (round(width * scale), round(height * scale)),
                interpolation=cv2.INTER_AREA,
            )

        ok, encoded = cv2.imencode(".webp", image, [cv2.IMWRITE_WEBP_QUALITY, quality])
        if not ok:
            raise RuntimeError(f"Could not encode {source}")
        target.write_bytes(encoded.tobytes())
        converted += 1
        if converted % 25 == 0:
            print(f"{name}: converted {converted}", flush=True)

    return converted, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-edge", type=int, default=1800)
    parser.add_argument("--quality", type=int, default=86)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if args.max_edge < 800:
        parser.error("--max-edge must be at least 800 pixels")
    if not 1 <= args.quality <= 100:
        parser.error("--quality must be between 1 and 100")

    total_converted = total_skipped = 0
    for folder in ("page_images", "debug"):
        converted, skipped = convert_folder(folder, args.max_edge, args.quality, args.force)
        total_converted += converted
        total_skipped += skipped
        print(f"{folder}: {converted} converted, {skipped} current", flush=True)

    size_mb = sum(p.stat().st_size for p in ASSET_ROOT.rglob("*.webp")) / (1024 * 1024)
    print(f"Review assets: {size_mb:.1f} MiB ({total_converted} converted, {total_skipped} current)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
