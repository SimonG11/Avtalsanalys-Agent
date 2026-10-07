/**
 * What: the CopilotKit runtime, mounted at /api/copilotkit. It connects the chat in the browser
 * to the agent in the backend.
 *
 * Why: CopilotKit's v2 architecture puts a small runtime between the browser and the agent.
 * The browser asks the runtime which agents exist (GET /api/copilotkit/info) and starts runs
 * through it; the runtime forwards each run to the agent's AG-UI endpoint and streams the
 * events back. The agent itself (LangGraph in Python) only has to speak AG-UI.
 *
 * How: one agent, "avtalsagent", reached with AG-UI's HttpAgent at API_URL/agui. The agent
 * is created per request (`agents` is a function) so API_URL is read at runtime.
 */
import { HttpAgent } from "@ag-ui/client";
import { CopilotRuntime, createCopilotRuntimeHandler } from "@copilotkit/runtime/v2";

import { AGENT_ID } from "@/lib/agent";
import { agentUrl } from "@/lib/backend";

const runtime = new CopilotRuntime({
  agents: () => ({ [AGENT_ID]: new HttpAgent({ url: agentUrl() }) }),
});

const handler = createCopilotRuntimeHandler({ runtime, basePath: "/api/copilotkit" });

export const GET = handler;
export const POST = handler;
