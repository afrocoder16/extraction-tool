"""Read-only environment and book diagnostics; does not import OCR packages."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
from pathlib import Path

from .provenance import read_json


def inspect(bk) -> dict:
    stats = read_json(bk.stats_file, {})
    content = [int(p) for p, s in stats.items() if not s.get("is_blank") and not s.get("is_illustration")]
    transcripts = [read_json(p) for p in bk.transcript_files()]
    dependencies = {name: importlib.util.find_spec(name) is not None
                    for name in ("fitz", "cv2", "numpy", "anthropic", "PIL")}
    renderer_available = dependencies["fitz"] or bool(shutil.which("pdfinfo") and shutil.which("pdftoppm"))
    try:
        source = bk.source_pdf
    except FileNotFoundError:
        source = bk.root / "source"
    return {"book_id": bk.book_id, "source_pdf": str(source), "source_available": source.is_file(),
            "source_manifest_available": (bk.work / "source_manifest.json").is_file(),
            "pdf_renderer_available": renderer_available,
            "analysed_pages": len(stats), "content_pages": len(content), "transcripts": len(transcripts),
            "draft_pages": [t["page"] for t in transcripts if "draft" in str(t.get("_source", "")).lower()],
            "original_page_images": len(list(bk.page_images.glob("page_*.png"))),
            "dependencies": dependencies, "tesseract_executable": shutil.which("tesseract"),
            "missing_tesseract_data": [lang for lang in bk.tesseract_lang.split("+")
              if not (Path(__file__).parent / "tessdata" / f"{lang}.traineddata").is_file()],
            "anthropic_api_key_configured": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "dots_endpoint_configured": bool(os.environ.get("DOTS_BASE_URL") or bk.config.get("ocr", {}).get("dots", {}).get("base_url")),
            "note": "Configured endpoints and keys are not proof of connectivity or inference accuracy."}


def show(bk) -> int:
    result = inspect(bk)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["source_available"] and result["pdf_renderer_available"] and all(result["dependencies"][n] for n in ("cv2", "numpy")) else 2
