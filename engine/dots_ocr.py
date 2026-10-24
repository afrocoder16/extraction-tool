"""dots.mocr/dots.ocr client: resumable candidates, never overwrite accepted text.

Uses an OpenAI-compatible vLLM endpoint and the upstream native layout format.
Only Pillow is needed on the client; model weights belong on the inference host.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import io
import json
import math
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .provenance import atomic_json, file_hash, json_hash, read_json

PROMPT_VERSION = "layout-literal-v1"
PROMPT = """Output the page layout as a JSON array in human reading order.
Each element has bbox [x1,y1,x2,y2] in image pixel coordinates, category, and text.
Categories: Caption, Footnote, Formula, List-item, Page-footer, Page-header,
Picture, Section-header, Table, Text, Title. Omit text for Picture.
Transcribe the original visible text exactly without translation, correction,
modernization or completing missing text. Preserve Ethiopic punctuation and
numerals. Format formulas as LaTeX, tables as HTML, other text as Markdown.
Return only the JSON array, without code fences or explanations."""
CATEGORIES = {"Caption", "Footnote", "Formula", "List-item", "Page-footer", "Page-header",
              "Picture", "Section-header", "Table", "Text", "Title"}


def model_dimensions(width: int, height: int, max_pixels: int = 11289600) -> tuple[int, int]:
    """Use dimensions divisible by 28 so server/client coordinates agree.

    Based on the constraints documented in dots.mocr image_utils.smart_resize.
    """
    if min(width, height) <= 0 or max(width, height) / min(width, height) > 200:
        raise ValueError("unsupported image dimensions")
    if max_pixels < 3136:
        raise ValueError("max_pixels must be at least 3136")
    w, h = max(28, round(width / 28) * 28), max(28, round(height / 28) * 28)
    if w * h > max_pixels:
        scale = math.sqrt(max_pixels / (width * height))
        w, h = max(28, math.floor(width * scale / 28) * 28), max(28, math.floor(height * scale / 28) * 28)
    if w * h < 3136:
        scale = math.sqrt(3136 / (width * height))
        w, h = math.ceil(width * scale / 28) * 28, math.ceil(height * scale / 28) * 28
    return w, h


def prepare_image(path: Path, max_pixels: int) -> tuple[str, tuple[int, int], tuple[int, int]]:
    from PIL import Image

    with Image.open(path) as source:
        original = source.size
        dimensions = model_dimensions(*original, max_pixels)
        image = source.convert("RGB").resize(dimensions, Image.Resampling.LANCZOS)
        data = io.BytesIO()
        image.save(data, format="PNG")
    return "data:image/png;base64," + base64.b64encode(data.getvalue()).decode(), original, dimensions


def parse_layout(text: str, original: tuple[int, int], sent: tuple[int, int]) -> list[dict]:
    """Strict parsing: invalid output is retained as a failure, never text-repaired."""
    cells = json.loads(text)
    if not isinstance(cells, list) or not cells or len(cells) > 2000:
        raise ValueError("expected a nonempty layout array with at most 2000 regions")
    output, seen = [], set()
    for order, cell in enumerate(cells):
        if not isinstance(cell, dict) or cell.get("category") not in CATEGORIES:
            raise ValueError("invalid layout category")
        box = cell.get("bbox")
        if not isinstance(box, list) or len(box) != 4 or any(
            isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in box
        ):
            raise ValueError("invalid bounding box")
        x1, y1, x2, y2 = box
        if not (0 <= x1 < x2 <= sent[0] and 0 <= y1 < y2 <= sent[1]):
            raise ValueError("bounding box lies outside the submitted image")
        content = cell.get("text", "")
        if not isinstance(content, str) or (cell["category"] != "Picture" and not content.strip()):
            raise ValueError("text region contains no text")
        signature = (tuple(box), cell["category"], content)
        if signature in seen:
            raise ValueError("duplicate layout region")
        seen.add(signature)
        output.append({"order": order, "category": cell["category"], "text": content,
                       "bbox": [x1 * original[0] / sent[0], y1 * original[1] / sent[1],
                                x2 * original[0] / sent[0], y2 * original[1] / sent[1]]})
    return output


def request_completion(base_url: str, payload: dict, *, timeout: float = 180,
                       retries: int = 2, api_key: str | None = None) -> dict:
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("base URL must be an HTTP(S) endpoint without credentials or query")
    if timeout <= 0 or not 0 <= retries <= 5:
        raise ValueError("invalid timeout/retry count")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    request = Request(base_url.rstrip("/") + "/chat/completions",
                      data=json.dumps(payload).encode(), headers=headers, method="POST")
    for attempt in range(retries + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise ValueError("model response exceeded 16 MiB")
            return json.loads(raw)
        except HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504} or attempt == retries:
                detail = error.read(2000).decode("utf-8", "replace").strip()
                raise RuntimeError(f"OCR server returned HTTP {error.code}: {detail}") from None
        except (URLError, TimeoutError) as error:
            if attempt == retries:
                raise RuntimeError(f"OCR server is unreachable ({type(error).__name__})") from None
        time.sleep(min(2 ** attempt, 8))
    raise RuntimeError("OCR request failed")


def align_colour(text: str, box: list, layout: dict | None) -> tuple[list[dict], bool]:
    """Assign colour only when the entire region's word sequence matches geometry.

    Unknown regions are displayed in black provisionally and explicitly flagged.
    A region-level box or the meaning of a word never decides its ink colour.
    """
    words = []
    for line in (layout or {}).get("lines", []):
        for word in line.get("words", []):
            x1, y1, x2, y2 = word["bbox"]
            if box[0] <= (x1 + x2) / 2 <= box[2] and box[1] <= (y1 + y2) / 2 <= box[3]:
                words.append(word)
    tokens = list(re.finditer(r"\S+", text))
    if not tokens or [t.group() for t in tokens] != [w.get("text") for w in words]:
        return [{"t": text, "c": "black"}], False
    spans, cursor = [], 0
    for token, word in zip(tokens, words):
        end = token.end()
        colour = "red" if word.get("is_red") else "black"
        fragment = text[cursor:end]
        if spans and spans[-1]["c"] == colour:
            spans[-1]["t"] += fragment
        else:
            spans.append({"t": fragment, "c": colour})
        cursor = end
    spans[-1]["t"] += text[cursor:]
    return spans, True


def candidate_transcript(page: int, cells: list[dict], layout: dict | None, provenance: dict) -> dict:
    blocks, unresolved = [], []
    for cell in cells:
        if cell["category"] == "Picture":
            continue
        spans, aligned = align_colour(cell["text"], cell["bbox"], layout)
        if not aligned:
            unresolved.append(cell["order"])
        blocks.append({"type": "title" if cell["category"] in {"Title", "Section-header"} else "prayer",
                       "verse_num_am": "", "spans": spans, "bbox": cell["bbox"],
                       "layout_category": cell["category"], "colour_aligned": aligned})
    if not blocks:
        raise ValueError("content page produced no text regions")
    return {"page": page, "blocks": blocks, "notes": "OCR draft. Check text, verse labels, block roles and colour against the scan.",
            "corrections": [], "uncertain": [], "clipped": [], "_source": "dots OCR draft",
            "_colour_unresolved": unresolved, "_provenance": provenance}


def run_pages(bk, pages: list[int], *, image_dir: Path | None = None, base_url: str | None = None,
              model: str | None = None, force: bool = False) -> dict:
    settings = bk.config.get("ocr", {}).get("dots", {})
    endpoint = base_url or os.environ.get("DOTS_BASE_URL") or settings.get("base_url")
    if not endpoint:
        raise ValueError("set DOTS_BASE_URL or --base-url to a running dots model server (ending in /v1)")
    model = model or settings.get("model", "dots-studio/dots.mocr")
    parameters = {"model": model, "temperature": 0.1, "top_p": 0.9,
                  "max_completion_tokens": int(os.environ.get("DOTS_MAX_COMPLETION_TOKENS") or settings.get("max_completion_tokens", 32768)),
                  "max_pixels": int(os.environ.get("DOTS_MAX_PIXELS") or settings.get("max_pixels", 11289600)), "prompt_version": PROMPT_VERSION,
                  "prompt_sha256": json_hash(PROMPT), "model_revision": os.environ.get("DOTS_MODEL_REVISION") or settings.get("model_revision", "unrecorded")}
    images = image_dir or bk.page_images
    summary = {"requested": pages, "completed": [], "cached": [], "failed": {}}
    folder = bk.work / "candidates" / "dots"
    for page in pages:
        stem = f"page_{page:03d}"
        image_path = next((images / (stem + ext) for ext in (".png", ".webp", ".jpg", ".jpeg") if (images / (stem + ext)).is_file()), None)
        if image_path is None:
            summary["failed"][str(page)] = "page image is missing"
            continue
        layout_path = bk.work / "layout" / f"{stem}.json"
        key = json_hash({"parameters": parameters, "image": file_hash(image_path), "layout": file_hash(layout_path)})
        destination = folder / f"{stem}.{key[:16]}.json"
        if destination.exists() and not force:
            summary["cached"].append(page)
            continue
        raw = None
        started = time.monotonic()
        try:
            encoded, original, sent = prepare_image(image_path, parameters["max_pixels"])
            payload = {k: parameters[k] for k in ("model", "temperature", "top_p", "max_completion_tokens")}
            payload["messages"] = [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": encoded}},
                {"type": "text", "text": "<|img|><|imgpad|><|endofimg|>" + PROMPT}]}]
            raw = request_completion(endpoint, payload, timeout=float(settings.get("timeout", 180)),
                                     retries=int(settings.get("retries", 2)), api_key=os.environ.get("DOTS_API_KEY"))
            choice = raw["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise ValueError("model did not finish normally; output may be truncated")
            cells = parse_layout(choice["message"]["content"], original, sent)
            provenance = {**parameters, "cache_key": key, "source_sha256": file_hash(image_path),
                          "image_path": str(image_path), "source_dimensions": original, "input_dimensions": sent,
                          "elapsed_seconds": round(time.monotonic() - started, 3),
                          "created_at": datetime.now(timezone.utc).isoformat(),
                          "derivative_input": images.resolve() != bk.page_images.resolve()}
            transcript = candidate_transcript(page, cells, read_json(layout_path, {}), provenance)
            atomic_json(destination, {"status": "draft", "raw_response": raw, "layout": cells, "transcript": transcript})
            summary["completed"].append(page)
            print(f"page {page}: dots candidate saved ({len(cells)} regions)", flush=True)
        except (ValueError, KeyError, IndexError, TypeError, RuntimeError, OSError) as error:
            summary["failed"][str(page)] = str(error)
            atomic_json(folder / f"{stem}.{key[:16]}.error.json", {"status": "failed", "page": page,
                        "error": str(error), "raw_response": raw, "parameters": parameters})
    atomic_json(folder / "run-summary.json", summary)
    return summary
