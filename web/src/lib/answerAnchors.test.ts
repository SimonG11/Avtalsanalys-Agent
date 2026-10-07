import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { answerAnchors, isLatestToolCall, lastQuestionId } from "./answerAnchors.ts";

const CHAT = [
  { id: "q1", role: "user" },
  { id: "a1", role: "assistant" },
  { id: "t1", role: "tool" },
  { id: "q2", role: "user" },
  { id: "a2", role: "assistant" },
  { id: "t2", role: "tool" },
  { id: "a3", role: "assistant" },
];

describe("answerAnchors", () => {
  it("puts each answer after the last message before the next question", () => {
    assert.deepEqual(
      answerAnchors(CHAT),
      new Map([
        ["t1", "q1"],
        ["a3", "q2"],
      ]),
    );
  });

  it("anchors a question nobody has answered yet to the question itself", () => {
    assert.deepEqual(answerAnchors([{ id: "q1", role: "user" }]), new Map([["q1", "q1"]]));
  });

  it("ignores messages before the first question", () => {
    assert.deepEqual(answerAnchors([{ id: "s", role: "system" }]), new Map());
  });
});

describe("lastQuestionId", () => {
  it("finds the most recent user message", () => {
    assert.equal(lastQuestionId(CHAT), "q2");
    assert.equal(lastQuestionId([]), null);
  });
});

describe("isLatestToolCall", () => {
  const chat = [
    { id: "q1", role: "user" },
    { id: "a1", role: "assistant", toolCalls: [{ id: "c1" }] },
    { id: "q2", role: "user" },
    { id: "a2", role: "assistant", toolCalls: [{ id: "c2" }] },
    { id: "a3", role: "assistant", toolCalls: [{ id: "c3" }, { id: "c4" }] },
  ];

  it("is true only for the last call after the last question", () => {
    assert.equal(isLatestToolCall(chat, "c4"), true);
    assert.equal(isLatestToolCall(chat, "c3"), false);
    assert.equal(isLatestToolCall(chat, "c2"), false);
    assert.equal(isLatestToolCall(chat, "c1"), false);
  });

  it("is false when the last question has no calls yet, or the call is not in the messages", () => {
    assert.equal(isLatestToolCall([...chat, { id: "q3", role: "user" }], "c4"), false);
    assert.equal(isLatestToolCall(chat, "c5"), false);
  });
});
