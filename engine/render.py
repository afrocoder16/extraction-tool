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
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .provenance import atomic_json, file_hash, read_json


def page_count(pdf_path: Path) -> int:
    if importlib.util.find_spec("fitz"):
        import fitz
        with fitz.open(pdf_path) as doc:
            return doc.page_count
    if not shutil.which("pdfinfo"):
        raise RuntimeError("Install PyMuPDF or Poppler (pdfinfo and pdftoppm) to read PDFs")
    info = subprocess.run(["pdfinfo", str(pdf_path)], capture_output=True, text=True,
                          check=True, env={**os.environ, "LC_ALL": "C"})
    match = re.search(r"^Pages:\s+(\d+)\s*$", info.stdout, re.MULTILINE)
    if not match:
        raise ValueError("pdfinfo did not return a valid page count")
    return int(match.group(1))


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
    if dpi < 72 or dpi > 1200:
        raise ValueError("DPI must be between 72 and 1200")
    backend = "pymupdf" if importlib.util.find_spec("fitz") else "poppler"
    if backend == "poppler" and not shutil.which("pdftoppm"):
        raise RuntimeError("Install PyMuPDF or Poppler (pdftoppm) to render PDFs")
    total = page_count(pdf_path)

    written: list[int] = []
    manifest_path = out_dir.parent / "source_manifest.json"
    source_hash = file_hash(pdf_path)
    manifest = read_json(manifest_path, {})
    if (manifest.get("source_sha256") != source_hash or manifest.get("dpi") != dpi
            or manifest.get("renderer", backend) != backend):
        if any(out_dir.glob("page_*.png")) and not force:
            raise ValueError("source/render settings changed or lack provenance; render with --force")
        manifest = {"source_sha256": source_hash, "dpi": dpi, "rendered": {}}
    manifest.update(pages=total, renderer=backend)
    wanted = list(range(1, total + 1) if pages is None else pages)
    pending = []
    for page_no in wanted:
        if not 1 <= page_no <= total:
            raise ValueError(f"page {page_no} is outside the PDF")
        target = out_dir / f"page_{page_no:03d}.png"
        if target.exists() and not force:
            if manifest["rendered"].get(str(page_no)) != file_hash(target):
                raise ValueError(f"page {page_no}: cached render changed; render with --force")
        else:
            pending.append(page_no)

    def render_one(page_no):
        target = out_dir / f"page_{page_no:03d}.png"
        with tempfile.TemporaryDirectory(dir=out_dir) as temporary:
            draft = Path(temporary) / "page.png"
            if backend == "pymupdf":
                import fitz
                with fitz.open(pdf_path) as doc:
                    pixmap = doc.load_page(page_no - 1).get_pixmap(
                        matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0), alpha=False)
                    pixmap.save(draft)
            elif importlib.util.find_spec("PIL"):
                # Render uncompressed pixels and use fast lossless PNG compression.
                # Compression level affects bytes/CPU, not colour or image quality.
                from PIL import Image
                subprocess.run(["pdftoppm", "-f", str(page_no), "-l", str(page_no),
                                "-r", str(dpi), "-singlefile", str(pdf_path),
                                str(draft.with_suffix(""))], check=True, capture_output=True)
                with Image.open(draft.with_suffix(".ppm")) as pixels:
                    pixels.save(draft, compress_level=1)
            else:
                subprocess.run(["pdftoppm", "-f", str(page_no), "-l", str(page_no),
                                "-r", str(dpi), "-singlefile", "-png", str(pdf_path),
                                str(draft.with_suffix(""))], check=True, capture_output=True)
            os.replace(draft, target)
        return page_no, file_hash(target)

    # PyMuPDF is kept serial; independent Poppler processes can run concurrently.
    with ThreadPoolExecutor(max_workers=4 if backend == "poppler" else 1) as pool:
        for page_no, digest in pool.map(render_one, pending):
            manifest["rendered"][str(page_no)] = digest
            atomic_json(manifest_path, manifest)
            written.append(page_no)
            if len(written) % 10 == 0 or page_no == total:
                print(f"[render] page {page_no}/{total} saved at {dpi} DPI ({backend})", flush=True)

    atomic_json(manifest_path, manifest)
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
