import { getStore } from "@netlify/blobs";
import { getUser } from "@netlify/identity";

import { errorResponse, json, requireUser } from "../lib/http.mjs";
import { parseBook, readJsonBody, validateReview, verifySameOrigin } from "../lib/validation.mjs";

const STORE_NAME = "extraction-review";

export default async function handler(request, context) {
  const user = await requireUser(getUser);
  if (!user) return json({ error: "Sign in to access saved review decisions." }, 401);

  let book;
  try {
    book = parseBook(context.params.book);
  } catch (error) {
    return errorResponse(error);
  }
  const store = getStore(STORE_NAME);
  const key = `${book.book_id}/review`;

  if (request.method === "GET") {
    const saved = await store.get(key, { type: "json", consistency: "strong" });
    return json(saved || { entries: {} });
  }
  if (request.method !== "POST") return json({ error: "Method not allowed" }, 405, { Allow: "GET, POST" });

  try {
    verifySameOrigin(request);
    const validated = validateReview(await readJsonBody(request), book.max_page);
    // Merge page by page so two reviewers deciding different pages never
    // overwrite each other, and stamp who made each changed decision.
    const previous = (await store.get(key, { type: "json", consistency: "strong" }))?.entries || {};
    const now = new Date().toISOString();
    const by = user.email || user.id;
    const entries = { ...previous };
    for (const [page, entry] of Object.entries(validated.entries)) {
      const before = previous[page];
      const changed = !before || before.status !== entry.status || before.note !== entry.note;
      entries[page] = changed ? { ...entry, decided_at: now, decided_by: by } : before;
    }
    const saved = { entries, saved_at: now, saved_by: by };
    await store.setJSON(key, saved, { metadata: { book: book.book_id, kind: "review" } });
    return json({ ok: true, saved: Object.keys(validated.entries).length, ...saved });
  } catch (error) {
    return errorResponse(error);
  }
}

export const config = { path: "/api/books/:book/review" };
