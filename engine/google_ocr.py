"""Transcribe pages with Google Cloud Vision OCR.

Why an OCR engine rather than a vision language model: an OCR engine reports what
it saw and how confident it was. A language model always produces something
fluent, and on the hardest pages of this book that meant plausible Amharic prose
that was never on the page - `ለተጠማ ያጠጣ ከመዓር` came back as `ስተጠም የጠጣ ከመጠጥ`.
Misreadings are recoverable and get flagged; fabrication is neither.

Vision returns a word-level bounding box for every word, which is exactly what the
red mask needs. So colour is still decided by pixels, never by the recogniser:

    Vision  ->  what the words say  (+ where each word sits)
    mask    ->  which of those words are red

That leaves no model anywhere in the critical path.

Amharic (`am`, script Ethi) is listed by Google as *experimental* - supported on
both text endpoints but not regularly evaluated - so quality on this 1960s print
has to be measured, not assumed. `--compare` exists for that.

Auth is a plain API key, passed as GOOGLE_API_KEY or --api-key. The REST endpoint
takes one directly, so no service-account JSON is needed.

    python -m engine.google_ocr books/wudase-mariam --pages 159,183,137 --compare
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import cv2
import numpy as np

from . import book as book_mod
from .console import configure_utf8_stdio
from .layout import RED_WORD_THRESHOLD, measure_redness

ENDPOINT = "https://vision.googleapis.com/v1/images:annotate"

# Amharic. Passing the hint matters: without it Vision often decides an Ethiopic
# page is a better-supported script and returns confident nonsense.
LANGUAGE_HINTS = ["am"]

# Vision reports a confidence per word. Below this the reading is offered to the
# reviewer rather than presented as fact.
LOW_CONFIDENCE = 0.80

# Which rendered layer to send. The black layer has bleed-through suppressed and
# red ink erased, so it is a far cleaner image than the raw scan - but it is also
# missing the red words entirely, so the red layer is read separately and the two
# are interleaved by position.
SOURCE_LAYERS = ("black_layer", "red_layer")


def _annotate(image_path: Path, api_key: str, timeout: int = 90) -> dict:
    """One DOCUMENT_TEXT_DETECTION call. Returns the raw response for one image."""
    payload = {
        "requests": [
            {
                "image": {"content": base64.b64encode(image_path.read_bytes()).decode()},
                "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
                "imageContext": {"languageHints": LANGUAGE_HINTS},
            }
        ]
    }
    request = urllib.request.Request(
        f"{ENDPOINT}?key={api_key}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(f"Vision API {err.code}: {detail}") from None

    result = body.get("responses", [{}])[0]
    if "error" in result:
        raise RuntimeError(f"Vision API: {result['error'].get('message')}")
    return result


def _words_from_response(response: dict) -> list[dict]:
    """Flatten Vision's page/block/paragraph/word/symbol tree into words.

    Vision puts punctuation and line breaks on the symbol, not the word, so the
    break type has to be read off the last symbol to know whether a space or a
    newline follows.
    """
    words: list[dict] = []
    annotation = response.get("fullTextAnnotation")
    if not annotation:
        return words

    for page in annotation.get("pages", []):
        for block in page.get("blocks", []):
            for paragraph in block.get("paragraphs", []):
                for word in paragraph.get("words", []):
                    symbols = word.get("symbols", [])
                    text = "".join(s.get("text", "") for s in symbols)
                    if not text:
                        continue

                    # Trailing whitespace/newline lives on the final symbol.
                    break_type = ""
                    if symbols:
                        detected = symbols[-1].get("property", {}).get("detectedBreak", {})
                        break_type = detected.get("type", "")

                    vertices = word.get("boundingBox", {}).get("vertices", [])
                    xs = [v.get("x", 0) for v in vertices] or [0]
                    ys = [v.get("y", 0) for v in vertices] or [0]

                    words.append(
                        {
                            "text": text,
                            "left": min(xs),
                            "top": min(ys),
                            "width": max(xs) - min(xs),
                            "height": max(ys) - min(ys),
                            "confidence": float(word.get("confidence", 0.0)),
                            "break": break_type,
                            "block": id(block),
                        }
                    )
    return words


def _colour_words(words: list[dict], bk: book_mod.Book, page: int) -> list[dict]:
    """Attach red/black to each word by measuring the mask under its box."""
    name = f"page_{page:03d}.png"
    red_mask = cv2.imread(str(bk.dir("red_mask") / name), cv2.IMREAD_GRAYSCALE)
    black_layer = cv2.imread(str(bk.dir("black_layer") / name), cv2.IMREAD_GRAYSCALE)
    red_layer = cv2.imread(str(bk.dir("red_layer") / name), cv2.IMREAD_GRAYSCALE)
    if red_mask is None or black_layer is None or red_layer is None:
        raise FileNotFoundError(f"run --stage split first; missing layers for page {page}")

    all_ink = cv2.bitwise_or(
        (black_layer < 128).astype(np.uint8) * 255,
        (red_layer < 128).astype(np.uint8) * 255,
    )
    for word in words:
        redness = measure_redness(word, red_mask, all_ink)
        word["redness"] = round(redness, 3)
        word["is_red"] = redness >= RED_WORD_THRESHOLD
    return words


def _spans_from_words(words: list[dict]) -> list[dict]:
    """Merge consecutive same-colour words into spans, restoring the spacing."""
    spans: list[dict] = []
    for i, word in enumerate(words):
        colour = "red" if word["is_red"] else "black"
        text = word["text"]
        # SPACE/EOL_SURE_SPACE mean a space follows; LINE_BREAK a newline.
        if word["break"] in ("SPACE", "EOL_SURE_SPACE"):
            text += " "
        elif word["break"] in ("LINE_BREAK", "HYPHEN"):
            text += " "
        elif i < len(words) - 1:
            text += ""

        if spans and spans[-1]["c"] == colour:
            spans[-1]["t"] += text
        else:
            spans.append({"t": text, "c": colour})

    for span in spans:
        span["t"] = span["t"].replace("  ", " ")
    return [s for s in spans if s["t"].strip()]


def transcribe_page(bk: book_mod.Book, page: int, api_key: str) -> dict:
    """OCR one page and emit a transcript in the engine's schema."""
    image = bk.page_images / f"page_{page:03d}.png"
    response = _annotate(image, api_key)
    words = _words_from_response(response)
    if not words:
        return {
            "page": page,
            "notes": "Google Vision returned no text for this page.",
            "_source": "google-cloud-vision DOCUMENT_TEXT_DETECTION (am)",
            "corrections": [],
            "uncertain": [],
            "clipped": [],
            "blocks": [],
        }

    words = _colour_words(words, bk, page)

    # Vision's own block boundaries are a reasonable paragraph proxy.
    blocks: list[dict] = []
    current_id = None
    for word in words:
        if word["block"] != current_id:
            blocks.append({"type": "prayer", "verse_num_am": "", "_words": []})
            current_id = word["block"]
        blocks[-1]["_words"].append(word)

    out_blocks = []
    for block in blocks:
        spans = _spans_from_words(block["_words"])
        if spans:
            out_blocks.append(
                {"type": block["type"], "verse_num_am": "", "spans": spans}
            )

    # Anything Vision was unsure of goes to the reviewer rather than being asserted.
    uncertain = [
        {"text": w["text"], "why": f"Vision confidence {w['confidence']:.2f}"}
        for w in words
        if 0 < w["confidence"] < LOW_CONFIDENCE
    ]

    confidences = [w["confidence"] for w in words if w["confidence"] > 0]
    mean_conf = sum(confidences) / len(confidences) if confidences else 0.0

    return {
        "page": page,
        "notes": (
            f"Transcribed by Google Cloud Vision (Amharic is an experimental "
            f"language for Vision). Mean word confidence {mean_conf:.2f} over "
            f"{len(words)} words. Block types and verse numerals are not detected "
            f"by OCR and need setting during review."
        ),
        "_source": "google-cloud-vision DOCUMENT_TEXT_DETECTION (am)",
        "_mean_confidence": round(mean_conf, 3),
        "corrections": [],
        "uncertain": uncertain[:40],
        "clipped": [],
        "blocks": out_blocks,
    }


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book", type=Path)
    parser.add_argument("--pages", required=True, help="e.g. 159,183,137 or 121-162")
    parser.add_argument("--api-key", default=os.environ.get("GOOGLE_API_KEY", ""))
    parser.add_argument("--compare", action="store_true",
                        help="print the text instead of writing transcripts")
    parser.add_argument("--force", action="store_true",
                        help="overwrite existing transcripts")
    args = parser.parse_args(argv)

    if not args.api_key:
        print("No API key. Pass --api-key or set GOOGLE_API_KEY.", file=sys.stderr)
        return 1

    bk = book_mod.load(args.book)
    pages: list[int] = []
    for part in args.pages.split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            pages.extend(range(int(a), int(b) + 1))
        elif part.strip():
            pages.append(int(part))

    out_dir = bk.dir("transcripts")
    out_dir.mkdir(parents=True, exist_ok=True)

    for page in sorted(set(pages)):
        try:
            result = transcribe_page(bk, page, args.api_key)
        except (RuntimeError, FileNotFoundError) as err:
            print(f"page {page}: {err}", file=sys.stderr)
            continue

        text = " ".join(s["t"] for b in result["blocks"] for s in b["spans"])
        red = sum(1 for b in result["blocks"] for s in b["spans"] if s["c"] == "red")
        print(f"\n=== page {page} — confidence {result.get('_mean_confidence')}, "
              f"{len(result['blocks'])} blocks, {red} red spans ===")
        print(text[:700])

        if not args.compare:
            target = out_dir / f"page_{page:03d}.json"
            if target.exists() and not args.force:
                print(f"  (exists, skipped — use --force)", file=sys.stderr)
                continue
            target.write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
