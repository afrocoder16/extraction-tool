import { getStore } from "@netlify/blobs";
import { getUser } from "@netlify/identity";

import { errorResponse, json, requireUser } from "../lib/http.mjs";
import { parseBook } from "../lib/validation.mjs";

const STORE_NAME = "extraction-review";

export default async function handler(request, context) {
  const user = await requireUser(getUser);
  if (!user) return json({ error: "Sign in to export saved work." }, 401);
  if (request.method !== "GET") return json({ error: "Method not allowed" }, 405, { Allow: "GET" });

  let book;
  try {
    book = parseBook(context.params.book);
  } catch (error) {
    return errorResponse(error);
  }
  const store = getStore(STORE_NAME);
  const prefix = `${book.book_id}/transcripts/`;
  // Read known page keys directly with strong consistency. A just-created key
  // can take time to appear in an eventually-consistent list operation.
  const [saved, review] = await Promise.all([
    Promise.all(Array.from({ length: book.max_page }, async (_, index) => {
      const key = `${prefix}page_${String(index + 1).padStart(3, "0")}`;
      const value = await store.get(key, { type: "json", consistency: "strong" });
      return { key, value };
    })),
    store.get(`${book.book_id}/review`, { type: "json", consistency: "strong" }),
  ]);

  const transcripts = {};
  const edit_history = {};
  for (const { key, value } of saved) {
    if (!value?.transcript) continue;
    const pageKey = key.slice(prefix.length);
    transcripts[pageKey] = value.transcript;
    edit_history[pageKey] = { saved_at: value.saved_at, saved_by: value.saved_by };
  }

  return json(
    {
      book_id: book.book_id,
      exported_at: new Date().toISOString(),
      exported_by: user.email || user.id,
      review: review || { entries: {} },
      transcripts,
      edit_history,
    },
    200,
    { "Content-Disposition": `attachment; filename="${book.book_id}_review_export.json"` },
  );
}

export const config = { path: "/api/books/:book/export" };
