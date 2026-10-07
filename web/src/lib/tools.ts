/**
 * What: Swedish labels for the agent's tools and a short summary of each call's arguments.
 *
 * Why: the agent's steps are shown live in the chat. The tool names are English identifiers
 * (search_documents, read_section ...), but the person asking reads Swedish, and a step is
 * only useful if it says what the agent looked for ("Söker i dokumenten: uppsägningstid").
 *
 * How: AgentSteps calls `describeToolCall` for every tool call event. Unknown tools still get
 * a step, with the raw tool name, so a tool added to avtal-mcp later shows up without a code
 * change here.
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
 * Tool calls that are not steps. The agent hands in its answer by calling FinalAnswer; the
 * answer is shown from the state, so the call and its result are hidden.
 */
const HIDDEN_TOOLS = new Set(["FinalAnswer"]);

export function isHiddenTool(name: string): boolean {
  return HIDDEN_TOOLS.has(name);
}

/** Swedish names for the arguments the tools take; other arguments keep their own name. */
const ARGUMENT_LABELS: Record<string, string> = {
  query: "sökord",
  agreement_number: "avtal",
  framework_area: "område",
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
  options: "alternativ",
};

/** The arguments that best say what a call is about, shown first and without a label. */
const MAIN_ARGUMENT: Record<string, string> = {
  search_documents: "query",
  search_register: "supplier",
  read_section: "section_number",
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
    const text = formatValue(key, value);
    if (key === mainKey) subject = text;
    else details.push([ARGUMENT_LABELS[key] ?? key, text]);
  }
  return { title, subject, details };
}

/** Long hashes are shortened, lists of words joined; other objects are written compactly. */
function formatValue(key: string, value: unknown): string {
  if (typeof value === "string") {
    return key === "sha256" && value.length > 12 ? `${value.slice(0, 8)}…` : value;
  }
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value) && value.every((item) => typeof item === "string")) {
    return value.join(", ");
  }
  return JSON.stringify(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
