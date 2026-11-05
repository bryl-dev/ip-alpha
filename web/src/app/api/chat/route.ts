const API_URL = process.env.API_URL ?? "http://localhost:8000";

// Proxies the backend's server-sent events so the browser never needs the API's address.
export async function POST(request: Request) {
  let upstream: Response;
  try {
    upstream = await fetch(`${API_URL}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: await request.text(),
      signal: request.signal,
    });
  } catch {
    return Response.json({ error: "The API is unreachable." }, { status: 502 });
  }
  if (!upstream.ok || !upstream.body) {
    return Response.json({ error: `API returned ${upstream.status}` }, { status: upstream.status });
  }
  return new Response(upstream.body, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
    },
  });
}
