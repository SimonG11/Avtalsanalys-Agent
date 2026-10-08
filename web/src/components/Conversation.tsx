"use client";
/**
 * What: the conversation: each question with the agent's work on it and its answer, the field
 * to ask in, and before the first question a welcome with example questions.
 *
 * Why: the point of the app is to see an agent find an answer, so the conversation is built
 * around that, as in Claude: the question, then a timeline of what the agent thought and did,
 * then the answer with its sources. The agent's own questions come in the conversation too.
 *
 * How: CopilotKit's `useAgent` holds the AG-UI agent and its messages; lib/turns.ts groups the
 * messages into one turn per question. A question is sent by adding a user message and asking
 * CopilotKit to run the agent, which goes through the CopilotKit runtime at /api/copilotkit to
 * the backend's AG-UI endpoint. Answers.tsx keeps each question's answer, and AskUser.tsx
 * shows the agent's question under the turn that asked it. The list keeps to the bottom while
 * new things arrive, unless the person has scrolled up to read.
 */
import { UseAgentUpdate, randomUUID, useAgent, useCopilotKit } from "@copilotkit/react-core/v2";
import { useCallback, useLayoutEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

import { AGENT_ID } from "@/lib/agent";
import { buildTurns } from "@/lib/turns";
import type { ConversationMessage, Turn } from "@/lib/turns";

import { PendingAnswerContext, useAskUser } from "./AskUser";
import { Composer } from "./Composer";
import { TurnOutcome, useAnswers } from "./Answers";
import type { Outcome } from "./Answers";
import { Timeline } from "./Timeline";
import styles from "./Conversation.module.css";

const UPDATES = [
  UseAgentUpdate.OnMessagesChanged,
  UseAgentUpdate.OnStateChanged,
  UseAgentUpdate.OnRunStatusChanged,
];

const EXAMPLE_QUESTIONS = [
  { title: "Uppsägningstid", message: "Vilken uppsägningstid gäller för ett kontrakt?" },
  { title: "Uppsägning i IT-drift", message: "Hur säger kunden upp ett kontrakt inom IT-drift?" },
  { title: "Vite", message: "Vilket vite gäller vid försenad leverans?" },
  { title: "Avtal i registret", message: "Vilka avtalsnummer finns inom IT-drift?" },
];

const DISCLAIMER = "Svaren bygger på avtalstexten. Öppna källan innan du agerar på ett svar.";

export function Conversation() {
  const { agent, isReady } = useAgent({ agentId: AGENT_ID, updates: UPDATES });
  const { copilotkit } = useCopilotKit();
  const { byQuestion, durations } = useAnswers();
  const [pendingAnswer, setPendingAnswer] = useState<((text: string) => void) | null>(null);
  const registerPendingAnswer = useCallback(
    (answer: ((text: string) => void) | null) => setPendingAnswer(() => answer),
    [],
  );
  const askCards = useAskUser();
  const { scrollRef, onScroll, pin } = useStickToBottom();

  const turns = buildTurns(agent.messages as readonly ConversationMessage[]);
  const running = agent.isRunning;

  async function send(text: string) {
    if (pendingAnswer) {
      pendingAnswer(text);
      return;
    }
    pin();
    agent.addMessage({ id: randomUUID(), role: "user", content: text });
    try {
      await copilotkit.runAgent({ agent });
    } catch (error) {
      // Answers.tsx shows the failed run under the question.
      console.error("The question could not be sent to the agent", error);
    }
  }

  function stop() {
    try {
      copilotkit.stopAgent({ agent });
    } catch {
      agent.abortRun();
    }
  }

  const composer = (
    <Composer
      placeholder={pendingAnswer ? "Skriv ett eget svar…" : "Ställ en fråga om ramavtalen…"}
      ready={isReady}
      running={running}
      onSend={(text) => void send(text)}
      onStop={stop}
    />
  );

  if (turns.length === 0) {
    return (
      <div className={styles.welcome}>
        <div className={styles.welcomeInner}>
          <h2 className={styles.welcomeTitle}>Vad vill du veta om ramavtalen?</h2>
          <p className={styles.welcomeText}>
            Agenten söker själv i Statens inköpscentrals ramavtal och i registret över avtalen,
            kontrollerar sitt svar och visar källorna, så att du kan läsa dem i avtalet.
          </p>
          {composer}
          {isReady && (
            <div className={styles.examples}>
              {EXAMPLE_QUESTIONS.map((example) => (
                <button
                  key={example.title}
                  type="button"
                  className={styles.example}
                  onClick={() => void send(example.message)}
                >
                  <span className={styles.exampleTitle}>{example.title}</span>
                  <span className={styles.exampleText}>{example.message}</span>
                </button>
              ))}
            </div>
          )}
          <p className={styles.disclaimer}>{DISCLAIMER}</p>
        </div>
      </div>
    );
  }

  return (
    <PendingAnswerContext.Provider value={registerPendingAnswer}>
      <div className={styles.conversation}>
        <div className={styles.scroll} ref={scrollRef} onScroll={onScroll}>
          <div className={styles.thread}>
            {turns.map((turn, index) => {
              const last = index === turns.length - 1;
              return (
                <TurnView
                  key={turn.questionId}
                  turn={turn}
                  running={running && last}
                  waiting={last && pendingAnswer !== null}
                  outcome={byQuestion.get(turn.questionId)}
                  milliseconds={durations.get(turn.questionId) ?? null}
                >
                  {last && askCards}
                </TurnView>
              );
            })}
          </div>
        </div>
        <div className={styles.dock}>
          {composer}
          <p className={styles.disclaimer}>{DISCLAIMER}</p>
        </div>
      </div>
    </PendingAnswerContext.Provider>
  );
}

function TurnView({
  turn,
  running,
  waiting,
  outcome,
  milliseconds,
  children,
}: {
  turn: Turn;
  running: boolean;
  waiting: boolean;
  outcome: Outcome | undefined;
  milliseconds: number | null;
  children: ReactNode;
}) {
  const answered = outcome !== undefined && outcome.kind === "answer";
  return (
    <article className={styles.turn} data-testid="turn">
      <div className={styles.question} data-testid="question">
        {turn.question}
      </div>
      {(running || turn.items.length > 0) && (
        <Timeline
          items={turn.items}
          running={running}
          answered={answered}
          waiting={waiting}
          seconds={milliseconds === null ? null : Math.max(1, Math.round(milliseconds / 1000))}
        />
      )}
      {children}
      <TurnOutcome outcome={outcome} />
    </article>
  );
}

/**
 * Keeps a scrolling list at its bottom while content arrives, unless the person has scrolled
 * up; `pin` brings it back down, for example when a new question is sent.
 */
function useStickToBottom() {
  const scrollRef = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);

  useLayoutEffect(() => {
    const element = scrollRef.current;
    if (element && pinned.current) element.scrollTop = element.scrollHeight;
  });

  return {
    scrollRef,
    onScroll: () => {
      const element = scrollRef.current;
      if (!element) return;
      pinned.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80;
    },
    pin: () => {
      pinned.current = true;
    },
  };
}
