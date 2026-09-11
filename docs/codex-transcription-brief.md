# Transcription brief

You are transcribing scanned pages of a printed Ethiopian Orthodox Tewahedo prayer
book (ውዳሴ ማርያም / *Wudase Mariam*) into JSON.

Accuracy matters more than speed. This is liturgical text that people pray from. A
wrong character is a wrong prayer. If you cannot read something, say so — a visible
gap is recoverable, a confident wrong guess is not.

---

## 0. Before you start — check you can see images

Open this file and describe what you see:

```
books/wudase-mariam/work/page_images/page_021.png
```

You should see a page of Ethiopic text with **some words printed in red**. If you
cannot open or view the image, **stop and say so.** This task is impossible without
vision — do not attempt it from OCR text or filenames.

---

## 1. What you produce

One JSON file per page:

```
books/wudase-mariam/work/transcripts/page_NNN.json
```

`NNN` is the zero-padded page number (`page_027.json`). Write nothing else. Do not
modify any file under `engine/`, `book.json`, or any page you were not assigned.

---

## 2. The two images for each page

| File | What it is |
| --- | --- |
| `work/page_images/page_NNN.png` | The original colour scan. **Read the words from this.** |
| `work/debug/page_NNN.png` | The same page with every pixel of true red ink repainted **bright green**, and decorative red borders repainted **magenta**. **Decide colour from this.** |

The overlay was produced by deterministic pixel analysis, not by a model. It is
**authoritative about which words are red.** Do not second-guess it, and do not
infer red from meaning — divine names and refrains are often red, but only the
overlay decides.

The overlay also shows the black text unchanged, so for most pages you can work
from the overlay alone and open the original only when a glyph is unclear.

---

## 3. File format

```json
{
  "page": 27,
  "notes": "",
  "corrections": [],
  "uncertain": [],
  "clipped": [],
  "blocks": [
    {
      "type": "verse",
      "verse_num_am": "፬",
      "spans": [
        { "t": "ነቢዩ ኢሳያስ በመንፈስ ቅዱስ ", "c": "black" },
        { "t": "የዐማኑኤልን", "c": "red" },
        { "t": " ምሥጢር አየ ስለዚህም ሕፃን ተወለደልን። ", "c": "black" },
        { "t": "ቅድስት ሆይ ለምኝልን።", "c": "red" }
      ]
    }
  ]
}
```

### `blocks[].type`

| Value | Use for |
| --- | --- |
| `title` | A heading, or the running header line at the top of a page |
| `verse` | A numbered stanza of the prayer |
| `prayer` | Unnumbered prayer text |
| `instruction` | A rubric telling the reader what to do (e.g. `፫ ጊዜ በል` — "say it 3 times"), or an editorial footnote |
| `ornament` | A decorative border. `"spans": []` — no text |

### `blocks[].verse_num_am`

The Ethiopic numeral printed at the start of the stanza (`፩ ፪ ፫ ፬ …`), or `""`.
Put it **here only** — never repeat it inside `spans`.

### `blocks[].spans`

An ordered list of runs. `c` is `"black"` or `"red"`.

- Concatenating every `t` in order must reproduce the block exactly, spaces included.
- Merge neighbouring runs of the same colour into one span.
- Keep the spaces **inside** the spans (note the leading/trailing spaces above).

---

## 4. Transcription rules, in priority order

0. **The scan is the only source. Never consult another edition of this text.**
   Do not open a Wudase Mariam PDF, website, or any other copy to resolve a word.
   Other editions differ from this printing, and a word imported from one is a
   silent substitution that no automated check can catch. If a word is unclear,
   zoom into the scan; if it is still unclear, transcribe your best reading and add
   it to `uncertain` (§6b). An honest "unclear" is useful; a confident word from the
   wrong book is not.

   *This is not hypothetical.* On page 27 this printing reads **ተገኘልን**, and an
   external edition's **ተገለጠልን** was written instead — a different word, in a
   prayer, that passed every automated check.

1. **Transcribe exactly what is printed.** Do not normalise spelling, do not
   modernise, do not fix what looks wrong. Reproduce the page.

   This includes orthography. Where the page prints two separate characters, keep
   two — e.g. page 27 prints **የመውለድዋ** (ድ + ዋ), which must not be normalised to the
   combined form **የመውለዷ**. The same applies to spacing: if two words are printed
   run together, transcribe them run together.
2. **A span is red if and only if those glyphs are green in the overlay.**
3. **Preserve Ethiopic punctuation exactly:** `።` `፤` `፣` `፥` `፦` `፧` `፡`
4. **Preserve Ethiopic numerals** (`፩ ፪ ፫ … ፲ ፳ ፻`). Never substitute Arabic digits.
5. **Ignore bleed-through.** These scans show faint grey and pink mirrored text from
   the reverse side of the page. It is not on this page. Only transcribe ink that is
   sharp and dark, or sharp and red.
6. **Ornaments are not text.** A magenta border in the overlay becomes a block of
   `"type": "ornament"` with empty spans.
7. **Output only Ethiopic script, spaces and Ethiopic punctuation** inside `t` — with
   one exception: if the book itself prints Latin letters or Arabic numerals (some
   pages carry a scripture cross-reference footnote like `ሉቃስ ም.1 ቁ.47-55`),
   transcribe them as printed and note it.
8. **Never invent text.** See §6 on clipped lines.

---

## 5. Misprints: suggest, never apply

The book contains genuine typographical errors. **Never silently correct one.**
Transcribe the word as printed in `spans`, and report it in `corrections`:

```json
"corrections": [
  {
    "printed": "ተሰሣ",
    "suggested": "ተነሣ",
    "reason": "ሰ should be ነ — the Creed reads 'ከሙታን ተለይቶ ተነሣ' (rose from the dead)"
  }
]
```

A human accepts or rejects every suggestion in a review UI, so a suggestion costs
one click and a silent correction is unrecoverable.

**Report** a missing/extra/transposed letter in an otherwise standard word, or a
wrong but visually similar character (`ሠ`/`ሥ`, `ጸ`/`ፀ`, `ሀ`/`ሐ`/`ኀ`, `አ`/`ዐ`, `እ`/`አ`)
where the surrounding word makes the intended form clear.

**Do not report** spacing, punctuation, or your own uncertainty about a smudged
glyph.

**If you are unsure whether a form is an error or simply archaic, leave it out of
`corrections` and describe the doubt in `notes` instead.** Ge'ez and older Amharic
orthography vary legitimately, and normalising a valid archaism is itself an error.
If the doubt is about *reading* the glyphs rather than about the spelling, put it in
`uncertain` instead (§6b).

Real examples already found in this book:

| Printed | Should be | Why |
| --- | --- | --- |
| የእ**ዚ**አብሔር | የእ**ግዚ**አብሔር | missing ግ |
| ክ**ስር**ቶስን | ክ**ርስ**ቶስን | ስ and ር transposed |
| ተ**ሰ**ሣ | ተ**ነ**ሣ | Creed: "rose" |
| **ጉ**ብስት | **ኅ**ብስት | "bread", paired with "cup" |

And one that was flagged and turned out to be **correct as printed**: `ንጉሠ` is the
Ge'ez construct form used before a following noun (`ንጉሠ ክርስቶስን`), not a misprint for
`ንጉሥ`. When in doubt, `notes`.

---

## 6. Clipped and unreadable text

Several pages are partial — the scan slices through the top or bottom line, leaving
only half the glyph height.

**Do not reconstruct a clipped line from context.** Transcribe the lines you can
actually read, leave the clipped one out, and record it in the `clipped` array
(§6b) — not in prose.

This is deliberate. A visible gap gets fixed; a plausible invention does not.

---

## 6b. Flag it, don't solve it — the two required arrays

The reviewer reads Amharic and will fix things himself. **He cannot fix what he
cannot see.** So anything you are unsure about must be reported in a structured
field, not buried in prose — the review UI reads these two arrays and shows them
on screen.

### `uncertain` — words you could not read confidently

```json
"uncertain": [
  { "text": "በሥራት", "why": "may be በሥርዓት; the third glyph is smudged" }
]
```

`text` must be **exactly the string you put in the spans**, so the UI can find and
underline it in blue. Use this whenever you would otherwise have guessed, looked a
word up, or thought "that's probably…".

**Report far too many rather than too few.** An unnecessary flag costs the reviewer
two seconds; an unflagged wrong word in a prayer can go to press.

### `clipped` — text the scan cut off

```json
"clipped": [
  { "edge": "bottom", "note": "Final line runs into the bottom edge; begins 'እሰግዳለሁ'." }
]
```

`edge` is `"top"` or `"bottom"`. Add one entry per cut-off line. The UI shows a red
banner naming the page, because these words exist in the printed book and are
**missing from the data** — this is the most important thing to get right.

Add `clipped` **whenever any text is lost at a page edge**, including when a page
simply ends mid-sentence with the last line only half-visible. Both arrays must be
present on every page — use `[]` when there is nothing to report.

## 7. `notes`

Free text for anything that does not fit the two arrays above: unusual layout, a
page where the book prints Latin characters, section starts, or general context.
Empty string if the page is clean. **Do not use `notes` as a substitute for
`uncertain` or `clipped`** — prose is not shown as a flag in the UI.

---

## 8. Check your own work

After each page:

```
python -m engine books/wudase-mariam --stage verify --pages NNN
```

This compares your text against the pixel mask independently of you. It checks:

- **Red coverage** — the share of red characters in your text vs the share of red ink
  on the page. A big gap means you marked the wrong words red.
- **Latin characters and Arabic digits** — should be zero unless the book prints them.
- **Text agreement with Tesseract** — very low agreement means one of you misread the
  page badly.

A clean page reports no flags. Spelling-suggestion flags are expected and fine.

If a page reports *"red coverage over/under-marked"*, re-open the overlay and fix the
spans before moving on.

---

## 9. Your assignment — batch 3

**25 pages:**

```
121, 122, 123, 125, 127, 129, 131, 132, 133, 135, 137, 139, 141,
143, 147, 149, 150, 151, 153, 154, 155, 157, 159, 161, 162
```

The numbering is not all-odd: roughly 40% of this book's scans are blank verso
sheets and have already been excluded, which is why 50, 70 and 71 appear. Every page
in this list has text on it.

Do them one at a time, in order. Verify each before starting the next.

### Do not touch

- **Any page not in your list.** Pages 197–217 are being transcribed in parallel by
  someone else — leave them alone.
- Pages 3, 5, 7, 8, 9, 11, 12, 13, 15, 17, 19, 21, 23, 25 — already done and
  human-reviewed. **Use them as worked examples of the format.**
- Any file with `.original.json` or `.youredit.json` in the name — backups of human
  corrections.
- Anything under `engine/`, or `books/wudase-mariam/book.json`.

### What this batch contains

Pages 121–162 run through the rest of **አንቀጸ ብርሃን**, then **ይወድስዋ መላእክት**, and into
**የሰኔ ጎልጎታ**, which begins at page 147 with a red title under an ornament border.

Two things specific to this range:

- **Page 147 has heavy bleed-through** — the reverse side of the sheet shows through
  clearly. Only transcribe ink that is sharp; the ghosted text belongs to another
  page.
- **Page 145 is a full-colour devotional plate**, not text. It is excluded from your
  list — do not create a file for it.

The numbering is not all-odd (121, 122, 123 … 150, 151 …). That is expected: the
blank verso sheets have already been filtered out, so consecutive numbers appear
wherever both sides of a leaf carry text.

### Feedback on batch 2

This was a clear improvement and exactly what was wanted. Verified independently:
red coverage matched the pixel mask on all 25 pages with no drift, no Latin
characters leaked in, and text agreement with Tesseract sat in the normal range.

Most importantly, **the flagging worked**: 4 corrections, 17 uncertain readings and
20 clipped lines. Batch 1 reported zero of all three, which was the signal that
words were being quietly resolved instead of surfaced. Batch 2 surfaced them.

Keep doing exactly that. The reviewer reads Amharic and fixes things himself — your
job is to make sure nothing needing a decision is invisible to him. When in doubt,
flag it.

One flag resolved for you, so you can stop reporting it: **ባለሟልነትን** (flagged on
pages 81 and 111) is correct as printed. Page 213 prints the same word clearly.

### When finished

Report, per page: number of blocks, number of red spans, number of corrections
suggested, and anything you flagged in `notes`. Then run:

```
python -m engine books/wudase-mariam --stage verify --pages <your full list>
```

and paste the output.
