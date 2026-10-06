/**
 * What: the id the web app uses for the agent, shared by the runtime route and the UI.
 *
 * Why: the CopilotKit runtime registers the agent under this id and every hook in the browser
 * names it, so a typo in one place would leave the chat without an agent.
 */
export const AGENT_ID = "avtalsagent";
