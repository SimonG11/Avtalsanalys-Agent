"use client";
/**
 * What: the agent's question to the person (the ask_user tool), shown in the chat as a message
 * from the agent with its answer options as buttons. An answer of one's own is written in the
 * ordinary chat field.
 *
 * Why: some questions cannot be answered well without knowing more, such as which framework
 * agreement or which case is meant. The agent decides itself when to ask, and it asks seldom
 * (webbapp-kontrakt.md, points 31-32). The question belongs to the conversation, so it is shown
 * where the conversation is, not in a box on top of it.
 *
 * How: on the backend, ask_user is a LangGraph interrupt, which ag-ui-langgraph sends as an
 * AG-UI interrupt (the standard outcome, the older on_interrupt event, or both).
 * `useAskUser` hands it to CopilotKit's `useInterrupt` and returns the cards for the chat to
 * place under the question. A run may ask more than one question; each has its own interrupt id
 * and its own card, and `resolve(answer, id)` records each answer. CopilotKit resumes the graph
 * once every question has one. The first unanswered card tells the chat field (through
 * PendingAnswerContext) that what is typed there answers it.
 */
import { useInterrupt } from "@copilotkit/react-core/v2";
import type { Interrupt, InterruptEvent, InterruptResolveFn } from "@copilotkit/react-core/v2";
import { createContext, useContext, useEffect, useId, useRef, useState } from "react";
import type { ReactElement } from "react";

import { AGENT_ID } from "@/lib/agent";
import { parseAskUser } from "@/lib/contract";
import type { AskUser } from "@/lib/contract";

import { Icon } from "./icons";
import styles from "./AskUser.module.css";

/** Lets the chat field answer the open question: set to a function, or null when none is open. */
export const PendingAnswerContext = createContext<
  (answer: ((text: string) => void) | null) => void
>(() => {});

export function useAskUser(): ReactElement | null {
  const element = useInterrupt({
    agentId: AGENT_ID,
    renderInChat: false,
    // Only ask_user interrupts are ours; for standard interrupts `event.value` is the interrupt.
    enabled: (event) => parseAskUser(event.value, event.value) !== null,
    render: ({ event, interrupts, resolve }) => (
      <AskUserCards event={event} interrupts={interrupts} resolve={resolve} />
    ),
  });
  return element ?? null;
}

interface OpenQuestion {
  /** The interrupt's id, or undefined for the older on_interrupt event, which has none. */
  id: string | undefined;
  key: string;
  ask: AskUser;
}

function questionsOf(event: InterruptEvent, interrupts: readonly Interrupt[]): OpenQuestion[] {
  if (interrupts.length === 0) {
    const ask = parseAskUser(null, event.value);
    return ask ? [{ id: undefined, key: "legacy", ask }] : [];
  }
  return interrupts.flatMap((interrupt) => {
    const ask = parseAskUser(interrupt, undefined);
    return ask ? [{ id: interrupt.id, key: interrupt.id, ask }] : [];
  });
}

function AskUserCards({
  event,
  interrupts,
  resolve,
}: {
  event: InterruptEvent;
  interrupts: readonly Interrupt[];
  resolve: InterruptResolveFn;
}) {
  const setPendingAnswer = useContext(PendingAnswerContext);
  const answeredRef = useRef(new Set<string>());
  const [answered, setAnswered] = useState<ReadonlySet<string>>(new Set());
  const open = questionsOf(event, interrupts).filter((question) => !answered.has(question.key));

  async function answer(question: OpenQuestion, text: string) {
    const trimmed = text.trim();
    if (!trimmed || answeredRef.current.has(question.key)) return;
    answeredRef.current.add(question.key);
    setAnswered(new Set(answeredRef.current));
    try {
      await resolve(trimmed, question.id);
    } catch (error) {
      // CopilotKit has already closed the interrupt, and the chat shows the failed run.
      console.error("The answer to the agent's question could not be sent", error);
    }
  }

  // The chat field answers the first open question.
  const answerRef = useRef(answer);
  useEffect(() => {
    answerRef.current = answer;
  });
  const first = open[0];
  const firstKey = first?.key;
  useEffect(() => {
    if (!first) return;
    setPendingAnswer((text) => void answerRef.current(first, text));
    return () => setPendingAnswer(null);
    // `first` is the same question as long as its key is the same.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [firstKey, setPendingAnswer]);

  return (
    <>
      {open.map((question) => (
        <AskUserCard
          key={question.key}
          ask={question.ask}
          onAnswer={(text) => void answer(question, text)}
        />
      ))}
    </>
  );
}

function AskUserCard({ ask, onAnswer }: { ask: AskUser; onAnswer: (text: string) => void }) {
  const titleId = useId();
  const options = ask.options ?? [];
  return (
    <section className={styles.card} aria-labelledby={titleId} data-testid="clarify-card">
      <div className={styles.eyebrow}>
        <Icon name="question" size={15} />
        Agenten behöver veta mer
      </div>
      <p id={titleId} className={styles.question}>
        {ask.question}
      </p>
      {options.length > 0 && (
        <div className={styles.options}>
          {options.map((option) => (
            <button
              key={option}
              type="button"
              className={styles.option}
              onClick={() => onAnswer(option)}
            >
              {option}
            </button>
          ))}
        </div>
      )}
      <p className={styles.hint}>
        {options.length > 0
          ? "Eller skriv ett eget svar i rutan nedan."
          : "Skriv ditt svar i rutan nedan."}
      </p>
    </section>
  );
}
