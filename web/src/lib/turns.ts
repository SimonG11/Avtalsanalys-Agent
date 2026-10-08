/**
 * What: groups the conversation's messages into turns, one per question, each with the timeline
 * of what the agent did for it: its thoughts, its tool calls with their results, and the drafts
 * of the answer it handed in for the check.
 *
 * Why: AG-UI delivers a flat list of messages (the question, reasoning messages, assistant
 * messages with tool calls, tool messages with results), and an ask_user interrupt splits one
 * question over two runs. The chat shows one question at a time with its work underneath, the
 * way Claude shows its thinking and tool use, so the list has to be read as turns.
 *
 * How: every user message starts a turn. Reasoning messages and assistant text become thoughts,
 * each tool call becomes a step and gets the content of the tool message that answers it, in the
 * order the messages came. A FinalAnswer call is a draft, not a step: its tool message says
 * whether the check took it ("Svaret är lämnat för kontroll.") or sent it back with a reason
 * (webbapp-kontrakt.md, points 13 and 26). The real stream may send that message only at the end
 * of the run, so a draft without one that is followed by more work was sent back. A draft the
 * format check refused ("Error: Failed to parse") is left out, as before. So is an ask_user call
 * the backend refused before asking (point 32: no options, or too many): its tool message has
 * `error` set, and before that message comes, the agent going on with other work shows it. Any
 * other call whose tool message has `error` is a failed step: the backend refused it (point 39,
 * a call without `syfte`) or the tool answered with an error. It stays in the timeline, but not
 * as a step that was done.
 */
import { parsePartialJson } from "./partialJson.ts";
import { ANSWER_TOOL, TOOL_LABELS } from "./tools.ts";

/** The parts of an AG-UI message the timeline reads. */
export interface ConversationMessage {
  id: string;
  role: string;
  content?: unknown;
  toolCalls?: readonly { id: string; function: { name: string; arguments: string } }[];
  toolCallId?: string;
  /** Set on a tool message when the call failed or was refused. */
  error?: string;
}

export type TimelineItem =
  /** A summary of the model's reasoning, or text it wrote while working. May be empty. */
  | { kind: "thought"; id: string; text: string }
  /**
   * A tool call; `result` is the tool's answer once it has come. `failed` is set when the call
   * was refused or failed (its tool message has `error`); `result` is then the error.
   */
  | {
      kind: "tool";
      id: string;
      name: string;
      args: unknown;
      result: string | undefined;
      failed: boolean;
    }
  /**
   * A draft of the answer handed in with FinalAnswer: "pending" until the check answers it,
   * "submitted" when the check took it, "rejected" when it sent it back (`reason` is the check's
   * reason, or null when it has not come).
   */
  | {
      kind: "draft";
      id: string;
      outcome: "pending" | "submitted" | "rejected";
      reason: string | null;
    };

export interface Turn {
  /** The id of the user message that asked the question. */
  questionId: string;
  question: string;
  items: TimelineItem[];
}

const REJECTED = "Kontrollen underkände svaret";
const FORMAT_ERROR = "Error: Failed to parse";

export function buildTurns(messages: readonly ConversationMessage[]): Turn[] {
  const results = new Map<string, string>();
  const failed = new Set<string>();
  for (const message of messages) {
    if (message.role === "tool" && message.toolCallId) {
      results.set(message.toolCallId, textOf(message.content) || (message.error ?? ""));
      if (message.error) failed.add(message.toolCallId);
    }
  }

  const turns: Turn[] = [];
  for (const message of messages) {
    if (message.role === "user") {
      turns.push({ questionId: message.id, question: textOf(message.content), items: [] });
      continue;
    }
    const turn = turns.at(-1);
    if (!turn) continue;
    if (message.role === "reasoning") {
      turn.items.push({ kind: "thought", id: message.id, text: textOf(message.content) });
    } else if (message.role === "assistant") {
      const text = textOf(message.content).trim();
      if (text) turn.items.push({ kind: "thought", id: message.id, text });
      for (const call of message.toolCalls ?? []) {
        const result = results.get(call.id);
        if (call.function.name === ANSWER_TOOL) {
          if (result?.startsWith(FORMAT_ERROR)) continue;
          turn.items.push(draftOf(call.id, result));
        } else if (call.function.name === ASK_USER && failed.has(call.id)) {
          continue;
        } else {
          const args = parsePartialJson(call.function.arguments);
          turn.items.push({
            kind: "tool",
            id: call.id,
            name: call.function.name,
            args,
            result,
            failed: failed.has(call.id),
          });
        }
      }
    }
  }

  for (const turn of turns) {
    // A draft the check has not answered, followed by more work, was sent back.
    turn.items = turn.items.map((item, index) =>
      item.kind === "draft" && item.outcome === "pending" && index < turn.items.length - 1
        ? { ...item, outcome: "rejected" }
        : item,
    );
    // An unanswered ask_user call followed by other work was refused: only an interrupt asks.
    turn.items = turn.items.filter(
      (item, index) => !(isOpenQuestion(item) && turn.items.slice(index + 1).some(isOtherWork)),
    );
  }
  return turns;
}

const ASK_USER = "ask_user";

function isOpenQuestion(item: TimelineItem): boolean {
  return item.kind === "tool" && item.name === ASK_USER && item.result === undefined;
}

/** A step or draft other than a question; thoughts do not count. */
function isOtherWork(item: TimelineItem): boolean {
  return item.kind === "draft" || (item.kind === "tool" && item.name !== ASK_USER);
}

function draftOf(id: string, result: string | undefined): TimelineItem {
  if (result === undefined) return { kind: "draft", id, outcome: "pending", reason: null };
  if (result.startsWith(REJECTED)) {
    // "Kontrollen underkände svaret (försök 1 av 3): <reason>"
    const colon = result.indexOf(":");
    const reason = colon >= 0 ? result.slice(colon + 1).trim() : "";
    return { kind: "draft", id, outcome: "rejected", reason: reason || null };
  }
  // "Svaret är lämnat för kontroll.": the draft went on to the review.
  return { kind: "draft", id, outcome: "submitted", reason: null };
}

/**
 * What the agent is doing now, for the timeline's header while the run goes on: the running
 * label of the step that waits for its result, "Kontrollerar svaret …" while a draft is
 * checked, and otherwise "Tänker …", since the model is working on its next move.
 */
export function currentActivity(items: readonly TimelineItem[]): string {
  const last = items.at(-1);
  if (last?.kind === "tool" && last.result === undefined) {
    return `${TOOL_LABELS[last.name]?.running ?? last.name} …`;
  }
  if (last?.kind === "draft" && last.outcome !== "rejected") return "Kontrollerar svaret …";
  return "Tänker …";
}

/** The id of the most recent question, or null when nobody has asked anything yet. */
export function lastQuestionId(messages: readonly { id: string; role: string }[]): string | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "user") return messages[i].id;
  }
  return null;
}

/** The number of steps the agent took: tool calls and checked drafts, not thoughts. */
export function stepCount(items: readonly TimelineItem[]): number {
  return items.filter((item) => item.kind !== "thought").length;
}

/**
 * A thought split into its title and the rest. OpenAI often starts a summary of its reasoning
 * with a title in **bold** on a line of its own (webbapp-kontrakt.md, point 30).
 */
export function splitThought(text: string): { title: string | null; body: string } {
  const match = /^\s*\*\*(.+?)\*\*[ \t]*(?:\r?\n|$)/.exec(text);
  if (!match) {
    // A title still streaming ("**Letar efter") is shown without its asterisks.
    const open = /^\s*\*\*([^*]*)$/.exec(text);
    if (open) return { title: open[1].trim() || null, body: "" };
    return { title: null, body: text.trim() };
  }
  return { title: match[1].trim(), body: text.slice(match[0].length).trim() };
}

/** A message's content as text: a string, or the text parts of a list of parts. */
export function textOf(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .map((part: unknown) =>
      typeof part === "object" && part !== null && "text" in part && typeof part.text === "string"
        ? part.text
        : "",
    )
    .filter(Boolean)
    .join("\n");
}
