"""Render a source PDF to per-page images.

This is the entry point of the pipeline: every later stage reads
`work/page_images/page_NNN.png`. Pages are rendered in colour and never
downsampled, because the red-ink separation in `color_split` depends on the
original hue and saturation — a greyscale or heavily compressed render destroys
exactly the signal the engine exists to preserve.

Rendering is skipped for pages that already exist unless `--force` is passed, so
re-running is cheap.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import fitz  # PyMuPDF


def page_count(pdf_path: Path) -> int:
    with fitz.open(pdf_path) as doc:
        return doc.page_count


def render_pdf(
    pdf_path: Path,
    out_dir: Path,
    dpi: int = 300,
    force: bool = False,
    pages: list[int] | None = None,
) -> list[int]:
    """Render pages to PNG. Returns the page numbers actually written."""
    if not pdf_path.exists():
        raise FileNotFoundError(f"source PDF not found: {pdf_path}")

    out_dir.mkdir(parents=True, exist_ok=True)
    # PDF user space is 72 dpi; this matrix scales to the requested resolution.
    matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)

    written: list[int] = []
    with fitz.open(pdf_path) as doc:
        wanted = pages or range(1, doc.page_count + 1)
        for page_no in wanted:
            if not 1 <= page_no <= doc.page_count:
                continue
            target = out_dir / f"page_{page_no:03d}.png"
            if target.exists() and not force:
                continue
            pixmap = doc.load_page(page_no - 1).get_pixmap(matrix=matrix, alpha=False)
            pixmap.save(target)
            written.append(page_no)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    written = render_pdf(args.pdf, args.out, args.dpi, args.force)
    print(f"{page_count(args.pdf)} pages in PDF; rendered {len(written)}")


if __name__ == "__main__":
    main()
