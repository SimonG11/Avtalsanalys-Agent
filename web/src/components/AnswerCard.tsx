"use client";
/**
 * What: the agent's answer as a card in the chat: a status badge, the text with clickable
 * source numbers, the reservations, the agreements the register facts come from, and the list
 * of sources with their quotes.
 *
 * Why: the answer is more than text. Each claim points to a quote in an agreement or a row in
 * the register, and the status says whether the validation passed ("Verifierat"), passed with
 * a reservation, or found no answer. The reader has to see that at a glance, see what could
 * not be checked, and be able to check every source.
 *
 * How: Answers.tsx decides where a card goes and which answer it shows; this component only
 * draws it. Clicking a source opens it in the source panel through OpenSourceContext.
 */
import { splitAnswerText } from "@/lib/answerText";
import { sourceLabel } from "@/lib/citation";
import { STATUS_LABELS, statusHelp } from "@/lib/answerStatus";
import type { Answer, Citation, RegisterFact } from "@/lib/contract";
import { describeAgreement, groupRegisterFacts } from "@/lib/registerFacts";

import { useOpenSource } from "./SourceContext";
import styles from "./AnswerCard.module.css";

/** Up to this many agreements from the register are listed open; more are folded away. */
const OPEN_AGREEMENTS = 3;

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

      {answer.reservations.length > 0 && (
        <section className={styles.reservations} data-testid="reservations">
          <h3 className={styles.sectionTitle}>
            {answer.reservations.length === 1 ? "Reservation" : "Reservationer"}
          </h3>
          <ul>
            {answer.reservations.map((reservation, index) => (
              <li key={index}>{reservation}</li>
            ))}
          </ul>
        </section>
      )}

      {answer.register_facts.length > 0 && <RegisterFacts facts={answer.register_facts} />}

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

/** The agreements the register facts come from, one line each (lib/registerFacts.ts). */
function RegisterFacts({ facts }: { facts: readonly RegisterFact[] }) {
  const agreements = groupRegisterFacts(facts).map(describeAgreement);
  const title =
    agreements.length === 1 ? "Ur registret: 1 avtal" : `Ur registret: ${agreements.length} avtal`;
  return (
    <details
      className={styles.register}
      open={agreements.length <= OPEN_AGREEMENTS}
      data-testid="register-facts"
    >
      <summary className={styles.sectionTitle}>{title}</summary>
      <ul>
        {agreements.map((agreement) => (
          <li key={agreement.agreementNumber} data-testid="register-agreement">
            <span className={styles.agreementNumber}>{agreement.agreementNumber}</span>{" "}
            {agreement.supplier}
            {agreement.formerNames.length > 0 && `, tidigare ${agreement.formerNames.join(", ")}`}
            <span className={styles.agreementDetails}>
              {agreement.where} · {agreement.period}
            </span>
          </li>
        ))}
      </ul>
    </details>
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
