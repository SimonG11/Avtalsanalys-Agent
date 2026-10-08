import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { EventType } from "@ag-ui/core";
import type { BaseEvent, RunAgentInput } from "@ag-ui/core";

import { parseAnswer } from "../src/lib/contract.ts";
import { findQuote } from "../src/lib/highlight.ts";
import { OWN_CONTRACT_PAGES, PAGES, buildFixturePdf } from "./fixture-pdf.ts";
import { planRun } from "./scenarios.ts";
import type { UploadedFile } from "./scenarios.ts";

const CONTEXT = { documentSha256: "b".repeat(64) };

function input(question: string, extra: Partial<RunAgentInput> = {}): RunAgentInput {
  return {
    threadId: "t1",
    runId: "r1",
    messages: [{ id: "m1", role: "user", content: question }],
    tools: [],
    context: [],
    state: {},
    forwardedProps: {},
    ...extra,
  };
}

function events(run: RunAgentInput): (BaseEvent & Record<string, unknown>)[] {
  return planRun(run, CONTEXT).map(({ event }) => event as BaseEvent & Record<string, unknown>);
}

/** The `answer` of the last state snapshot in a run. */
function finalAnswer(run: RunAgentInput) {
  const snapshots = events(run).filter((event) => event.type === EventType.STATE_SNAPSHOT);
  const last = snapshots.at(-1)?.snapshot as { answer: Record<string, unknown> | null };
  return last.answer as {
    text: string;
    status: string;
    citations: { page: number; quote: string; verified: boolean }[];
  };
}

describe("planRun", () => {
  const questions = [
    "Hur säger kunden upp ett kontrakt inom IT-drift?",
    "Vilket vite gäller vid försenad leverans?",
    "Vad kostar en lunch?",
  ];

  it("starts and ends every run like ag-ui-langgraph, with each tool call complete", () => {
    for (const question of questions) {
      const list = events(input(question));
      assert.equal(list[0].type, EventType.RUN_STARTED);
      assert.equal(list.at(-1)?.type, EventType.RUN_FINISHED);
      const count = (type: EventType) => list.filter((event) => event.type === type).length;
      assert.equal(count(EventType.STEP_STARTED), count(EventType.STEP_FINISHED));
      assert.equal(count(EventType.TOOL_CALL_START), count(EventType.TOOL_CALL_END));
    }
  });

  it("gives every tool call a result event, except the drafts handed in with FinalAnswer", () => {
    for (const question of questions) {
      const list = events(input(question));
      const answered = new Set(
        list.filter((e) => e.type === EventType.TOOL_CALL_RESULT).map((e) => e.toolCallId),
      );
      const unanswered = list
        .filter((e) => e.type === EventType.TOOL_CALL_START && !answered.has(e.toolCallId))
        .map((e) => e.toolCallName);
      assert.ok(unanswered.length > 0, question);
      assert.ok(
        unanswered.every((name) => name === "FinalAnswer"),
        question,
      );
    }
  });

  it("sets the answer after a pause for the reviewer that MOCK_FAST keeps", () => {
    const timed = planRun(input(questions[0]), CONTEXT);
    const answerState = timed.find(
      ({ event }) =>
        event.type === EventType.STATE_SNAPSHOT &&
        (event as BaseEvent & { snapshot: { answer: unknown } }).snapshot.answer !== null,
    );
    assert.ok((answerState?.minPauseMs ?? 0) > 0);
  });

  it("rejects the first draft about vite in the messages at the end of the run", () => {
    const list = events(input(questions[1]));
    const snapshot = list.findLast((event) => event.type === EventType.MESSAGES_SNAPSHOT);
    const messages = snapshot?.messages as RunAgentInput["messages"];
    const drafts = messages.flatMap((message) =>
      message.role === "assistant"
        ? (message.toolCalls ?? []).filter((call) => call.function.name === "FinalAnswer")
        : [],
    );
    assert.equal(drafts.length, 2);
    const rejection = messages.find(
      (message) => message.role === "tool" && message.toolCallId === drafts[0].id,
    );
    assert.match(String(rejection?.content), /^Kontrollen underkände svaret \(försök 1 av 3\):/);
    assert.equal(finalAnswer(input(questions[1])).status, "with_reservation");
  });

  it("counts back the notice period with calculate_date when the question has a date", () => {
    const question =
      "Kontraktet inom IT-drift ska upphöra 2027-02-17. När måste kunden säga upp det?";
    const list = events(input(question));
    const call = list.find(
      (event) =>
        event.type === EventType.TOOL_CALL_START && event.toolCallName === "calculate_date",
    );
    assert.ok(call);
    const result = list.find(
      (event) => event.type === EventType.TOOL_CALL_RESULT && event.toolCallId === call.toolCallId,
    );
    assert.deepEqual(JSON.parse(String(result?.content)), {
      result: "2026-11-17",
      weekday: "tisdag",
      step: "2027-02-17 minus 3 månader = 2026-11-17",
      skipped: [],
      notes: [],
    });
    const answer = finalAnswer(input(question));
    assert.match(answer.text, /senast 2026-11-17: 2027-02-17 minus 3 månader = 2026-11-17\.$/);
    assert.equal(answer.citations.length, 1);
    // A month without the day uses its last day, as avtal-mcp does.
    const may = finalAnswer(input(question.replace("2027-02-17", "2027-05-31")));
    assert.match(may.text, /2027-05-31 minus 3 månader = 2027-02-28/);
  });

  it("clears the previous answer at the start of a new question", () => {
    const firstState = events(input(questions[0])).find(
      (event) => event.type === EventType.STATE_SNAPSHOT,
    );
    assert.deepEqual(firstState?.snapshot, { answer: null });
  });

  it("gives answers that follow the contract", () => {
    const all = [
      ...questions,
      "Vilken säkerhetsnivå gäller? Står det i en bilaga?",
      "Vilket avtalsnummer har IT-drift?",
    ];
    for (const question of all) {
      const snapshots = events(input(question)).filter(
        (event) => event.type === EventType.STATE_SNAPSHOT,
      );
      assert.equal(parseAnswer(snapshots.at(-1)?.snapshot).kind, "answer", question);
    }
  });

  it("cites quotes that are in the fixture from the cited page on, unless unverified", () => {
    const pagesWith = (quote: string, from: number) =>
      PAGES.map((lines, index) => ({ page: index + 1, lines }))
        .filter(({ page }) => page >= from)
        .filter(
          ({ lines }) =>
            findQuote(
              lines.map((str) => ({ str })),
              quote,
            ).kind === "full",
        )
        .map(({ page }) => page);
    for (const question of questions) {
      for (const citation of finalAnswer(input(question)).citations) {
        const found = pagesWith(citation.quote, citation.page);
        assert.equal(found.length > 0, citation.verified, citation.quote);
      }
    }
    // The vite answer's third quote is on the page after the one its section starts on.
    const third = finalAnswer(input(questions[1])).citations[2];
    assert.deepEqual([third.page, pagesWith(third.quote, third.page)], [3, [4]]);
  });

  it("asks which area is meant with both interrupt events, or with one of them", () => {
    const question = "Vilken uppsägningstid gäller för ett kontrakt?";
    const standard = events(input(question)).at(-1);
    const outcome = standard?.outcome as { type: string; interrupts: { metadata: unknown }[] };
    assert.equal(outcome.type, "interrupt");
    assert.deepEqual(outcome.interrupts[0].metadata, {
      langgraph: {
        raw: {
          question: "Vilket ramavtalsområde gäller frågan?",
          options: ["IT-drift", "Programvaror och tjänster", "Bemanningstjänster"],
        },
        ns: ["research_agent"],
        resumable: true,
        when: "during",
      },
    });

    assert.ok(events(input(question)).some((event) => event.type === EventType.CUSTOM));

    const legacy = events(input(`${question} [legacy]`));
    assert.equal(legacy.at(-1)?.outcome, undefined);
    assert.ok(legacy.some((event) => event.type === EventType.CUSTOM));

    const outcomeOnly = events(input(`${question} [outcome]`));
    assert.equal((outcomeOnly.at(-1)?.outcome as { type: string }).type, "interrupt");
    assert.ok(!outcomeOnly.some((event) => event.type === EventType.CUSTOM));
  });

  it("ends a failed run with RUN_ERROR and no state", () => {
    const list = events(input("Vad gäller för underleverantörer? [fel]"));
    assert.equal(list.at(-1)?.type, EventType.RUN_ERROR);
    assert.ok(!list.some((event) => event.type === EventType.STATE_SNAPSHOT));
    assert.ok(!list.some((event) => event.type === EventType.RUN_FINISHED));
  });

  it("calls ask_user and gives its result, the answer, in the run that resumes", () => {
    const question = "Vilken uppsägningstid gäller för ett kontrakt?";
    const asked = events(input(question));
    const calls = asked.filter(
      (event) => event.type === EventType.TOOL_CALL_START && event.toolCallName === "ask_user",
    );
    // The first call is refused for want of options; the second asks.
    assert.equal(calls.length, 2);
    const start = calls[1];
    const toolCallId = start.toolCallId as string;
    assert.ok(
      !asked.some((e) => e.type === EventType.TOOL_CALL_RESULT && e.toolCallId === toolCallId),
    );

    const snapshot = asked.findLast((event) => event.type === EventType.MESSAGES_SNAPSHOT);
    const messages = snapshot?.messages as RunAgentInput["messages"];
    const resumed = events(
      input(question, {
        messages,
        resume: [{ interruptId: "i1", status: "resolved", payload: "IT-drift" }],
      }),
    );
    const result = resumed.find(
      (event) => event.type === EventType.TOOL_CALL_RESULT && event.toolCallId === toolCallId,
    );
    assert.equal(result?.content, "IT-drift");
    // Like ag-ui-langgraph, the resumed run streams the ask_user call again before its result.
    const repeated = resumed.findIndex(
      (event) => event.type === EventType.TOOL_CALL_START && event.toolCallId === toolCallId,
    );
    assert.ok(repeated >= 0 && repeated < resumed.indexOf(result!));

    // The refused call has no result event, only a tool message with the error in the snapshot.
    const refused = calls[0].toolCallId;
    assert.ok(
      !asked.some((e) => e.type === EventType.TOOL_CALL_RESULT && e.toolCallId === refused),
    );
    const refusal = messages.find((m) => m.role === "tool" && m.toolCallId === refused);
    assert.ok(refusal?.role === "tool" && refusal.error);
  });

  it("gives every call to avtal-mcp a syfte first, and refuses the register call without one", () => {
    const own = new Set(["ask_user", "FinalAnswer", "list_uploads", "read_upload"]);
    const all = [
      ...questions,
      "Vilken uppsägningstid gäller för ett kontrakt?",
      "Kontraktet inom IT-drift ska upphöra 2027-02-17. När måste kunden säga upp det?",
      "Vilken säkerhetsnivå gäller? Står det i en bilaga?",
      "Vilka avtalsnummer finns för IT-drift?",
    ];
    for (const question of all) {
      const list = events(input(question));
      const snapshot = list.findLast((event) => event.type === EventType.MESSAGES_SNAPSHOT);
      const messages = (snapshot?.messages ?? []) as RunAgentInput["messages"];
      const refused = new Set(
        messages.flatMap((m) => (m.role === "tool" && m.error ? [m.toolCallId] : [])),
      );
      for (const message of messages) {
        if (message.role !== "assistant") continue;
        for (const call of message.toolCalls ?? []) {
          if (own.has(call.function.name) || refused.has(call.id)) continue;
          const keys = Object.keys(JSON.parse(call.function.arguments));
          assert.equal(keys[0], "syfte", `${question}: ${call.function.name}`);
        }
      }
    }

    // The refused call streams as a step without a result, and the model calls again.
    const list = events(input("Vilka avtalsnummer finns för IT-drift?"));
    const starts = list.filter(
      (event) =>
        event.type === EventType.TOOL_CALL_START && event.toolCallName === "search_register",
    );
    assert.equal(starts.length, 2);
    const results = list.filter((event) => event.type === EventType.TOOL_CALL_RESULT);
    assert.deepEqual(
      starts.map((start) => results.some((result) => result.toolCallId === start.toolCallId)),
      [false, true],
    );
    const snapshot = list.findLast((event) => event.type === EventType.MESSAGES_SNAPSHOT);
    const refusal = (snapshot?.messages as RunAgentInput["messages"]).find(
      (m) => m.role === "tool" && m.toolCallId === starts[0].toolCallId,
    );
    assert.match(refusal?.role === "tool" ? (refusal.error ?? "") : "", /^Anropet nekades/);
  });

  it("continues with the answer from either resume channel", () => {
    const question = "Vilken uppsägningstid gäller för ett kontrakt?";
    const viaResume = input(question, {
      resume: [{ interruptId: "i1", status: "resolved", payload: "Bemanningstjänster" }],
    });
    const viaCommand = input(question, {
      forwardedProps: { command: { resume: "Programvaror och tjänster" } },
    });
    assert.match(finalAnswer(viaResume).text, /^I ramavtalet för Bemanningstjänster/);
    assert.match(finalAnswer(viaCommand).text, /^I ramavtalet för Programvaror och tjänster/);
  });
});

describe("planRun with an uploaded file", () => {
  const file = (kind: UploadedFile["kind"]): UploadedFile => ({
    upload_id: "upl_1",
    filename: kind === "pdf" ? "vårt-kontrakt.pdf" : "vårt-kontrakt.txt",
    kind,
    pages: kind === "pdf" ? 1 : null,
    sections: 2,
    sha256: "c".repeat(64),
  });
  const run = (uploaded: UploadedFile | null, question = "Jämför min fil med ramavtalet") =>
    planRun(input(question), { ...CONTEXT, uploads: uploaded ? [uploaded] : [] }).map(
      ({ event }) => event as BaseEvent & Record<string, unknown>,
    );
  const answerOf = (list: (BaseEvent & Record<string, unknown>)[]) =>
    parseAnswer(list.filter((event) => event.type === EventType.STATE_SNAPSHOT).at(-1)?.snapshot);

  it("reads the file with the upload tools and cites it next to the agreement", () => {
    const list = run(file("pdf"));
    const tools = list
      .filter((event) => event.type === EventType.TOOL_CALL_START)
      .map((event) => event.toolCallName);
    assert.deepEqual(tools, [
      "list_uploads",
      "read_upload",
      "search_documents",
      "read_section",
      "FinalAnswer",
    ]);
    const parsed = answerOf(list);
    assert.equal(parsed.kind, "answer");
    const [own, framework] = parsed.kind === "answer" ? parsed.answer.citations : [];
    assert.equal(own.source, "upload");
    assert.equal(own.upload_id, "upl_1");
    assert.equal(own.file_title, "vårt-kontrakt.pdf");
    assert.equal(own.page, 1);
    assert.equal(framework.source, "framework");
    // The quote is on the own contract's page, as it is in the agreement's.
    const lines = OWN_CONTRACT_PAGES[0].map((str) => ({ str, hasEOL: true }));
    assert.equal(findQuote(lines, own.quote).kind, "full");
  });

  it("cites a text file without a page", () => {
    const parsed = answerOf(run(file("text")));
    assert.equal(parsed.kind === "answer" && parsed.answer.citations[0].page, null);
  });

  it("answers as before without a file, or when the question is not about it", () => {
    const withoutFile = answerOf(run(null));
    assert.equal(withoutFile.kind === "answer" && withoutFile.answer.status, "no_answer");
    const termination = run(file("pdf"), "Hur säger kunden upp ett kontrakt inom IT-drift?");
    assert.ok(!termination.some((event) => event.toolCallName === "read_upload"));
  });
});

describe("buildFixturePdf", () => {
  it("builds the same file every time, so its SHA-256 is stable", async () => {
    const [first, second] = await Promise.all([buildFixturePdf(), buildFixturePdf()]);
    assert.equal(first.sha256, second.sha256);
    assert.equal(Buffer.from(first.bytes.subarray(0, 5)).toString(), "%PDF-");
  });
});
