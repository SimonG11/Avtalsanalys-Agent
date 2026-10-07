/**
 * What: the person's own files in a conversation: which files can be uploaded, what the
 * backend says about an upload, the Swedish error messages, and where a cited file is opened.
 *
 * Why: the agent can compare a contract the person uploads with the framework agreements
 * (webbapp-kontrakt.md, points 33-38). A file belongs to the conversation's AG-UI thread, so
 * every request names the thread, and a file the backend cannot take should get a clear reason
 * in Swedish, preferably before it is sent.
 *
 * How: pure functions and a Zod schema; components/Uploads.tsx does the requests through the
 * web app's own /api/uploads routes, which forward them to the API.
 */
import { z } from "zod";

import type { Citation } from "./contract.ts";

/** The largest file the backend takes (point 33). */
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;

/** The file types the backend reads (point 33), for the file picker's `accept`. */
export const ACCEPTED_EXTENSIONS = [".pdf", ".docx", ".txt", ".md"];

export const UploadSchema = z.object({
  upload_id: z.string().min(1),
  filename: z.string().min(1),
  kind: z.enum(["pdf", "docx", "text"]),
  // Pages for a PDF; null or missing for Word and text.
  pages: z.number().int().nonnegative().nullish(),
  sections: z.number().int().nonnegative().nullish(),
  characters: z.number().int().nonnegative().nullish(),
  // Swedish sentences, e.g. about pages without text.
  warnings: z.array(z.string()).default([]),
});

export type Upload = z.infer<typeof UploadSchema>;

/** Why a file cannot be uploaded, checked in the browser, or null when it can be tried. */
export function checkFile(file: { name: string; size: number }): string | null {
  const name = file.name.toLowerCase();
  if (!ACCEPTED_EXTENSIONS.some((extension) => name.endsWith(extension))) {
    return "Filtypen stöds inte. Ladda upp en PDF, en Word-fil (.docx) eller en textfil.";
  }
  if (file.size > MAX_UPLOAD_BYTES) return "Filen är större än 10 MB.";
  if (file.size === 0) return "Filen är tom.";
  return null;
}

const STATUS_MESSAGES: Record<number, string> = {
  413: "Filen är större än 10 MB.",
  415: "Filtypen stöds inte. Ladda upp en PDF, en Word-fil (.docx) eller en textfil.",
  422: "Filen har ingen text som går att läsa, till exempel en inskannad PDF.",
  503: "Filen kunde inte sparas just nu. Försök igen om en stund.",
};

/**
 * The message for a failed upload: the backend's own Swedish `detail` when it gave one,
 * otherwise a message for the status code.
 */
export function uploadErrorMessage(status: number, body: unknown): string {
  if (typeof body === "object" && body !== null && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string" && detail.trim()) return detail.trim();
  }
  return STATUS_MESSAGES[status] ?? "Filen kunde inte laddas upp.";
}

/** "12 sidor", "3 avsnitt" or "Word-fil": a short line about an uploaded file. */
export function describeUpload(upload: Upload): string {
  if (upload.kind === "pdf" && upload.pages) {
    return upload.pages === 1 ? "1 sida" : `${upload.pages} sidor`;
  }
  if (upload.sections) return upload.sections === 1 ? "1 avsnitt" : `${upload.sections} avsnitt`;
  return upload.kind === "docx" ? "Word-fil" : "Textfil";
}

/** Whether a citation is from the person's own file rather than the framework agreements. */
export function isUploadCitation(citation: Pick<Citation, "source">): boolean {
  return citation.source === "upload";
}

/**
 * Where the source panel loads a cited PDF from, or null when there is none to show: an
 * uploaded file is a PDF only when the citation has a page (Word and text files have none,
 * point 37), and an agreement's file needs its SHA-256. The link is chosen by `source`, since
 * an uploaded file's citation may also carry a SHA-256.
 */
export function citationPdfUrl(
  citation: Pick<Citation, "source" | "sha256" | "upload_id" | "page">,
  threadId: string,
): string | null {
  if (isUploadCitation(citation)) {
    if (!citation.upload_id || citation.page === null) return null;
    const thread = encodeURIComponent(threadId);
    return `/api/uploads/${encodeURIComponent(citation.upload_id)}/file?thread_id=${thread}`;
  }
  return citation.sha256 ? `/api/documents/${citation.sha256}/pdf` : null;
}
