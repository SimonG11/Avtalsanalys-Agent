/**
 * What: POST /api/uploads uploads one of the person's files to the conversation, and
 * GET /api/uploads?thread_id=… lists the conversation's files (webbapp-kontrakt.md, points
 * 33-34).
 *
 * Why: the agent can only read a file the API has, and the browser only talks to the web app.
 *
 * How: the form's `file` and `thread_id` are checked and sent on to the API as a new form. The
 * API's answer, including its Swedish error messages, comes back as it is (lib/uploadProxy.ts).
 */
import type { NextRequest } from "next/server";

import { uploadsUrl } from "@/lib/backend";
import { forward, isId, problem } from "@/lib/uploadProxy";

export async function POST(request: NextRequest) {
  let form: FormData;
  try {
    form = await request.formData();
  } catch {
    return problem(400, "Ingen fil kom med.");
  }
  const file = form.get("file");
  const threadId = form.get("thread_id");
  if (!(file instanceof File)) return problem(400, "Ingen fil kom med.");
  if (!isId(threadId)) return problem(400, "Konversationens id saknas.");

  const body = new FormData();
  body.append("file", file, file.name);
  body.append("thread_id", threadId);
  return forward(uploadsUrl(), { method: "POST", body });
}

export async function GET(request: NextRequest) {
  const threadId = request.nextUrl.searchParams.get("thread_id");
  if (!isId(threadId)) return problem(400, "Konversationens id saknas.");
  return forward(`${uploadsUrl()}?thread_id=${encodeURIComponent(threadId)}`);
}
