import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { parseAnswer, parseAskUser } from "./contract.ts";

const CITATION = {
  id: 1,
  sha256: "a".repeat(64),
  file_title: "Allmänna villkor",
  page_title: "IT-drift Större, fler än 200 anställda",
  section_number: "6.21.9",
  section_title: "Uppsägning",
  page: 14,
  quote: "Kunden har rätt att säga upp Kontraktet.",
  verified: true,
};

describe("parseAnswer", () => {
  it("reads a valid answer", () => {
    const state = {
      answer: { text: "Tre månader [1].", status: "verified", citations: [CITATION] },
    };
    const parsed = parseAnswer(state);
    assert.equal(parsed.kind, "answer");
    assert.equal(parsed.kind === "answer" && parsed.answer.citations[0].page, 14);
  });

  it("treats a missing or null answer as no answer yet", () => {
    assert.deepEqual(parseAnswer(undefined), { kind: "none" });
    assert.deepEqual(parseAnswer({}), { kind: "none" });
    assert.deepEqual(parseAnswer({ answer: null }), { kind: "none" });
  });

  it("reports where an answer breaks the contract", () => {
    const state = {
      answer: { text: "x", status: "verified", citations: [{ ...CITATION, page: "14" }] },
    };
    const parsed = parseAnswer(state);
    assert.equal(parsed.kind, "invalid");
    assert.match(parsed.kind === "invalid" ? parsed.problem : "", /^citations\.0\.page:/);
  });

  it("rejects an unknown status", () => {
    const parsed = parseAnswer({ answer: { text: "x", status: "maybe", citations: [] } });
    assert.equal(parsed.kind, "invalid");
  });
});

describe("parseAskUser", () => {
  const value = { question: "Vilket avtal menar du?", options: ["IT-drift", "Programvaror"] };

  it("reads the AG-UI standard interrupt from ag-ui-langgraph", () => {
    const standard = {
      id: "i1",
      reason: "langgraph:interrupt",
      metadata: { langgraph: { raw: value } },
    };
    assert.deepEqual(parseAskUser(standard, standard), value);
  });

  it("reads the older on_interrupt event, whose value is a JSON string", () => {
    assert.deepEqual(parseAskUser(null, JSON.stringify(value)), value);
  });

  it("falls back to the interrupt message when the raw value is missing", () => {
    assert.deepEqual(parseAskUser({ id: "i1", message: "Vilket år?" }, undefined), {
      question: "Vilket år?",
    });
  });

  it("returns null for an interrupt that is not ask_user", () => {
    assert.equal(parseAskUser(null, "not json"), null);
    assert.equal(parseAskUser(null, { approve: true }), null);
  });
});
