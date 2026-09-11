import { getStore } from "@netlify/blobs";
import { getUser } from "@netlify/identity";

import { errorResponse, json, requireUser } from "../lib/http.mjs";
import { BOOK_ID, readJsonBody, validateReview, verifySameOrigin } from "../lib/validation.mjs";

const STORE_NAME = "extraction-review";
const KEY = `${BOOK_ID}/review`;

export default async function handler(request) {
  const user = await requireUser(getUser);
  if (!user) return json({ error: "Sign in to access saved review decisions." }, 401);

  const store = getStore(STORE_NAME);
  if (request.method === "GET") {
    const saved = await store.get(KEY, { type: "json", consistency: "strong" });
    return json(saved || { entries: {} });
  }
  if (request.method !== "POST") return json({ error: "Method not allowed" }, 405, { Allow: "GET, POST" });

  try {
    verifySameOrigin(request);
    const validated = validateReview(await readJsonBody(request));
    const saved = {
      ...validated,
      saved_at: new Date().toISOString(),
      saved_by: user.email || user.id,
    };
    await store.setJSON(KEY, saved, { metadata: { book: BOOK_ID, kind: "review" } });
    return json({ ok: true, saved: Object.keys(validated.entries).length, ...saved });
  } catch (error) {
    return errorResponse(error);
  }
}

export const config = { path: "/api/review" };
