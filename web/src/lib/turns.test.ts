import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  buildTurns,
  currentActivity,
  lastQuestionId,
  splitThought,
  stepCount,
  textOf,
} from "./turns.ts";
import type { ConversationMessage } from "./turns.ts";

function call(id: string, name: string, args: string) {
  return { id, function: { name, arguments: args } };
}

const QUESTION: ConversationMessage = { id: "q1", role: "user", content: "Uppsägning i IT-drift?" };

describe("buildTurns", () => {
  it("puts thoughts, steps and their results under the question, in order", () => {
    const turns = buildTurns([
      QUESTION,
      { id: "r1", role: "reasoning", content: "**Söker**\n\nJag letar i villkoren." },
      {
        id: "a1",
        role: "assistant",
        content: "",
        toolCalls: [call("c1", "search_documents", '{"query": "uppsägning"}')],
      },
      { id: "t1", role: "tool", toolCallId: "c1", content: '{"hits": []}' },
    ]);
    assert.equal(turns.length, 1);
    assert.equal(turns[0].questionId, "q1");
    assert.equal(turns[0].question, "Uppsägning i IT-drift?");
    assert.deepEqual(turns[0].items, [
      { kind: "thought", id: "r1", text: "**Söker**\n\nJag letar i villkoren." },
      {
        kind: "tool",
        id: "c1",
        name: "search_documents",
        args: { query: "uppsägning" },
        result: '{"hits": []}',
      },
    ]);
  });

  it("starts a new turn for every question and skips messages before the first", () => {
    const turns = buildTurns([
      { id: "s", role: "system", content: "ignored" },
      QUESTION,
      { id: "q2", role: "user", content: [{ type: "text", text: "Och vite?" }] },
      { id: "r2", role: "reasoning", content: "" },
    ]);
    assert.deepEqual(
      turns.map((turn) => [turn.question, turn.items.length]),
      [
        ["Uppsägning i IT-drift?", 0],
        ["Och vite?", 1],
      ],
    );
  });

  it("reads streaming arguments as far as they have come", () => {
    const [turn] = buildTurns([
      QUESTION,
      {
        id: "a1",
        role: "assistant",
        toolCalls: [call("c1", "search_documents", '{"query": "upp')],
      },
    ]);
    assert.deepEqual(turn.items[0], {
      kind: "tool",
      id: "c1",
      name: "search_documents",
      args: { query: "upp" },
      result: undefined,
    });
  });

  it("shows FinalAnswer as drafts: submitted, sent back with a reason, or still checked", () => {
    const [turn] = buildTurns([
      QUESTION,
      { id: "a1", role: "assistant", toolCalls: [call("d1", "FinalAnswer", "{}")] },
      {
        id: "t1",
        role: "tool",
        toolCallId: "d1",
        content: "Kontrollen underkände svaret (försök 1 av 3): citat 1 står inte i avsnittet.",
      },
      { id: "a2", role: "assistant", toolCalls: [call("d2", "FinalAnswer", "{}")] },
      { id: "t2", role: "tool", toolCallId: "d2", content: "Svaret är lämnat för kontroll." },
      { id: "a3", role: "assistant", toolCalls: [call("d3", "FinalAnswer", "{}")] },
    ]);
    assert.deepEqual(turn.items, [
      { kind: "draft", id: "d1", outcome: "rejected", reason: "citat 1 står inte i avsnittet." },
      { kind: "draft", id: "d2", outcome: "submitted", reason: null },
      { kind: "draft", id: "d3", outcome: "pending", reason: null },
    ]);
  });

  it("counts a draft without an answer as sent back when the agent went on working", () => {
    const [turn] = buildTurns([
      QUESTION,
      { id: "a1", role: "assistant", toolCalls: [call("d1", "FinalAnswer", "{}")] },
      { id: "a2", role: "assistant", toolCalls: [call("c2", "read_section", "{}")] },
    ]);
    assert.equal(turn.items[0].kind === "draft" && turn.items[0].outcome, "rejected");
  });

  it("leaves out a draft the format check refused, and empty assistant text", () => {
    const [turn] = buildTurns([
      QUESTION,
      { id: "a1", role: "assistant", content: "  ", toolCalls: [call("d1", "FinalAnswer", "{")] },
      { id: "t1", role: "tool", toolCallId: "d1", content: "Error: Failed to parse FinalAnswer" },
      { id: "a2", role: "assistant", content: "Jag läser avsnittet." },
    ]);
    assert.deepEqual(turn.items, [{ kind: "thought", id: "a2", text: "Jag läser avsnittet." }]);
  });
});

describe("buildTurns and refused questions", () => {
  const ask = (id: string, args: string) => ({
    id: `a-${id}`,
    role: "assistant",
    toolCalls: [call(id, "ask_user", args)],
  });

  it("leaves out an ask_user call the backend refused, once its tool message says so", () => {
    const [turn] = buildTurns([
      QUESTION,
      ask("c1", '{"question": "Vilket område?"}'),
      { id: "t1", role: "tool", toolCallId: "c1", content: "", error: "options saknas" },
      ask("c2", '{"question": "Vilket område?", "options": ["IT-drift", "Bemanning"]}'),
    ]);
    assert.deepEqual(
      turn.items.map((item) => item.id),
      ["c2"],
    );
  });

  it("leaves out an unanswered ask_user call once the agent goes on with other work", () => {
    const [turn] = buildTurns([
      QUESTION,
      ask("c1", '{"question": "Vilket område?"}'),
      { id: "a2", role: "assistant", toolCalls: [call("c2", "search_documents", "{}")] },
    ]);
    assert.deepEqual(
      turn.items.map((item) => item.id),
      ["c2"],
    );
  });

  it("keeps questions that wait for their answers, also two at once and before a thought", () => {
    const [turn] = buildTurns([
      QUESTION,
      ask("c1", '{"question": "Vilket område?", "options": ["A", "B"]}'),
      ask("c2", '{"question": "Vilket fall?", "options": ["C", "D"]}'),
      // The messages snapshot ends with empty reasoning messages.
      { id: "rs_1", role: "reasoning", content: "" },
    ]);
    assert.deepEqual(
      turn.items.map((item) => item.id),
      ["c1", "c2", "rs_1"],
    );
  });

  it("shows a failed tool's error when it has no other content", () => {
    const [turn] = buildTurns([
      QUESTION,
      { id: "a1", role: "assistant", toolCalls: [call("c1", "read_section", "{}")] },
      { id: "t1", role: "tool", toolCallId: "c1", content: "", error: "Avsnittet finns inte." },
    ]);
    assert.equal(turn.items[0].kind === "tool" && turn.items[0].result, "Avsnittet finns inte.");
  });
});

describe("currentActivity", () => {
  it("names the step that waits for its result, the check, or the model's next move", () => {
    assert.equal(currentActivity([]), "Tänker …");
    const search = { kind: "tool", id: "c", name: "search_documents", args: {} } as const;
    assert.equal(currentActivity([{ ...search, result: undefined }]), "Söker i dokumenten …");
    assert.equal(currentActivity([{ ...search, result: "{}" }]), "Tänker …");
    assert.equal(
      currentActivity([{ kind: "draft", id: "d", outcome: "pending", reason: null }]),
      "Kontrollerar svaret …",
    );
    assert.equal(
      currentActivity([{ kind: "draft", id: "d", outcome: "rejected", reason: null }]),
      "Tänker …",
    );
  });
});

describe("stepCount", () => {
  it("counts tool calls and drafts, not thoughts", () => {
    assert.equal(
      stepCount([
        { kind: "thought", id: "r", text: "" },
        { kind: "tool", id: "c", name: "read_section", args: {}, result: "{}" },
        { kind: "draft", id: "d", outcome: "submitted", reason: null },
      ]),
      2,
    );
  });
});

describe("textOf", () => {
  it("reads a string or the text parts of a list", () => {
    assert.equal(textOf("hej"), "hej");
    assert.equal(
      textOf([{ type: "text", text: "a" }, { type: "binary" }, { type: "text", text: "b" }]),
      "a\nb",
    );
    assert.equal(textOf(undefined), "");
  });
});

describe("splitThought", () => {
  it("takes a leading bold line as the title", () => {
    assert.deepEqual(splitThought("**Söker i villkoren**\n\nJag letar efter uppsägning."), {
      title: "Söker i villkoren",
      body: "Jag letar efter uppsägning.",
    });
  });

  it("keeps text without a bold first line as it is", () => {
    assert.deepEqual(splitThought("Jag letar **noga**."), {
      title: null,
      body: "Jag letar **noga**.",
    });
  });

  it("shows a title that is still streaming without its asterisks", () => {
    assert.deepEqual(splitThought("**Letar efter"), { title: "Letar efter", body: "" });
    assert.deepEqual(splitThought("**"), { title: null, body: "" });
  });
});

describe("lastQuestionId", () => {
  it("finds the most recent user message", () => {
    const chat = [QUESTION, { id: "a1", role: "assistant" }, { id: "q2", role: "user" }];
    assert.equal(lastQuestionId(chat), "q2");
    assert.equal(lastQuestionId([]), null);
  });
});
