export function json(payload, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
      ...extraHeaders,
    },
  });
}

export function errorResponse(error, fallbackStatus = 400) {
  console.error(error);
  return json({ error: error instanceof Error ? error.message : "request failed" }, fallbackStatus);
}

export async function requireUser(getUser) {
  const user = await getUser();
  return user || null;
}
