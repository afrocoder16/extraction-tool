from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from engine import book as book_mod
from engine import build_book
from engine.cli import parse_pages
from engine.transcribe import PAGE_SCHEMA


class EngineTests(unittest.TestCase):
    def make_book(self, root: Path, content_pages: tuple[int, ...] = (1,)) -> book_mod.Book:
        (root / "source").mkdir(parents=True)
        config = {
            "book_id": "test_book",
            "title_am": "ሙከራ",
            "title_en": "Test",
            "source_pdf": "source/test.pdf",
            "sections": [],
        }
        (root / "book.json").write_text(json.dumps(config), encoding="utf-8")
        bk = book_mod.load(root)
        bk.ensure_dirs()
        stats = {
            str(page): {"is_blank": False, "is_illustration": False}
            for page in content_pages
        }
        bk.stats_file.write_text(json.dumps(stats), encoding="utf-8")
        return bk

    def write_transcript(self, bk: book_mod.Book, page: int) -> None:
        payload = {
            "page": page,
            "notes": "",
            "corrections": [],
            "uncertain": [],
            "clipped": [],
            "blocks": [
                {
                    "type": "prayer",
                    "verse_num_am": "",
                    "spans": [{"t": "ሙከራ።", "c": "black"}],
                }
            ],
        }
        (bk.dir("transcripts") / f"page_{page:03d}.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

    def test_page_parser(self) -> None:
        self.assertEqual(parse_pages("1,3-5,3", 6), [1, 3, 4, 5])
        self.assertEqual(parse_pages("all", 3), [1, 2, 3])

    def test_canonical_transcript_count_excludes_sidecars(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            bk = self.make_book(Path(folder))
            transcripts = bk.dir("transcripts")
            for name in (
                "page_001.json",
                "page_001.original.json",
                "page_001.youredit.json",
                "page_02.json",
            ):
                (transcripts / name).write_text("{}", encoding="utf-8")
            self.assertEqual([p.name for p in bk.transcript_files()], ["page_001.json"])
            self.assertEqual(bk.transcript_pages(), {1})

    def test_ocr_schema_requires_review_fields(self) -> None:
        required = set(PAGE_SCHEMA["required"])
        self.assertIn("uncertain", required)
        self.assertIn("clipped", required)
        self.assertFalse(PAGE_SCHEMA["properties"]["uncertain"]["items"]["additionalProperties"])
        self.assertFalse(PAGE_SCHEMA["properties"]["clipped"]["items"]["additionalProperties"])

    def test_build_readiness_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            bk = self.make_book(Path(folder), content_pages=(1,))
            self.write_transcript(bk, 1)
            bk.verification_file.write_text(
                json.dumps({"reports": [{"page": 1, "review_flags": []}]}),
                encoding="utf-8",
            )
            bk.review_file.write_text(
                json.dumps({"entries": {"1": {"status": "approved", "note": ""}}}),
                encoding="utf-8",
            )

            ready = build_book.build(bk, [1], 1)
            self.assertTrue(ready["quality"]["production_ready"])
            self.assertEqual(ready["quality"]["status"], "ready")
            self.assertEqual(ready["quality"]["missing_sections"], [])

            # A content page without a transcript must make the next build a draft.
            stats = json.loads(bk.stats_file.read_text(encoding="utf-8"))
            stats["2"] = {"is_blank": False, "is_illustration": False}
            bk.stats_file.write_text(json.dumps(stats), encoding="utf-8")
            incomplete = build_book.build(bk, [1, 2], 2)
            self.assertFalse(incomplete["quality"]["production_ready"])
            self.assertEqual(incomplete["quality"]["missing_transcript_pages"], [2])


if __name__ == "__main__":
    unittest.main()
