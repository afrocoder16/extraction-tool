import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest

from engine.provenance import file_hash, read_json
from engine.render import page_count, render_pdf


@unittest.skipUnless(importlib.util.find_spec("PIL") and (
    importlib.util.find_spec("fitz") or (shutil.which("pdfinfo") and shutil.which("pdftoppm"))),
    "Pillow and a PDF renderer are required")
class RenderTests(unittest.TestCase):
    def test_pdf_renders_colour_pages_and_resumes_with_matching_hashes(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf, output = root / "book.pdf", root / "work/page_images"
            Image.new("RGB", (32, 32), (255, 0, 0)).save(
                pdf, save_all=True, append_images=[Image.new("RGB", (32, 32), (0, 0, 0))])
            self.assertEqual(page_count(pdf), 2)
            self.assertEqual(render_pdf(pdf, output, dpi=72), [1, 2])
            manifest = read_json(root / "work/source_manifest.json")
            for page in (1, 2):
                self.assertEqual(manifest["rendered"][str(page)], file_hash(output / f"page_{page:03d}.png"))
            with Image.open(output / "page_001.png") as pixels:
                red, green, blue = pixels.convert("RGB").getpixel((16, 16))
                self.assertGreater(red, 200)
                self.assertLess(green, 30)
                self.assertLess(blue, 30)
            self.assertEqual(render_pdf(pdf, output, dpi=72), [])
            pdf.write_bytes(pdf.read_bytes() + b"\n")
            with self.assertRaisesRegex(ValueError, "source/render settings changed"):
                render_pdf(pdf, output, dpi=72)
