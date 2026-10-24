# Book workflow: scan → OCR → browser review → app JSON

The same steps work for every book. Commands run from `/home/gabar/extraction-tool`.
`<book>` is the book folder, e.g. `books/wudase-mariam`.

| Step | Where | Command / action | Output |
| --- | --- | --- | --- |
| 1. Set up the book | local | Create `<book>/book.json` (copy Wudase Mariam's and change id, titles, sections) and put the PDF in `<book>/source/` | |
| 2. Render pages | local | `.venv/bin/python -m engine <book> --stage render` | `work/page_images/` (300 DPI) |
| 3. Split red/black ink | local | `.venv/bin/python -m engine <book> --stage split` | `work/red_mask/`, blank/illustration classification |
| 4. OCR the text | GPU host, needs `sudo` | `bash scripts/start-local-dots.sh <book>` | `work/candidates/dots/` |
| 5. Read the red ink | local, server running | `DOTS_BASE_URL=http://127.0.0.1:8001/v1 .venv/bin/python -m engine <book> --stage colour` | `work/candidates/dots-red/` |
| 6. Adopt as editor baseline | local | `.venv/bin/python -m engine <book> --stage adopt` | `work/transcripts/` (old versions kept in `work/transcripts-legacy/`) |
| 7. Publish to the editor | local → GitHub → Netlify | `npm run build:assets`, then commit and push `main` | live editor updates automatically |
| 8. Proofread | browser | Correct text and colour, **Save**, then **Approve** each page | edits stored in Netlify |
| 9. Bring edits back | browser → local | **Export all saved changes**, then `.venv/bin/python -m engine.import_review <book> <export.json>` | `work/transcripts/`, `work/review.json` |
| 10. Build the app file | local | `.venv/bin/python -m engine <book> --stage verify`, then `--stage build --require-ready` | `<book>/out/<book_id>.json` |

Stop the GPU server when finished: `sudo docker stop extraction-dots-mocr`.

## What each automatic step does to the text

- **OCR (dots.mocr)** reads each page as layout blocks in reading order. Pages are sent at
  about 3.2 MP (≈185 DPI); see `DOTS-USAGE.md` for the GPU settings.
- **Red ink** is decided by pixels, never by the OCR model: each red region from the mask is
  cropped and read, then located in the page text. Red is widened to whole words. A red
  reading that cannot be placed is listed under *Uncertain readings* in the editor.
- **Adopt** also:
  - moves running page headers out of the text (shown as a note in the editor);
  - drops text matching `ocr.dots.ignore_text` patterns in `book.json` (watermarks);
  - moves a leading Ethiopic numeral into the verse-number field;
  - normalises `::` / `፡፡` to `።` and joins printed line wraps.
- Pages a proofreader saved in the hosted editor keep the proofreader's version, because
  hosted edits always win over the checked-in baseline.

## Per-book settings (`book.json` → `ocr.dots`)

| Key | Purpose |
| --- | --- |
| `ignore_text` | Regex patterns for watermark/publisher text to drop |
| `max_pixels`, `max_completion_tokens`, `timeout` | Must match the launcher's server settings |

## Not yet multi-book

The hosted editor still serves one book (`BOOK_ID` in `netlify/lib/validation.mjs`, paths in
`scripts/build_netlify_site.mjs`). Before the second book goes live, the editor needs a
book picker and per-book routes; storage keys are already prefixed by book id.
