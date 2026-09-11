import { getStore } from "@netlify/blobs";
import { getUser } from "@netlify/identity";

import { errorResponse, json, requireUser } from "../lib/http.mjs";
import {
  BOOK_ID,
  parsePage,
  readJsonBody,
  validateTranscript,
  verifySameOrigin,
} from "../lib/validation.mjs";

const STORE_NAME = "extraction-review";

export default async function handler(request, context) {
  const user = await requireUser(getUser);
  if (!user) return json({ error: "Sign in to access saved transcripts." }, 401);

  let page;
  try {
    page = parsePage(context.params.page);
  } catch (error) {
    return errorResponse(error);
  }

  const store = getStore(STORE_NAME);
  const key = `${BOOK_ID}/transcripts/page_${String(page).padStart(3, "0")}`;

  if (request.method === "GET") {
    const saved = await store.get(key, { type: "json", consistency: "strong" });
    if (!saved) return json({ error: "No hosted edit exists for this page." }, 404);
    return json(saved.transcript);
  }
  if (request.method !== "POST") return json({ error: "Method not allowed" }, 405, { Allow: "GET, POST" });

  try {
    verifySameOrigin(request);
    const transcript = validateTranscript(await readJsonBody(request), page);
    const saved = {
      transcript,
      saved_at: new Date().toISOString(),
      saved_by: user.email || user.id,
    };
    await store.setJSON(key, saved, {
      metadata: { book: BOOK_ID, kind: "transcript", page },
    });
    return json({ ok: true, page, ...saved });
  } catch (error) {
    return errorResponse(error);
  }
}

export const config = { path: "/api/transcript/:page" };
