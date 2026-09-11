from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz
from PIL import Image, ImageFilter, ImageOps


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PDF = ROOT / "Wudase Mariam.pdf"
DEFAULT_OUTPUT = ROOT / "output"
DEFAULT_TESSERACT = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
AMH_TRAINEDDATA_URL = (
    "https://github.com/tesseract-ocr/tessdata_best/raw/main/amh.traineddata"
)

ETHIOPIC_RE = re.compile(r"[\u1200-\u137f\u1380-\u139f\u2d80-\u2ddf\uab00-\uab2f]")
LATIN_RE = re.compile(r"[A-Za-z]")
WEBSITE_RE = re.compile(
    r"(www\.|https?://|zenadebretsion|debretsion|\.org|\.com|gmail|facebook)",
    re.IGNORECASE,
)
ETHIOPIC_NUMERALS = set("፩፪፫፬፭፮፯፰፱፲፳፴፵፶፷፸፹፺፻፼")
PAGE_NUMBER_CHARS = set("0123456789 .,:;:-_()[]{}|/\\፡።፣፤፥፦፧፨") | ETHIOPIC_NUMERALS


SECTIONS: list[dict[str, Any]] = [
    {
        "section_id": "daily_prayer",
        "order": 1,
        "title_am": "የዘወትር ጸሎት",
        "title_en": "Daily Prayer",
        "category": "daily_prayer",
        "weekday_am": None,
        "weekday_en": None,
        "keywords": ["ዘወትር", "ጸሎት"],
        "required_any": [["ዘወትር"]],
    },
    {
        "section_id": "monday_wudase_mariam",
        "order": 2,
        "title_am": "የሰኞ ውዳሴ ማርያም",
        "title_en": "Monday Praise of Mary",
        "category": "wudase_mariam_weekday",
        "weekday_am": "ሰኞ",
        "weekday_en": "Monday",
        "keywords": ["ሰኞ", "ውዳሴ", "ማርያም"],
        "required_any": [["የሰኞ"]],
    },
    {
        "section_id": "tuesday_wudase_mariam",
        "order": 3,
        "title_am": "የማክሰኞ ውዳሴ ማርያም",
        "title_en": "Tuesday Praise of Mary",
        "category": "wudase_mariam_weekday",
        "weekday_am": "ማክሰኞ",
        "weekday_en": "Tuesday",
        "keywords": ["ማክሰኞ", "ማክሰኛ", "ውዳሴ", "ማርያም"],
        "required_any": [["ማክሰኞ", "ማክሰኛ"]],
    },
    {
        "section_id": "wednesday_wudase_mariam",
        "order": 4,
        "title_am": "የረቡዕ ውዳሴ ማርያም",
        "title_en": "Wednesday Praise of Mary",
        "category": "wudase_mariam_weekday",
        "weekday_am": "ረቡዕ",
        "weekday_en": "Wednesday",
        "keywords": ["ረቡዕ", "ውዳሴ", "ማርያም"],
        "required_any": [["ረቡዕ"]],
    },
    {
        "section_id": "thursday_wudase_mariam",
        "order": 5,
        "title_am": "የሐሙስ ውዳሴ ማርያም",
        "title_en": "Thursday Praise of Mary",
        "category": "wudase_mariam_weekday",
        "weekday_am": "ሐሙስ",
        "weekday_en": "Thursday",
        "keywords": ["ሐሙስ", "ኃሙስ", "ውዳሴ", "ማርያም"],
        "required_any": [["ሐሙስ", "ኃሙስ"]],
    },
    {
        "section_id": "friday_wudase_mariam",
        "order": 6,
        "title_am": "የዓርብ ውዳሴ ማርያም",
        "title_en": "Friday Praise of Mary",
        "category": "wudase_mariam_weekday",
        "weekday_am": "ዓርብ",
        "weekday_en": "Friday",
        "keywords": ["ዓርብ", "አርብ", "ውዳሴ", "ማርያም"],
        "required_any": [["ዓርብ", "አርብ"]],
    },
    {
        "section_id": "saturday_wudase_mariam",
        "order": 7,
        "title_am": "የቅዳሜ ውዳሴ ማርያም",
        "title_en": "Saturday Praise of Mary",
        "category": "wudase_mariam_weekday",
        "weekday_am": "ቅዳሜ",
        "weekday_en": "Saturday",
        "keywords": ["ቅዳሜ", "ውዳሴ", "ዉዳሴ", "ጸሎት", "ማርያም"],
        "required_any": [["ቅዳሜ"]],
    },
    {
        "section_id": "sunday_wudase_mariam",
        "order": 8,
        "title_am": "የእሁድ ውዳሴ ማርያም",
        "title_en": "Sunday Praise of Mary",
        "category": "wudase_mariam_weekday",
        "weekday_am": "እሁድ",
        "weekday_en": "Sunday",
        "keywords": ["እሁድ", "እሑድ", "አሁድ", "ውዳሴ", "ዉዳሴ", "ጸሎት", "ማርያም"],
        "required_any": [["እሁድ", "እሑድ", "አሁድ"]],
    },
    {
        "section_id": "anqetse_birhan",
        "order": 9,
        "title_am": "አንቀጸ ብርሃን",
        "title_en": "Anqetse Birhan",
        "category": "marian_hymn",
        "weekday_am": None,
        "weekday_en": None,
        "keywords": ["አንቀጸ", "ብርሃን", "ብርፃን"],
        "required_any": [["አንቀጸ"]],
    },
    {
        "section_id": "yewediswa_melaekt",
        "order": 10,
        "title_am": "ይወድስዋ መላእክት",
        "title_en": "Angels Praise Her",
        "category": "marian_hymn",
        "weekday_am": None,
        "weekday_en": None,
        "keywords": ["ይወድስዋ", "ይዌድስዋ", "መላእክት", "መሳእክት"],
        "required_any": [["ይወድስዋ", "ይዌድስዋ"], ["መላእክት", "መሳእክት"]],
    },
    {
        "section_id": "sene_golgota",
        "order": 11,
        "title_am": "የሰኔ ጎልጎታ",
        "title_en": "Sene Golgotha",
        "category": "other",
        "weekday_am": None,
        "weekday_en": None,
        "keywords": ["ሰኔ", "ስኔ", "ጎልጎታ", "ጎለጎታ"],
        "required_any": [["ሰኔ", "ስኔ"], ["ጎልጎታ", "ጎለጎታ"]],
    },
    {
        "section_id": "conscience_prayer",
        "order": 12,
        "title_am": "የኅሊና ጸሎት",
        "title_en": "Prayer of Conscience",
        "category": "repentance_or_conscience_prayer",
        "weekday_am": None,
        "weekday_en": None,
        "keywords": ["ኅሊና", "ጸሎት"],
        "required_any": [["ኅሊና"]],
    },
]


@dataclass
class OcrCandidate:
    source: str
    language: str
    psm: int
    text: str
    confidence: float | None
    word_count: int
    ethiopic_chars: int
    latin_chars: int
    nonempty_lines: int
    score: float
    stderr: str


@dataclass
class PageResult:
    page: int
    raw_text: str
    clean_text: str
    confidence: float | None
    source: str
    language: str
    psm: int
    score: float
    ethiopic_chars: int
    latin_chars: int
    nonempty_lines: int
    variants_tried: int
    removed_artifacts: list[str]
    notes: list[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OCR Wudase Mariam into app-ready JSON.")
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--workers", type=int, default=min(4, max(1, (os.cpu_count() or 2) // 2)))
    parser.add_argument("--tesseract", type=Path, default=None)
    parser.add_argument("--max-pages", type=int, default=None, help="Debug limit; omit for all pages.")
    parser.add_argument("--force-render", action="store_true")
    parser.add_argument(
        "--reuse-ocr",
        action="store_true",
        help="Reuse existing output/ocr_raw and output/ocr_clean files to rebuild JSON/CSV/report.",
    )
    return parser.parse_args()


def find_tesseract(explicit: Path | None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(explicit)
    env_cmd = os.environ.get("TESSERACT_CMD")
    if env_cmd:
        candidates.append(Path(env_cmd))
    path_cmd = shutil.which("tesseract")
    if path_cmd:
        candidates.append(Path(path_cmd))
    candidates.append(DEFAULT_TESSERACT)
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate
    raise FileNotFoundError("Tesseract was not found. Set TESSERACT_CMD or pass --tesseract.")


def copy_if_exists(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def prepare_tessdata(output_dir: Path, tesseract_path: Path) -> tuple[Path, list[str], list[str]]:
    tessdata_dir = output_dir / "tessdata"
    tessdata_dir.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []

    system_tessdata = tesseract_path.parent / "tessdata"
    for name in ["eng.traineddata", "osd.traineddata"]:
        src = system_tessdata / name
        dst = tessdata_dir / name
        if src.exists() and not dst.exists():
            copy_if_exists(src, dst)
    for name in ["configs", "tessconfigs"]:
        copy_if_exists(system_tessdata / name, tessdata_dir / name)

    amh_path = tessdata_dir / "amh.traineddata"
    if not amh_path.exists():
        try:
            urllib.request.urlretrieve(AMH_TRAINEDDATA_URL, amh_path)
            notes.append(f"Downloaded amh.traineddata from {AMH_TRAINEDDATA_URL}.")
        except Exception as exc:  # pragma: no cover - depends on network.
            notes.append(f"Could not download amh.traineddata: {exc}")

    available = sorted(path.stem for path in tessdata_dir.glob("*.traineddata"))
    return tessdata_dir, available, notes


def ensure_dirs(output_dir: Path) -> dict[str, Path]:
    dirs = {
        "page_images": output_dir / "page_images",
        "processed_images": output_dir / "processed_images",
        "ocr_raw": output_dir / "ocr_raw",
        "ocr_clean": output_dir / "ocr_clean",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def render_pages(pdf_path: Path, page_dir: Path, dpi: int, force: bool, max_pages: int | None) -> int:
    doc = fitz.open(pdf_path)
    total_pages = doc.page_count if max_pages is None else min(max_pages, doc.page_count)
    matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    for index in range(total_pages):
        out_path = page_dir / f"page_{index + 1:03d}.png"
        if out_path.exists() and not force:
            continue
        page = doc.load_page(index)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        pix.save(out_path)
    doc.close()
    return total_pages


def safe_crop_bbox(gray: Image.Image) -> tuple[int, int, int, int] | None:
    small = gray.resize((max(1, gray.width // 4), max(1, gray.height // 4)))
    contrast = ImageOps.autocontrast(small)
    inverted = ImageOps.invert(contrast)
    mask = inverted.point(lambda p: 255 if p > 25 else 0)
    bbox = mask.getbbox()
    if not bbox:
        return None
    sx = gray.width / small.width
    sy = gray.height / small.height
    left = max(0, int(bbox[0] * sx) - 70)
    top = max(0, int(bbox[1] * sy) - 70)
    right = min(gray.width, int(bbox[2] * sx) + 70)
    bottom = min(gray.height, int(bbox[3] * sy) + 70)
    if (right - left) < gray.width * 0.45 or (bottom - top) < gray.height * 0.45:
        return None
    return left, top, right, bottom


def preprocess_image(src_path: Path, dst_path: Path) -> None:
    image = Image.open(src_path).convert("RGB")
    gray = ImageOps.grayscale(image)
    bbox = safe_crop_bbox(gray)
    if bbox:
        gray = gray.crop(bbox)
    gray = ImageOps.autocontrast(gray, cutoff=1)
    gray = gray.filter(ImageFilter.UnsharpMask(radius=1.0, percent=110, threshold=4))
    gray.save(dst_path)


def preprocess_pages(page_dir: Path, processed_dir: Path, total_pages: int) -> None:
    for page in range(1, total_pages + 1):
        src = page_dir / f"page_{page:03d}.png"
        dst = processed_dir / f"page_{page:03d}.png"
        preprocess_image(src, dst)


def count_ethiopic(text: str) -> int:
    return len(ETHIOPIC_RE.findall(text))


def count_latin(text: str) -> int:
    return len(LATIN_RE.findall(text))


def parse_tsv_confidence(tsv_text: str) -> tuple[float | None, int]:
    confidences: list[float] = []
    reader = csv.DictReader(tsv_text.splitlines(), delimiter="\t")
    for row in reader:
        token = (row.get("text") or "").strip()
        if not token:
            continue
        try:
            conf = float(row.get("conf", "-1"))
        except ValueError:
            continue
        if conf >= 0:
            confidences.append(conf)
    if not confidences:
        return None, 0
    return round(statistics.mean(confidences), 2), len(confidences)


def score_ocr(text: str, confidence: float | None, word_count: int) -> float:
    ethiopic_chars = count_ethiopic(text)
    latin_chars = count_latin(text)
    nonempty_lines = len([line for line in text.splitlines() if line.strip()])
    letters = max(1, ethiopic_chars + latin_chars)
    ethiopic_ratio = ethiopic_chars / letters
    latin_ratio = latin_chars / letters
    artifact_lines = sum(1 for line in text.splitlines() if WEBSITE_RE.search(line))
    score = confidence if confidence is not None else 0.0
    score += min(25.0, ethiopic_chars / 35.0)
    score += ethiopic_ratio * 20.0
    score += min(10.0, nonempty_lines * 0.4)
    score += min(5.0, word_count / 60.0)
    score -= latin_ratio * 18.0
    score -= artifact_lines * 4.0
    if ethiopic_chars < 20:
        score -= 10.0
    return round(score, 3)


def run_tesseract_candidate(
    tesseract_path: str,
    tessdata_dir: str,
    image_path: str,
    source: str,
    language: str,
    psm: int,
) -> OcrCandidate:
    with tempfile.TemporaryDirectory() as temp_dir:
        base = str(Path(temp_dir) / "ocr")
        command = [
            tesseract_path,
            image_path,
            base,
            "-l",
            language,
            "--tessdata-dir",
            tessdata_dir,
            "--psm",
            str(psm),
            "-c",
            "tessedit_create_txt=1",
            "tsv",
        ]
        env = os.environ.copy()
        env["OMP_THREAD_LIMIT"] = "1"
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        txt_path = Path(base + ".txt")
        tsv_path = Path(base + ".tsv")
        text = txt_path.read_text(encoding="utf-8", errors="replace") if txt_path.exists() else ""
        tsv_text = tsv_path.read_text(encoding="utf-8", errors="replace") if tsv_path.exists() else ""
        confidence, word_count = parse_tsv_confidence(tsv_text)
        ethiopic_chars = count_ethiopic(text)
        latin_chars = count_latin(text)
        nonempty_lines = len([line for line in text.splitlines() if line.strip()])
        stderr = result.stderr.strip()
        if result.returncode != 0 and stderr:
            stderr = f"returncode={result.returncode}: {stderr}"
        return OcrCandidate(
            source=source,
            language=language,
            psm=psm,
            text=text,
            confidence=confidence,
            word_count=word_count,
            ethiopic_chars=ethiopic_chars,
            latin_chars=latin_chars,
            nonempty_lines=nonempty_lines,
            score=score_ocr(text, confidence, word_count),
            stderr=stderr[:1000],
        )


def candidate_plan(available_langs: list[str]) -> list[tuple[str, str, int]]:
    languages: list[str] = []
    if "amh" in available_langs:
        languages.append("amh")
    if "amh" in available_langs and "eng" in available_langs:
        languages.append("amh+eng")
    elif "eng" in available_langs:
        languages.append("eng")

    plan: list[tuple[str, str, int]] = []
    for language in languages:
        for psm in [6, 4, 3]:
            plan.append(("processed", language, psm))

    preferred_original_language = "amh+eng" if "amh" in available_langs and "eng" in available_langs else languages[-1]
    for psm in [4, 6]:
        plan.append(("original", preferred_original_language, psm))
    return plan


def clean_ocr_text(raw_text: str) -> tuple[str, list[str]]:
    removed: list[str] = []
    out_lines: list[str] = []
    blank_pending = False
    for original_line in raw_text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = original_line.strip()
        if not line:
            blank_pending = True
            continue
        if WEBSITE_RE.search(line):
            removed.append(f"website/watermark: {line[:120]}")
            continue
        if line and all(ch in PAGE_NUMBER_CHARS for ch in line):
            removed.append(f"page number: {line[:80]}")
            continue
        if count_ethiopic(line) == 0 and count_latin(line) > 0:
            removed.append(f"latin artifact: {line[:120]}")
            continue
        if blank_pending and out_lines:
            out_lines.append("")
        out_lines.append(re.sub(r"[ \t]+", " ", line))
        blank_pending = False

    while out_lines and out_lines[0] == "":
        out_lines.pop(0)
    while out_lines and out_lines[-1] == "":
        out_lines.pop()
    return "\n".join(out_lines), removed


def ocr_page(
    page: int,
    tesseract_path: str,
    tessdata_dir: str,
    page_dir: str,
    processed_dir: str,
    available_langs: list[str],
) -> PageResult:
    image_paths = {
        "original": str(Path(page_dir) / f"page_{page:03d}.png"),
        "processed": str(Path(processed_dir) / f"page_{page:03d}.png"),
    }
    candidates: list[OcrCandidate] = []
    for source, language, psm in candidate_plan(available_langs):
        image_path = image_paths[source]
        candidates.append(
            run_tesseract_candidate(
                tesseract_path=tesseract_path,
                tessdata_dir=tessdata_dir,
                image_path=image_path,
                source=source,
                language=language,
                psm=psm,
            )
        )

    best = max(candidates, key=lambda item: item.score)

    eng_artifact_note = ""
    if best.ethiopic_chars < 20 and "eng" in available_langs:
        eng_candidate = run_tesseract_candidate(
            tesseract_path=tesseract_path,
            tessdata_dir=tessdata_dir,
            image_path=image_paths["processed"],
            source="processed",
            language="eng",
            psm=6,
        )
        candidates.append(eng_candidate)
        if eng_candidate.latin_chars > best.latin_chars:
            eng_artifact_note = "eng-only OCR was used only to inspect Latin/page artifacts."

    clean_text, removed = clean_ocr_text(best.text)
    notes: list[str] = []
    if best.stderr:
        notes.append(best.stderr)
    if eng_artifact_note:
        notes.append(eng_artifact_note)
    if best.confidence is None:
        notes.append("Tesseract confidence was unavailable for the selected OCR result.")
    elif best.confidence < 60:
        notes.append("Low Tesseract confidence; manual review recommended.")
    if best.ethiopic_chars < 20:
        notes.append("Very little Ethiopic text detected; page may be cover/artwork/blank or OCR failed.")

    return PageResult(
        page=page,
        raw_text=best.text,
        clean_text=clean_text,
        confidence=best.confidence,
        source=best.source,
        language=best.language,
        psm=best.psm,
        score=best.score,
        ethiopic_chars=best.ethiopic_chars,
        latin_chars=best.latin_chars,
        nonempty_lines=best.nonempty_lines,
        variants_tried=len(candidates),
        removed_artifacts=removed,
        notes=notes,
    )


def write_page_texts(page_results: list[PageResult], raw_dir: Path, clean_dir: Path) -> None:
    for result in page_results:
        (raw_dir / f"page_{result.page:03d}.txt").write_text(
            result.raw_text, encoding="utf-8", newline="\n"
        )
        (clean_dir / f"page_{result.page:03d}.txt").write_text(
            result.clean_text, encoding="utf-8", newline="\n"
        )


def read_previous_page_metadata(output_dir: Path) -> dict[int, dict[str, str]]:
    path = output_dir / "page_section_map.csv"
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        return {int(row["page"]): row for row in csv.DictReader(handle)}


def load_existing_page_results(output_dir: Path, total_pages: int) -> list[PageResult]:
    raw_dir = output_dir / "ocr_raw"
    clean_dir = output_dir / "ocr_clean"
    previous = read_previous_page_metadata(output_dir)
    page_results: list[PageResult] = []
    missing: list[str] = []
    for page in range(1, total_pages + 1):
        raw_path = raw_dir / f"page_{page:03d}.txt"
        clean_path = clean_dir / f"page_{page:03d}.txt"
        if not raw_path.exists() or not clean_path.exists():
            missing.append(f"page_{page:03d}")
            continue
        raw_text = raw_path.read_text(encoding="utf-8", errors="replace")
        clean_text, removed = clean_ocr_text(raw_text)
        clean_path.write_text(clean_text, encoding="utf-8", newline="\n")
        meta = previous.get(page, {})
        confidence_text = meta.get("confidence", "")
        try:
            confidence = float(confidence_text) if confidence_text != "" else None
        except ValueError:
            confidence = None
        try:
            psm = int(meta.get("psm", "0") or 0)
        except ValueError:
            psm = 0
        try:
            variants_tried = int(meta.get("variants_tried", "0") or 0)
        except ValueError:
            variants_tried = 0
        ethiopic_chars = count_ethiopic(clean_text)
        latin_chars = count_latin(clean_text)
        nonempty_lines = len([line for line in clean_text.splitlines() if line.strip()])
        notes: list[str] = []
        previous_notes = meta.get("notes", "")
        if previous_notes:
            notes.extend(note.strip() for note in previous_notes.split("|") if note.strip())
        if confidence is None:
            notes.append("Tesseract confidence was unavailable for the selected OCR result.")
        elif confidence < 60:
            notes.append("Low Tesseract confidence; manual review recommended.")
        if ethiopic_chars < 20:
            notes.append("Very little Ethiopic text detected; page may be cover/artwork/blank or OCR failed.")
        page_results.append(
            PageResult(
                page=page,
                raw_text=raw_text,
                clean_text=clean_text,
                confidence=confidence,
                source=meta.get("ocr_source", "reused"),
                language=meta.get("ocr_language", "reused"),
                psm=psm,
                score=0.0,
                ethiopic_chars=ethiopic_chars,
                latin_chars=latin_chars,
                nonempty_lines=nonempty_lines,
                variants_tried=variants_tried,
                removed_artifacts=removed,
                notes=notes,
            )
        )
    if missing:
        raise FileNotFoundError(f"Missing OCR files for: {', '.join(missing[:20])}")
    return page_results


def normalize_for_match(text: str) -> str:
    chars: list[str] = []
    for ch in text:
        if ETHIOPIC_RE.match(ch) and ch not in ETHIOPIC_NUMERALS:
            chars.append(ch)
    return "".join(chars)


def fuzzy_ratio(needle: str, haystack: str) -> float:
    # Import lazily to keep process worker imports minimal.
    from difflib import SequenceMatcher

    if not needle or not haystack:
        return 0.0
    if needle in haystack:
        return 1.0
    if len(haystack) <= len(needle) + 4:
        return SequenceMatcher(None, needle, haystack).ratio()
    limit = min(len(haystack), 500)
    window = max(len(needle), 8)
    step = max(1, window // 4)
    best = 0.0
    for start in range(0, max(1, limit - window + 1), step):
        chunk = haystack[start : start + window]
        best = max(best, SequenceMatcher(None, needle, chunk).ratio())
    return best


def section_score(text: str, section: dict[str, Any]) -> float:
    header_lines = [line.strip() for line in text.splitlines() if line.strip()][:6]
    header_text = "\n".join(header_lines)
    haystack = normalize_for_match(header_text)
    title_norm = normalize_for_match(section["title_am"])
    keyword_norms = [normalize_for_match(keyword) for keyword in section["keywords"]]
    keyword_hits = sum(1 for keyword in keyword_norms if keyword and keyword in haystack)
    keyword_score = keyword_hits / max(1, len(keyword_norms))
    title_match = fuzzy_ratio(title_norm, haystack)
    base_score = max(title_match, keyword_score * 0.82 + title_match * 0.18)

    required_groups = section.get("required_any") or []
    for group in required_groups:
        alternatives = [normalize_for_match(item) for item in group]
        if not any(item and item in haystack for item in alternatives):
            return round(min(base_score, 0.52), 3)
    if required_groups:
        base_score = max(base_score, 0.78)
    return round(base_score, 3)


def section_required_present(text: str, section: dict[str, Any]) -> bool:
    haystack = normalize_for_match(text)
    required_groups = section.get("required_any") or []
    for group in required_groups:
        alternatives = [normalize_for_match(item) for item in group]
        if not any(item and item in haystack for item in alternatives):
            return False
    return True


def find_section_start_line(text: str, section: dict[str, Any]) -> int:
    lines = text.splitlines()
    search_limit = min(len(lines), 24)
    for index in range(search_limit):
        window = "\n".join(lines[index : min(len(lines), index + 3)])
        if section_required_present(window, section):
            return index
    return 0


def detect_sections(page_results: list[PageResult]) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    pages_by_number = {result.page: result for result in page_results}
    total_pages = len(page_results)
    detected: list[dict[str, Any]] = []
    search_start = 1

    for section in SECTIONS:
        best_page = None
        best_score = 0.0
        threshold = 0.72
        for page in range(search_start, total_pages + 1):
            score = section_score(pages_by_number[page].clean_text, section)
            if score > best_score:
                best_page = page
                best_score = score
            if score >= threshold:
                best_page = page
                best_score = score
                break
        detected_section = dict(section)
        detected_section["detection_score"] = best_score
        if best_page is None or best_score < 0.55:
            detected_section["start_page"] = None
            detected_section["start_line"] = None
            warnings.append(f"{section['section_id']} was not detected confidently.")
        else:
            detected_section["start_page"] = best_page
            detected_section["start_line"] = find_section_start_line(
                pages_by_number[best_page].clean_text, section
            )
            if best_score < threshold:
                warnings.append(
                    f"{section['section_id']} was weakly detected on page {best_page} "
                    f"(score {best_score})."
                )
            search_start = best_page + 1
        detected.append(detected_section)

    known_starts = [
        (index, section["start_page"])
        for index, section in enumerate(detected)
        if section.get("start_page") is not None
    ]
    for known_index, (section_index, start_page) in enumerate(known_starts):
        if known_index + 1 < len(known_starts):
            next_section_index, next_start = known_starts[known_index + 1]
            next_start_line = detected[next_section_index].get("start_line") or 0
            detected[section_index]["end_page"] = next_start if next_start_line > 0 else next_start - 1
            detected[section_index]["end_line"] = next_start_line if next_start_line > 0 else None
        else:
            detected[section_index]["end_page"] = total_pages
            detected[section_index]["end_line"] = None

    for section in detected:
        if section.get("start_page") is None:
            section["end_page"] = None
            section["end_line"] = None
    return detected, warnings


def classify_block(lines: list[str], section: dict[str, Any]) -> str:
    joined = "\n".join(lines).strip()
    first = lines[0].strip() if lines else ""
    if section_score(joined, section) >= 0.72:
        return "heading"
    if len(lines) <= 2 and any(keyword in first for keyword in ["ጸሎት", "ውዳሴ", "ማርያም", "ብርሃን"]):
        return "heading"
    if any(keyword in joined for keyword in ["ወንጌል", "መዝሙር", "ምዕራፍ"]):
        return "scripture_reference"
    if any(keyword in joined for keyword in ["በል", "ይበል", "ትበል"]):
        return "instruction"
    if section["category"] in {"wudase_mariam_weekday", "marian_hymn"}:
        if len(lines) <= 4:
            return "stanza"
        return "hymn"
    if section["category"] == "repentance_or_conscience_prayer":
        return "prayer"
    if section["category"] == "daily_prayer":
        return "prayer"
    return "unknown"


def split_page_blocks(text: str) -> list[list[str]]:
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.strip():
            current.append(line.rstrip())
            continue
        if current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def build_sections_with_blocks(
    detected_sections: list[dict[str, Any]], page_results: list[PageResult]
) -> list[dict[str, Any]]:
    pages_by_number = {result.page: result for result in page_results}
    output_sections: list[dict[str, Any]] = []

    for section in detected_sections:
        section_out = {
            "section_id": section["section_id"],
            "order": section["order"],
            "title_am": section["title_am"],
            "title_en": section["title_en"],
            "category": section["category"],
            "weekday_am": section["weekday_am"],
            "weekday_en": section["weekday_en"],
            "start_page": section.get("start_page"),
            "end_page": section.get("end_page"),
            "content_blocks": [],
            "notes": [],
        }
        if section.get("detection_score") is not None:
            section_out["notes"].append(f"Automated section detection score: {section['detection_score']}.")
        if section.get("start_line"):
            section_out["notes"].append(
                f"Section starts mid-page at OCR line {section['start_line']} on page {section['start_page']}."
            )
        if section.get("end_line") is not None:
            section_out["notes"].append(
                f"Section ends mid-page before OCR line {section['end_line']} on page {section['end_page']}."
            )
        if section.get("start_page") is None or section.get("end_page") is None:
            section_out["notes"].append("Section boundaries require manual review.")
            output_sections.append(section_out)
            continue

        block_index = 1
        for page in range(section["start_page"], section["end_page"] + 1):
            page_result = pages_by_number[page]
            page_lines = page_result.clean_text.splitlines()
            start_line = section.get("start_line") if page == section.get("start_page") else 0
            end_line = section.get("end_line") if page == section.get("end_page") else None
            page_text = "\n".join(page_lines[start_line:end_line])
            for lines in split_page_blocks(page_text):
                text = "\n".join(lines)
                block_type = classify_block(lines, section)
                title_am = lines[0] if block_type == "heading" else None
                block = {
                    "block_id": f"{section['section_id']}_{block_index:03d}",
                    "block_type": block_type,
                    "title_am": title_am,
                    "title_en": section["title_en"] if block_type == "heading" and title_am else None,
                    "page_start": page,
                    "page_end": page,
                    "text": text,
                    "lines": lines,
                    "confidence": page_result.confidence,
                    "notes": list(page_result.notes),
                }
                section_out["content_blocks"].append(block)
                block_index += 1
        output_sections.append(section_out)
    return output_sections


def build_page_section_rows(
    detected_sections: list[dict[str, Any]], page_results: list[PageResult]
) -> list[dict[str, Any]]:
    assignments: dict[int, list[dict[str, Any]]] = {}
    for section in detected_sections:
        if section.get("start_page") is None or section.get("end_page") is None:
            continue
        for page in range(section["start_page"], section["end_page"] + 1):
            assignments.setdefault(page, []).append(section)

    rows: list[dict[str, Any]] = []
    for result in page_results:
        sections = assignments.get(result.page, [])
        low_confidence = result.confidence is None or result.confidence < 60 or result.ethiopic_chars < 20
        notes = list(result.notes)
        if len(sections) > 1:
            notes.append(
                "Transition page split across sections: "
                + " | ".join(section["section_id"] for section in sections)
            )
        rows.append(
            {
                "page": result.page,
                "section_id": "|".join(section["section_id"] for section in sections)
                if sections
                else "front_matter_or_unassigned",
                "section_order": "|".join(str(section["order"]) for section in sections) if sections else "",
                "section_title_am": "|".join(section["title_am"] for section in sections) if sections else "",
                "ocr_source": result.source,
                "ocr_language": result.language,
                "psm": result.psm,
                "confidence": result.confidence if result.confidence is not None else "",
                "low_confidence": "yes" if low_confidence else "no",
                "variants_tried": result.variants_tried,
                "notes": " | ".join(notes),
            }
        )
    return rows


def confidence_summary(page_results: list[PageResult]) -> dict[str, Any]:
    values = [result.confidence for result in page_results if result.confidence is not None]
    if not values:
        return {"min": None, "median": None, "mean": None, "max": None}
    return {
        "min": round(min(values), 2),
        "median": round(statistics.median(values), 2),
        "mean": round(statistics.mean(values), 2),
        "max": round(max(values), 2),
    }


def build_quality_report(
    page_results: list[PageResult],
    detected_warnings: list[str],
    page_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    low_pages = [
        result.page
        for result in page_results
        if result.confidence is None or result.confidence < 60 or result.ethiopic_chars < 20
    ]
    removed: list[str] = []
    for result in page_results:
        for item in result.removed_artifacts[:5]:
            removed.append(f"page {result.page}: {item}")
    unassigned = [row["page"] for row in page_rows if row["section_id"] == "front_matter_or_unassigned"]
    known_issues = [
        "OCR output is automated and has not been manually proofread against every page image.",
        "Faint red watermarks and bleed-through may still be present where removing them risked deleting prayer text.",
        "Tesseract confidence is an OCR signal only; liturgical correctness still requires human review.",
    ]
    if unassigned:
        known_issues.append(
            "Pages mapped to front_matter_or_unassigned are outside confidently detected major section ranges."
        )
    return {
        "pages_with_low_confidence": low_pages,
        "pages_requiring_manual_review": sorted(set(low_pages + unassigned)),
        "missing_or_uncertain_sections": detected_warnings,
        "removed_artifacts": removed[:250],
        "known_issues": known_issues,
    }


def build_book_json(
    pdf_path: Path,
    total_pages: int,
    dpi: int,
    available_langs: list[str],
    sections: list[dict[str, Any]],
    quality_report: dict[str, Any],
) -> dict[str, Any]:
    attempted_langs = []
    if "amh" in available_langs:
        attempted_langs.append("amh")
    if "amh" in available_langs and "eng" in available_langs:
        attempted_langs.append("amh+eng")
    if "eng" in available_langs:
        attempted_langs.append("eng")
    return {
        "book_id": "wudase_mariam",
        "title_am": "ውዳሴ ማርያም",
        "title_en": "Praise of Mary",
        "tradition": "Ethiopian Orthodox Tewahedo Church",
        "source_file": pdf_path.name,
        "source_type": "scanned_pdf",
        "language": ["am", "gez"],
        "script": "Ethiopic",
        "total_pages": total_pages,
        "extraction_method": {
            "ocr_engine": "tesseract",
            "ocr_languages": attempted_langs,
            "dpi": dpi,
            "preprocessing": [
                "300 DPI PyMuPDF page rendering",
                "safe margin crop based on non-background content",
                "grayscale conversion",
                "autocontrast",
                "light unsharp mask",
                "OCR variant comparison across amh/amh+eng and PSM 6/4/3 on processed images",
                "original color OCR fallback comparison",
                "conservative removal of explicit website/page-number artifacts",
            ],
            "manual_review": False,
        },
        "sections_count": 12,
        "sections": sections,
        "quality_report": quality_report,
    }


def write_json_outputs(book: dict[str, Any], output_dir: Path) -> None:
    pretty_path = output_dir / "wudase_mariam.json"
    min_path = output_dir / "wudase_mariam_minified.json"
    pretty_path.write_text(
        json.dumps(book, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    min_path.write_text(json.dumps(book, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def write_page_section_csv(rows: list[dict[str, Any]], output_dir: Path) -> None:
    path = output_dir / "page_section_map.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_report(
    output_dir: Path,
    pdf_path: Path,
    total_pages: int,
    dpi: int,
    tesseract_path: Path,
    available_langs: list[str],
    tessdata_notes: list[str],
    page_results: list[PageResult],
    detected_sections: list[dict[str, Any]],
    page_rows: list[dict[str, Any]],
    elapsed_seconds: float,
    reused_ocr: bool,
) -> None:
    conf = confidence_summary(page_results)
    low_pages = [
        result.page
        for result in page_results
        if result.confidence is None or result.confidence < 60 or result.ethiopic_chars < 20
    ]
    unassigned = [row["page"] for row in page_rows if row["section_id"] == "front_matter_or_unassigned"]
    found_sections = [section for section in detected_sections if section.get("start_page") is not None]

    lines = [
        "# Wudase Mariam OCR Extraction Report",
        "",
        f"- Source file: `{pdf_path.name}`",
        f"- Pages processed: {total_pages}",
        f"- Render DPI: {dpi}",
        f"- Runtime seconds: {round(elapsed_seconds, 1)}"
        + (" (structure rebuild; existing OCR text reused)" if reused_ocr else ""),
        "",
        "## Tools Used",
        "",
        f"- Python: {sys.version.split()[0]}",
        f"- PyMuPDF: {fitz.VersionBind}",
        f"- Pillow: {Image.__version__}",
        f"- Tesseract: `{tesseract_path}`",
        "",
        "## OCR Language Data",
        "",
        f"- Available local traineddata: {', '.join(available_langs) if available_langs else 'none'}",
        "- OCR languages attempted: amh, amh+eng, and eng for low-Ethiopic artifact inspection when available.",
        "- Amharic trained data source: official `tesseract-ocr/tessdata_best` `amh.traineddata`.",
    ]
    if reused_ocr:
        lines.append("- OCR text source for this report build: existing `output/ocr_raw` and `output/ocr_clean` files.")
    if tessdata_notes:
        lines.extend(["", "### Tessdata Notes", ""])
        lines.extend(f"- {note}" for note in tessdata_notes)

    lines.extend(
        [
            "",
            "## Section Detection",
            "",
            f"- Major sections found: {len(found_sections)} / 12",
            "",
            "| Order | Section ID | Title | Start | End | Detection score |",
            "| ---: | --- | --- | ---: | ---: | ---: |",
        ]
    )
    for section in detected_sections:
        lines.append(
            "| {order} | `{sid}` | {title} | {start} | {end} | {score} |".format(
                order=section["order"],
                sid=section["section_id"],
                title=section["title_am"],
                start=section.get("start_page") or "",
                end=section.get("end_page") or "",
                score=section.get("detection_score", ""),
            )
        )

    lines.extend(
        [
            "",
            "## OCR Confidence Summary",
            "",
            f"- Minimum confidence: {conf['min']}",
            f"- Median confidence: {conf['median']}",
            f"- Mean confidence: {conf['mean']}",
            f"- Maximum confidence: {conf['max']}",
            f"- Pages requiring manual review: {len(set(low_pages + unassigned))}",
            f"- Low-confidence or low-text pages: {', '.join(map(str, low_pages[:120])) if low_pages else 'none'}",
        ]
    )
    if len(low_pages) > 120:
        lines.append(f"- Additional low-confidence pages omitted from this list: {len(low_pages) - 120}")

    lines.extend(
        [
            "",
            "## Page Coverage",
            "",
            f"- Page map rows: {len(page_rows)}",
            f"- Expected page map rows: {total_pages}",
            f"- Unassigned/front-matter pages: {', '.join(map(str, unassigned)) if unassigned else 'none'}",
            "",
            "## Known Weak Spots",
            "",
            "- This is OCR from scanned page images, not a manually proofread diplomatic transcription.",
            "- Red headings and pale red watermark/bleed-through occupy similar color ranges on some pages.",
            "- Artifact cleanup removes explicit URL/website lines and standalone page numbers only.",
            "- Section boundaries are detected from OCR headings and should be checked visually before publication.",
            "",
            "## Recommendations For Manual Proofreading",
            "",
            "- Review every page listed as low confidence or front matter/unassigned.",
            "- Compare section start and end pages against the rendered page images.",
            "- Pay special attention to red headings, stanza markers, Ethiopic numerals, and bottom-of-page text near watermarks.",
            "- Treat the JSON as an app-ready OCR draft until a human proofreader signs off.",
        ]
    )
    (output_dir / "extraction_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def validate_outputs(book: dict[str, Any], page_rows: list[dict[str, Any]], total_pages: int) -> dict[str, Any]:
    serialized = json.dumps(book, ensure_ascii=False)
    parsed = json.loads(serialized)
    section_count = len(parsed["sections"])
    weekday_sections = [
        section
        for section in parsed["sections"]
        if section.get("category") == "wudase_mariam_weekday"
    ]
    mapped_pages = [int(row["page"]) for row in page_rows]
    expected_pages = list(range(1, total_pages + 1))
    return {
        "json_valid": True,
        "sections_count": section_count,
        "sections_count_ok": section_count == 12,
        "weekday_wudase_sections": len(weekday_sections),
        "weekday_wudase_sections_ok": len(weekday_sections) == 7,
        "page_map_rows": len(page_rows),
        "page_map_rows_ok": len(page_rows) == total_pages,
        "no_page_numbers_missing_from_map": mapped_pages == expected_pages,
    }


def main() -> None:
    args = parse_args()
    start_time = time.time()
    output_dir = args.output.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    dirs = ensure_dirs(output_dir)

    pdf_path = args.pdf.resolve()
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)

    tesseract_path = find_tesseract(args.tesseract).resolve()
    tessdata_dir, available_langs, tessdata_notes = prepare_tessdata(output_dir, tesseract_path)
    if "amh" not in available_langs:
        print("WARNING: amh.traineddata is unavailable; OCR quality for Ethiopic text will be poor.")

    source_doc = fitz.open(pdf_path)
    source_total_pages = source_doc.page_count
    source_doc.close()
    total_pages = source_total_pages if args.max_pages is None else min(args.max_pages, source_total_pages)

    if args.reuse_ocr:
        print(f"Reusing existing OCR text for {total_pages} pages...")
        page_results = load_existing_page_results(output_dir, total_pages)
    else:
        print(f"Rendering pages from {pdf_path.name} at {args.dpi} DPI...")
        total_pages = render_pages(
            pdf_path=pdf_path,
            page_dir=dirs["page_images"],
            dpi=args.dpi,
            force=args.force_render,
            max_pages=args.max_pages,
        )
        print(f"Preprocessing {total_pages} page images...")
        preprocess_pages(dirs["page_images"], dirs["processed_images"], total_pages)

        print(f"Running OCR with {args.workers} worker(s)...")
        page_results = []
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(
                    ocr_page,
                    page,
                    str(tesseract_path),
                    str(tessdata_dir),
                    str(dirs["page_images"]),
                    str(dirs["processed_images"]),
                    available_langs,
                ): page
                for page in range(1, total_pages + 1)
            }
            completed = 0
            for future in as_completed(futures):
                result = future.result()
                page_results.append(result)
                completed += 1
                if completed == 1 or completed % 10 == 0 or completed == total_pages:
                    print(f"OCR complete: {completed}/{total_pages} pages")

        page_results.sort(key=lambda item: item.page)
        write_page_texts(page_results, dirs["ocr_raw"], dirs["ocr_clean"])

    print("Detecting sections and building JSON...")
    detected_sections, detected_warnings = detect_sections(page_results)
    page_rows = build_page_section_rows(detected_sections, page_results)
    sections_with_blocks = build_sections_with_blocks(detected_sections, page_results)
    quality_report = build_quality_report(page_results, detected_warnings, page_rows)
    book = build_book_json(
        pdf_path=pdf_path,
        total_pages=total_pages,
        dpi=args.dpi,
        available_langs=available_langs,
        sections=sections_with_blocks,
        quality_report=quality_report,
    )

    write_json_outputs(book, output_dir)
    write_page_section_csv(page_rows, output_dir)
    elapsed_seconds = time.time() - start_time
    write_report(
        output_dir=output_dir,
        pdf_path=pdf_path,
        total_pages=total_pages,
        dpi=args.dpi,
        tesseract_path=tesseract_path,
        available_langs=available_langs,
        tessdata_notes=tessdata_notes,
        page_results=page_results,
        detected_sections=detected_sections,
        page_rows=page_rows,
        elapsed_seconds=elapsed_seconds,
        reused_ocr=args.reuse_ocr,
    )

    validation = validate_outputs(book, page_rows, total_pages)
    (output_dir / "validation_summary.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(validation, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
