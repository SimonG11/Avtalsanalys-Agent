import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { splitAnswerText } from "./answerText.ts";
import { describeToolCall } from "./tools.ts";

describe("describeToolCall", () => {
  it("gives a Swedish title and puts the main argument first", () => {
    const description = describeToolCall(
      "search_documents",
      { query: "uppsägningstid", agreement_number: "23.3-6372-2021-001", limit: 5 },
      "executing",
    );
    assert.deepEqual(description, {
      title: "Söker i dokumenten",
      subject: "uppsägningstid",
      details: [
        ["avtal", "23.3-6372-2021-001"],
        ["antal", "5"],
      ],
    });
  });

  it("uses the past tense when the tool has answered and shortens hashes", () => {
    const description = describeToolCall(
      "read_section",
      { sha256: "0123456789abcdef".repeat(4), section_number: "6.21.9" },
      "complete",
    );
    assert.equal(description.title, "Läste avsnitt");
    assert.equal(description.subject, "6.21.9");
    assert.deepEqual(description.details, [["dokument", "01234567…"]]);
  });

  it("leaves out arguments that repeat a default or the section number", () => {
    const sha256 = "0123456789abcdef".repeat(4);
    assert.deepEqual(
      describeToolCall("search_register", { framework_area: "IT-drift", offset: 0 }, "complete")
        .details,
      [["område", "IT-drift"]],
    );
    assert.deepEqual(
      describeToolCall(
        "read_section",
        { sha256, section_number: "6.21.9", section_position: 4 },
        "complete",
      ).details,
      [["dokument", "01234567…"]],
    );
    // A section without a number is found by its place, so the place is shown.
    assert.deepEqual(
      describeToolCall("read_section", { sha256, section_position: 0 }, "complete").details,
      [
        ["dokument", "01234567…"],
        ["plats i filen", "0"],
      ],
    );
  });

  it("shows unknown tools and arguments under their own names", () => {
    const description = describeToolCall("new_tool", { region: "Norr", empty: "" }, "inProgress");
    assert.deepEqual(description, {
      title: "new_tool",
      subject: null,
      details: [["region", "Norr"]],
    });
  });

  it("shows the question to the person with its options as a list", () => {
    const description = describeToolCall(
      "ask_user",
      { question: "Vilket område?", options: ["IT-drift", "Bemanningstjänster"] },
      "executing",
    );
    assert.deepEqual(description, {
      title: "Frågar dig",
      subject: "Vilket område?",
      details: [["alternativ", "IT-drift, Bemanningstjänster"]],
    });
  });

  it("copes with arguments that are still streaming", () => {
    assert.deepEqual(describeToolCall("get_outline", undefined, "inProgress").details, []);
  });
});

describe("splitAnswerText", () => {
  const ids = new Set([1, 2]);

  it("turns known markers into citation segments", () => {
    assert.deepEqual(splitAnswerText("Tre månader [1], skriftligt [1, 2].", ids), [
      { kind: "text", text: "Tre månader " },
      { kind: "citation", id: 1 },
      { kind: "text", text: ", skriftligt " },
      { kind: "citation", id: 1 },
      { kind: "citation", id: 2 },
      { kind: "text", text: "." },
    ]);
  });

  it("reads adjacent markers as separate citations", () => {
    assert.deepEqual(splitAnswerText("Skriftligt [1][2].", ids), [
      { kind: "text", text: "Skriftligt " },
      { kind: "citation", id: 1 },
      { kind: "citation", id: 2 },
      { kind: "text", text: "." },
    ]);
  });

  it("leaves markers without a citation as text", () => {
    assert.deepEqual(splitAnswerText("Se [3] och [1]", ids), [
      { kind: "text", text: "Se [3] och " },
      { kind: "citation", id: 1 },
    ]);
  });
});
