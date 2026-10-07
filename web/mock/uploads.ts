/**
 * What: the mock's stand-in for the API's uploads (webbapp-kontrakt.md, points 33-34):
 *   POST   /api/uploads                         upload a file to a thread
 *   GET    /api/uploads?thread_id=…             the thread's files, as {"uploads": [...]}
 *   DELETE /api/uploads/{upload_id}?thread_id=… remove one
 *   GET    /api/uploads/{upload_id}/file?…      the file itself, for the source panel
 *
 * Why: the web app's uploads, and the agent's answer that compares a file with the agreement,
 * have to be tested before the backend has them, and without a database.
 *
 * How: the files are kept in memory per thread, as long as the mock runs. The checks follow the
 * contract: PDF, Word and text up to 10 MB, at most five files per thread, and the same file
 * twice gives the first upload back with 200. A file whose name says it is scanned
 * ("inskannad") has no text, so the mock refuses it like the API refuses a scanned PDF. Errors
 * carry a Swedish `detail`, as the API's do. The API's other limits (pages, characters, total
 * storage) are left out: the web app shows their `detail` the same way.
 */
import { createHash, randomUUID } from "node:crypto";
import type { IncomingMessage, ServerResponse } from "node:http";

import { PDFDocument } from "pdf-lib";

export interface StoredUpload {
  upload_id: string;
  filename: string;
  kind: "pdf" | "docx" | "text";
  pages: number | null;
  sections: number;
  characters: number;
  warnings: string[];
  sha256: string;
  size: number;
  created_at: string;
  bytes: Uint8Array;
}

const MAX_BYTES = 10 * 1024 * 1024;
const MAX_FILES = 5;
const KINDS: Record<string, StoredUpload["kind"]> = {
  ".pdf": "pdf",
  ".docx": "docx",
  ".txt": "text",
  ".md": "text",
};
const CONTENT_TYPES: Record<StoredUpload["kind"], string> = {
  pdf: "application/pdf",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  text: "text/plain; charset=utf-8",
};

const byThread = new Map<string, StoredUpload[]>();

/** The files uploaded to a thread, oldest first. */
export function uploadsOf(threadId: string): readonly StoredUpload[] {
  return byThread.get(threadId) ?? [];
}

/** What the API answers about an upload: everything but the file itself. */
export function describe(upload: StoredUpload) {
  const { upload_id, filename, kind, pages, sections, characters, warnings, size, created_at } =
    upload;
  return { upload_id, filename, kind, pages, sections, characters, warnings, size, created_at };
}

function send(response: ServerResponse, status: number, body?: unknown): void {
  if (body === undefined) {
    response.writeHead(status);
    response.end();
    return;
  }
  response.writeHead(status, { "Content-Type": "application/json" });
  response.end(JSON.stringify(body));
}

async function readBody(request: IncomingMessage): Promise<Buffer> {
  const chunks: Buffer[] = [];
  for await (const chunk of request) chunks.push(chunk as Buffer);
  return Buffer.concat(chunks);
}

async function upload(request: IncomingMessage, response: ServerResponse): Promise<void> {
  // Node's own Request reads the multipart form.
  const form = await new Request("http://mock/api/uploads", {
    method: "POST",
    headers: { "Content-Type": request.headers["content-type"] ?? "" },
    body: new Uint8Array(await readBody(request)),
  }).formData();
  const file = form.get("file");
  const threadId = form.get("thread_id");
  if (!(file instanceof File) || typeof threadId !== "string" || !threadId) {
    return send(response, 400, { detail: "Filen eller trådens id saknas." });
  }

  const extension = /\.[^.]+$/.exec(file.name.toLowerCase())?.[0] ?? "";
  const kind = KINDS[extension];
  if (!kind) {
    return send(response, 415, {
      detail: "Filtypen stöds inte. Ladda upp en PDF, en Word-fil (.docx) eller en textfil.",
    });
  }
  if (file.size > MAX_BYTES) return send(response, 413, { detail: "Filen är större än 10 MB." });

  const bytes = new Uint8Array(await file.arrayBuffer());
  const sha256 = createHash("sha256").update(bytes).digest("hex");
  const files = byThread.get(threadId) ?? [];
  const same = files.find((stored) => stored.sha256 === sha256);
  if (same) return send(response, 200, describe(same));
  if (files.length >= MAX_FILES) {
    return send(response, 409, {
      detail: "Konversationen har redan 5 filer. Ta bort en innan du laddar upp en ny.",
    });
  }
  if (file.name.toLowerCase().includes("inskannad")) {
    return send(response, 422, {
      detail: "Filen har ingen text som går att läsa. Den är troligen inskannad.",
    });
  }

  let pages: number | null = null;
  if (kind === "pdf") {
    try {
      pages = (await PDFDocument.load(bytes)).getPageCount();
    } catch {
      return send(response, 422, { detail: "Filen gick inte att läsa som PDF." });
    }
  }
  const text = kind === "text" ? new TextDecoder().decode(bytes) : "";
  const stored: StoredUpload = {
    upload_id: `upl_${randomUUID().replaceAll("-", "")}`,
    filename: file.name,
    kind,
    pages,
    sections: kind === "text" ? text.split(/\n\s*\n/).filter((part) => part.trim()).length : 2,
    characters: kind === "text" ? text.length : bytes.length,
    warnings: [],
    sha256,
    size: bytes.length,
    created_at: new Date().toISOString(),
    bytes,
  };
  byThread.set(threadId, [...files, stored]);
  send(response, 201, describe(stored));
}

/**
 * Answers the request if it is one of the upload endpoints, and says whether it was. Errors
 * while reading the request are answered with 500, as an unexpected error in the API would be.
 */
export function handleUploads(
  request: IncomingMessage,
  response: ServerResponse,
  url: URL,
): boolean {
  if (!url.pathname.startsWith("/api/uploads")) return false;
  const threadId = url.searchParams.get("thread_id") ?? "";
  const one = url.pathname.match(/^\/api\/uploads\/([A-Za-z0-9_-]+)(\/file)?$/);
  const stored = one ? uploadsOf(threadId).find((file) => file.upload_id === one[1]) : undefined;

  if (request.method === "POST" && url.pathname === "/api/uploads") {
    upload(request, response).catch((error: unknown) => {
      console.error(error);
      if (!response.headersSent) send(response, 500, { detail: "Mocken kunde inte läsa filen." });
    });
  } else if (!threadId) {
    send(response, 422, { detail: "Konversationens id saknas." });
  } else if (request.method === "GET" && url.pathname === "/api/uploads") {
    send(response, 200, { uploads: uploadsOf(threadId).map(describe) });
  } else if (one && !stored) {
    send(response, 404, { detail: "Filen finns inte i den här konversationen." });
  } else if (one && stored && request.method === "DELETE" && !one[2]) {
    byThread.set(
      threadId,
      uploadsOf(threadId).filter((file) => file !== stored),
    );
    send(response, 204);
  } else if (one && stored && request.method === "GET" && one[2]) {
    // A Word file is downloaded, PDF and text are shown, as the API does.
    const disposition = stored.kind === "docx" ? "attachment" : "inline";
    response.writeHead(200, {
      "Content-Type": CONTENT_TYPES[stored.kind],
      "Content-Disposition": `${disposition}; filename*=UTF-8''${encodeURIComponent(stored.filename)}`,
      "Content-Length": stored.bytes.length,
      "Cache-Control": "private, no-store",
    });
    response.end(stored.bytes);
  } else {
    send(response, 405);
  }
  return true;
}
