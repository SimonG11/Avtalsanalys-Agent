/**
 * What: the shared part of the /api/uploads routes: checking the ids in a request and passing
 * the request on to the API.
 *
 * Why: the browser only talks to the web app's own server, which forwards uploads to the API
 * like agent runs and PDFs (lib/backend.ts). The ids come from the browser, so they are checked
 * before they become part of a URL on the API.
 *
 * How: `isId` accepts the characters of a UUID or a similar token. `forward` sends the request
 * and returns the API's status and body as they are, since the API's errors carry a Swedish
 * `detail` the web app shows; when the API cannot be reached it answers 502 itself.
 */
import "server-only";

const ID = /^[A-Za-z0-9_-]{1,128}$/;

export function isId(value: unknown): value is string {
  return typeof value === "string" && ID.test(value);
}

/** A JSON error with a Swedish `detail`, as the API's own errors have. */
export function problem(status: number, detail: string): Response {
  return Response.json({ detail }, { status });
}

export async function forward(url: string, init: RequestInit = {}): Promise<Response> {
  let upstream: Response;
  try {
    upstream = await fetch(url, { ...init, cache: "no-store" });
  } catch {
    return problem(502, "Webbappen når inte API:t just nu.");
  }
  const headers = new Headers({ "Cache-Control": "no-store" });
  // Not Content-Length: fetch has already undone any compression, so the length may differ.
  for (const name of ["Content-Type", "Content-Disposition"]) {
    const value = upstream.headers.get(name);
    if (value) headers.set(name, value);
  }
  const empty = upstream.status === 204 || upstream.status === 304;
  return new Response(empty ? null : upstream.body, { status: upstream.status, headers });
}
