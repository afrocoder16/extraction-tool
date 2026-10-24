import { getStore } from "@netlify/blobs";
import { getUser } from "@netlify/identity";

import { errorResponse, json, requireUser } from "../lib/http.mjs";
import { parseBook, parsePage } from "../lib/validation.mjs";

const STORE_NAME = "extraction-review";

/** Who saved a page and when, newest first. Each entry is an immutable stored version. */
export default async function handler(request, context) {
  const user = await requireUser(getUser);
  if (!user) return json({ error: "Sign in to see page history." }, 401);
  if (request.method !== "GET") return json({ error: "Method not allowed" }, 405, { Allow: "GET" });

  try {
    const book = parseBook(context.params.book);
    const page = parsePage(context.params.page, book.max_page);
    const prefix = `${book.book_id}/versions/page_${String(page).padStart(3, "0")}/`;
    const { blobs } = await getStore(STORE_NAME).list({ prefix });
    const versions = blobs
      .map((blob) => ({ saved_at: blob.key.slice(prefix.length) }))
      .sort((a, b) => b.saved_at.localeCompare(a.saved_at));
    const store = getStore(STORE_NAME);
    for (const version of versions.slice(0, 50)) {
      const meta = await store.getMetadata(prefix + version.saved_at);
      version.saved_by = meta?.metadata?.saved_by || "unknown";
    }
    return json({ page, versions: versions.slice(0, 50) });
  } catch (error) {
    return errorResponse(error);
  }
}

export const config = { path: "/api/books/:book/history/:page" };
