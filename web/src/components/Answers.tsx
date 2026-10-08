"use client";
/**
 * What: keeps every question's answer, and how long the agent worked on it, and shows a
 * question's outcome under its timeline: the answer, or a line saying the agent could not
 * answer.
 *
 * Why: the backend delivers the answer in the agent's shared state (`answer`), not as a chat
 * message, because the answer needs structured data: the status and the citations. The state
 * only holds the latest answer, so the answers to earlier questions are kept here, one per
 * question, and the conversation keeps all of them.
 *
 * How: when a run finishes successfully and has sent state, the answer in the state is saved
 * under the question that run answered (the last user message). A run that fails is marked as
 * failed instead, since the state it leaves is the previous question's. An ask_user interrupt
 * finishes a run without an answer; the resumed run saves it. Each run's time is added to its
 * question's, so a question that paused for the person's answer counts only the agent's work.
 */
import { UseAgentUpdate, useAgent } from "@copilotkit/react-core/v2";
import { createContext, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";

import { AGENT_ID } from "@/lib/agent";
import { lastQuestionId } from "@/lib/turns";
import { parseAnswer } from "@/lib/contract";
import type { ParsedAnswer } from "@/lib/contract";

import { AnswerCard } from "./AnswerCard";
import { Icon } from "./icons";
import styles from "./AnswerCard.module.css";

/** What a question's run ended with: an answer (or a broken one), or a failed run. */
export type Outcome = ParsedAnswer | { kind: "failed" };

interface Answers {
  /** Question id -> how its run ended. */
  byQuestion: ReadonlyMap<string, Outcome>;
  /** Question id -> milliseconds the agent worked on it. */
  durations: ReadonlyMap<string, number>;
  /** Forgets every answer, for a new conversation. */
  clear: () => void;
}

const AnswersContext = createContext<Answers>({
  byQuestion: new Map(),
  durations: new Map(),
  clear: () => {},
});

const NO_UPDATES: UseAgentUpdate[] = [];

export function AnswersProvider({ children }: { children: ReactNode }) {
  const { agent } = useAgent({ agentId: AGENT_ID, updates: NO_UPDATES });
  const [byQuestion, setByQuestion] = useState<ReadonlyMap<string, Outcome>>(new Map());
  const [durations, setDurations] = useState<ReadonlyMap<string, number>>(new Map());

  useEffect(() => {
    // Whether the current run has sent state. Until it does, the state is the previous run's.
    let stateReceived = false;
    let started: { question: string; at: number } | null = null;
    const save = (messages: Parameters<typeof lastQuestionId>[0], outcome: Outcome) => {
      const question = lastQuestionId(messages);
      if (question === null) return;
      setByQuestion((previous) => new Map(previous).set(question, outcome));
    };
    const subscription = agent.subscribe({
      onRunStartedEvent: ({ messages }) => {
        stateReceived = false;
        const question = lastQuestionId(messages);
        started = question === null ? null : { question, at: Date.now() };
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
      onRunFinalized: () => {
        if (started === null) return;
        const { question, at } = started;
        started = null;
        setDurations((previous) =>
          new Map(previous).set(question, (previous.get(question) ?? 0) + Date.now() - at),
        );
      },
    });
    return () => subscription.unsubscribe();
  }, [agent]);

  const clear = () => {
    setByQuestion(new Map());
    setDurations(new Map());
  };
  return (
    <AnswersContext.Provider value={{ byQuestion, durations, clear }}>
      {children}
    </AnswersContext.Provider>
  );
}

export function useAnswers(): Answers {
  return useContext(AnswersContext);
}

/** A question's outcome: its answer, or why there is none. Nothing while there is none yet. */
export function TurnOutcome({ outcome }: { outcome: Outcome | undefined }) {
  if (outcome === undefined || outcome.kind === "none") return null;
  if (outcome.kind === "failed") {
    return (
      <div className={styles.problem} role="alert" data-testid="run-failed">
        <Icon name="alert" size={16} />
        Agenten kunde inte svara på frågan. Försök igen om en stund.
      </div>
    );
  }
  if (outcome.kind === "invalid") {
    return (
      <div className={styles.problem} role="alert">
        <Icon name="alert" size={16} />
        Svaret från agenten följer inte det avtalade formatet ({outcome.problem}).
      </div>
    );
  }
  return <AnswerCard answer={outcome.answer} />;
}
