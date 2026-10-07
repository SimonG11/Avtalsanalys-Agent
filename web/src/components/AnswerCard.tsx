"use client";
/**
 * What: the agent's answer as a card in the chat: a status badge, the text with clickable
 * source numbers, and the list of sources with their quotes.
 *
 * Why: the answer is more than text. Each claim points to a quote in an agreement, and the
 * status says whether the validation passed ("Verifierat"), passed with a reservation, or
 * found no answer. The reader has to see that at a glance and be able to check every source.
 *
 * How: Answers.tsx decides where a card goes and which answer it shows; this component only
 * draws it. Clicking a source opens it in the source panel through OpenSourceContext.
 */
import { splitAnswerText } from "@/lib/answerText";
import { sourceLabel } from "@/lib/citation";
import type { Answer, AnswerStatus, Citation } from "@/lib/contract";

import { useOpenSource } from "./SourceContext";
import styles from "./AnswerCard.module.css";

const STATUS_LABELS: Record<AnswerStatus, string> = {
  verified: "Verifierat",
  with_reservation: "Med reservation",
  no_answer: "Inget svar",
};

const STATUS_HELP: Record<AnswerStatus, string> = {
  verified: "Varje källa är kontrollerad mot avtalstexten.",
  with_reservation: "Allt i svaret kunde inte kontrolleras. Läs reservationen och källorna.",
  no_answer: "Agenten hittade inget i avtalen som besvarar frågan.",
};

/** Help for the two statuses that read differently when the answer has no sources, or has. */
function statusHelp(answer: Answer): string {
  const hasSources = answer.citations.length > 0;
  if (answer.status === "with_reservation" && !hasSources) {
    return "Svaret har inga källor i avtalstexten att kontrollera mot, till exempel uppgifter ur registret.";
  }
  if (answer.status === "no_answer" && hasSources) {
    return "Agenten hittade inget svar i avtalen. Källorna visar var frågan regleras.";
  }
  return STATUS_HELP[answer.status];
}

export function AnswerCard({ answer }: { answer: Answer }) {
  const openSource = useOpenSource();
  const byId = new Map(answer.citations.map((citation) => [citation.id, citation]));
  const segments = splitAnswerText(answer.text, new Set(byId.keys()));

  return (
    <article className={styles.card} data-testid="answer-card" data-status={answer.status}>
      <header className={styles.header}>
        <span className={`${styles.badge} ${styles[answer.status]}`}>
          {STATUS_LABELS[answer.status]}
        </span>
        <span className={styles.help}>{statusHelp(answer)}</span>
      </header>

      <p className={styles.text}>
        {segments.map((segment, index) =>
          segment.kind === "text" ? (
            <span key={index}>{segment.text}</span>
          ) : (
            <CitationRef key={index} citation={byId.get(segment.id)!} onOpen={openSource} />
          ),
        )}
      </p>

      {answer.citations.length > 0 && (
        <ol className={styles.sources}>
          {answer.citations.map((citation) => (
            <li key={citation.id}>
              <button
                type="button"
                className={styles.source}
                onClick={() => openSource(citation)}
                data-testid={`source-${citation.id}`}
              >
                <span className={styles.sourceNumber}>{citation.id}</span>
                <span className={styles.sourceBody}>
                  <span className={styles.sourceTitle}>{sourceLabel(citation)}</span>
                  <span className={styles.quote}>”{citation.quote}”</span>
                  {!citation.verified && (
                    <span className={styles.unverified}>
                      Citatet kunde inte kontrolleras mot avtalet
                    </span>
                  )}
                </span>
              </button>
            </li>
          ))}
        </ol>
      )}
    </article>
  );
}

function CitationRef({ citation, onOpen }: { citation: Citation; onOpen: (c: Citation) => void }) {
  return (
    <button
      type="button"
      className={citation.verified ? styles.ref : `${styles.ref} ${styles.refUnverified}`}
      onClick={() => onOpen(citation)}
      title={sourceLabel(citation)}
      data-testid={`ref-${citation.id}`}
    >
      {citation.id}
    </button>
  );
}
