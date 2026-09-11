"""Assemble per-page transcripts into the app-ready book JSON.

Section boundaries are found by matching title blocks against the canonical
12-section table. With accurate text there is no need for the old pipeline's fuzzy
scoring - a title block whose text contains the section's required keywords is the
section start.

The section table itself is reused from the v1 script rather than duplicated, so
the two pipelines can never drift apart on section IDs or English titles.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import unicodedata
from pathlib import Path

ETHIOPIC_RE = re.compile(r"[ሀ-፿]")


def load_section_table() -> list[dict]:
    """Import SECTIONS from the v1 script without copying it."""
    v1_path = Path(__file__).resolve().parent.parent / "extract_wudase_mariam.py"
    spec = importlib.util.spec_from_file_location("extract_wudase_mariam_v1", v1_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load section table from {v1_path}")
    module = importlib.util.module_from_spec(spec)
    # The v1 script defines dataclasses, and dataclasses resolves type hints via
    # sys.modules — register before executing or the import raises.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.SECTIONS


def normalize(text: str) -> str:
    return "".join(ETHIOPIC_RE.findall(unicodedata.normalize("NFC", text)))


def block_text(block: dict) -> str:
    return "".join(span["t"] for span in block["spans"])


def match_section(text: str, sections: list[dict]) -> dict | None:
    """Return the section whose required keyword groups all appear in the text."""
    haystack = normalize(text)
    if not haystack:
        return None
    for section in sections:
        groups = section.get("required_any") or []
        if not groups:
            continue
        if all(
            any(normalize(alt) in haystack for alt in group if alt) for group in groups
        ):
            return section
    return None


def load_transcripts(pages: list[int], v2_root: Path) -> list[dict]:
    transcripts = []
    for page in pages:
        path = v2_root / "transcripts" / f"page_{page:03d}.json"
        if path.exists():
            transcripts.append(json.loads(path.read_text(encoding="utf-8")))
    return sorted(transcripts, key=lambda t: t["page"])


def load_flags(v2_root: Path) -> dict[int, list[str]]:
    path = v2_root / "verification.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {r["page"]: r["review_flags"] for r in payload["reports"]}


def build_book(transcripts: list[dict], v2_root: Path, total_pages: int) -> dict:
    sections_table = load_section_table()
    flags_by_page = load_flags(v2_root)

    # Walk pages in order; a title block that matches the table opens a section,
    # and every block after it belongs to that section until the next match.
    out_sections: list[dict] = []
    current: dict | None = None
    front_matter: list[dict] = []

    for transcript in transcripts:
        page = transcript["page"]
        page_flags = flags_by_page.get(page, [])

        for block in transcript["blocks"]:
            text = block_text(block)
            matched = match_section(text, sections_table) if block["type"] == "title" else None

            if matched is not None and not any(
                s["section_id"] == matched["section_id"] for s in out_sections
            ):
                current = {
                    "section_id": matched["section_id"],
                    "order": matched["order"],
                    "title_am": matched["title_am"],
                    "title_en": matched["title_en"],
                    "category": matched["category"],
                    "weekday_am": matched["weekday_am"],
                    "weekday_en": matched["weekday_en"],
                    "start_page": page,
                    "end_page": page,
                    "blocks": [],
                }
                out_sections.append(current)

            target = current["blocks"] if current is not None else front_matter
            prefix = current["section_id"] if current is not None else "front_matter"
            entry = {
                "block_id": f"{prefix}_{len(target) + 1:03d}",
                "type": block["type"],
                "verse_num_am": block["verse_num_am"] or None,
                "page": page,
                "spans": block["spans"],
                "text": text,
                "review_flags": page_flags,
            }
            target.append(entry)
            if current is not None:
                current["end_page"] = page

    out_sections.sort(key=lambda s: s["order"])

    return {
        "book_id": "wudase_mariam",
        "title_am": "ውዳሴ ማርያም",
        "title_en": "Praise of Mary",
        "tradition": "Ethiopian Orthodox Tewahedo Church",
        "language": ["am", "gez"],
        "script": "Ethiopic",
        "source": {
            "file": "Wudase Mariam.pdf",
            "type": "scanned_pdf",
            "pages": total_pages,
            "dpi": 300,
        },
        "extraction": {
            "method": "colour-separated mask + Claude vision transcription",
            "red_assignment": "deterministic, from HSV ink mask (not OCR)",
            "pages_transcribed": len(transcripts),
        },
        "front_matter": front_matter,
        "sections_count": len(out_sections),
        "sections": out_sections,
    }


def write_outputs(book: dict, v2_root: Path) -> tuple[Path, Path]:
    full = v2_root / "wudase_mariam.json"
    minified = v2_root / "wudase_mariam_minified.json"
    full.write_text(json.dumps(book, ensure_ascii=False, indent=2), encoding="utf-8")
    minified.write_text(
        json.dumps(book, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    return full, minified


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v2-root", type=Path, required=True)
    parser.add_argument("--pages", required=True)
    parser.add_argument("--total-pages", type=int, default=218)
    args = parser.parse_args()

    pages = [int(p) for p in args.pages.split(",") if p.strip()]
    transcripts = load_transcripts(pages, args.v2_root)
    book = build_book(transcripts, args.v2_root, args.total_pages)
    full, minified = write_outputs(book, args.v2_root)

    print(f"{len(transcripts)} pages -> {book['sections_count']} sections")
    print(f"wrote {full}")
    print(f"wrote {minified}")


if __name__ == "__main__":
    main()
