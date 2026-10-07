/**
 * What: the short descriptions of a citation the web app shows: "Allmänna villkor, avsnitt
 * 6.21.9 Uppsägning, s. 14" in the sources list and "Avsnitt 6.21.9 Uppsägning · sida 14" in
 * the source panel.
 *
 * Why: some of a citation's fields can be missing (see the contract in
 * docs/steg/10-webbapp.md): a section without a number, a Word file without pages, or a
 * section that could not be read and has no titles. The descriptions leave out what is missing
 * instead of showing "avsnitt null" or "s. null".
 *
 * How: pure functions over a Citation, used by AnswerCard and SourcePanel.
 */
import type { Citation } from "./contract.ts";

type CitationFields = Pick<Citation, "file_title" | "section_number" | "section_title" | "page">;

/** The document's title, or a placeholder when the backend could not read the section. */
export function documentTitle(citation: Pick<Citation, "file_title">): string {
  return citation.file_title.trim() || "Okänt dokument";
}

/** "6.21.9 Uppsägning", "Uppsägning" for a section without a number, or null. */
export function sectionLabel(citation: Pick<Citation, "section_number" | "section_title">) {
  const label = [citation.section_number, citation.section_title]
    .map((part) => part?.trim())
    .filter(Boolean)
    .join(" ");
  return label || null;
}

/** "Allmänna villkor, avsnitt 6.21.9 Uppsägning, s. 14", leaving out what is missing. */
export function sourceLabel(citation: CitationFields): string {
  const section = sectionLabel(citation);
  return [
    documentTitle(citation),
    section && `avsnitt ${section}`,
    citation.page !== null && `s. ${citation.page}`,
  ]
    .filter(Boolean)
    .join(", ");
}

/** "Avsnitt 6.21.9 Uppsägning · sida 14", leaving out what is missing; null if nothing is known. */
export function locationLabel(citation: CitationFields): string | null {
  const section = sectionLabel(citation);
  const parts = [
    section && `Avsnitt ${section}`,
    citation.page !== null && `sida ${citation.page}`,
  ];
  return parts.filter(Boolean).join(" · ") || null;
}
