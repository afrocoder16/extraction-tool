from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from engine import book as book_mod
from engine import build_book
from engine.cli import parse_pages
from engine.dots_colour import clean_text
from engine.transcribe import PAGE_SCHEMA
from engine.provenance import file_hash, page_revision


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

    def add_source_evidence(self, bk: book_mod.Book, pages: int) -> None:
        bk.source_pdf.write_bytes(b"fixture source identity")
        rendered = {}
        for page in range(1, pages + 1):
            image = bk.page_images / f"page_{page:03d}.png"
            image.write_bytes(f"fixture image {page}".encode())
            rendered[str(page)] = file_hash(image)
        (bk.work / "source_manifest.json").write_text(json.dumps({
            "source_sha256": file_hash(bk.source_pdf), "pages": pages,
            "dpi": bk.dpi, "rendered": rendered,
        }))

    def approve(self, bk: book_mod.Book, page: int) -> None:
        transcript = json.loads((bk.dir("transcripts") / f"page_{page:03d}.json").read_text())
        revision = page_revision(bk.work, page, transcript)
        bk.verification_file.write_text(json.dumps({"reports": [{"page": page, "review_flags": [], **revision}]}))
        bk.review_file.write_text(json.dumps({"entries": {str(page): {"status": "approved", **revision}}}))

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
            self.add_source_evidence(bk, 1)
            self.approve(bk, 1)

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

    def test_changed_text_invalidates_review_and_verification(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            bk = self.make_book(Path(folder))
            self.write_transcript(bk, 1)
            self.add_source_evidence(bk, 1)
            self.approve(bk, 1)
            path = bk.dir("transcripts") / "page_001.json"
            value = json.loads(path.read_text())
            value["blocks"][0]["spans"][0]["t"] = "changed text"
            path.write_text(json.dumps(value))
            result = build_book.build(bk, [1], 1)
            self.assertFalse(result["quality"]["production_ready"])
            self.assertEqual(result["quality"]["unverified_transcript_pages"], [1])
            self.assertEqual(result["quality"]["unapproved_content_pages"], [1])

    def test_unanalysed_source_page_prevents_release(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            bk = self.make_book(Path(folder))
            self.write_transcript(bk, 1)
            self.add_source_evidence(bk, 2)
            self.approve(bk, 1)
            result = build_book.build(bk, [1], 2)
            self.assertFalse(result["quality"]["production_ready"])
            self.assertEqual(result["quality"]["missing_analysis_pages"], [2])

    def test_empty_section_config_has_readable_default_section(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            bk = self.make_book(Path(folder))
            self.write_transcript(bk, 1)
            result = build_book.build(bk, [1], 1)
            self.assertEqual(result["sections_count"], 1)
            self.assertEqual(result["sections"][0]["blocks"][0]["text"], "ሙከራ።")

    def test_page_range_sections_open_on_their_first_page(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            bk = self.make_book(Path(folder), (1, 2, 3))
            bk.config["sections"] = [
                {"section_id": "a", "order": 1, "pages": [1, 2]},
                {"section_id": "b", "order": 2, "pages": [3, 3]},
            ]
            for page in (1, 2, 3):
                self.write_transcript(bk, page)
            result = build_book.build(bk, [1, 2, 3], 3)
            spans = [(s["section_id"], s["start_page"], s["end_page"]) for s in result["sections"]]
            self.assertEqual(spans, [("a", 1, 2), ("b", 3, 3)])

    def test_clean_text_drops_markdown_and_typed_guillemets(self) -> None:
        self.assertEqual(clean_text("### ጥያቄ፦ ምንድን ነው?"), "ጥያቄ፦ ምንድን ነው?")
        self.assertEqual(clean_text("አንደኛው ትእዛዝ\n### «አትግደል»"), "አንደኛው ትእዛዝ «አትግደል»")
        self.assertEqual(clean_text("<<ቃል>> ::"), "«ቃል»።")

    def test_page_parser_rejects_invalid_and_out_of_range_selection(self) -> None:
        for spec in ("0", "4", "2-1", "1,", "-1"):
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                parse_pages(spec, 3)


if __name__ == "__main__":
    unittest.main()
