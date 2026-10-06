/**
 * What: the scripted runs of the mock agent, as lists of AG-UI events.
 *
 * Why: the web app (M10) is built before the real agent (M7) and API (M9) exist. The mock
 * answers in the same AG-UI events that ag-ui-langgraph emits for a LangGraph agent, so the web
 * app can be developed and tested end to end against the agreed contract.
 *
 * How: `planRun` reads the last user question and picks a scenario by keyword:
 *   - termination ("uppsägning", "säga upp") and an area (IT-drift ...) -> a verified answer
 *   - termination without an area -> the agent asks which area first (ask_user interrupt)
 *   - "vite" -> an answer with a reservation and one unverified citation
 *   - anything else -> no answer
 * The question is sent as ag-ui-langgraph does with `emit_interrupt_outcome=True`: the older
 * on_interrupt event and the AG-UI standard outcome on RUN_FINISHED. "[legacy]" in the question
 * sends only the older event (ag-ui-langgraph's default) and "[outcome]" only the standard one
 * (`enable_legacy_on_interrupt_event=False`), so each shape is tested on its own.
 * A question containing "[fel]" ends in RUN_ERROR after one step, as when the backend fails.
 * The events follow the order ag-ui-langgraph uses: RUN_STARTED, STEP_STARTED per node, tool
 * calls with their results, STATE_SNAPSHOT, MESSAGES_SNAPSHOT and RUN_FINISHED.
 */
import { EventType } from "@ag-ui/core";
import type { BaseEvent, Message, RunAgentInput } from "@ag-ui/core";

import { DOCUMENT_TITLE, PAGE_TITLE } from "./fixture-pdf.ts";

/** An event and how long the mock waits before sending it, so the steps appear live. */
export interface TimedEvent {
  event: BaseEvent;
  pauseMs: number;
}

export interface MockContext {
  documentSha256: string;
}

/** Which interrupt events a run sends: both (the default), the older event only, or the outcome only. */
type InterruptShape = "both" | "legacy" | "outcome";

const AREAS = ["IT-drift", "Programvaror och tjänster", "Bemanningstjänster"];

/** The text of the last user message, whether it is a string or a list of parts. */
export function lastUserQuestion(messages: readonly Message[]): string {
  for (let i = messages.length - 1; i >= 0; i--) {
    const message = messages[i];
    if (message.role !== "user") continue;
    const content: unknown = message.content;
    if (typeof content === "string") return content;
    if (Array.isArray(content)) {
      return content
        .map((part: unknown) =>
          typeof part === "object" && part !== null && "text" in part ? String(part.text) : "",
        )
        .join(" ");
    }
  }
  return "";
}

/** The answer to an ask_user interrupt, from either resume channel. */
export function resumeAnswer(input: RunAgentInput): string | null {
  const entry = input.resume?.[0];
  if (entry && entry.status === "resolved") return String(entry.payload ?? "");
  const command: unknown = (input.forwardedProps as Record<string, unknown> | undefined)?.command;
  if (typeof command === "object" && command !== null && "resume" in command) {
    return String((command as { resume: unknown }).resume ?? "");
  }
  return null;
}

/** Collects the events of one run and the messages the run adds to the conversation. */
class RunBuilder {
  readonly events: TimedEvent[] = [];
  readonly newMessages: Message[] = [];
  private count = 0;
  private readonly input: RunAgentInput;

  constructor(input: RunAgentInput) {
    this.input = input;
    this.push({ type: EventType.RUN_STARTED, threadId: input.threadId, runId: input.runId }, 0);
  }

  private push(event: BaseEvent, pauseMs = 150): void {
    this.events.push({ event, pauseMs });
  }

  private nextId(kind: string): string {
    this.count += 1;
    return `${this.input.runId}-${kind}-${this.count}`;
  }

  step(name: string, body: () => void): void {
    this.push({ type: EventType.STEP_STARTED, stepName: name } as BaseEvent);
    body();
    this.push({ type: EventType.STEP_FINISHED, stepName: name } as BaseEvent, 50);
  }

  /** One model turn that calls one tool, then the tool's result (as create_agent does). */
  toolCall(name: string, args: Record<string, unknown>, result: unknown): void {
    const messageId = this.nextId("ai");
    const toolCallId = this.nextId("call");
    const argsJson = JSON.stringify(args);
    const half = Math.ceil(argsJson.length / 2);
    this.push({
      type: EventType.TOOL_CALL_START,
      toolCallId,
      toolCallName: name,
      parentMessageId: messageId,
    } as BaseEvent);
    // Arguments stream in pieces, like the model's tokens.
    this.push({
      type: EventType.TOOL_CALL_ARGS,
      toolCallId,
      delta: argsJson.slice(0, half),
    } as BaseEvent);
    this.push({
      type: EventType.TOOL_CALL_ARGS,
      toolCallId,
      delta: argsJson.slice(half),
    } as BaseEvent);
    this.push({ type: EventType.TOOL_CALL_END, toolCallId } as BaseEvent);

    const content = JSON.stringify(result);
    const toolMessageId = this.nextId("tool");
    this.push(
      {
        type: EventType.TOOL_CALL_RESULT,
        messageId: toolMessageId,
        toolCallId,
        content,
        role: "tool",
      } as BaseEvent,
      700,
    );
    this.newMessages.push(
      {
        id: messageId,
        role: "assistant",
        content: "",
        toolCalls: [{ id: toolCallId, type: "function", function: { name, arguments: argsJson } }],
      },
      { id: toolMessageId, role: "tool", toolCallId, content },
    );
  }

  state(snapshot: Record<string, unknown>): void {
    this.push({ type: EventType.STATE_SNAPSHOT, snapshot } as BaseEvent);
  }

  messagesSnapshot(): void {
    this.push({
      type: EventType.MESSAGES_SNAPSHOT,
      messages: [...this.input.messages, ...this.newMessages],
    } as BaseEvent);
  }

  interrupt(value: { question: string; options: string[] }, shape: InterruptShape): void {
    const interruptId = this.nextId("interrupt");
    if (shape !== "outcome") {
      this.push({
        type: EventType.CUSTOM,
        name: "on_interrupt",
        value: JSON.stringify(value),
      } as BaseEvent);
    }
    const outcome =
      shape === "legacy"
        ? undefined
        : {
            type: "interrupt",
            interrupts: [
              {
                id: interruptId,
                reason: "langgraph:interrupt",
                metadata: {
                  langgraph: {
                    raw: value,
                    ns: ["research_agent"],
                    resumable: true,
                    when: "during",
                  },
                },
              },
            ],
          };
    this.finish(outcome);
  }

  /** Ends the run with an error instead of RUN_FINISHED, without sending any state. */
  fail(message: string): void {
    this.push({ type: EventType.RUN_ERROR, message } as BaseEvent);
  }

  finish(outcome?: unknown): void {
    this.push({
      type: EventType.RUN_FINISHED,
      threadId: this.input.threadId,
      runId: this.input.runId,
      ...(outcome ? { outcome } : {}),
    } as BaseEvent);
  }
}

function citation(
  context: MockContext,
  fields: {
    id: number;
    section_number: string;
    section_title: string;
    page: number;
    quote: string;
    verified: boolean;
  },
) {
  return {
    sha256: context.documentSha256,
    file_title: DOCUMENT_TITLE,
    page_title: PAGE_TITLE,
    ...fields,
  };
}

function searchHits(context: MockContext, sections: [string, string, number][]) {
  return {
    hits: sections.map(([section_number, section_title, page]) => ({
      sha256: context.documentSha256,
      file_title: DOCUMENT_TITLE,
      section_number,
      section_title,
      page,
    })),
  };
}

function noticePeriodAnswer(context: MockContext, area: string) {
  return {
    text:
      `I ramavtalet för ${area} får kunden säga upp kontraktet med tre månaders uppsägningstid, ` +
      "och uppsägningen ska vara skriftlig [1]. Vid väsentligt avtalsbrott får en part säga upp " +
      "kontraktet med omedelbar verkan [2].",
    status: "verified",
    citations: [
      citation(context, {
        id: 1,
        section_number: "6.21.9",
        section_title: "Uppsägning",
        page: 2,
        quote:
          "Kunden har rätt att säga upp Kontraktet med tre (3) månaders uppsägningstid. " +
          "Uppsägningen ska vara skriftlig",
        verified: true,
      }),
      citation(context, {
        id: 2,
        section_number: "6.21.10",
        section_title: "Uppsägning vid väsentligt avtalsbrott",
        page: 2,
        quote:
          "Part har rätt att säga upp Kontraktet med omedelbar verkan om den andra Parten gör sig " +
          "skyldig till väsentligt avtalsbrott.",
        verified: true,
      }),
    ],
  };
}

function answerNoticePeriod(run: RunBuilder, context: MockContext, area: string): void {
  run.step("research_agent", () => {
    run.toolCall(
      "search_documents",
      { query: "uppsägningstid kontrakt", framework_area: area },
      searchHits(context, [
        ["6.21.9", "Uppsägning", 2],
        ["6.21.10", "Uppsägning vid väsentligt avtalsbrott", 2],
      ]),
    );
    run.toolCall(
      "read_section",
      { sha256: context.documentSha256, section_number: "6.21.9" },
      { section_number: "6.21.9", page: 2, text: "Kunden har rätt att säga upp Kontraktet ..." },
    );
  });
  run.step("finalize", () => run.state({ answer: noticePeriodAnswer(context, area) }));
}

export function planRun(input: RunAgentInput, context: MockContext): TimedEvent[] {
  const question = lastUserQuestion(input.messages);
  const lower = question.toLowerCase();
  const run = new RunBuilder(input);
  const resumed = resumeAnswer(input);

  if (lower.includes("[fel]")) {
    run.step("research_agent", () => {
      run.toolCall("search_documents", { query: question.slice(0, 80) }, { hits: [] });
    });
    run.fail("Mocken avbröt körningen.");
    return run.events;
  }

  if (resumed !== null) {
    // The person answered which area the question is about.
    answerNoticePeriod(run, context, resumed || AREAS[0]);
  } else {
    // A new question: the previous answer no longer applies.
    run.state({ answer: null });
    const area = AREAS.find((name) => lower.includes(name.split(" ")[0].toLowerCase()));
    const aboutTermination = /uppsäg|säg(a|er) [^.?!]*upp\b/.test(lower);

    if (aboutTermination && area) {
      answerNoticePeriod(run, context, area);
    } else if (aboutTermination) {
      run.step("research_agent", () => {
        run.toolCall("search_register", { query: "uppsägning" }, { framework_areas: AREAS });
      });
      run.messagesSnapshot();
      const shape: InterruptShape = lower.includes("[legacy]")
        ? "legacy"
        : lower.includes("[outcome]")
          ? "outcome"
          : "both";
      run.interrupt({ question: "Vilket ramavtalsområde gäller frågan?", options: AREAS }, shape);
      return run.events;
    } else if (lower.includes("vite")) {
      run.step("research_agent", () => {
        run.toolCall(
          "search_documents",
          { query: "vite försenad leverans" },
          searchHits(context, [["7.2", "Vite vid försenad leverans", 3]]),
        );
      });
      run.step("finalize", () =>
        run.state({
          answer: {
            text:
              "Vid försenad leverans har kunden rätt till vite med 0,5 procent av det avropade " +
              "värdet per påbörjad vecka, högst tio procent [1]. Om vitet också gäller " +
              "delleveranser kunde inte bekräftas [2].",
            status: "with_reservation",
            citations: [
              citation(context, {
                id: 1,
                section_number: "7.2",
                section_title: "Vite vid försenad leverans",
                page: 3,
                quote:
                  "Om Leverantören inte levererar i tid har Kunden rätt till vite med 0,5 procent " +
                  "av det avropade värdet för varje påbörjad vecka, dock högst tio (10) procent.",
                verified: true,
              }),
              citation(context, {
                id: 2,
                section_number: "7.2",
                section_title: "Vite vid försenad leverans",
                page: 3,
                quote: "Vite utgår även vid försenad delleverans.",
                verified: false,
              }),
            ],
          },
        }),
      );
    } else {
      run.step("research_agent", () => {
        run.toolCall("search_documents", { query: question.slice(0, 80) }, { hits: [] });
      });
      run.step("finalize", () =>
        run.state({
          answer: {
            text:
              "Jag hittar inget i avtalen som besvarar frågan. Ange gärna vilket ramavtal du menar " +
              "eller formulera frågan på ett annat sätt.",
            status: "no_answer",
            citations: [],
          },
        }),
      );
    }
  }

  run.messagesSnapshot();
  run.finish();
  return run.events;
}
