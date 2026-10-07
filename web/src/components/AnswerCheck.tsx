"use client";
/**
 * What: a line in the chat that says the answer is being checked, from the moment the agent
 * starts handing it in until the answer arrives.
 *
 * Why: the agent hands in its answer by calling FinalAnswer, and the check then reads every
 * cited section and the register, and a second model reviews the answer. The review alone
 * takes about nine seconds, and no events come meanwhile (webbapp-kontrakt.md, point 27).
 * Without this line the chat would look stuck right before the answer.
 *
 * How: Chat renders this component for every FinalAnswer call instead of a step. It shows
 * while the call has no result yet, the run is going, it is the latest tool call of the latest
 * question and the state has no answer. The check answers a rejected draft, but the answer may
 * only come at the end of the run, so a rejected draft's line goes away when the agent calls its
 * next tool, and the next draft gets a line of its own. CopilotKit gives a backend tool the
 * same status while its arguments stream and while it waits for its result, so the line cannot
 * tell writing from checking and says one thing throughout.
 */
import { UseAgentUpdate, useAgent } from "@copilotkit/react-core/v2";

import { AGENT_ID } from "@/lib/agent";
import { isLatestToolCall } from "@/lib/answerAnchors";
import { parseAnswer } from "@/lib/contract";

import type { AgentStepProps } from "./AgentSteps";
import styles from "./AgentSteps.module.css";

const UPDATES = [
  UseAgentUpdate.OnMessagesChanged,
  UseAgentUpdate.OnStateChanged,
  UseAgentUpdate.OnRunStatusChanged,
];

export function AnswerCheck({ toolCallId, status }: AgentStepProps) {
  const { agent } = useAgent({ agentId: AGENT_ID, updates: UPDATES });
  const waiting =
    status !== "complete" &&
    agent.isRunning &&
    isLatestToolCall(agent.messages, toolCallId) &&
    parseAnswer(agent.state).kind === "none";
  if (!waiting) return null;

  return (
    <div className={styles.step} role="status" data-testid="answer-check">
      <span className={styles.running} aria-hidden="true" />
      <div className={styles.body}>
        <div className={styles.title}>Kontrollerar svaret …</div>
      </div>
    </div>
  );
}
