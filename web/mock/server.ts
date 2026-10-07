/**
 * What: a stand-in for the backend API, with the two endpoints the web app uses:
 *   POST /agui                         the agent over AG-UI (server-sent events)
 *   GET  /api/documents/{sha256}/pdf   the PDF a citation points to
 *
 * Why: the web app is built before the real API (M9). The mock lets it run, and be tested in
 * CI, without a database, an OpenAI key or documents from avropa.se.
 *
 * How: each POST is answered from mock/scenarios.ts, one event at a time with short pauses so
 * the steps appear live. Only Node's own http module is used. Start: npm run mock
 * (port 8000, or MOCK_PORT).
 */
import { createServer } from "node:http";
import type { IncomingMessage, ServerResponse } from "node:http";

import type { RunAgentInput } from "@ag-ui/core";
import { EventEncoder } from "@ag-ui/encoder";

import { buildFixturePdf } from "./fixture-pdf.ts";
import { planRun } from "./scenarios.ts";

const port = Number(process.env.MOCK_PORT ?? 8000);
/** MOCK_FAST=1 removes the pauses, for tests, except those a test needs to see (minPauseMs). */
const pauseFactor = process.env.MOCK_FAST === "1" ? 0 : 1;

const pdf = await buildFixturePdf();

async function readJson(request: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  for await (const chunk of request) chunks.push(chunk as Buffer);
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function runAgent(request: IncomingMessage, response: ServerResponse): Promise<void> {
  const input = (await readJson(request)) as RunAgentInput;
  const encoder = new EventEncoder({ accept: request.headers.accept });
  response.writeHead(200, {
    "Content-Type": encoder.getContentType(),
    "Cache-Control": "no-cache",
  });
  for (const { event, pauseMs, minPauseMs } of planRun(input, { documentSha256: pdf.sha256 })) {
    await sleep(Math.max(pauseMs * pauseFactor, minPauseMs ?? 0));
    response.write(encoder.encode(event));
  }
  response.end();
}

function sendPdf(sha256: string, response: ServerResponse): void {
  if (sha256 !== pdf.sha256) {
    response.writeHead(404, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ detail: "Document not found" }));
    return;
  }
  response.writeHead(200, {
    "Content-Type": "application/pdf",
    "Content-Length": pdf.bytes.length,
  });
  response.end(pdf.bytes);
}

const server = createServer((request, response) => {
  const url = new URL(request.url ?? "/", "http://localhost");
  const pdfPath = url.pathname.match(/^\/api\/documents\/([0-9a-f]{64})\/pdf$/);

  if (request.method === "POST" && url.pathname === "/agui") {
    runAgent(request, response).catch((error: unknown) => {
      console.error(error);
      if (!response.headersSent) response.writeHead(500);
      response.end();
    });
  } else if (request.method === "GET" && url.pathname === "/agui/health") {
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ status: "ok", agent: { name: "avtalsagent (mock)" } }));
  } else if (request.method === "GET" && pdfPath) {
    sendPdf(pdfPath[1], response);
  } else {
    response.writeHead(404);
    response.end();
  }
});

server.listen(port, () => {
  console.log(`Mock agent on http://localhost:${port}/agui (document ${pdf.sha256.slice(0, 12)}…)`);
});
