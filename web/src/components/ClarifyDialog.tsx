"use client";
/**
 * What: a dialog that opens when the agent asks the person a question (the ask_user tool), with
 * the agent's options as buttons and a field for an answer of one's own.
 *
 * Why: some questions cannot be answered well without more information, such as which
 * framework agreement is meant. The agent decides itself when to ask; the run then pauses until
 * the person answers, and continues with that answer.
 *
 * How: on the backend, ask_user is a LangGraph interrupt, which ag-ui-langgraph sends as an
 * AG-UI interrupt. CopilotKit's `useInterrupt` hands it to this component; `resolve(answer)`
 * starts a new run that resumes the graph with the answer as the interrupt's value. The run
 * waits for the answer, so the dialog cannot be closed without one: `closedby="none"` turns
 * off Escape where browsers support it, and the dialog reopens if it is closed anyway.
 */
import { useInterrupt } from "@copilotkit/react-core/v2";
import { useEffect, useRef, useState } from "react";

import { AGENT_ID } from "@/lib/agent";
import { parseAskUser } from "@/lib/contract";
import type { AskUser } from "@/lib/contract";

import styles from "./ClarifyDialog.module.css";

export function ClarifyDialog() {
  const element = useInterrupt({
    agentId: AGENT_ID,
    renderInChat: false,
    // Only ask_user interrupts are ours; for standard interrupts `event.value` is the interrupt.
    enabled: (event) => parseAskUser(event.value, event.value) !== null,
    render: ({ event, interrupt, resolve }) => (
      <AskUserDialog
        ask={parseAskUser(interrupt, event.value) as AskUser}
        onAnswer={(answer) => resolve(answer)}
      />
    ),
  });
  return element ?? null;
}

function AskUserDialog({
  ask,
  onAnswer,
}: {
  ask: AskUser;
  onAnswer: (answer: string) => Promise<unknown>;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const answered = useRef(false);
  const [own, setOwn] = useState("");
  const [sending, setSending] = useState(false);

  useEffect(() => {
    const element = dialog.current;
    if (element && !element.open) element.showModal();
  }, []);

  async function answer(text: string) {
    const trimmed = text.trim();
    if (!trimmed || answered.current) return;
    answered.current = true;
    setSending(true);
    dialog.current?.close();
    try {
      await onAnswer(trimmed);
    } catch (error) {
      // CopilotKit has already closed the interrupt, and Answers shows the failed run.
      console.error("The answer to the agent's question could not be sent", error);
    }
  }

  return (
    <dialog
      ref={dialog}
      className={styles.dialog}
      aria-labelledby="clarify-question"
      data-testid="clarify-dialog"
      closedby="none"
      onCancel={(event) => event.preventDefault()}
      onClose={() => {
        if (!answered.current) dialog.current?.showModal();
      }}
    >
      <p className={styles.eyebrow}>Agenten behöver veta mer</p>
      <h2 id="clarify-question" className={styles.question}>
        {ask.question}
      </h2>

      {ask.options && ask.options.length > 0 && (
        <div className={styles.options}>
          {ask.options.map((option) => (
            <button
              key={option}
              type="button"
              className={styles.option}
              disabled={sending}
              onClick={() => answer(option)}
            >
              {option}
            </button>
          ))}
        </div>
      )}

      <form
        className={styles.own}
        onSubmit={(event) => {
          event.preventDefault();
          void answer(own);
        }}
      >
        <label htmlFor="clarify-own">
          {ask.options?.length ? "Eller svara med egna ord" : "Ditt svar"}
        </label>
        <div className={styles.row}>
          <input
            id="clarify-own"
            value={own}
            onChange={(event) => setOwn(event.target.value)}
            disabled={sending}
            autoComplete="off"
          />
          <button type="submit" disabled={sending || own.trim() === ""}>
            Svara
          </button>
        </div>
      </form>
    </dialog>
  );
}
