import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { EventType } from "@ag-ui/core";
import type { BaseEvent, RunAgentInput } from "@ag-ui/core";

import { findQuote } from "../src/lib/highlight.ts";
import { PAGES, buildFixturePdf } from "./fixture-pdf.ts";
import { planRun } from "./scenarios.ts";

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
      assert.equal(count(EventType.TOOL_CALL_START), count(EventType.TOOL_CALL_RESULT));
    }
  });

  it("clears the previous answer at the start of a new question", () => {
    const firstState = events(input(questions[0])).find(
      (event) => event.type === EventType.STATE_SNAPSHOT,
    );
    assert.deepEqual(firstState?.snapshot, { answer: null });
  });

  it("cites only quotes that are on the cited page of the fixture, unless unverified", () => {
    for (const question of questions) {
      for (const citation of finalAnswer(input(question)).citations) {
        const lines = PAGES[citation.page - 1].map((str) => ({ str }));
        const match = findQuote(lines, citation.quote);
        assert.equal(match.kind, citation.verified ? "full" : "none", citation.quote);
      }
    }
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

describe("buildFixturePdf", () => {
  it("builds the same file every time, so its SHA-256 is stable", async () => {
    const [first, second] = await Promise.all([buildFixturePdf(), buildFixturePdf()]);
    assert.equal(first.sha256, second.sha256);
    assert.equal(Buffer.from(first.bytes.subarray(0, 5)).toString(), "%PDF-");
  });
});
