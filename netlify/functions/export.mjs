import { getStore } from "@netlify/blobs";
import { getUser } from "@netlify/identity";

import { json, requireUser } from "../lib/http.mjs";
import { BOOK_ID, MAX_PAGE } from "../lib/validation.mjs";

const STORE_NAME = "extraction-review";
const PREFIX = `${BOOK_ID}/transcripts/`;

export default async function handler(request) {
  const user = await requireUser(getUser);
  if (!user) return json({ error: "Sign in to export saved work." }, 401);
  if (request.method !== "GET") return json({ error: "Method not allowed" }, 405, { Allow: "GET" });

  const store = getStore(STORE_NAME);
  // Read known page keys directly with strong consistency. A just-created key
  // can take time to appear in an eventually-consistent list operation.
  const [saved, review] = await Promise.all([
    Promise.all(Array.from({ length: MAX_PAGE }, async (_, index) => {
      const key = `${PREFIX}page_${String(index + 1).padStart(3, "0")}`;
      const value = await store.get(key, { type: "json", consistency: "strong" });
      return { key, value };
    })),
    store.get(`${BOOK_ID}/review`, { type: "json", consistency: "strong" }),
  ]);

  const transcripts = {};
  const edit_history = {};
  for (const { key, value } of saved) {
    if (!value?.transcript) continue;
    const pageKey = key.slice(PREFIX.length);
    transcripts[pageKey] = value.transcript;
    edit_history[pageKey] = { saved_at: value.saved_at, saved_by: value.saved_by };
  }

  return json(
    {
      book_id: BOOK_ID,
      exported_at: new Date().toISOString(),
      exported_by: user.email || user.id,
      review: review || { entries: {} },
      transcripts,
      edit_history,
    },
    200,
    { "Content-Disposition": `attachment; filename="${BOOK_ID}_review_export.json"` },
  );
}

export const config = { path: "/api/export" };
