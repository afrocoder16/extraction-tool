"""A book: its config and the paths every pipeline stage reads and writes.

The engine itself knows nothing about Wudase Mariam. Everything book-specific —
titles, the section table, the source PDF, DPI, OCR settings — lives in a
`book.json` beside the book's data. Adding another PDF is a new folder and a new
config file, not a code change.

Layout of a book folder:

    books/<slug>/
      book.json          config (this module reads it)
      source/*.pdf       the scanned original
      work/              everything derived; safe to delete and regenerate
        page_images/     rendered pages          <- render.py
        red_mask/        red ink, binary         <- color_split.py
        black_layer/     text with red removed   <- color_split.py
        red_layer/       red rendered for OCR    <- color_split.py
        debug/           overlay for the review UI
        layout/          word geometry           <- layout.py
        transcripts/     per-page text           <- transcribe.py
        page_stats.json  blank/illustration/red stats
        verification.json
        review.json      human approve/flag decisions
      out/               the finished book JSON
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONFIG_NAME = "book.json"
TRANSCRIPT_NAME_RE = re.compile(r"^page_(\d{3})\.json$")

# Derived directories, created on demand. Named here so no stage has to guess.
WORK_DIRS = (
    "page_images",
    "red_mask",
    "black_layer",
    "red_layer",
    "debug",
    "layout",
    "transcripts",
)


@dataclass
class Book:
    """Config plus resolved paths for one book."""

    root: Path
    config: dict[str, Any]

    # --- identity ---

    @property
    def book_id(self) -> str:
        return self.config.get("book_id", self.root.name)

    @property
    def title_am(self) -> str:
        return self.config.get("title_am", "")

    @property
    def title_en(self) -> str:
        return self.config.get("title_en", self.book_id)

    @property
    def sections(self) -> list[dict[str, Any]]:
        """Section table. An empty list is valid — the book is then one section."""
        return self.config.get("sections", [])

    # --- settings ---

    @property
    def dpi(self) -> int:
        return int(self.config.get("render", {}).get("dpi", 300))

    @property
    def tesseract_lang(self) -> str:
        return self.config.get("ocr", {}).get("tesseract_lang", "amh")

    @property
    def model(self) -> str:
        return self.config.get("ocr", {}).get("model", "claude-opus-5")

    @property
    def effort(self) -> str:
        return self.config.get("ocr", {}).get("effort", "high")

    # --- paths ---

    @property
    def source_pdf(self) -> Path:
        rel = self.config.get("source_pdf")
        if rel:
            return self.root / rel
        # Fall back to the only PDF in source/, so a config need not name it.
        candidates = sorted((self.root / "source").glob("*.pdf"))
        if not candidates:
            raise FileNotFoundError(f"no PDF in {self.root / 'source'}")
        return candidates[0]

    @property
    def work(self) -> Path:
        return self.root / "work"

    @property
    def out(self) -> Path:
        return self.root / "out"

    def dir(self, name: str) -> Path:
        return self.work / name

    @property
    def page_images(self) -> Path:
        return self.dir("page_images")

    @property
    def stats_file(self) -> Path:
        return self.work / "page_stats.json"

    @property
    def verification_file(self) -> Path:
        return self.work / "verification.json"

    @property
    def review_file(self) -> Path:
        return self.work / "review.json"

    @property
    def book_json(self) -> Path:
        return self.out / f"{self.book_id}.json"

    @property
    def book_json_min(self) -> Path:
        return self.out / f"{self.book_id}.min.json"

    def ensure_dirs(self) -> None:
        for name in WORK_DIRS:
            self.dir(name).mkdir(parents=True, exist_ok=True)
        self.out.mkdir(parents=True, exist_ok=True)

    def transcript_files(self) -> list[Path]:
        """Canonical page transcripts, excluding edit/archive sidecars."""
        folder = self.dir("transcripts")
        if not folder.exists():
            return []
        return sorted(
            path for path in folder.iterdir()
            if path.is_file() and TRANSCRIPT_NAME_RE.fullmatch(path.name)
        )

    def transcript_pages(self) -> set[int]:
        """Page numbers that have a canonical transcript."""
        return {
            int(match.group(1))
            for path in self.transcript_files()
            if (match := TRANSCRIPT_NAME_RE.fullmatch(path.name)) is not None
        }


def load(book_root: Path) -> Book:
    """Load a book from its folder."""
    book_root = Path(book_root).resolve()
    config_path = book_root / CONFIG_NAME
    if not config_path.exists():
        raise FileNotFoundError(
            f"{config_path} not found — a book folder needs a {CONFIG_NAME}"
        )
    return Book(root=book_root, config=json.loads(config_path.read_text(encoding="utf-8")))


def discover(books_dir: Path) -> list[Path]:
    """Every book folder under books/, by presence of a config file."""
    return sorted(p.parent for p in Path(books_dir).glob(f"*/{CONFIG_NAME}"))
