import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine.book import Book
from engine.dots_ocr import align_colour, model_dimensions, parse_layout, run_pages
from engine.provenance import atomic_json, json_hash, page_revision
from engine.review_store import RevisionConflict, load_transcript, save_review, save_transcript


class DotsTests(unittest.TestCase):
    def test_coordinate_conversion_preserves_original_page_reference(self):
        raw = json.dumps([{"bbox": [28, 56, 280, 560], "category": "Text", "text": "ሰላም።"}])
        cells = parse_layout(raw, (560, 1120), (280, 560))
        self.assertEqual(cells[0]["bbox"], [56, 112, 560, 1120])
        self.assertEqual(cells[0]["text"], "ሰላም።")

    def test_bad_output_is_not_silently_repaired(self):
        cell = {"bbox": [0, 0, 28, 28], "category": "Text", "text": "ሰላም"}
        invalid = ["```json\n[]\n```", "[]", json.dumps([cell, cell]),
                   json.dumps([{**cell, "bbox": [-1, 0, 28, 28]}]),
                   json.dumps([{**cell, "bbox": [0, 0, float('nan'), 28]}]),
                   json.dumps([{**cell, "category": "made-up"}])]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_layout(value, (28, 28), (28, 28))

    def test_colour_requires_matching_text_geometry(self):
        layout = {"lines": [{"words": [
            {"text": "ሰላም", "bbox": [0, 0, 20, 20], "is_red": True},
            {"text": "፩", "bbox": [20, 0, 30, 20], "is_red": False},
        ]}]}
        spans, known = align_colour("ሰላም ፩", [0, 0, 30, 20], layout)
        self.assertTrue(known)
        self.assertEqual("".join(s["t"] for s in spans), "ሰላም ፩")
        self.assertEqual([s["c"] for s in spans], ["red", "black"])
        _, known = align_colour("different wording", [0, 0, 30, 20], layout)
        self.assertFalse(known)

    def test_model_dimensions_are_recordable_and_bounded(self):
        w, h = model_dimensions(2550, 3300, 2000000)
        self.assertEqual(w % 28, 0)
        self.assertEqual(h % 28, 0)
        self.assertLessEqual(w * h, 2000000)

    def test_candidates_resume_and_preserve_existing_transcript(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as folder:
            bk = Book(Path(folder), {"source_pdf": "source/book.pdf", "ocr": {"dots": {}}})
            bk.ensure_dirs()
            Image.new("RGB", (56, 56), "white").save(bk.page_images / "page_001.png")
            canonical = bk.dir("transcripts") / "page_001.json"
            canonical.write_text('{"human_edit": true}')
            response = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps([
                {"bbox": [0, 0, 56, 56], "category": "Text", "text": "ሰላም"}
            ])}}]}
            with patch("engine.dots_ocr.request_completion", return_value=response) as request:
                first = run_pages(bk, [1], base_url="http://localhost:8001/v1")
                again = run_pages(bk, [1], base_url="http://localhost:8001/v1")
                self.assertEqual(first["completed"], [1])
                self.assertEqual(again["cached"], [1])
                self.assertEqual(request.call_count, 1)
            self.assertEqual(canonical.read_text(), '{"human_edit": true}')
            candidate = json.loads(next((bk.work / "candidates/dots").glob("page_001.*.json")).read_text())
            self.assertTrue(candidate["transcript"]["_colour_unresolved"])
            response["choices"][0]["finish_reason"] = "length"
            with patch("engine.dots_ocr.request_completion", return_value=response):
                failed = run_pages(bk, [1], base_url="http://localhost:8001/v1", force=True)
            self.assertIn("1", failed["failed"])
            self.assertEqual(canonical.read_text(), '{"human_edit": true}')


class ReviewTests(unittest.TestCase):
    def test_edit_invalidates_approval_and_preserves_history(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            original = {"page": 1, "blocks": [{"type": "prayer", "verse_num_am": "",
                         "spans": [{"t": "ሰላም", "c": "red"}]}],
                        "notes": "", "corrections": [], "clipped": [], "uncertain": []}
            atomic_json(work / "transcripts/page_001.json", original)
            shown = load_transcript(work, 1)
            decision = {"status": "approved", **page_revision(work, 1, original)}
            save_review(work, {"1": decision})
            edited = copy.deepcopy(shown)
            edited["blocks"][0]["spans"][0]["t"] = "changed"
            saved = save_transcript(work, 1, edited)
            self.assertNotEqual(saved["_revision"], shown["_revision"])
            self.assertEqual(json.loads((work / "transcripts/page_001.original.json").read_text()), original)
            self.assertTrue((work / "history/1" / f"{json_hash(original)}.json").exists())
            self.assertIsNone(json.loads((work / "review.json").read_text())["entries"]["1"]["status"])
            with self.assertRaises(RevisionConflict):
                save_review(work, {"1": decision})
            with self.assertRaises(RevisionConflict):
                save_transcript(work, 1, edited)

    def test_approval_requires_the_displayed_revision(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            atomic_json(work / "transcripts/page_001.json", {"page": 1, "blocks": []})
            with self.assertRaises(RevisionConflict):
                save_review(work, {"1": {"status": "approved"}})


if __name__ == "__main__":
    unittest.main()
