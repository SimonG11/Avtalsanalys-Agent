"use client";
/**
 * What: keeps every question's answer and places its card in the chat, after the last message
 * that belongs to the question.
 *
 * Why: the backend delivers the answer in the agent's shared state (`answer`), not as a chat
 * message, because the card needs structured data: the status and the citations. The state
 * only holds the latest answer, so the answers to earlier questions are kept here, one per
 * question, and the chat history keeps all its cards.
 *
 * How: when a run finishes successfully and has sent state, the answer in the state is saved
 * under the question that run answered (the last user message). A run that fails is marked as
 * failed instead, since the state it leaves is the previous question's. An ask_user interrupt
 * finishes a run without an answer; the resumed run saves it. CopilotKit calls
 * `answerRenderers` around every chat message, and lib/answerAnchors.ts says after which
 * message each question's card goes.
 */
import { UseAgentUpdate, useAgent } from "@copilotkit/react-core/v2";
import type { ReactCustomMessageRenderer } from "@copilotkit/react-core/v2";
import { createContext, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";

import { AGENT_ID } from "@/lib/agent";
import { answerAnchors, lastQuestionId } from "@/lib/answerAnchors";
import { parseAnswer } from "@/lib/contract";
import type { ParsedAnswer } from "@/lib/contract";

import { AnswerCard } from "./AnswerCard";
import styles from "./AnswerCard.module.css";

/** What a question's run ended with: an answer (or a broken one), or a failed run. */
type Outcome = ParsedAnswer | { kind: "failed" };

interface Answers {
  /** Message id -> id of the question whose card goes after that message. */
  anchors: ReadonlyMap<string, string>;
  /** Question id -> how its run ended. */
  byQuestion: ReadonlyMap<string, Outcome>;
}

const AnswersContext = createContext<Answers>({ anchors: new Map(), byQuestion: new Map() });

const MESSAGE_UPDATES = [UseAgentUpdate.OnMessagesChanged];

export function AnswersProvider({ children }: { children: ReactNode }) {
  const { agent } = useAgent({ agentId: AGENT_ID, updates: MESSAGE_UPDATES });
  const [byQuestion, setByQuestion] = useState<ReadonlyMap<string, Outcome>>(new Map());

  useEffect(() => {
    // Whether the current run has sent state. Until it does, the state is the previous run's.
    let stateReceived = false;
    const save = (messages: Parameters<typeof lastQuestionId>[0], outcome: Outcome) => {
      const question = lastQuestionId(messages);
      if (question === null) return;
      setByQuestion((previous) => new Map(previous).set(question, outcome));
    };
    const subscription = agent.subscribe({
      onRunStartedEvent: () => {
        stateReceived = false;
      },
      onStateSnapshotEvent: () => {
        stateReceived = true;
      },
      onStateDeltaEvent: () => {
        stateReceived = true;
      },
      onRunFinishedEvent: ({ outcome, messages, state }) => {
        if (outcome !== "success" || !stateReceived) return;
        const parsed = parseAnswer(state);
        if (parsed.kind !== "none") save(messages, parsed);
      },
      // The backend reported an error (RUN_ERROR), or the run itself failed, e.g. no connection.
      onRunErrorEvent: ({ messages }) => save(messages, { kind: "failed" }),
      onRunFailed: ({ messages }) => save(messages, { kind: "failed" }),
    });
    return () => subscription.unsubscribe();
  }, [agent]);

  const anchors = answerAnchors(agent.messages);
  return (
    <AnswersContext.Provider value={{ anchors, byQuestion }}>{children}</AnswersContext.Provider>
  );
}

function AnswerAfterMessage({
  message,
  position,
}: {
  message: { id: string };
  position: "before" | "after";
}) {
  const { anchors, byQuestion } = useContext(AnswersContext);
  if (position !== "after") return null;
  const question = anchors.get(message.id);
  const parsed = question === undefined ? undefined : byQuestion.get(question);
  if (parsed === undefined || parsed.kind === "none") return null;
  if (parsed.kind === "failed") {
    return (
      <div className={styles.invalid} role="alert" data-testid="run-failed">
        Agenten kunde inte svara på frågan. Försök igen om en stund.
      </div>
    );
  }
  if (parsed.kind === "invalid") {
    return (
      <div className={styles.invalid} role="alert">
        Svaret från agenten följer inte det avtalade formatet ({parsed.problem}).
      </div>
    );
  }
  return <AnswerCard answer={parsed.answer} />;
}

/** Registered on CopilotKitProvider; a module-level constant so it is not re-registered. */
export const answerRenderers: ReactCustomMessageRenderer[] = [
  { agentId: AGENT_ID, render: AnswerAfterMessage },
];
