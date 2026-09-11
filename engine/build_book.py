"""Assemble per-page transcripts into the finished book JSON.

Section boundaries come from the book's own config, not from code, so a new PDF
only needs a new `book.json`. A book with no section table is emitted as a single
implicit section covering every page.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from . import book as book_mod

ETHIOPIC_RE = re.compile(r"[ሀ-፿]")


def normalize(text: str) -> str:
    """Reduce to bare Ethiopic letters, so matching ignores spacing and punctuation."""
    return "".join(ETHIOPIC_RE.findall(unicodedata.normalize("NFC", text)))


def block_text(block: dict) -> str:
    return "".join(span["t"] for span in block.get("spans", []))


def match_section(
    text: str, sections: list[dict], used: set[str] | None = None
) -> dict | None:
    """The section whose required keyword groups all appear in this title text.

    Sections already opened are skipped rather than re-matched. This matters
    because section keywords can nest: 'ማክሰኞ' (Tuesday) contains 'ሰኞ' (Monday) as
    a substring, so once Monday is open the Tuesday title must be allowed to fall
    through to Tuesday instead of matching Monday again and opening nothing.
    """
    haystack = normalize(text)
    if not haystack:
        return None
    used = used or set()
    for section in sections:
        if section["section_id"] in used:
            continue
        groups = section.get("required_any") or []
        if not groups:
            continue
        if all(
            any(normalize(alt) in haystack for alt in group if alt) for group in groups
        ):
            return section
    return None


def load_transcripts(bk: book_mod.Book, pages: list[int]) -> list[dict]:
    transcripts = []
    for page in pages:
        path = bk.dir("transcripts") / f"page_{page:03d}.json"
        if path.exists():
            transcripts.append(json.loads(path.read_text(encoding="utf-8")))
    return sorted(transcripts, key=lambda t: t["page"])


def load_flags(bk: book_mod.Book) -> dict[int, list[str]]:
    if not bk.verification_file.exists():
        return {}
    payload = json.loads(bk.verification_file.read_text(encoding="utf-8"))
    return {r["page"]: r["review_flags"] for r in payload.get("reports", [])}


def load_review(bk: book_mod.Book) -> dict[int, dict]:
    if not bk.review_file.exists():
        return {}
    entries = json.loads(bk.review_file.read_text(encoding="utf-8")).get("entries", {})
    return {int(k): v for k, v in entries.items()}


def _new_section(matched: dict, page: int) -> dict:
    return {
        "section_id": matched["section_id"],
        "order": matched.get("order", 0),
        "title_am": matched.get("title_am", ""),
        "title_en": matched.get("title_en", ""),
        "category": matched.get("category"),
        "weekday_am": matched.get("weekday_am"),
        "weekday_en": matched.get("weekday_en"),
        "start_page": page,
        "end_page": page,
        "blocks": [],
    }


def build(bk: book_mod.Book, pages: list[int], total_pages: int) -> dict[str, Any]:
    transcripts = load_transcripts(bk, pages)
    flags_by_page = load_flags(bk)
    review = load_review(bk)
    table = bk.sections

    out_sections: list[dict] = []
    current: dict | None = None
    front_matter: list[dict] = []

    for transcript in transcripts:
        page = transcript["page"]
        page_flags = flags_by_page.get(page, [])
        page_review = review.get(page, {})

        for block in transcript.get("blocks", []):
            text = block_text(block)
            # A running header repeats the section title on later pages, so a
            # section already opened is never reopened — match_section skips it.
            opened = {s["section_id"] for s in out_sections}
            matched = (
                match_section(text, table, opened)
                if block.get("type") == "title"
                else None
            )
            if matched:
                previous = current["blocks"] if current is not None else front_matter
                current = _new_section(matched, page)
                out_sections.append(current)
                # An ornament border printed directly above a section title belongs
                # to the section it introduces, not to the one that just ended.
                while previous and previous[-1]["type"] == "ornament" \
                        and previous[-1]["page"] == page:
                    current["blocks"].insert(0, previous.pop())

            target = current["blocks"] if current is not None else front_matter
            prefix = current["section_id"] if current is not None else "front_matter"
            target.append(
                {
                    "block_id": f"{prefix}_{len(target) + 1:03d}",
                    "type": block.get("type", "prayer"),
                    "verse_num_am": block.get("verse_num_am") or None,
                    "page": page,
                    "spans": block.get("spans", []),
                    "text": text,
                    "review_flags": page_flags,
                    "review_status": page_review.get("status"),
                }
            )
            if current is not None:
                current["end_page"] = page

    out_sections.sort(key=lambda s: s["order"])

    stats = json.loads(bk.stats_file.read_text(encoding="utf-8")) if bk.stats_file.exists() else {}
    content_pages = {
        int(page)
        for page, value in stats.items()
        if not value.get("is_blank") and not value.get("is_illustration")
    }
    transcript_pages = {int(t["page"]) for t in transcripts}
    missing_pages = sorted(content_pages - transcript_pages)
    reports_by_page: dict[int, dict] = {}
    if bk.verification_file.exists():
        verification = json.loads(bk.verification_file.read_text(encoding="utf-8"))
        reports_by_page = {
            int(report["page"]): report for report in verification.get("reports", [])
        }
    unverified_pages = sorted(transcript_pages - reports_by_page.keys())
    flagged_pages = sorted(
        page for page in transcript_pages
        if reports_by_page.get(page, {}).get("review_flags")
    )
    approved_pages = {
        page for page, entry in review.items() if entry.get("status") == "approved"
    }
    unapproved_pages = sorted(content_pages - approved_pages)
    expected_section_ids = {section["section_id"] for section in table}
    built_section_ids = {section["section_id"] for section in out_sections}
    missing_sections = sorted(expected_section_ids - built_section_ids)
    production_ready = bool(stats) and not (
        missing_pages
        or unverified_pages
        or flagged_pages
        or unapproved_pages
        or missing_sections
    )
    if not stats:
        quality_status = "not_analyzed"
    elif missing_pages:
        quality_status = "incomplete"
    elif unverified_pages or flagged_pages:
        quality_status = "needs_review"
    elif unapproved_pages:
        quality_status = "awaiting_approval"
    elif missing_sections:
        quality_status = "sectioning_incomplete"
    else:
        quality_status = "ready"

    return {
        "book_id": bk.book_id,
        "title_am": bk.title_am,
        "title_en": bk.title_en,
        "tradition": bk.config.get("tradition"),
        "language": bk.config.get("language", []),
        "script": bk.config.get("script"),
        "source": {
            "file": bk.source_pdf.name,
            "type": "scanned_pdf",
            "pages": total_pages,
            "dpi": bk.dpi,
        },
        "extraction": {
            "engine": "wudase extraction engine v2",
            "red_assignment": "deterministic, from HSV ink mask (not OCR)",
            "model": bk.model,
            "pages_transcribed": len(transcripts),
            "pages_expected": len(content_pages),
            "pages_blank": sum(1 for v in stats.values() if v.get("is_blank")),
            "pages_illustration": sum(1 for v in stats.values() if v.get("is_illustration")),
            "pages_approved": len(approved_pages & content_pages),
            "pages_flagged": len(flagged_pages),
        },
        "quality": {
            "status": quality_status,
            "production_ready": production_ready,
            "page_stats_available": bool(stats),
            "missing_transcript_pages": missing_pages,
            "unverified_transcript_pages": unverified_pages,
            "flagged_transcript_pages": flagged_pages,
            "unapproved_content_pages": unapproved_pages,
            "missing_sections": missing_sections,
        },
        "front_matter": front_matter,
        "sections_count": len(out_sections),
        "sections": out_sections,
    }


def write(bk: book_mod.Book, payload: dict) -> tuple[Path, Path]:
    bk.out.mkdir(parents=True, exist_ok=True)
    bk.book_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    bk.book_json_min.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    return bk.book_json, bk.book_json_min
