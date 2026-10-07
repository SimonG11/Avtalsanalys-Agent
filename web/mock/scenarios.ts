/**
 * What: the scripted runs of the mock agent, as lists of AG-UI events.
 *
 * Why: the web app (M10) is built before the real agent (M7) and API (M9) exist. The mock
 * answers in the same AG-UI events that ag-ui-langgraph emits for a LangGraph agent, so the web
 * app can be developed and tested end to end against the agreed contract.
 *
 * How: `planRun` reads the last user question and picks a scenario by keyword:
 *   - termination ("uppsägning", "säga upp") and an area (IT-drift ...) -> a verified answer;
 *     with a date (2027-02-17) in the question, the agent also counts back the notice period
 *     with calculate_date and answers with the last day to give notice
 *   - termination without an area -> the agent asks which area first: it calls the ask_user
 *     tool, which stops the run with an interrupt, and the run that resumes it gives the tool's
 *     result (the person's answer) before it goes on
 *   - "vite" -> the check rejects the first draft; the second has a reservation, one
 *     unverified citation and one quote on the page after its section's first page
 *   - "bilaga" -> no answer, with a source in a Word file (no page, no PDF) and a list in the text
 *   - "avtalsnummer" -> a verified answer from the register: no quotes, but the register rows
 *     it rests on, with one agreement written two ways (-001 and -01)
 *   - anything else -> no answer
 * Every answer is handed in with FinalAnswer and set by the check after a pause for the
 * reviewer, as the real agent does (webbapp-kontrakt.md, points 24-27); the web app hides the
 * call and shows that the answer is being checked.
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
  /** A pause that MOCK_FAST keeps, so the tests can see what the web app shows meanwhile. */
  minPauseMs?: number;
}

/** The reviewer's pause before the answer is set: about nine seconds in the real agent. */
const REVIEW_MS = 3000;
const REVIEW_MIN_MS = 800;

export interface MockContext {
  documentSha256: string;
}

/** Which interrupt events a run sends: both (the default), the older event only, or the outcome only. */
type InterruptShape = "both" | "legacy" | "outcome";

const AREAS = ["IT-drift", "Programvaror och tjänster", "Bemanningstjänster"];

const ASK_AREA = { question: "Vilket ramavtalsområde gäller frågan?", options: AREAS };

/** A Word file the mock has no PDF of, so the PDF route answers 404 for it. */
const WORD_FILE_SHA256 = "e".repeat(64);

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

/** The id of the ask_user call that is still waiting for its result, if there is one. */
export function pendingAskUser(messages: readonly Message[]): string | null {
  const answered = new Set(
    messages.flatMap((message) => (message.role === "tool" ? [message.toolCallId] : [])),
  );
  for (let i = messages.length - 1; i >= 0; i--) {
    const message = messages[i];
    if (message.role !== "assistant") continue;
    const call = message.toolCalls?.find((toolCall) => toolCall.function.name === "ask_user");
    if (call && !answered.has(call.id)) return call.id;
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

  private push(event: BaseEvent, pauseMs = 150, minPauseMs?: number): void {
    this.events.push({ event, pauseMs, ...(minPauseMs ? { minPauseMs } : {}) });
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

  /**
   * The model reasons before it acts. With a text, the summary of its reasoning streams in
   * pieces (webbapp-kontrakt.md, point 29); without one, the reasoning message is empty, as
   * before the backend asked OpenAI for summaries. Like the real stream, the messages snapshot
   * at the end has no reasoning messages; the client keeps the streamed ones.
   */
  reason(text = ""): void {
    const messageId = this.nextId("reasoning");
    this.push({ type: EventType.REASONING_START, messageId } as BaseEvent);
    this.push({
      type: EventType.REASONING_MESSAGE_START,
      messageId,
      role: "reasoning",
    } as BaseEvent);
    for (const delta of text.match(/\S+\s*/g) ?? []) {
      this.push({ type: EventType.REASONING_MESSAGE_CONTENT, messageId, delta } as BaseEvent, 40);
    }
    this.push({ type: EventType.REASONING_MESSAGE_END, messageId } as BaseEvent, 600, 300);
    this.push({ type: EventType.REASONING_END, messageId } as BaseEvent);
  }

  /** One model turn that calls one tool, then the tool's result (as create_agent does). */
  toolCall(name: string, args: Record<string, unknown>, result: unknown): void {
    const toolCallId = this.callTool(name, args);
    this.toolResult(toolCallId, JSON.stringify(result));
  }

  /** The model's tool call, streamed: start, the arguments in pieces, end. */
  private callTool(name: string, args: Record<string, unknown>): string {
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
    this.newMessages.push({
      id: messageId,
      role: "assistant",
      content: "",
      toolCalls: [{ id: toolCallId, type: "function", function: { name, arguments: argsJson } }],
    });
    return toolCallId;
  }

  /** The tool's answer to a call, which may have been made in an earlier run. */
  toolResult(toolCallId: string, content: string): void {
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
    this.newMessages.push({ id: toolMessageId, role: "tool", toolCallId, content });
  }

  /**
   * The agent hands in its answer with FinalAnswer, and the check (AnswerCheck) sets `answer`
   * after the reviewer has read it, as the real agent does: no events come during the review,
   * and the call gets no result event. Its tool message ("Svaret är lämnat för kontroll.") only
   * comes with the messages snapshot at the end of the run.
   */
  handIn(answer: Record<string, unknown>): void {
    let toolCallId = "";
    this.step("model", () => {
      toolCallId = this.callTool("FinalAnswer", answer);
    });
    this.step("AnswerCheck.after_agent", () => this.state({ answer }, REVIEW_MS, REVIEW_MIN_MS));
    const content = "Svaret är lämnat för kontroll.";
    this.newMessages.push({ id: this.nextId("tool"), role: "tool", toolCallId, content });
  }

  /**
   * A draft the check rejects: it answers the call with why, and the agent tries again. Like the
   * accepted draft's, the answer only comes with the messages snapshot at the end of the run.
   */
  rejectedDraft(answer: Record<string, unknown>, reason: string): void {
    let toolCallId = "";
    this.step("model", () => {
      toolCallId = this.callTool("FinalAnswer", answer);
    });
    this.step("AnswerCheck.after_agent", () => {});
    const content = `Kontrollen underkände svaret (försök 1 av 3): ${reason}`;
    this.newMessages.push({ id: this.nextId("tool"), role: "tool", toolCallId, content });
  }

  state(snapshot: Record<string, unknown>, pauseMs = 150, minPauseMs?: number): void {
    this.push({ type: EventType.STATE_SNAPSHOT, snapshot } as BaseEvent, pauseMs, minPauseMs);
  }

  messagesSnapshot(): void {
    this.push({
      type: EventType.MESSAGES_SNAPSHOT,
      messages: [...this.input.messages, ...this.newMessages],
    } as BaseEvent);
  }

  /**
   * The agent calls ask_user. The tool stops the run with an interrupt (`interrupt`), and its
   * result, the person's answer, comes in the run that resumes it (see `pendingAskUser`).
   */
  askUser(value: { question: string; options: string[] }): void {
    this.callTool("ask_user", value);
  }

  /**
   * An ask_user call the backend refuses before asking, here for want of options
   * (webbapp-kontrakt.md, point 32): no result event and no interrupt, and in the messages
   * snapshot a tool message with `error` set. The model then asks again.
   */
  refusedAskUser(question: string): void {
    const toolCallId = this.callTool("ask_user", { question });
    const error = "ask_user behöver 2-5 korta svarsalternativ.";
    this.newMessages.push({
      id: this.nextId("tool"),
      role: "tool",
      toolCallId,
      content: error,
      error,
    });
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

/** An answer with the lists the contract always has, empty unless given. */
function answerOf(fields: { text: string; status: string } & Record<string, unknown>) {
  return { citations: [], reservations: [], register_facts: [], ...fields };
}

function noticePeriodAnswer(context: MockContext, area: string) {
  return answerOf({
    text:
      `I ramavtalet för ${area} får kunden säga upp kontraktet med tre månaders uppsägningstid, ` +
      "och uppsägningen ska vara skriftlig [1]. Vid väsentligt avtalsbrott får en part säga upp " +
      "kontraktet med omedelbar verkan [2]. Båda reglerna står under 6.21 [1][2].",
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
  });
}

const WEEKDAYS = ["söndag", "måndag", "tisdag", "onsdag", "torsdag", "fredag", "lördag"];

/**
 * calculate_date's answer for a date some months before another, as avtal-mcp gives it: a
 * month without the day uses its last day. The mock only needs "before" in months.
 */
function monthsBefore(start: string, months: number) {
  const [year, month, day] = start.split("-").map(Number);
  const moved = new Date(Date.UTC(year, month - 1 - months, 1));
  const lastDay = new Date(Date.UTC(moved.getUTCFullYear(), moved.getUTCMonth() + 1, 0));
  moved.setUTCDate(Math.min(day, lastDay.getUTCDate()));
  const result = moved.toISOString().slice(0, 10);
  return {
    result,
    weekday: WEEKDAYS[moved.getUTCDay()],
    step: `${start} minus ${months} månader = ${result}`,
    skipped: [],
    notes: [],
  };
}

/** The notice period, and with an end date the last day to give notice (calculate_date). */
function answerNoticePeriod(
  run: RunBuilder,
  context: MockContext,
  area: string,
  endDate?: string,
): void {
  let answer = noticePeriodAnswer(context, area);
  run.step("research_agent", () => {
    run.reason(
      "**Letar efter reglerna om uppsägning**\n\n" +
        `Frågan gäller hur ett kontrakt inom ${area} sägs upp. Jag söker i de allmänna ` +
        "villkoren efter avsnitten om uppsägning.",
    );
    run.toolCall(
      "search_documents",
      { query: "uppsägningstid kontrakt", framework_area: area },
      searchHits(context, [
        ["6.21.9", "Uppsägning", 2],
        ["6.21.10", "Uppsägning vid väsentligt avtalsbrott", 2],
      ]),
    );
    run.reason(
      "**Läser avsnittet om uppsägning**\n\n" +
        "Sökningen pekar på 6.21.9. Jag läser hela avsnittet, så att jag kan citera det ordagrant.",
    );
    run.toolCall(
      "read_section",
      { sha256: context.documentSha256, section_number: "6.21.9" },
      { section_number: "6.21.9", page: 2, text: "Kunden har rätt att säga upp Kontraktet ..." },
    );
    if (endDate) {
      run.reason(
        "**Räknar ut sista dagen för uppsägning**\n\n" +
          `Kontraktet ska upphöra ${endDate} och uppsägningstiden är tre månader, så jag ` +
          "räknar tre månader bakåt.",
      );
      const args = { start: endDate, amount: 3, unit: "months", direction: "before" };
      const calculation = monthsBefore(endDate, 3);
      run.toolCall("calculate_date", args, calculation);
      answer = {
        ...answer,
        text:
          `Kontraktet inom ${area} har tre månaders uppsägningstid, och uppsägningen ska vara ` +
          `skriftlig [1]. Ska kontraktet upphöra ${endDate} måste kunden säga upp det senast ` +
          `${calculation.result}: ${calculation.step}.`,
        citations: answer.citations.slice(0, 1),
      };
    }
  });
  run.handIn(answer);
}

/** No answer, but a source that says where the question is regulated: a Word file. */
function answerAttachment(run: RunBuilder): void {
  run.step("research_agent", () => {
    run.toolCall(
      "search_documents",
      { query: "säkerhetsnivå avrop bilaga" },
      { hits: [{ sha256: WORD_FILE_SHA256, section_number: null, section_title: "Avropsbilaga" }] },
    );
  });
  run.handIn(
    answerOf({
      text:
        "Avtalen säger inte vilken säkerhetsnivå som gäller. Den bestäms i avropsbilagan, " +
        "som kunden skriver själv vid avropet [1].\n\nAvropsbilagan ska ange:\n" +
        "- krav på säkerhetsnivå\n- kontaktpersoner",
      status: "no_answer",
      citations: [
        {
          id: 1,
          sha256: WORD_FILE_SHA256,
          file_title: "Exempelbilaga Avropsförfrågan (fiktiv)",
          page_title: null,
          section_number: null,
          section_title: "Avropsbilaga",
          page: null,
          quote: "Kunden anger säkerhetsnivå och kontaktpersoner i avropsbilagan.",
          verified: true,
        },
      ],
    }),
  );
}

/** One row of the register, made up, as the check reads it. */
function registerRow(fields: Record<string, unknown>) {
  return {
    agreement_number: "00.0-0000-2026-001",
    supplier_name: "Exempelleverantören AB (fiktiv)",
    former_names: ["Gamla Exempelbolaget AB (fiktivt)"],
    org_number: "000000-0001",
    sub_area: "IT-drift / Större (fiktivt)",
    valid_from: "2026-01-01",
    valid_to: "2028-12-31",
    max_extension_to: "2030-12-31",
    ...fields,
  };
}

/**
 * A verified answer from the register: nothing in the agreement text to quote, but the rows it
 * rests on. The first agreement has two sub-areas, and the register writes its number two ways.
 */
function answerFromRegister(run: RunBuilder): void {
  const rows = [
    registerRow({}),
    registerRow({ agreement_number: "00.0-0000-2026-01", sub_area: "IT-drift / Mindre (fiktivt)" }),
    registerRow({
      agreement_number: "00.0-0000-2026-002",
      supplier_name: "Testleverantören AB (fiktiv)",
      former_names: [],
      org_number: "000000-0002",
      sub_area: "IT-drift / Mindre (fiktivt)",
      max_extension_to: null,
    }),
  ];
  run.step("research_agent", () => {
    run.toolCall("search_register", { framework_area: "IT-drift" }, { rows, total: rows.length });
  });
  run.handIn(
    answerOf({
      text:
        "Enligt registret har exempelområdet IT-drift två avtal: 00.0-0000-2026-001 med " +
        "Exempelleverantören AB och 00.0-0000-2026-002 med Testleverantören AB. Båda gäller " +
        "från 2026-01-01 till 2028-12-31.",
      status: "verified",
      register_facts: rows,
    }),
  );
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
    // The person answered which area the question is about: ask_user returns the answer.
    const pending = pendingAskUser(input.messages);
    if (pending) run.step("research_agent", () => run.toolResult(pending, resumed));
    answerNoticePeriod(run, context, resumed || AREAS[0]);
  } else {
    // A new question: the previous answer no longer applies.
    run.state({ answer: null });
    const area = AREAS.find((name) => lower.includes(name.split(" ")[0].toLowerCase()));
    const aboutTermination = /uppsäg|säg(a|er) [^.?!]*upp\b/.test(lower);

    if (aboutTermination && area) {
      answerNoticePeriod(run, context, area, /\d{4}-\d{2}-\d{2}/.exec(question)?.[0]);
    } else if (aboutTermination) {
      run.step("research_agent", () => {
        run.toolCall(
          "search_documents",
          { query: "uppsägningstid kontrakt" },
          {
            hits: AREAS.map((area) => ({ section_title: "Uppsägning", framework_areas: [area] })),
          },
        );
        run.refusedAskUser(ASK_AREA.question);
        run.askUser(ASK_AREA);
      });
      run.messagesSnapshot();
      const shape: InterruptShape = lower.includes("[legacy]")
        ? "legacy"
        : lower.includes("[outcome]")
          ? "outcome"
          : "both";
      run.interrupt(ASK_AREA, shape);
      return run.events;
    } else if (lower.includes("bilaga")) {
      answerAttachment(run);
    } else if (lower.includes("avtalsnummer")) {
      answerFromRegister(run);
    } else if (lower.includes("vite")) {
      run.step("research_agent", () => {
        run.reason();
        run.toolCall(
          "search_documents",
          { query: "vite försenad leverans" },
          searchHits(context, [["7.2", "Vite vid försenad leverans", 3]]),
        );
      });
      const quote2 = citation(context, {
        id: 2,
        section_number: "7.2",
        section_title: "Vite vid försenad leverans",
        page: 3,
        quote: "Vite utgår även vid försenad delleverans.",
        verified: false,
      });
      run.rejectedDraft(
        answerOf({ text: "Vite utgår även vid försenad delleverans [1].", status: "verified" }),
        "citat 1 står inte i avsnittet. Läs avsnittet och citera det ordagrant.",
      );
      run.step("research_agent", () => {
        run.toolCall(
          "read_section",
          { sha256: context.documentSha256, section_number: "7.2" },
          { section_number: "7.2", page: 3, text: "Om Leverantören inte levererar i tid ..." },
        );
      });
      run.handIn(
        answerOf({
          text:
            "Vid försenad leverans har kunden rätt till vite med 0,5 procent av det avropade " +
            "värdet per påbörjad vecka, högst tio procent [1]. Om vitet också gäller " +
            "delleveranser kunde inte bekräftas [2]. Vitet ska betalas inom 30 dagar från " +
            "kravet [3].",
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
            quote2,
            // The section starts on page 3, the page a citation names; the quote is on page 4.
            citation(context, {
              id: 3,
              section_number: "7.2",
              section_title: "Vite vid försenad leverans",
              page: 3,
              quote:
                "Vitet ska betalas inom trettio (30) dagar från det att Kunden har framställt " +
                "krav på vite.",
              verified: true,
            }),
          ],
          reservations: [
            "Citat 2 kunde inte kontrolleras mot avtalstexten.",
            "Svaret granskades inte mot källorna, eftersom det inte klarade kontrollen av " +
              "citat och registeruppgifter.",
          ],
        }),
      );
    } else {
      run.step("research_agent", () => {
        run.toolCall("search_documents", { query: question.slice(0, 80) }, { hits: [] });
      });
      run.handIn(
        answerOf({
          text:
            "Jag hittar inget i avtalen som besvarar frågan. Ange gärna vilket ramavtal du menar " +
            "eller formulera frågan på ett annat sätt.",
          status: "no_answer",
        }),
      );
    }
  }

  run.messagesSnapshot();
  run.finish();
  return run.events;
}
