# Scanned-book extraction engine

Turns scanned photographs of printed liturgical books into structured JSON that
preserves **red ink**, which in Ethiopian Orthodox prayer books is semantic: it
marks divine names and the congregation's responses. Losing it loses the prayer's
structure.

The engine is book-agnostic. Adding another PDF is a new folder and a config file,
not a code change.

---

## How it works

Ordinary OCR converts the page to greyscale first, which throws the colour away
before a single character is read. This engine separates the ink by colour *before*
recognition, and keeps the two jobs apart:

| Job | Done by | Why |
| --- | --- | --- |
| **Which words are red** | Pixel colour analysis (HSV mask) | Deterministic. Cannot be corrupted by an OCR mistake. |
| **What the words say** | Claude vision, guided by that mask | Tesseract's Amharic is not accurate enough for liturgical text. |

Because the two are independent, they cross-check each other: `verify` compares the
proportion of red *characters* in the text against the proportion of red *ink* on
the page, and flags any page where they disagree.

The scans also carry three things that must never become text, all handled
automatically: blank verso sheets (87 of 218 in Wudase Mariam), full-colour
illustration plates, and bleed-through from the reverse side of the page.

---

## Quick start

```bash
python -m venv .venv
# PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt

# see what books exist
python -m engine --list

# run everything for one book
python -m engine books/wudase-mariam --stage all

# proofread the result in a browser
python -m engine.serve books/wudase-mariam
```

`ANTHROPIC_API_KEY` is needed for the `ocr` stage only. Every other stage runs
offline and free.

On PowerShell, set it for the current terminal with:

```powershell
$env:ANTHROPIC_API_KEY = "your-key"
```

Do not put an API key in `book.json` or commit it to the project.

---

## Share the review UI with Netlify

This repository includes a Netlify build for the Wudase Mariam review screen.
It publishes compact WebP review images, requires an invited user to sign in,
and saves page edits and approve/needs-fix decisions in Netlify Blobs. The
extractor and the original 300-DPI images continue to run and stay on your
computer.

For the complete owner and proofreader workflow, see
[Netlify review workflow](docs/netlify-review-workflow.md).

### Deploy from GitHub

1. In Netlify, choose **Add new project** and **Import an existing project**.
2. Select this GitHub repository. `netlify.toml` already supplies the build
   command, publish folder, functions folder, and Node version, so accept those
   values and deploy.
3. In the new site's configuration, enable **Identity**.
4. Set Identity registration to **Invite only**. Do not enable open sign-ups.
5. Open the Identity user list and invite the proofreader's email address.
6. Send the proofreader the normal Netlify site URL. Their invitation link lets
   them choose a password; afterward the same site URL opens the editor.

Edits made with the page-level **Save** button and review decisions are shared
across devices. Use **Export all saved changes** in the sidebar to download
`wudase_mariam_review_export.json`. That file contains every hosted transcript
edit, the review decisions, timestamps, and editor identities.

Netlify setup references:

- <https://docs.netlify.com/manage/security/secure-access-to-sites/identity/overview/>
- <https://docs.netlify.com/build/data-and-storage/netlify-blobs/>

### Test the web build locally

```bash
npm install
npm test
npm run build
```

The generated site is written to `netlify-dist/` and is intentionally ignored
by Git. If the source page PNGs or overlays are regenerated, refresh the
committed browser-size copies before pushing:

```bash
npm run build:assets
```

The hosted app contains the current extraction baseline. All 129 content pages
now have editable text, but many later pages are explicitly marked as Tesseract
drafts. Those drafts must be read word by word against the scan before the book
can be considered complete.

---

## Stages

Each stage depends on the one before it, so `--stage all` is the normal
invocation. Run them individually while iterating.

| Stage | Cost | Produces |
| --- | --- | --- |
| `render` | free | `work/page_images/` — the PDF at 300 DPI, in colour |
| `split` | free | red mask, black layer, red layer, review overlay; detects blank and illustration pages |
| `layout` | free | word boxes from Tesseract, each flagged red or black **from the mask, not from OCR** |
| `audit` | free | `work/red_audit.md` — red coverage per page, listing pages to eyeball |
| `ocr` | **API** | `work/transcripts/page_NNN.json` — the text, with red spans and suspected misprints |
| `verify` | free | `work/verification.json` — cross-checks and per-page review flags |
| `build` | free | `out/<book_id>.json` — the finished book |

```bash
python -m engine books/wudase-mariam --stage split --pages 21,147
python -m engine books/wudase-mariam --stage ocr   --pages 21-40
```

`--pages` accepts `all`, `21`, `21,147`, or `21-26,99`.

Before shipping a build, require the engine to prove that every content page has
a transcript, verification result, and human approval:

```bash
python -m engine books/wudase-mariam --stage build --require-ready
```

An ordinary `build` is still useful during proofreading, but its JSON carries a
top-level `quality` object and `production_ready: false` until those conditions are
met. `--require-ready` exits without overwriting the output when work remains.

---

## Adding a new book

```
books/<slug>/
  book.json
  source/<the>.pdf
```

```json
{
  "book_id": "my_book",
  "title_am": "…",
  "title_en": "…",
  "source_pdf": "source/my-book.pdf",
  "render": { "dpi": 300 },
  "ocr": { "tesseract_lang": "amh", "model": "claude-opus-5", "effort": "high" },
  "sections": []
}
```

Then `python -m engine books/<slug> --stage all`.

`sections` is the table used to split the book into parts. Each entry needs
`section_id`, `order`, `title_am`, `title_en`, and `required_any` — a list of
keyword groups that must *all* appear in a title block for it to open that section.
Leave it empty and the book is emitted as one section.

---

## Proofreading

```bash
python -m engine.serve books/wudase-mariam    # http://127.0.0.1:8000/
```

The review UI shows each page's scan beside its transcribed text.

- **Hold `O`** to flash the red-ink overlay over the scan — green is detected red
  ink, magenta is a rejected ornament. The fastest way to confirm the mask.
- **Split view** shows the original and the overlay side by side on the same words.
- Text is directly editable. Select and use **Mark red** / **Mark black** to change
  a word's colour.
- **Suspected misprints** appear in a yellow box with Accept / Keep as printed.
  Nothing is corrected without a human decision — see below.
- **Approve** / **Needs fix** per page, with notes. `⌃S` saves.

Keyboard: `←` `→` pages · `O` overlay · `A` approve · `F` flag · `⌃S` save.

### Misprints are suggested, never applied

The books contain genuine typographical errors. The engine transcribes **exactly
what is printed** and reports suspected errors separately, for a human to accept or
reject. Silently "correcting" a prayer is unrecoverable; a rejected suggestion costs
one click.

The prompt is also told to leave alone anything that might be legitimate archaic
orthography — Ge'ez and older Amharic vary, and normalising a valid old form is
itself an error.

### Your corrections are protected

- The first edit to a page copies the untouched version to `page_NNN.original.json`,
  so every change is revertible and diffable.
- `--stage ocr` **skips** any page you have approved or edited. `--force` overrides.

---

## Layout

```
engine/                  the pipeline — knows nothing about any particular book
  book.py                config + path resolution
  render.py              PDF -> page images
  color_split.py         red/black separation, blank + illustration detection
  layout.py              word geometry, deterministic red flags
  transcribe.py          Claude vision, prompt and schema
  verify.py              mask-vs-text cross-checks
  build_book.py          section assembly, final JSON
  serve.py               review server
  review.html            review UI
  cli.py                 python -m engine
  tessdata/              Tesseract language data

books/<slug>/
  book.json              identity, sections, settings
  source/                the PDF
  work/                  everything derived — safe to delete and regenerate
  out/                   the finished JSON

archive/v1/              the superseded first attempt, kept for reference
```

Large generated files under `work/` are gitignored. Transcripts, review status,
and the small verification inputs used by the hosted reviewer are tracked.

---

## Output

```json
{
  "book_id": "wudase_mariam",
  "sections": [
    {
      "section_id": "monday_wudase_mariam",
      "title_am": "የሰኞ ውዳሴ ማርያም",
      "start_page": 21,
      "blocks": [
        {
          "type": "verse",
          "verse_num_am": "፬",
          "page": 21,
          "spans": [
            { "t": "ነቢዩ ኢሳያስ በመንፈስ ቅዱስ ", "c": "black" },
            { "t": "የዐማኑኤልን", "c": "red" },
            { "t": " ምሥጢር አየ …", "c": "black" },
            { "t": "ቅድስት ሆይ ለምኝልን።", "c": "red" }
          ],
          "text": "ነቢዩ ኢሳያስ በመንፈስ ቅዱስ የዐማኑኤልን ምሥጢር አየ …",
          "review_flags": [],
          "review_status": "approved"
        }
      ]
    }
  ]
}
```

`spans` render directly as Flutter `TextSpan`s. `text` is the same content flattened
for search indexing.

---

## Why v1 was replaced

`archive/v1/` holds the first attempt, kept so its output can be compared. It is not
usable as a base:

- It converted every page to greyscale before OCR, so **no red survived at all**,
  and it stored no word geometry, so red cannot be recovered from it afterwards.
- Its text is wrong at word level — on one page: `ጸጋተበዛለኝ፡፡` for `ጸጋትበዛለች።`, `#8` for
  the numeral `፱`, and `እጠጋ3፡ኘማአሕጧ..,` where the page reads `ቅድስት ሆይ ለምኝልን።`.
  Book-wide it carries 886 Latin characters inside Ethiopic text.
- 18% of its blocks were OCR'd off blank pages.
