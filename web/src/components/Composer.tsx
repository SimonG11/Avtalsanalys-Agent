"use client";
/**
 * What: the field where the person writes a question, or an answer to the agent's question,
 * with a button that sends it or stops the agent while it works, and a button to attach files.
 *
 * Why: the chat's one input. Enter sends and Shift+Enter starts a new line, as in other chat
 * apps, and the field grows with the text up to a few lines. Files to compare with the
 * agreements are attached here too, with the paperclip or by dropping them on the field.
 *
 * How: a plain textarea. The parent decides what sending means (a new question, or the answer
 * to an open ask_user question) and passes `running` while a run goes on, which turns the send
 * button into a stop button. With `files`, the field shows the attached files as chips and
 * waits to send until they are uploaded (components/Uploads.tsx).
 */
import { useLayoutEffect, useRef, useState } from "react";

import { ACCEPTED_EXTENSIONS } from "@/lib/uploads";

import { FileChip } from "./FileChip";
import { Icon } from "./icons";
import type { AttachedFile } from "./Uploads";
import styles from "./Composer.module.css";

/** The field grows up to this height, then scrolls. */
const MAX_HEIGHT = 200;

export function Composer({
  placeholder,
  ready,
  running,
  onSend,
  onStop,
  files,
}: {
  placeholder: string;
  /** Whether the agent is connected, so that something can be sent. */
  ready: boolean;
  running: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
  /** The attached files, or null where files cannot be attached (an answer to the agent). */
  files: {
    attached: readonly AttachedFile[];
    onAdd: (files: File[]) => void;
    onRemove: (key: string) => void;
  } | null;
}) {
  const [value, setValue] = useState("");
  const [dragging, setDragging] = useState(false);
  const field = useRef<HTMLTextAreaElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const uploading = files?.attached.some((file) => file.status === "uploading") ?? false;
  const canSend = ready && !running && !uploading && value.trim() !== "";

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

  const hasFiles = (event: React.DragEvent) => event.dataTransfer.types.includes("Files");

  return (
    <form
      className={dragging ? `${styles.composer} ${styles.dragging}` : styles.composer}
      onSubmit={(event) => {
        event.preventDefault();
        send();
      }}
      onDragOver={(event) => {
        if (!files || !hasFiles(event)) return;
        event.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => {
        if (!files || !hasFiles(event)) return;
        event.preventDefault();
        setDragging(false);
        files.onAdd([...event.dataTransfer.files]);
      }}
    >
      {files && files.attached.length > 0 && (
        <div className={styles.files}>
          {files.attached.map((file) => (
            <FileChip key={file.key} file={file} onRemove={() => files.onRemove(file.key)} />
          ))}
        </div>
      )}
      <div className={styles.row}>
        {files && (
          <>
            <button
              type="button"
              className={styles.attach}
              onClick={() => picker.current?.click()}
              aria-label="Bifoga fil"
              title="Bifoga en fil att jämföra med avtalen: PDF, Word (.docx) eller text, högst 10 MB"
            >
              <Icon name="attach" size={18} />
            </button>
            <input
              ref={picker}
              type="file"
              hidden
              multiple
              accept={ACCEPTED_EXTENSIONS.join(",")}
              data-testid="file-input"
              onChange={(event) => {
                files.onAdd([...(event.target.files ?? [])]);
                event.target.value = "";
              }}
            />
          </>
        )}
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
      </div>
    </form>
  );
}
