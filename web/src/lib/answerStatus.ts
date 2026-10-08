/**
 * What: the answer's status as the card shows it: a label and a line that says what it means.
 *
 * Why: what "Verifierat" means depends on what the answer rests on: quotes from the agreements,
 * rows from the register, or both, and a second model has reviewed it (webbapp-kontrakt.md,
 * point 25). "Inget svar" with sources means the sources show where the question is regulated.
 * A quote can also come from the person's own file (point 37), which is not the agreement.
 * The line has to say that, or the badge promises more or less than the check did.
 *
 * How: AnswerCard shows `STATUS_LABELS[status]` and `statusHelp(answer)`. Pure, with tests.
 */
import type { Answer, AnswerStatus } from "./contract.ts";

export const STATUS_LABELS: Record<AnswerStatus, string> = {
  verified: "Verifierat",
  with_reservation: "Med reservation",
  no_answer: "Inget svar",
};

/** What the status means for this answer: it depends on what the answer rests on. */
export function statusHelp(answer: Answer): string {
  const quotes = answer.citations.length > 0;
  const register = answer.register_facts.length > 0;
  const where = quoteSources(answer);
  switch (answer.status) {
    case "verified":
      if (quotes && register) {
        return `Citaten står ${where} och uppgifterna stämmer med registret. En granskare har bekräftat svaret.`;
      }
      if (register) return "Uppgifterna stämmer med registret. En granskare har bekräftat svaret.";
      return `Citaten står ${where}. En granskare har bekräftat att de stöder svaret.`;
    case "with_reservation":
      return answer.reservations.length > 0
        ? "Allt i svaret kunde inte kontrolleras. Se reservationerna under svaret."
        : "Allt i svaret kunde inte kontrolleras.";
    case "no_answer":
      return quotes
        ? "Agenten hittade inget svar i avtalen. Källorna visar var frågan regleras."
        : "Agenten hittade inget i avtalen som besvarar frågan.";
  }
}

/** Where the quotes are: in the agreements, in the person's own file, or in both. */
function quoteSources(answer: Answer): string {
  const own = answer.citations.some((citation) => citation.source === "upload");
  const agreement = answer.citations.some((citation) => citation.source !== "upload");
  if (own && agreement) return "i avtalstexten och i din fil";
  return own ? "i din fil" : "i avtalstexten";
}
