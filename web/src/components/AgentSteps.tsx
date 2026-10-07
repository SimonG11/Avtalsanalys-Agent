"use client";
/**
 * What: one line in the chat for each tool the agent calls, updated live: what the agent is
 * doing in Swedish, the arguments it chose, and the tool's raw answer behind a disclosure.
 *
 * Why: the agent decides itself which tools to use and in which order. Showing each step as it
 * happens makes that visible, which is the point of an agent rather than a fixed workflow, and
 * lets the reader see what the answer is built on.
 *
 * How: Chat registers `AgentStep` as CopilotKit's wildcard tool renderer, so it is used for
 * every tool call in the AG-UI stream. CopilotKit passes the tool name, the (partly streamed)
 * arguments, the status and, once the tool has answered, the result.
 */
import { describeToolCall } from "@/lib/tools";

import styles from "./AgentSteps.module.css";

export interface AgentStepProps {
  name: string;
  toolCallId: string;
  parameters: unknown;
  status: "inProgress" | "executing" | "complete";
  result?: string;
}

export function AgentStep({ name, parameters, status, result }: AgentStepProps) {
  const { title, subject, details } = describeToolCall(name, parameters, status);
  const done = status === "complete";

  return (
    <div className={styles.step} data-testid="agent-step" data-tool={name} data-status={status}>
      <span className={done ? styles.done : styles.running} aria-hidden="true">
        {done ? "✓" : null}
      </span>
      <div className={styles.body}>
        <div className={styles.title}>
          {title}
          {subject && <span className={styles.subject}>{subject}</span>}
        </div>
        {details.length > 0 && (
          <div className={styles.details}>
            {details.map(([label, value]) => (
              <span key={label}>
                {label}: <code>{value}</code>
              </span>
            ))}
          </div>
        )}
        {done && result && (
          <details className={styles.result}>
            <summary>Visa svaret från verktyget</summary>
            <pre>{prettyJson(result)}</pre>
          </details>
        )}
      </div>
    </div>
  );
}

function prettyJson(text: string): string {
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}
