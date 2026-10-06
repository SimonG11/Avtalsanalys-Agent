/**
 * What: splits the answer text into plain text and citation markers such as [1] or [1, 2].
 *
 * Why: the answer refers to its sources by number. In the web app each number is a button that
 * opens the source in the PDF, so the text has to be split before it is rendered.
 *
 * How: AnswerCard renders the segments in order. A number that has no citation in the answer
 * stays plain text, so a wrong reference is visible instead of becoming a dead button.
 */

export type AnswerSegment = { kind: "text"; text: string } | { kind: "citation"; id: number };

const MARKER = /\[(\d+(?:\s*,\s*\d+)*)\]/g;

export function splitAnswerText(text: string, citationIds: ReadonlySet<number>): AnswerSegment[] {
  const segments: AnswerSegment[] = [];
  let last = 0;
  for (const match of text.matchAll(MARKER)) {
    const ids = match[1].split(",").map((part) => Number(part.trim()));
    if (!ids.every((id) => citationIds.has(id))) continue;
    if (match.index > last) segments.push({ kind: "text", text: text.slice(last, match.index) });
    for (const id of ids) segments.push({ kind: "citation", id });
    last = match.index + match[0].length;
  }
  if (last < text.length) segments.push({ kind: "text", text: text.slice(last) });
  return segments;
}
