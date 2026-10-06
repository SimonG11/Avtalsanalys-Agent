/**
 * What: GET /api/documents/{sha256}/pdf, which streams a document's PDF from the backend.
 *
 * Why: the source panel loads the PDF with PDF.js in the browser. Serving it from the web
 * app's own origin avoids CORS between the browser and the API, and the same URL works when
 * the API moves (localhost or the Compose service).
 *
 * How: the id is checked to be a SHA-256 before anything is forwarded, so the route cannot be
 * used to reach other paths on the API. The backend's status and body are passed on as they are.
 */
import type { NextRequest } from "next/server";

import { documentPdfUrl } from "@/lib/backend";

const SHA256 = /^[0-9a-f]{64}$/;

export async function GET(
  _request: NextRequest,
  context: RouteContext<"/api/documents/[sha256]/pdf">,
) {
  const { sha256 } = await context.params;
  if (!SHA256.test(sha256)) {
    return Response.json({ detail: "Invalid document id" }, { status: 400 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(documentPdfUrl(sha256), { cache: "no-store" });
  } catch {
    return Response.json({ detail: "The API cannot be reached" }, { status: 502 });
  }
  if (!upstream.ok || !upstream.body) {
    return Response.json(
      { detail: "Document not found" },
      { status: upstream.status === 404 ? 404 : 502 },
    );
  }
  return new Response(upstream.body, {
    headers: {
      "Content-Type": "application/pdf",
      "Cache-Control": "private, max-age=3600",
    },
  });
}
