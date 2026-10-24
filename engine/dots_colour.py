"""Turn dots.mocr candidates into reviewable transcripts with red/black spans.

Two steps, both resumable:

``read_red``  Find red-ink regions in ``work/red_mask/`` (ornaments are already
              removed by colour_split), crop each from the 300-DPI page image and
              read it with the dots server in plain-OCR mode. Readings are cached
              in ``work/candidates/dots-red/`` keyed by mask hash.
``adopt``     Clean each candidate (page headers, watermarks, verse numerals,
              punctuation), locate every red reading in its text block, and write
              the result to ``work/transcripts/``. Previous transcripts are moved
              once to ``work/transcripts-legacy/`` and never overwritten there.

Colour is decided by pixels; the OCR text only says *where* in the block the red
words are. A red reading that cannot be located is listed under ``uncertain`` so
the reviewer sees it instead of a silent guess.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import re
import shutil
from pathlib import Path

from .dots_ocr import request_completion
from .provenance import atomic_json, file_hash, read_json

READ_VERSION = "red-crop-v1"
OCR_PROMPT = "<|img|><|imgpad|><|endofimg|>Extract the text content from this image."
NUMERAL = "፩-፼"  # Ethiopic digits ፩–፼
ETHIOPIC = re.compile(r"[ሀ-፿ⶀ-⷟]")
LETTER = re.compile(r"[ሀ-ፚⶀ-⷟]")


def red_regions(mask) -> list[list[int]]:
    """Word boxes from the red mask, merged into same-line phrases."""
    import cv2
    import numpy as np

    joined = cv2.dilate((mask > 0).astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_RECT, (45, 15)))
    _, _, stats, _ = cv2.connectedComponentsWithStats(joined)
    boxes = sorted(([int(v) for v in s[:4]] for s in stats[1:] if s[4] > 1500),
                   key=lambda b: (b[1] + b[3] // 2) // 60 * 100000 + b[0])
    merged: list[list[int]] = []
    for x, y, w, h in boxes:
        if merged:
            px, py, pw, ph = merged[-1]
            if abs((py + ph / 2) - (y + h / 2)) < 50 and x - (px + pw) < 140:
                x0, y0 = min(px, x), min(py, y)
                merged[-1] = [x0, y0, max(px + pw, x + w) - x0, max(py + ph, y + h) - y0]
                continue
        merged.append([x, y, w, h])
    return merged


def read_red(bk, page: int, endpoint: str, model: str, force: bool = False) -> dict:
    """OCR every red region on a page; cached per mask hash."""
    import cv2

    mask_path = bk.work / "red_mask" / f"page_{page:03d}.png"
    image_path = bk.page_images / f"page_{page:03d}.png"
    cache = bk.work / "candidates" / "dots-red" / f"page_{page:03d}.json"
    key = {"mask": file_hash(mask_path), "image": file_hash(image_path), "version": READ_VERSION, "model": model}
    cached = read_json(cache, None)
    if cached and cached.get("key") == key and not force:
        return cached
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)

    def ocr(crop) -> str:
        ok, encoded = cv2.imencode(".png", crop)
        if not ok:
            raise RuntimeError(f"could not encode red crop on page {page}")
        url = "data:image/png;base64," + base64.b64encode(encoded.tobytes()).decode()
        raw = request_completion(endpoint, {
            "model": model, "temperature": 0.0, "max_completion_tokens": 256,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": url}}, {"type": "text", "text": OCR_PROMPT}]}],
        }, timeout=120)
        return raw["choices"][0]["message"]["content"].strip()

    regions = []
    for x, y, w, h in red_regions(cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)):
        text = ocr(image[max(0, y - 10):y + h + 10, max(0, x - 10):x + w + 10])
        if not ETHIOPIC.search(text) and w * h > 8000:
            # Short single-word crops sometimes come back empty; retry wider and enlarged.
            wide = image[max(0, y - 40):y + h + 40, max(0, x - 40):x + w + 40]
            text = ocr(cv2.resize(wide, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC))
        regions.append({"bbox": [x, y, x + w, y + h], "text": text})
    result = {"page": page, "key": key, "regions": regions,
              "created_at": datetime.now(timezone.utc).isoformat()}
    atomic_json(cache, result)
    return result


def clean_text(text: str) -> str:
    """Normalise model formatting without changing the reading."""
    text = text.replace("**", "").replace("、", "")
    text = re.sub(r"\s*\n\s*", " ", text)                 # printed line wraps are not semantic
    text = re.sub(r"\s*(?:::|፡፡|：：)", "።", text)          # four-dot full stop
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def _distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def locate(text: str, needle: str, expected: float, taken: list[tuple[int, int]]) -> tuple[int, int] | None:
    """Best fuzzy occurrence of ``needle``; ties go to the one nearest ``expected``."""
    needle = needle.strip("። ")
    if not needle:
        return None
    limit = max(1, -(-len(needle) // 3))
    best = None
    for start in range(len(text)):
        # Only start at a word boundary so a loose match cannot begin mid-word.
        if text[start].isspace() or (start and LETTER.match(text[start - 1])):
            continue
        for length in range(max(1, len(needle) - limit), len(needle) + limit + 1):
            end = start + length
            if end > len(text) or any(s < end and start < e for s, e in taken):
                continue
            score = _distance(text[start:end], needle)
            if score > limit:
                continue
            rank = (score, abs(start / max(1, len(text)) - expected))
            if best is None or rank < best[0]:
                best = (rank, (start, end))
    return best[1] if best else None


def _spans(text: str, red: list[tuple[int, int]]) -> list[dict]:
    flags = [False] * len(text)
    for start, end in red:
        # Red is printed per word: widen a partial-word hit to the word's edges
        # and over directly attached punctuation (ለምኝልን።).
        while start > 0 and LETTER.match(text[start - 1]):
            start -= 1
        while end < len(text) and (LETTER.match(text[end]) or text[end] in "።፡፣፤"):
            end += 1
        flags[start:end] = [True] * (end - start)
    # A gap of only whitespace/punctuation between two red runs is red too.
    for match in re.finditer(r"[\s።፡፣፤]+", text):
        s, e = match.span()
        if s > 0 and e < len(text) and flags[s - 1] and flags[e]:
            flags[s:e] = [True] * (e - s)
    spans: list[dict] = []
    for ch, is_red in zip(text, flags):
        colour = "red" if is_red else "black"
        if spans and spans[-1]["c"] == colour:
            spans[-1]["t"] += ch
        else:
            spans.append({"t": ch, "c": colour})
    return spans


def _ornament_reading(text: str) -> bool:
    """A red border or flourish read as text: punctuation runs or a stutter like 'በ በ በ'."""
    tokens = text.split()
    stutter = len(tokens) >= 3 and len(set(tokens)) == 1 and len(LETTER.findall(tokens[0])) <= 2
    return len(LETTER.findall(text)) < 2 or len(text) > 200 or stutter


def build_transcript(page: int, candidate: dict, red: dict, ignore: list[str]) -> dict:
    cells = []
    header = ""
    for cell in candidate["layout"]:
        text = clean_text(cell.get("text") or "")
        if cell["category"] == "Picture" or not text or any(re.search(p, text) for p in ignore):
            continue
        if cell["category"] in {"Page-header", "Page-footer"}:
            header = (header + " " + text).strip()
            continue
        cells.append({**cell, "text": text})

    regions = [r for r in red.get("regions", []) if len(ETHIOPIC.findall(r["text"])) >= 2]
    found: dict[int, list[tuple[int, int]]] = {}
    uncertain = []
    for region in regions:
        x1, y1, x2, y2 = region["bbox"]
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        owner = next((i for i, c in enumerate(cells)
                      if c["bbox"][0] - 40 <= cx <= c["bbox"][2] + 40 and c["bbox"][1] - 40 <= cy <= c["bbox"][3] + 40), None)
        if owner is None:
            continue  # red ink in a header, picture or margin
        bx1, by1, bx2, by2 = cells[owner]["bbox"]
        pitch = max(1.0, (y2 - y1) * 1.2)
        rows = max(1, round((by2 - by1) / pitch))
        row = min(rows - 1, max(0, int((cy - by1) // pitch)))
        expected = (row + (cx - bx1) / max(1.0, bx2 - bx1)) / rows
        reading = clean_text(region["text"])
        hit = locate(cells[owner]["text"], reading, expected, found.get(owner, []))
        if hit:
            found.setdefault(owner, []).append(hit)
        elif not _ornament_reading(reading):
            uncertain.append({"text": reading.strip("። "), "bbox": region["bbox"],
                              "why": "red ink on the scan that was not found in this text; check spelling and colour"})

    blocks = []
    for i, cell in enumerate(cells):
        text, red_runs, verse = cell["text"], found.get(i, []), ""
        match = re.match(rf"([{NUMERAL}]+)\.?\s+", text) if cell["category"] in {"Text", "List-item"} else None
        if match:
            verse, cut = match.group(1), match.end()
            text = text[cut:]
            red_runs = [(max(0, s - cut), e - cut) for s, e in red_runs if e > cut]
        kind = "title" if cell["category"] in {"Title", "Section-header"} else \
            "instruction" if cell["category"] in {"Footnote", "Caption"} else "verse" if verse else "prayer"
        blocks.append({"type": kind, "verse_num_am": verse, "spans": _spans(text, red_runs),
                       "bbox": [round(v) for v in cell["bbox"]], "layout_category": cell["category"]})
    if not blocks:
        raise ValueError(f"page {page}: no text blocks after cleanup")
    return {"page": page, "page_header": header, "blocks": blocks,
            "notes": "dots.mocr draft with pixel-based red marking. Check every word and colour against the scan.",
            "corrections": [], "uncertain": uncertain, "clipped": [],
            "_source": "dots.mocr OCR + red-mask crops (draft, needs human review)",
            "_provenance": {"candidate": candidate["transcript"]["_provenance"], "red": red.get("key"),
                            "red_regions": len(regions), "red_located": sum(map(len, found.values()))}}


def latest_candidates(bk) -> dict[int, Path]:
    latest: dict[int, Path] = {}
    for path in (bk.work / "candidates" / "dots").glob("page_*.json"):
        match = re.fullmatch(r"page_(\d+)\.[0-9a-f]{16}\.json", path.name)
        if match:
            page = int(match.group(1))
            if page not in latest or path.stat().st_mtime > latest[page].stat().st_mtime:
                latest[page] = path
    return latest


def adopt(bk, pages: list[int], ignore: list[str]) -> dict:
    """Write cleaned, coloured transcripts; keep every earlier transcript in legacy."""
    transcripts, legacy = bk.work / "transcripts", bk.work / "transcripts-legacy"
    legacy.mkdir(parents=True, exist_ok=True)
    candidates = latest_candidates(bk)
    summary = {"adopted": [], "missing_candidate": [], "missing_red": [], "uncertain": 0}
    for page in pages:
        if page not in candidates:
            summary["missing_candidate"].append(page)
            continue
        red = read_json(bk.work / "candidates" / "dots-red" / f"page_{page:03d}.json", None)
        if red is None:
            summary["missing_red"].append(page)
            continue
        for old in transcripts.glob(f"page_{page:03d}*.json"):
            kept = legacy / old.name
            if not kept.exists():
                shutil.copy2(old, kept)
            if old.name.endswith(".original.json"):
                old.unlink()  # Revert in the editor now restores the new baseline
        transcript = build_transcript(page, read_json(candidates[page], {}), red, ignore)
        legacy_file = legacy / f"page_{page:03d}.json"
        if legacy_file.exists():
            transcript["_legacy"] = f"transcripts-legacy/{legacy_file.name}"
        atomic_json(transcripts / f"page_{page:03d}.json", transcript)
        summary["adopted"].append(page)
        summary["uncertain"] += len(transcript["uncertain"])
    return summary
