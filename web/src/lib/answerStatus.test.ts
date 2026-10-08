import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { statusHelp } from "./answerStatus.ts";
import type { Answer } from "./contract.ts";

const CITATION = {
  id: 1,
  source: "framework" as const,
  sha256: "a".repeat(64),
  file_title: "Allmänna villkor",
  page_title: null,
  section_number: "6.21.9",
  section_title: "Uppsägning",
  page: 14,
  quote: "Kunden har rätt att säga upp Kontraktet.",
  verified: true,
};

const FACT = {
  agreement_number: "00.0-0000-2026-001",
  supplier_name: "Exempelleverantören AB",
  former_names: [],
  org_number: "000000-0000",
  sub_area: "IT-drift",
  valid_from: "2026-01-01",
  valid_to: "2028-12-31",
  max_extension_to: null,
};

function answer(fields: Partial<Answer>): Answer {
  return {
    text: "x",
    status: "verified",
    citations: [],
    reservations: [],
    register_facts: [],
    ...fields,
  };
}

describe("statusHelp", () => {
  it("says what a verified answer was checked against", () => {
    assert.match(statusHelp(answer({ citations: [CITATION] })), /^Citaten står i avtalstexten\./);
    assert.match(
      statusHelp(answer({ register_facts: [FACT] })),
      /^Uppgifterna stämmer med registret\./,
    );
    assert.match(
      statusHelp(answer({ citations: [CITATION], register_facts: [FACT] })),
      /^Citaten står i avtalstexten och uppgifterna stämmer med registret\./,
    );
  });

  it("says when a quote is from the person's own file", () => {
    const own = { ...CITATION, id: 2, source: "upload" as const, upload_id: "upl_1" };
    assert.match(statusHelp(answer({ citations: [own] })), /^Citaten står i din fil\./);
    assert.match(
      statusHelp(answer({ citations: [CITATION, own] })),
      /^Citaten står i avtalstexten och i din fil\./,
    );
  });

  it("points to the reservations when there are any", () => {
    const reserved = answer({
      status: "with_reservation",
      reservations: ["Svaret kunde inte granskas."],
    });
    assert.match(statusHelp(reserved), /Se reservationerna under svaret\.$/);
    assert.equal(
      statusHelp(answer({ status: "with_reservation" })),
      "Allt i svaret kunde inte kontrolleras.",
    );
  });

  it("says that the sources of no answer show where the question is regulated", () => {
    assert.match(
      statusHelp(answer({ status: "no_answer", citations: [CITATION] })),
      /var frågan regleras/,
    );
    assert.match(statusHelp(answer({ status: "no_answer" })), /inget i avtalen/);
  });
});
