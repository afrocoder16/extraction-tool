import assert from "node:assert/strict";
import test from "node:test";

import { parsePage, validateReview, validateTranscript } from "../../netlify/lib/validation.mjs";

test("parsePage accepts pages in the book and rejects unsafe values", () => {
  assert.equal(parsePage("1"), 1);
  assert.equal(parsePage("218"), 218);
  for (const value of ["0", "219", "../1", "1.5", "page_1"]) {
    assert.throws(() => parsePage(value));
  }
});

test("validateReview normalizes supported decisions", () => {
  assert.deepEqual(
    validateReview({ entries: { "3": { status: "approved", note: " checked " } } }),
    { entries: { "3": { status: "approved", note: "checked" } } },
  );
  assert.throws(() => validateReview({ entries: { "3": { status: "maybe" } } }));
});

test("validateTranscript upgrades legacy review fields", () => {
  const transcript = validateTranscript({
    page: 99,
    notes: "",
    blocks: [{ type: "verse", verse_num_am: "፩", spans: [{ t: "ሰላም", c: "red" }] }],
  }, "3");
  assert.equal(transcript.page, 3);
  assert.deepEqual(transcript.uncertain, []);
  assert.deepEqual(transcript.clipped, []);
});

test("validateTranscript rejects unsupported block and colour values", () => {
  assert.throws(() => validateTranscript({ blocks: [{ type: "html", verse_num_am: "", spans: [] }] }, 3));
  assert.throws(() => validateTranscript({
    blocks: [{ type: "verse", verse_num_am: "", spans: [{ t: "x", c: "blue" }] }],
  }, 3));
});
