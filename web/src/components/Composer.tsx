"use client";
/**
 * What: the field where the person writes a question, or an answer to the agent's question,
 * with a button that sends it or stops the agent while it works.
 *
 * Why: the chat's one input. Enter sends and Shift+Enter starts a new line, as in other chat
 * apps, and the field grows with the text up to a few lines.
 *
 * How: a plain textarea. The parent decides what sending means (a new question, or the answer
 * to an open ask_user question) and passes `running` while a run goes on, which turns the send
 * button into a stop button.
 */
import { useLayoutEffect, useRef, useState } from "react";

import { Icon } from "./icons";
import styles from "./Composer.module.css";

/** The field grows up to this height, then scrolls. */
const MAX_HEIGHT = 200;

export function Composer({
  placeholder,
  ready,
  running,
  onSend,
  onStop,
}: {
  placeholder: string;
  /** Whether the agent is connected, so that something can be sent. */
  ready: boolean;
  running: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
}) {
  const [value, setValue] = useState("");
  const field = useRef<HTMLTextAreaElement>(null);
  const canSend = ready && !running && value.trim() !== "";

  useLayoutEffect(() => {
    const element = field.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, MAX_HEIGHT)}px`;
  }, [value]);

  function send() {
    if (!canSend) return;
    onSend(value.trim());
    setValue("");
  }

  return (
    <form
      className={styles.composer}
      onSubmit={(event) => {
        event.preventDefault();
        send();
      }}
    >
      <textarea
        ref={field}
        className={styles.input}
        value={value}
        rows={1}
        placeholder={placeholder}
        aria-label={placeholder}
        data-testid="composer-input"
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault();
            send();
          }
        }}
      />
      <div className={styles.actions}>
        {running ? (
          <button type="button" className={styles.stop} onClick={onStop} aria-label="Stoppa">
            <Icon name="stop" size={16} />
          </button>
        ) : (
          <button type="submit" className={styles.send} disabled={!canSend} aria-label="Skicka">
            <Icon name="arrowUp" size={18} />
          </button>
        )}
      </div>
    </form>
  );
}
