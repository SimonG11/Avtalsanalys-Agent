import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { splitAnswerText } from "./answerText.ts";
import { describeToolCall, summarizeResult } from "./tools.ts";

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

  it("names the sub-area and the date calculation's arguments and values in Swedish", () => {
    assert.deepEqual(
      describeToolCall("search_register", { sub_area: "IT-tjänster / Övre Norrland" }, "complete")
        .details,
      [["delområde", "IT-tjänster / Övre Norrland"]],
    );
    const args = { start: "2027-02-17", amount: 3, unit: "months", direction: "before" };
    assert.deepEqual(describeToolCall("calculate_date", args, "complete"), {
      title: "Räknade ut datum",
      subject: null,
      details: [
        ["från", "2027-02-17"],
        ["antal", "3"],
        ["enhet", "månader"],
        ["riktning", "före"],
      ],
    });
    // include_start is shown only when the start day belongs to the period.
    const withStart = { ...args, unit: "working_days", direction: "after", include_start: true };
    assert.deepEqual(describeToolCall("calculate_date", withStart, "complete").details.slice(2), [
      ["enhet", "arbetsdagar"],
      ["riktning", "efter"],
      ["startdagen ingår", "ja"],
    ]);
    assert.equal(
      describeToolCall("calculate_date", { ...args, include_start: false }, "complete").details
        .length,
      4,
    );
  });

  it("puts the section first for find_amendments", () => {
    const sha256 = "0123456789abcdef".repeat(4);
    const description = describeToolCall(
      "find_amendments",
      { sha256, section_number: "3.2" },
      "inProgress",
    );
    assert.equal(description.title, "Letar efter ändringar");
    assert.equal(description.subject, "3.2");
  });

  it("names the person's uploaded file instead of its id", () => {
    const names = new Map([["upl_1", "vårt-kontrakt.pdf"]]);
    assert.deepEqual(
      describeToolCall(
        "read_upload",
        { upload_id: "upl_1", query: "uppsägning" },
        "complete",
        names,
      ),
      { title: "Läste din fil", subject: "vårt-kontrakt.pdf", details: [["sökord", "uppsägning"]] },
    );
    // A file the web app does not know, e.g. after a reload, is shown by its id.
    assert.equal(
      describeToolCall("read_upload", { upload_id: "upl_2" }, "inProgress").subject,
      "upl_2",
    );
    assert.equal(describeToolCall("list_uploads", {}, "inProgress").title, "Listar dina filer");
  });
});

describe("summarizeResult", () => {
  it("gives the calculation of calculate_date with its weekday", () => {
    const result = JSON.stringify({
      result: "2026-11-17",
      weekday: "tisdag",
      step: "2027-02-17 minus 3 månader = 2026-11-17",
      skipped: [],
      notes: [],
    });
    assert.equal(
      summarizeResult("calculate_date", result),
      "2027-02-17 minus 3 månader = 2026-11-17 (tisdag)",
    );
  });

  it("counts the amendments find_amendments found, and those it may not show", () => {
    const answer = (amendments: number, held_back: number) =>
      JSON.stringify({ target: {}, amendments: Array(amendments).fill({}), held_back });
    assert.equal(summarizeResult("find_amendments", answer(0, 0)), "Inga ändringar");
    assert.equal(summarizeResult("find_amendments", answer(1, 0)), "1 ändring");
    assert.equal(
      summarizeResult("find_amendments", answer(2, 1)),
      "2 ändringar, 1 till som inte kan visas",
    );
  });

  it("counts the files list_uploads found, as a list or under `uploads`", () => {
    assert.equal(summarizeResult("list_uploads", JSON.stringify([])), "Inga filer");
    assert.equal(summarizeResult("list_uploads", JSON.stringify([{ upload_id: "a" }])), "1 fil");
    assert.equal(summarizeResult("list_uploads", JSON.stringify({ uploads: [{}, {}] })), "2 filer");
    assert.equal(summarizeResult("list_uploads", "Inga filer i tråden."), null);
  });

  it("gives nothing for other tools, an error text or a missing result", () => {
    assert.equal(summarizeResult("search_documents", JSON.stringify({ hits: [] })), null);
    assert.equal(summarizeResult("calculate_date", "Startdatumet är inte ett datum."), null);
    assert.equal(summarizeResult("calculate_date", undefined), null);
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
