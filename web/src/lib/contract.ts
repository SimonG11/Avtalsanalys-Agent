/**
 * What: the data the web app receives from the agent, as Zod schemas and TypeScript types:
 * the answer in the shared state (`answer`) and the payload of the ask_user interrupt.
 *
 * Why: the backend and the web app are built in separate milestones (M7/M9 and M10). The
 * contract in docs/steg/10-webbapp.md is the only thing they share, so it is written down once
 * here and checked at runtime. A malformed answer is shown as an error instead of crashing.
 *
 * How: AnswerCard reads the run's state through `parseAnswer`, and ClarifyDialog reads the
 * interrupt through `parseAskUser`. Both are pure functions with tests next to them.
 */
import { z } from "zod";

export const CitationSchema = z.object({
  id: z.number().int().positive(),
  sha256: z.string().regex(/^[0-9a-f]{64}$/, "sha256 must be 64 lowercase hex characters"),
  file_title: z.string(),
  page_title: z.string(),
  section_number: z.string(),
  section_title: z.string(),
  page: z.number().int().positive(),
  quote: z.string().min(1),
  verified: z.boolean(),
});

export const AnswerStatusSchema = z.enum(["verified", "with_reservation", "no_answer"]);

export const AnswerSchema = z.object({
  text: z.string(),
  status: AnswerStatusSchema,
  citations: z.array(CitationSchema),
});

export type Citation = z.infer<typeof CitationSchema>;
export type AnswerStatus = z.infer<typeof AnswerStatusSchema>;
export type Answer = z.infer<typeof AnswerSchema>;

/** The result of reading `answer` from a state snapshot. */
export type ParsedAnswer =
  { kind: "none" } | { kind: "answer"; answer: Answer } | { kind: "invalid"; problem: string };

/**
 * Reads `answer` from the agent's state. A missing or null answer means the run has not
 * produced one yet; anything else that fails the schema is reported, not thrown.
 */
export function parseAnswer(state: unknown): ParsedAnswer {
  if (typeof state !== "object" || state === null) return { kind: "none" };
  const raw = (state as Record<string, unknown>).answer;
  if (raw === undefined || raw === null) return { kind: "none" };
  const result = AnswerSchema.safeParse(raw);
  if (!result.success) {
    const issue = result.error.issues[0];
    const where = issue?.path.join(".") || "answer";
    return { kind: "invalid", problem: `${where}: ${issue?.message ?? "invalid"}` };
  }
  return { kind: "answer", answer: result.data };
}

export const AskUserSchema = z.object({
  question: z.string().min(1),
  options: z.array(z.string().min(1)).optional(),
});

export type AskUser = z.infer<typeof AskUserSchema>;

/**
 * Reads the ask_user payload `{question, options?}` from an interrupt.
 *
 * ag-ui-langgraph delivers the interrupt in one of two shapes, and CopilotKit passes both on:
 * - the AG-UI standard interrupt, where the graph's value is in `metadata.langgraph.raw`;
 * - the older `on_interrupt` custom event, where the value is a JSON string.
 * The first shape that matches the schema wins. Returns null when neither does.
 */
export function parseAskUser(standard: unknown, legacyValue: unknown): AskUser | null {
  const candidates: unknown[] = [];
  if (isRecord(standard)) {
    const metadata = standard.metadata;
    if (isRecord(metadata) && isRecord(metadata.langgraph)) {
      candidates.push(metadata.langgraph.raw);
    }
    if (typeof standard.message === "string") {
      candidates.push({ question: standard.message });
    }
  }
  candidates.push(typeof legacyValue === "string" ? parseJson(legacyValue) : legacyValue);

  for (const candidate of candidates) {
    const result = AskUserSchema.safeParse(candidate);
    if (result.success) return result.data;
  }
  return null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function parseJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}
