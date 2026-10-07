"use client";
/**
 * What: how the agent worked on a question, live: its thoughts, every tool call with what it
 * looked for and found, and the check of its answer, in the order they happened.
 *
 * Why: the agent decides itself which tools to use and in which order, and checks its own
 * answer before it is shown. Showing that as it happens is the point of an agent rather than a
 * fixed workflow, and it lets the reader see what the answer rests on. A draft the check sent
 * back stays in the list with the reason, so the self-correction is visible too.
 *
 * How: lib/turns.ts turns the messages into the items. The block is open while the agent works
 * and folds into one line ("Arbetade i 14 s · 3 steg") once the answer is there, like a chat
 * model's thinking; a click opens or closes it, and then it stays as the person left it. The
 * header says what the agent is doing right now.
 */
import { useState } from "react";

import { currentActivity, splitThought, stepCount } from "@/lib/turns";
import type { TimelineItem } from "@/lib/turns";
import { describeToolCall, summarizeResult } from "@/lib/tools";

import { Icon, Spinner } from "./icons";
import type { IconName } from "./icons";
import styles from "./Timeline.module.css";

const TOOL_ICONS: Record<string, IconName> = {
  search_documents: "search",
  read_section: "document",
  get_outline: "outline",
  list_documents: "outline",
  resolve_reference: "link",
  search_register: "register",
  find_amendments: "history",
  calculate_date: "calendar",
  ask_user: "question",
};

export function Timeline({
  items,
  running,
  answered,
  waiting,
  seconds,
}: {
  items: readonly TimelineItem[];
  /** Whether the agent is working on this question now. */
  running: boolean;
  /** Whether the agent waits for the person's answer to its question. */
  waiting: boolean;
  /** Whether the question has its answer. */
  answered: boolean;
  /** How long the agent worked on the question, when known. */
  seconds: number | null;
}) {
  const [chosen, setChosen] = useState<boolean | null>(null);
  const open = chosen ?? (running || !answered);
  const visible = items.filter((item) => item.kind !== "thought" || item.text.trim() !== "");
  const steps = stepCount(items);

  return (
    <section className={styles.timeline} data-testid="timeline" data-open={open}>
      <button
        type="button"
        className={styles.header}
        aria-expanded={open}
        onClick={() => setChosen(!open)}
        data-testid="timeline-header"
      >
        {running ? (
          <span className={styles.headerIcon}>
            <Spinner />
          </span>
        ) : (
          <span className={`${styles.headerIcon} ${styles.chevron}`} data-open={open}>
            <Icon name="chevron" size={14} />
          </span>
        )}
        <span className={running ? styles.activity : undefined}>
          {running ? currentActivity(items) : summary(seconds, steps, waiting)}
        </span>
      </button>

      {open && visible.length > 0 && (
        <ol className={styles.items}>
          {visible.map((item, index) => (
            <li key={item.id} className={styles.item}>
              {item.kind === "thought" ? (
                <Thought text={item.text} />
              ) : item.kind === "tool" ? (
                <AgentStep name={item.name} args={item.args} result={item.result} />
              ) : (
                <Draft
                  outcome={item.outcome}
                  reason={item.reason}
                  checking={running && index === visible.length - 1 && !answered}
                  answered={answered}
                />
              )}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function summary(seconds: number | null, steps: number, waiting: boolean): string {
  const stepText = steps === 1 ? "1 steg" : `${steps} steg`;
  if (waiting) return `Väntar på ditt svar · ${stepText}`;
  if (seconds === null) return `Så arbetade agenten · ${stepText}`;
  return `Arbetade i ${seconds} s · ${stepText}`;
}

/**
 * A summary of the model's reasoning. OpenAI often starts it with a title in **bold**; that line
 * becomes the item's title, and the rest is shown as it streams.
 */
function Thought({ text }: { text: string }) {
  const { title, body } = splitThought(text);
  return (
    <div className={styles.row} data-testid="thought">
      <span className={styles.marker}>
        <Icon name="thought" size={15} />
      </span>
      <div className={styles.body}>
        <div className={styles.title}>{title ?? "Tänker"}</div>
        {body && <p className={styles.thought}>{renderBold(body)}</p>}
      </div>
    </div>
  );
}

/** Text with **bold** parts, as React nodes (no HTML is parsed). */
function renderBold(text: string) {
  return text
    .split(/(\*\*[^*]+\*\*)/g)
    .map((part, index) =>
      part.startsWith("**") && part.endsWith("**") && part.length > 4 ? (
        <strong key={index}>{part.slice(2, -2)}</strong>
      ) : (
        part
      ),
    );
}

/**
 * One tool call: what the agent is doing in Swedish, the arguments it chose, what the tool found
 * when that fits on a line (a date calculation, a number of amendments, the person's answer),
 * and the tool's raw answer behind a disclosure.
 */
export function AgentStep({
  name,
  args,
  result,
}: {
  name: string;
  args: unknown;
  result?: string;
}) {
  const status = result === undefined ? "inProgress" : "complete";
  const done = status === "complete";
  const { title, subject, details } = describeToolCall(name, args, status);
  const isQuestion = name === "ask_user";
  const outcome = done
    ? isQuestion
      ? `Du svarade: ${result}`
      : summarizeResult(name, result)
    : null;

  return (
    <div className={styles.row} data-testid="agent-step" data-tool={name} data-status={status}>
      <span className={done ? styles.marker : `${styles.marker} ${styles.markerRunning}`}>
        <Icon name={TOOL_ICONS[name] ?? "tool"} size={15} />
      </span>
      <div className={styles.body}>
        <div className={styles.title}>
          {title}
          {!done && " …"}
          {subject && <span className={styles.subject}>{subject}</span>}
        </div>
        {!isQuestion && details.length > 0 && (
          <div className={styles.details}>
            {details.map(([label, value]) => (
              <span key={label}>
                {label}: <code>{value}</code>
              </span>
            ))}
          </div>
        )}
        {outcome && (
          <div className={styles.outcome} data-testid="step-outcome">
            {outcome}
          </div>
        )}
        {done && !isQuestion && result && (
          <details className={styles.result}>
            <summary>Visa svaret från verktyget</summary>
            <pre>{prettyJson(result)}</pre>
          </details>
        )}
      </div>
    </div>
  );
}

/**
 * A draft of the answer handed in for the check. While the check runs (the review alone takes
 * about nine seconds and sends no events, webbapp-kontrakt.md point 27) it says so; a draft
 * sent back says why, and the one that passed says it was checked.
 */
function Draft({
  outcome,
  reason,
  checking,
  answered,
}: {
  outcome: "pending" | "submitted" | "rejected";
  reason: string | null;
  checking: boolean;
  answered: boolean;
}) {
  if (outcome === "rejected") {
    return (
      <div className={styles.row} data-testid="draft" data-outcome="rejected">
        <span className={`${styles.marker} ${styles.markerWarn}`}>
          <Icon name="retry" size={15} />
        </span>
        <div className={styles.body}>
          <div className={styles.title}>Kontrollen skickade tillbaka utkastet</div>
          <div className={styles.outcome}>{reason ?? "Agenten skriver om svaret."}</div>
        </div>
      </div>
    );
  }
  if (checking) {
    return (
      <div className={styles.row} role="status" data-testid="answer-check">
        <span className={`${styles.marker} ${styles.markerRunning}`}>
          <Icon name="shield" size={15} />
        </span>
        <div className={styles.body}>
          <div className={styles.title}>Kontrollerar svaret …</div>
          <div className={styles.details}>
            Citaten jämförs ordagrant med avtalstexten och en granskare läser svaret.
          </div>
        </div>
      </div>
    );
  }
  return (
    <div className={styles.row} data-testid="draft" data-outcome="submitted">
      <span className={`${styles.marker} ${answered ? styles.markerOk : ""}`}>
        <Icon name="shield" size={15} />
      </span>
      <div className={styles.body}>
        <div className={styles.title}>
          {answered ? "Kontrollerade svaret" : "Lämnade in svaret"}
        </div>
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
