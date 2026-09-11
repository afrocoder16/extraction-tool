export const BOOK_ID = "wudase_mariam";
export const MAX_PAGE = 218;
export const MAX_BODY_BYTES = 8 * 1024 * 1024;

const BLOCK_TYPES = new Set(["title", "verse", "prayer", "instruction", "ornament"]);
const REVIEW_STATUSES = new Set([null, "approved", "needs_fix"]);

export function parsePage(value) {
  if (!/^\d{1,4}$/.test(String(value))) throw new Error("page must be a number");
  const page = Number(value);
  if (!Number.isInteger(page) || page < 1 || page > MAX_PAGE) {
    throw new Error(`page must be between 1 and ${MAX_PAGE}`);
  }
  return page;
}

export function validateReview(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error("expected a JSON object");
  }
  if (!payload.entries || typeof payload.entries !== "object" || Array.isArray(payload.entries)) {
    throw new Error("expected an 'entries' object");
  }

  const entries = {};
  for (const [pageKey, raw] of Object.entries(payload.entries)) {
    const page = parsePage(pageKey);
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
      throw new Error(`review entry ${page} must be an object`);
    }
    const status = raw.status ?? null;
    const note = raw.note ?? "";
    if (!REVIEW_STATUSES.has(status)) throw new Error(`invalid status for page ${page}`);
    if (typeof note !== "string" || note.length > 4000) {
      throw new Error(`note for page ${page} must be at most 4000 characters`);
    }
    entries[String(page)] = { status, note: note.trim() };
  }
  return { entries };
}

function validateList(value, field) {
  if (!Array.isArray(value)) throw new Error(`'${field}' must be an array`);
  if (value.length > 2000) throw new Error(`'${field}' contains too many items`);
  return value;
}

export function validateTranscript(payload, pageValue) {
  const page = parsePage(pageValue);
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error("expected a JSON object");
  }
  if (!Array.isArray(payload.blocks) || payload.blocks.length > 1000) {
    throw new Error("expected a 'blocks' array with at most 1000 blocks");
  }

  const blocks = payload.blocks.map((block, blockIndex) => {
    if (!block || typeof block !== "object" || Array.isArray(block)) {
      throw new Error(`block ${blockIndex + 1} must be an object`);
    }
    if (!BLOCK_TYPES.has(block.type)) throw new Error(`invalid type in block ${blockIndex + 1}`);
    if (typeof block.verse_num_am !== "string" || block.verse_num_am.length > 100) {
      throw new Error(`invalid verse number in block ${blockIndex + 1}`);
    }
    if (!Array.isArray(block.spans) || block.spans.length > 5000) {
      throw new Error(`invalid spans in block ${blockIndex + 1}`);
    }
    const spans = block.spans.map((span, spanIndex) => {
      if (!span || typeof span !== "object" || typeof span.t !== "string") {
        throw new Error(`invalid span ${spanIndex + 1} in block ${blockIndex + 1}`);
      }
      if (span.t.length > 100_000 || !["red", "black"].includes(span.c)) {
        throw new Error(`invalid span ${spanIndex + 1} in block ${blockIndex + 1}`);
      }
      return { ...span, t: span.t, c: span.c };
    });
    return { ...block, type: block.type, verse_num_am: block.verse_num_am, spans };
  });

  const notes = payload.notes ?? "";
  if (typeof notes !== "string" || notes.length > 100_000) throw new Error("invalid notes");

  return {
    ...payload,
    page,
    notes,
    corrections: validateList(payload.corrections ?? [], "corrections"),
    uncertain: validateList(payload.uncertain ?? [], "uncertain"),
    clipped: validateList(payload.clipped ?? [], "clipped"),
    blocks,
  };
}

export function verifySameOrigin(request) {
  const origin = request.headers.get("origin");
  if (!origin || origin !== new URL(request.url).origin) {
    throw new Error("request origin is not allowed");
  }
}

export async function readJsonBody(request) {
  const declared = Number(request.headers.get("content-length") || 0);
  if (declared > MAX_BODY_BYTES) throw new Error("request body is too large");
  const text = await request.text();
  if (!text || new TextEncoder().encode(text).byteLength > MAX_BODY_BYTES) {
    throw new Error("request body is empty or too large");
  }
  try {
    return JSON.parse(text);
  } catch {
    throw new Error("request body is not valid JSON");
  }
}
