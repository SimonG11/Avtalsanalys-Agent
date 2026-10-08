/**
 * What: DELETE /api/uploads/{upload_id}?thread_id=… removes one of the person's files
 * (webbapp-kontrakt.md, point 34).
 *
 * Why: a file attached by mistake should not stay with the conversation for the agent to read.
 *
 * How: both ids are checked and the request goes on to the API (lib/uploadProxy.ts).
 */
import type { NextRequest } from "next/server";

import { uploadsUrl } from "@/lib/backend";
import { forward, isId, problem } from "@/lib/uploadProxy";

export async function DELETE(
  request: NextRequest,
  context: RouteContext<"/api/uploads/[uploadId]">,
) {
  const { uploadId } = await context.params;
  const threadId = request.nextUrl.searchParams.get("thread_id");
  if (!isId(uploadId) || !isId(threadId)) return problem(400, "Filens id saknas.");
  return forward(`${uploadsUrl(uploadId)}?thread_id=${encodeURIComponent(threadId)}`, {
    method: "DELETE",
  });
}
