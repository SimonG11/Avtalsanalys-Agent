/**
 * What: Swedish labels for the agent's tools and a short summary of each call's arguments.
 *
 * Why: the agent's steps are shown live in the chat. The tool names are English identifiers
 * (search_documents, read_section ...), but the person asking reads Swedish, and a step is
 * only useful if it says what the agent looked for ("Söker i dokumenten: uppsägningstid").
 *
 * How: the timeline (components/Timeline.tsx) calls `describeToolCall` for every tool call.
 * Unknown tools still get a step, with the raw tool name, so a tool added to avtal-mcp later
 * shows up without a code change here. `summarizeResult` gives the step a line with what the tool found, for the tools
 * whose answer has one.
 */

export interface ToolLabel {
  /** Shown while the tool runs, e.g. "Söker i dokumenten". */
  running: string;
  /** Shown when the tool has answered, e.g. "Sökte i dokumenten". */
  done: string;
}

/**
 * One label per tool, under the English names the code uses. docs/arkitektur.md (5.3) lists the
 * same tools under their Swedish plan names, e.g. sok_dokument for search_documents.
 */
export const TOOL_LABELS: Record<string, ToolLabel> = {
  search_documents: { running: "Söker i dokumenten", done: "Sökte i dokumenten" },
  read_section: { running: "Läser avsnitt", done: "Läste avsnitt" },
  get_outline: { running: "Hämtar innehållsförteckning", done: "Hämtade innehållsförteckning" },
  resolve_reference: { running: "Följer hänvisning", done: "Följde hänvisning" },
  list_documents: { running: "Listar dokument", done: "Listade dokument" },
  search_register: { running: "Söker i registret", done: "Sökte i registret" },
  find_amendments: { running: "Letar efter ändringar", done: "Letade efter ändringar" },
  calculate_date: { running: "Räknar ut datum", done: "Räknade ut datum" },
  ask_user: { running: "Frågar dig", done: "Fick svar" },
};

/**
 * The tool the agent hands in its answer with. It is not a step but a draft: the answer is shown
 * from the state, and the timeline shows the check of each draft instead (lib/turns.ts).
 */
export const ANSWER_TOOL = "FinalAnswer";

/** Swedish names for the arguments the tools take; other arguments keep their own name. */
const ARGUMENT_LABELS: Record<string, string> = {
  query: "sökord",
  agreement_number: "avtal",
  framework_area: "område",
  sub_area: "delområde",
  document_type: "dokumenttyp",
  sha256: "dokument",
  section_number: "avsnitt",
  section_position: "plats i filen",
  reference: "hänvisning",
  supplier: "leverantör",
  org_number: "orgnr",
  valid_on: "gäller den",
  limit: "antal",
  offset: "hoppar över",
  start: "från",
  amount: "antal",
  unit: "enhet",
  direction: "riktning",
  include_start: "startdagen ingår",
  options: "alternativ",
};

/** Swedish words for arguments whose value is one of a few English words (calculate_date). */
const VALUE_LABELS: Record<string, Record<string, string>> = {
  unit: {
    days: "dagar",
    working_days: "arbetsdagar",
    weeks: "veckor",
    months: "månader",
    years: "år",
  },
  direction: { after: "efter", before: "före" },
};

/** The arguments that best say what a call is about, shown first and without a label. */
const MAIN_ARGUMENT: Record<string, string> = {
  search_documents: "query",
  search_register: "supplier",
  read_section: "section_number",
  find_amendments: "section_number",
  resolve_reference: "reference",
  ask_user: "question",
};

export interface ToolCallDescription {
  /** Swedish title, e.g. "Söker i dokumenten". */
  title: string;
  /** The main argument, e.g. "uppsägningstid", or null when the tool has none. */
  subject: string | null;
  /** The other arguments as label and value, e.g. ["avtal", "23.3-..."]. */
  details: [string, string][];
}

export function describeToolCall(
  name: string,
  args: unknown,
  status: "inProgress" | "executing" | "complete",
): ToolCallDescription {
  const label = TOOL_LABELS[name];
  const title = label ? (status === "complete" ? label.done : label.running) : name;
  const entries = isRecord(args) ? Object.entries(args) : [];
  const mainKey = MAIN_ARGUMENT[name];

  let subject: string | null = null;
  const details: [string, string][] = [];
  for (const [key, value] of entries) {
    if (value === undefined || value === null || value === "") continue;
    if (isRedundant(key, value, args)) continue;
    const text = formatValue(key, value);
    if (key === mainKey) subject = text;
    else details.push([ARGUMENT_LABELS[key] ?? key, text]);
  }
  return { title, subject, details };
}

/**
 * Arguments that add nothing to the step. The model often sends the defaults `offset: 0` and
 * `include_start: false`, and both a section's number and its place in the file, where the
 * number says it already.
 */
function isRedundant(key: string, value: unknown, args: unknown): boolean {
  if (key === "offset") return value === 0;
  if (key === "include_start") return value === false;
  if (key === "section_position") {
    return isRecord(args) && typeof args.section_number === "string" && args.section_number !== "";
  }
  return false;
}

/**
 * Long hashes are shortened, English words get their Swedish name, yes and no are written out
 * and lists of words joined; other objects are written compactly.
 */
function formatValue(key: string, value: unknown): string {
  if (typeof value === "string") {
    if (key === "sha256" && value.length > 12) return `${value.slice(0, 8)}…`;
    return VALUE_LABELS[key]?.[value] ?? value;
  }
  if (typeof value === "boolean") return value ? "ja" : "nej";
  if (typeof value === "number") return String(value);
  if (Array.isArray(value) && value.every((item) => typeof item === "string")) {
    return value.join(", ");
  }
  return JSON.stringify(value);
}

/**
 * A line that says what a tool found, for the tools whose answer has one: the calculation
 * calculate_date made ("2027-02-17 minus 3 månader = 2026-11-17 (tisdag)") and the number of
 * amendments find_amendments found. Other answers, and a tool's error text, have none; they are
 * shown raw behind the disclosure.
 */
export function summarizeResult(name: string, result: string | undefined): string | null {
  const answer = parseJson(result);
  if (!isRecord(answer)) return null;
  if (name === "calculate_date" && typeof answer.step === "string") {
    return typeof answer.weekday === "string" ? `${answer.step} (${answer.weekday})` : answer.step;
  }
  if (name === "find_amendments" && Array.isArray(answer.amendments)) {
    const count = answer.amendments.length;
    let line = count === 0 ? "Inga ändringar" : count === 1 ? "1 ändring" : `${count} ändringar`;
    const held = answer.held_back;
    // Amending sections the tools may not show are only counted.
    if (typeof held === "number" && held > 0) line += `, ${held} till som inte kan visas`;
    return line;
  }
  return null;
}

function parseJson(text: string | undefined): unknown {
  if (text === undefined) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
