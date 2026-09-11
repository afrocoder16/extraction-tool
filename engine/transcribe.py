"""Transcribe pages with Claude vision, guided by the deterministic red mask.

Tesseract's Amharic recognition is not reliable enough for this scan (it renders
ለምኝልን as ሰምኘነልን), so the text comes from Claude instead. Each request carries:

  1. the original colour page image,
  2. the red-mask overlay, so the model can see exactly which glyphs are red,
  3. the Tesseract line/word geometry with per-word is_red flags as text context.

Structured outputs pin the response to the block schema, so the result is always
parseable - no prose to strip, no partial JSON to repair.

Responses are cached to work/transcripts/page_NNN.json; a page with a cached
transcript is skipped unless --force is passed, which keeps re-runs cheap.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path

import anthropic
import cv2

from .console import configure_utf8_stdio

DEFAULT_MODEL = "claude-opus-5"

# The 300 DPI page is far more resolution than the model needs, and image tokens
# scale with area. 1500px on the long edge keeps every glyph legible.
MAX_IMAGE_EDGE = 1500

SYSTEM_PROMPT = """\
You are transcribing scanned pages of a printed Ethiopian Orthodox Tewahedo prayer \
book (ውዳሴ ማርያም / Wudase Mariam) into structured JSON. Accuracy matters more than \
anything else: this is liturgical text that people pray, so a wrong character is a \
wrong prayer.

You receive two images of the same page:
  - IMAGE 1: the original colour scan.
  - IMAGE 2: a diagnostic overlay where every pixel of true red ink has been \
painted bright green, and decorative red ornaments have been painted magenta. This \
overlay was produced by deterministic colour analysis and is authoritative about \
WHICH WORDS ARE RED. Read the words from IMAGE 1; decide their colour from IMAGE 2.

You also receive Tesseract's line and word geometry, including a per-word is_red \
flag measured from the same mask. Tesseract's TEXT is frequently wrong - ignore it \
as a source of wording. Use it only as a hint about line breaks and word order.

Rules, in order of importance:

1. Transcribe exactly what is printed. Do not normalise spelling, do not \
modernise, do not correct what looks like an archaism. Reproduce the page.
2. A span is red if and only if the corresponding glyphs are green in IMAGE 2. \
Never guess red from meaning. Divine names and refrains are often red, but only \
the overlay decides.
3. Preserve Ethiopic punctuation exactly: ፡ ። ፣ ፤ ፥ ፦ ፧ ፨ and the word separator.
4. Preserve Ethiopic numerals (፩ ፪ ፫ ፬ ...). Verse numbers are printed as a small \
numeral, often with an overline, at the start of a stanza - put it in verse_num_am \
and do NOT repeat it inside the spans.
5. The scans have heavy bleed-through: faint grey or pink mirrored text from the \
reverse side of the page. IGNORE it completely. Only transcribe ink that is sharp \
and dark or sharp and red.
6. Decorative ornament borders (magenta in IMAGE 2) are not text. Emit them as a \
block with type "ornament" and an empty spans array.
7. Output ONLY Ethiopic script, spaces, and Ethiopic punctuation in span text. If \
you find yourself about to write a Latin letter or an Arabic numeral, you have \
misread a glyph - look again.
8. Do not invent, summarise, or complete text that is cut off at the page edge. \
Transcribe what is visible.
9. Merge consecutive spans of the same colour into one span. Keep the spaces \
between words inside the spans so that concatenating every span reproduces the \
line exactly.
10. This printing contains genuine typographical errors. Never silently correct \
one. Transcribe the word exactly as printed in the spans, and additionally report \
it in the "corrections" array with what you believe the correct form is. A human \
reviews every suggestion before it is applied, so a suggestion costs nothing and a \
silent correction is unrecoverable.
11. If a visible word cannot be read confidently, put your best literal reading in \
the spans and add the exact same string to "uncertain", with a short explanation. \
Do not hide uncertain readings in notes and do not resolve them from another edition.
12. If the scan cuts text off at the top or bottom, do not reconstruct it. Omit the \
missing text and add one "clipped" entry per affected line, naming the edge.

What belongs in "corrections":
  - A missing, extra, or transposed letter in a word that is otherwise standard \
(e.g. printed የእዚአብሔር where the standard form is የእግዚአብሔር).
  - A wrong but visually similar character (ሠ/ሥ, ጸ/ፀ, ሀ/ሐ/ኀ, አ/ዐ) where the \
surrounding word makes the intended form clear.
Only report a word you are confident is a misprint rather than an accepted \
variant. If you are unsure whether a form is an error or simply archaic, leave it \
out of "corrections" and describe the doubt in "notes" instead - Ge'ez and older \
Amharic orthography vary legitimately, and normalising a valid archaism is itself \
an error.
Do NOT report: differences in spacing, punctuation, or your own reading \
uncertainty about a smudged glyph.

Block types:
  - "title": a section heading, usually centred.
  - "verse": a numbered stanza of the prayer.
  - "prayer": unnumbered prayer text.
  - "instruction": a rubric telling the reader what to do or say.
  - "ornament": a decorative border. No text.
"""

PAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "page": {"type": "integer"},
        "notes": {
            "type": "string",
            "description": (
                "Anything a human proofreader should check on this page: damaged "
                "glyphs, ambiguous colour, text lost to the gutter. Empty if clean."
            ),
        },
        "corrections": {
            "type": "array",
            "description": (
                "Suspected misprints. The spans keep the printed form; these are "
                "suggestions a human accepts or rejects in the review UI."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "printed": {
                        "type": "string",
                        "description": "The word exactly as printed, as it appears in the spans.",
                    },
                    "suggested": {
                        "type": "string",
                        "description": "The standard form you believe was intended.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Short plain reason, e.g. 'missing ግ' or 'ሠ should be ሥ'.",
                    },
                },
                "required": ["printed", "suggested", "reason"],
                "additionalProperties": False,
            },
        },
        "uncertain": {
            "type": "array",
            "description": (
                "Words that could not be read confidently. The text must exactly "
                "match the best literal reading placed in a span."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "why": {"type": "string"},
                },
                "required": ["text", "why"],
                "additionalProperties": False,
            },
        },
        "clipped": {
            "type": "array",
            "description": "Lines whose text is lost beyond a scan edge.",
            "items": {
                "type": "object",
                "properties": {
                    "edge": {"type": "string", "enum": ["top", "bottom"]},
                    "note": {"type": "string"},
                },
                "required": ["edge", "note"],
                "additionalProperties": False,
            },
        },
        "blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["title", "verse", "prayer", "instruction", "ornament"],
                    },
                    "verse_num_am": {
                        "type": "string",
                        "description": "Ethiopic numeral of the verse, or empty string.",
                    },
                    "spans": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "t": {"type": "string"},
                                "c": {"type": "string", "enum": ["black", "red"]},
                            },
                            "required": ["t", "c"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["type", "verse_num_am", "spans"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["page", "notes", "corrections", "uncertain", "clipped", "blocks"],
    "additionalProperties": False,
}


def encode_image(path: Path, max_edge: int = MAX_IMAGE_EDGE) -> str:
    """Downscale to a sane token budget and return base64 JPEG.

    Image tokens are billed on dimensions, not bytes, so JPEG costs exactly what
    PNG would while uploading roughly ten times faster. Quality 92 is well clear
    of any artefact that could blur the boundary between a red and a black glyph.
    """
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(f"missing image: {path}")

    height, width = image.shape[:2]
    scale = max_edge / float(max(height, width))
    if scale < 1.0:
        image = cv2.resize(
            image,
            (int(round(width * scale)), int(round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )

    ok, buffer = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok:
        raise RuntimeError(f"could not encode {path}")
    return base64.standard_b64encode(buffer.tobytes()).decode("ascii")


def format_layout_hint(layout: dict) -> str:
    """Render the Tesseract geometry as compact text context for the model."""
    lines = [
        f"Tesseract found {layout['line_count']} lines, "
        f"{layout['word_count']} words, {layout['red_word_count']} of them red.",
        "Per line (Tesseract's text is unreliable; the [RED]/[BLACK] flags are not):",
    ]
    for line in layout["lines"]:
        parts = []
        for word in line["words"]:
            marker = "RED" if word["is_red"] else "BLACK"
            parts.append(f"{word['text']}[{marker}]")
        hint = " (looks like a centred title)" if line["looks_like_title"] else ""
        lines.append(f"  line {line['order']}{hint}: {' '.join(parts)}")
    return "\n".join(lines)


def transcribe_page(
    client: anthropic.Anthropic, page: int, work: Path, effort: str,
    model: str = DEFAULT_MODEL, page_images: Path | None = None
) -> dict:
    name_png = f"page_{page:03d}.png"
    layout_path = work / "layout" / f"page_{page:03d}.json"
    if not layout_path.exists():
        raise FileNotFoundError(f"run layout first; missing {layout_path}")
    layout = json.loads(layout_path.read_text(encoding="utf-8"))

    images = page_images or (work / "page_images")
    original_b64 = encode_image(images / name_png)
    overlay_b64 = encode_image(work / "debug" / name_png)

    content = [
        {"type": "text", "text": "IMAGE 1 - the original colour scan:"},
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": original_b64},
        },
        {
            "type": "text",
            "text": (
                "IMAGE 2 - the red-ink overlay. Green = true red ink. "
                "Magenta = decorative ornament, not text. Everything else is black "
                "ink, paper, or bleed-through:"
            ),
        },
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": overlay_b64},
        },
        {
            "type": "text",
            "text": (
                f"Tesseract geometry for page {page}:\n{format_layout_hint(layout)}\n\n"
                f"Transcribe page {page} into the required JSON structure."
            ),
        },
    ]

    with client.messages.stream(
        model=model,
        max_tokens=16000,
        output_config={
            "effort": effort,
            "format": {"type": "json_schema", "schema": PAGE_SCHEMA},
        },
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
    ) as stream:
        message = stream.get_final_message()

    if message.stop_reason == "refusal":
        raise RuntimeError(f"page {page}: request was declined by safety classifiers")
    if message.stop_reason == "max_tokens":
        raise RuntimeError(
            f"page {page}: response hit max_tokens and may contain incomplete JSON"
        )

    text = next((b.text for b in message.content if b.type == "text"), None)
    if text is None:
        raise RuntimeError(f"page {page}: no text block in response")

    result = json.loads(text)
    result["page"] = page
    result["_usage"] = {
        "input_tokens": message.usage.input_tokens,
        "output_tokens": message.usage.output_tokens,
        "stop_reason": message.stop_reason,
    }
    return result


def protected_pages(work: Path) -> dict[int, str]:
    """Pages a human has already worked on, which a re-run must not overwrite.

    Two independent signals, because either one on its own can be missed: a page
    marked approved in review.json, and a page with a `.original.json` beside it
    (written the first time the UI saved an edit).
    """
    protected: dict[int, str] = {}

    review = work / "review.json"
    if review.exists():
        entries = json.loads(review.read_text(encoding="utf-8")).get("entries", {})
        for page_str, entry in entries.items():
            if entry.get("status") == "approved":
                protected[int(page_str)] = "approved"

    for backup in (work / "transcripts").glob("page_*.original.json"):
        page = int(backup.name.split("_")[1].split(".")[0])
        protected.setdefault(page, "edited by hand")

    return protected


def transcribe_pages(
    pages: list[int], work: Path, effort: str, force: bool,
    model: str = DEFAULT_MODEL, page_images: Path | None = None
) -> list[dict]:
    out_dir = work / "transcripts"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Proofreading is the expensive human input in this pipeline; never let a
    # re-run silently destroy it.
    if not force:
        protected = protected_pages(work)
        skipped = [p for p in pages if p in protected]
        if skipped:
            for page in skipped:
                print(f"page {page}: SKIPPED — {protected[page]}", file=sys.stderr)
            print(
                f"{len(skipped)} page(s) protected from overwrite; pass --force to "
                f"re-transcribe them anyway.",
                file=sys.stderr,
            )
        pages = [p for p in pages if p not in protected]
        if not pages:
            return []

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit(
            "ANTHROPIC_API_KEY is not set. In PowerShell, set it for the current "
            "terminal with `$env:ANTHROPIC_API_KEY = \"...\"` before running the "
            "transcription stage."
        )

    client = anthropic.Anthropic()
    results = []
    for page in pages:
        cache_path = out_dir / f"page_{page:03d}.json"
        if cache_path.exists() and not force:
            results.append(json.loads(cache_path.read_text(encoding="utf-8")))
            print(f"page {page}: cached", file=sys.stderr)
            continue

        result = transcribe_page(client, page, work, effort, model, page_images)
        cache_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        usage = result["_usage"]
        print(
            f"page {page}: {len(result['blocks'])} blocks, "
            f"{usage['input_tokens']} in / {usage['output_tokens']} out",
            file=sys.stderr,
        )
        results.append(result)
    return results


def main() -> None:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--work", "--v2-root", dest="work", type=Path, required=True,
        help="book work directory (legacy name: --v2-root)",
    )
    parser.add_argument("--pages", required=True)
    parser.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--force", action="store_true", help="ignore cached transcripts")
    args = parser.parse_args()

    pages = [int(p) for p in args.pages.split(",") if p.strip()]
    transcribe_pages(pages, args.work, args.effort, args.force, model=args.model)


if __name__ == "__main__":
    main()
