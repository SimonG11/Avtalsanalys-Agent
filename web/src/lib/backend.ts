/**
 * What: where the backend API is, read from the environment on the server.
 *
 * Why: the browser only talks to the web app's own server (same origin). That server forwards
 * agent runs and PDF requests to the API, so the API needs no CORS setup and its address can
 * change between `npm run dev` (localhost) and Docker Compose (the `api` service) without a
 * new build.
 *
 * How: the route handlers under src/app/api call these functions on every request, so the
 * value is read at runtime, not frozen at build time.
 */
import "server-only";

/** The FastAPI backend, e.g. http://localhost:8000 locally or http://api:8000 in Compose. */
export function apiUrl(): string {
  return (process.env.API_URL ?? "http://localhost:8000").replace(/\/+$/, "");
}

/** The AG-UI endpoint that runs the LangGraph agent. */
export function agentUrl(): string {
  return `${apiUrl()}/agui`;
}

/** The PDF of one document, identified by its SHA-256. */
export function documentPdfUrl(sha256: string): string {
  return `${apiUrl()}/api/documents/${sha256}/pdf`;
}
