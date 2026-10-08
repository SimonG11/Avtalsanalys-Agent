/**
 * What: GET /api/uploads/{upload_id}/file?thread_id=… gives the person's own file back, for
 * the source panel (webbapp-kontrakt.md, point 34).
 *
 * Why: a citation from an uploaded PDF opens the file at the cited page, as a citation from
 * an agreement does, and the browser only talks to the web app.
 *
 * How: both ids are checked and the file is streamed from the API with its content type
 * (lib/uploadProxy.ts). It is not cached, since the file can be removed.
 */
import type { NextRequest } from "next/server";

import { uploadsUrl } from "@/lib/backend";
import { forward, isId, problem } from "@/lib/uploadProxy";

export async function GET(
  request: NextRequest,
  context: RouteContext<"/api/uploads/[uploadId]/file">,
) {
  const { uploadId } = await context.params;
  const threadId = request.nextUrl.searchParams.get("thread_id");
  if (!isId(uploadId) || !isId(threadId)) return problem(400, "Filens id saknas.");
  return forward(`${uploadsUrl(uploadId, "/file")}?thread_id=${encodeURIComponent(threadId)}`);
}
