import assert from "node:assert/strict";
import test from "node:test";

import { parseBook, parsePage, validateReview, validateTranscript } from "../../netlify/lib/validation.mjs";
import BOOKS from "../../netlify/lib/books.mjs";

test("parseBook accepts only published books", () => {
  assert.equal(parseBook("wudase_mariam").max_page, 218);
  const books = [{ book_id: "other", max_page: 40 }];
  assert.equal(parseBook("other", books).max_page, 40);
  for (const value of ["", "../wudase_mariam", "unknown", "wudase_mariam/review"]) {
    assert.throws(() => parseBook(value));
  }
});

test("the published registry has unique ids and page limits", () => {
  const ids = BOOKS.map((b) => b.book_id);
  assert.equal(new Set(ids).size, ids.length);
  for (const b of BOOKS) assert.ok(/^[a-z0-9_]+$/.test(b.book_id) && b.max_page > 0);
});

test("parsePage accepts pages in the book and rejects unsafe values", () => {
  assert.equal(parsePage("1", 218), 1);
  assert.equal(parsePage("218", 218), 218);
  for (const value of ["0", "219", "../1", "1.5", "page_1"]) {
    assert.throws(() => parsePage(value, 218));
  }
  assert.throws(() => parsePage("41", 40));
});

test("validateReview normalizes supported decisions", () => {
  assert.deepEqual(
    validateReview({ entries: { "3": { status: "approved", note: " checked " } } }, 218),
    { entries: { "3": { status: "approved", note: "checked" } } },
  );
  assert.throws(() => validateReview({ entries: { "3": { status: "maybe" } } }, 218));
});

test("validateTranscript upgrades legacy review fields", () => {
  const transcript = validateTranscript({
    page: 99,
    notes: "",
    blocks: [{ type: "verse", verse_num_am: "፩", spans: [{ t: "ሰላም", c: "red" }] }],
  }, "3", 218);
  assert.equal(transcript.page, 3);
  assert.deepEqual(transcript.uncertain, []);
  assert.deepEqual(transcript.clipped, []);
});

test("validateTranscript rejects unsupported block and colour values", () => {
  assert.throws(() => validateTranscript({ blocks: [{ type: "html", verse_num_am: "", spans: [] }] }, 3, 218));
  assert.throws(() => validateTranscript({
    blocks: [{ type: "verse", verse_num_am: "", spans: [{ t: "x", c: "blue" }] }],
  }, 3, 218));
});
