import { getStore } from "@netlify/blobs";
import { getUser } from "@netlify/identity";

import { errorResponse, json, requireUser } from "../lib/http.mjs";
import {
  parseBook,
  parsePage,
  readJsonBody,
  validateTranscript,
  verifySameOrigin,
} from "../lib/validation.mjs";

const STORE_NAME = "extraction-review";

// Client-side bookkeeping that must never be stored as transcript content.
const CLIENT_FIELDS = ["_saved_at", "_saved_by", "_base_saved_at", "_revision", "_evidence_revision"];

export default async function handler(request, context) {
  const user = await requireUser(getUser);
  if (!user) return json({ error: "Sign in to access saved transcripts." }, 401);

  let book, page;
  try {
    book = parseBook(context.params.book);
    page = parsePage(context.params.page, book.max_page);
  } catch (error) {
    return errorResponse(error);
  }

  const store = getStore(STORE_NAME);
  const name = `page_${String(page).padStart(3, "0")}`;
  const key = `${book.book_id}/transcripts/${name}`;

  if (request.method === "GET") {
    const saved = await store.get(key, { type: "json", consistency: "strong" });
    if (!saved) return json({ error: "No hosted edit exists for this page." }, 404);
    return json({ ...saved.transcript, _saved_at: saved.saved_at, _saved_by: saved.saved_by });
  }
  if (request.method !== "POST") return json({ error: "Method not allowed" }, 405, { Allow: "GET, POST" });

  try {
    verifySameOrigin(request);
    const body = await readJsonBody(request);
    // The editor sends the save time of the version it loaded (null for the
    // checked-in baseline). Anything else means another editor saved first.
    const current = await store.get(key, { type: "json", consistency: "strong" });
    const base = body?._base_saved_at ?? null;
    if ((current?.saved_at ?? null) !== base) {
      return json({
        error: `${current?.saved_by || "Someone"} saved this page at ${current?.saved_at}. Reload it before saving.`,
        saved_at: current?.saved_at ?? null,
        saved_by: current?.saved_by ?? null,
      }, 409);
    }
    for (const field of CLIENT_FIELDS) delete body[field];
    const transcript = validateTranscript(body, page, book.max_page);
    const saved = {
      transcript,
      saved_at: new Date().toISOString(),
      saved_by: user.email || user.id,
    };
    const metadata = { book: book.book_id, kind: "transcript", page, saved_by: saved.saved_by };
    // Every save is also kept as an immutable version for the audit trail.
    await store.setJSON(`${book.book_id}/versions/${name}/${saved.saved_at}`, saved, { metadata });
    await store.setJSON(key, saved, { metadata });
    return json({ ok: true, page, ...saved });
  } catch (error) {
    return errorResponse(error);
  }
}

export const config = { path: "/api/books/:book/transcript/:page" };
