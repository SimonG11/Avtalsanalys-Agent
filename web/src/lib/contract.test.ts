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

  it("accepts the fields the contract allows to be null", () => {
    const word = { ...CITATION, page_title: null, section_number: null, page: null };
    const parsed = parseAnswer({
      answer: { text: "Framgår inte [1].", status: "no_answer", citations: [word] },
    });
    assert.equal(parsed.kind, "answer");
  });

  it("reads a citation from an uploaded file, and takes older citations as the agreements'", () => {
    const upload = {
      ...CITATION,
      id: 2,
      source: "upload",
      upload_id: "upl_1",
      file_title: "vårt-kontrakt.pdf",
      page_title: null,
      section_number: null,
      page: 1,
    };
    const parsed = parseAnswer({
      answer: {
        text: "En månad [2], tre månader [1].",
        status: "verified",
        citations: [CITATION, upload],
      },
    });
    assert.equal(parsed.kind, "answer");
    const [framework, own] = parsed.kind === "answer" ? parsed.answer.citations : [];
    assert.equal(framework.source, "framework");
    assert.equal(own.source, "upload");
    assert.equal(own.upload_id, "upl_1");
    // An uploaded file's citation needs its upload id, since the file is opened by it.
    const withoutId = { ...upload, upload_id: null };
    assert.equal(
      parseAnswer({ answer: { text: "x", status: "verified", citations: [withoutId] } }).kind,
      "invalid",
    );
    // A source without a hash is still shown, only without its PDF.
    const withoutHash = { ...CITATION, sha256: null };
    assert.equal(
      parseAnswer({ answer: { text: "x", status: "verified", citations: [withoutHash] } }).kind,
      "answer",
    );
  });

  it("reads the reservations and register rows, and defaults them for older answers", () => {
    const fact = {
      agreement_number: "00.0-0000-2026-001",
      supplier_name: "Exempelleverantören AB",
      former_names: [],
      org_number: "000000-0000",
      sub_area: "IT-drift / IT-drift Större",
      valid_from: "2026-01-01",
      valid_to: "2028-12-31",
      max_extension_to: null,
    };
    const state = {
      answer: {
        text: "Avtalet gäller till 2028-12-31.",
        status: "with_reservation",
        citations: [],
        reservations: ["Svaret kunde inte granskas."],
        register_facts: [fact],
      },
    };
    const parsed = parseAnswer(state);
    assert.equal(parsed.kind, "answer");
    assert.deepEqual(parsed.kind === "answer" && parsed.answer.reservations, [
      "Svaret kunde inte granskas.",
    ]);
    assert.deepEqual(parsed.kind === "answer" && parsed.answer.register_facts, [fact]);

    const older = parseAnswer({ answer: { text: "x", status: "no_answer", citations: [] } });
    assert.deepEqual(older.kind === "answer" && older.answer.reservations, []);
    assert.deepEqual(older.kind === "answer" && older.answer.register_facts, []);
  });

  it("rejects an empty quote", () => {
    const state = {
      answer: { text: "x", status: "verified", citations: [{ ...CITATION, quote: "" }] },
    };
    assert.equal(parseAnswer(state).kind, "invalid");
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

  it("treats options sent as null (Python's None) as no options", () => {
    assert.deepEqual(
      parseAskUser(null, JSON.stringify({ question: "Vilket år?", options: null })),
      {
        question: "Vilket år?",
        options: null,
      },
    );
  });

  it("returns null for an interrupt that is not ask_user", () => {
    assert.equal(parseAskUser(null, "not json"), null);
    assert.equal(parseAskUser(null, { approve: true }), null);
    // An interrupt with only a message, such as an approval, is not ask_user either.
    const approval = { id: "i2", message: "Godkänn verktygsanropet?", metadata: {} };
    assert.equal(parseAskUser(approval, approval), null);
  });
});
