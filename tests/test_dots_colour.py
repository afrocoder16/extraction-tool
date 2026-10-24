import json
import tempfile
import unittest
from pathlib import Path

from engine import book as book_mod
from engine.dots_colour import _ornament_reading, _spans, build_transcript, clean_text, locate
from engine.import_review import import_export


def candidate(cells):
    return {"layout": cells, "transcript": {"_provenance": {"model": "test"}}}


class CleanText(unittest.TestCase):
    def test_full_stop_and_line_wraps(self):
        self.assertEqual(clean_text("ለምኝልን ::\nቅድስት፡፡ **ሆይ**"), "ለምኝልን። ቅድስት። ሆይ")


class Locate(unittest.TestCase):
    def test_tolerates_misread_letters_at_word_start(self):
        text = "ለዚህም ምስክሬ ማርያም ናት"
        start, end = locate(text, "ማርኛ", 0.5, [])
        self.assertEqual(start, text.index("ማርያም"))  # _spans widens to the whole word

    def test_does_not_start_mid_word(self):
        self.assertIsNone(locate("አምላክን", "ላክን", 0.5, []))

    def test_prefers_occurrence_near_expected_position(self):
        text = "ክርስቶስ ሰው ሆነ ክርስቶስ"
        self.assertEqual(locate(text, "ክርስቶስ", 0.9, [])[0], text.rindex("ክርስቶስ"))


class Spans(unittest.TestCase):
    def test_partial_hit_widens_to_word_and_punctuation(self):
        text = "ወይም በሥላሴ እያመንኩ ለምኝልን።"
        spans = _spans(text, [(5, 8), (text.index("ለምኝ"), text.index("ለምኝ") + 3)])
        self.assertEqual([s["t"] for s in spans if s["c"] == "red"], ["በሥላሴ", "ለምኝልን።"])

    def test_joins_red_runs_across_spaces(self):
        text = "ቅድስት ሆይ ለምኝልን"
        spans = _spans(text, [(0, 5), (6, 8), (9, 15)])
        self.assertEqual(spans, [{"t": text, "c": "red"}])


class OrnamentReading(unittest.TestCase):
    def test_flourishes_are_not_reported_but_short_words_are(self):
        for junk in ("ዐወ ዐወ ዐወ", "፲, ፲, ፲", "0/ ፡ ፡ ፡ ፡"):
            self.assertTrue(_ornament_reading(junk), junk)
        for word in ("ቃል", "ልዕል", "ቅዱስ ቅዱስ ቅዱስ"):
            self.assertFalse(_ornament_reading(word), word)


class BuildTranscript(unittest.TestCase):
    def test_headers_watermarks_verses_and_unlocated_red(self):
        cells = [
            {"category": "Page-header", "bbox": [0, 0, 100, 10], "text": "፲ የሰኞ ውዳሴ ማርያም"},
            {"category": "Text", "bbox": [0, 20, 1000, 300], "text": "፭ ነቢዩ ኢሳያስ\nየዐማኑኤልን ምሥጢር አየ ::"},
            {"category": "Text", "bbox": [0, 400, 1000, 450], "text": "www.zenadebretsion.org"},
        ]
        red = {"key": {}, "regions": [
            {"bbox": [0, 120, 300, 220], "text": "የዐማኑኤልን"},
            {"bbox": [500, 120, 800, 220], "text": "ገብርኤል ሊቀ"},
        ]}
        t = build_transcript(21, candidate(cells), red, [r"zenadebretsion\.org"])
        self.assertEqual(t["page_header"], "፲ የሰኞ ውዳሴ ማርያም")
        self.assertEqual(len(t["blocks"]), 1)
        block = t["blocks"][0]
        self.assertEqual((block["type"], block["verse_num_am"]), ("verse", "፭"))
        self.assertEqual(block["spans"], [{"t": "ነቢዩ ኢሳያስ ", "c": "black"},
                                          {"t": "የዐማኑኤልን", "c": "red"},
                                          {"t": " ምሥጢር አየ።", "c": "black"}])
        self.assertEqual([u["text"] for u in t["uncertain"]], ["ገብርኤል ሊቀ"])


class ImportReview(unittest.TestCase):
    def test_hosted_edit_replaces_baseline_and_keeps_revert_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "book"
            (root / "work" / "transcripts").mkdir(parents=True)
            (root / "book.json").write_text(json.dumps({"book_id": "demo", "sections": []}), encoding="utf-8")
            base = {"page": 3, "blocks": [{"type": "prayer", "verse_num_am": "", "spans": [{"t": "ሀ", "c": "black"}]}],
                    "notes": "", "corrections": [], "uncertain": [], "clipped": []}
            (root / "work" / "transcripts" / "page_003.json").write_text(json.dumps(base), encoding="utf-8")
            edited = {**base, "blocks": [{"type": "prayer", "verse_num_am": "", "spans": [{"t": "ለ", "c": "red"}]}]}
            export = {"book_id": "demo", "exported_at": "2026-10-23T00:00:00Z",
                      "transcripts": {"page_003": edited, },
                      "edit_history": {"page_003": {"saved_by": "a@b.c"}},
                      "review": {"entries": {"3": {"status": "approved", "note": ""},
                                             "5": {"status": "approved", "note": "ok"}}}}
            summary = import_export(book_mod.load(root), export)
            work = root / "work"
            saved = json.loads((work / "transcripts" / "page_003.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["blocks"][0]["spans"][0]["t"], "ለ")
            self.assertTrue((work / "transcripts" / "page_003.original.json").exists())
            self.assertEqual(summary["approvals_kept"], [3])
            self.assertEqual(summary["approvals_withdrawn"], [5])
            review = json.loads((work / "review.json").read_text(encoding="utf-8"))
            self.assertIsNone(review["entries"]["5"]["status"])


if __name__ == "__main__":
    unittest.main()
