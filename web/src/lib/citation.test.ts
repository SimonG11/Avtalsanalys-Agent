import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { locationLabel, sectionLabel, sourceLabel } from "./citation.ts";

const FULL = {
  file_title: "Allmänna villkor",
  section_number: "6.21.9",
  section_title: "Uppsägning",
  page: 14,
};

describe("sourceLabel and locationLabel", () => {
  it("describe a citation with every field", () => {
    assert.equal(sourceLabel(FULL), "Allmänna villkor, avsnitt 6.21.9 Uppsägning, s. 14");
    assert.equal(locationLabel(FULL), "Avsnitt 6.21.9 Uppsägning · sida 14");
  });

  it("leave out the number of a section without one and the page of a Word file", () => {
    const word = { ...FULL, section_number: null, section_title: "Avropsbilaga", page: null };
    assert.equal(sourceLabel(word), "Allmänna villkor, avsnitt Avropsbilaga");
    assert.equal(locationLabel(word), "Avsnitt Avropsbilaga");
  });

  it("name an unknown document when the section could not be read", () => {
    const unread = { file_title: "", section_number: null, section_title: "", page: null };
    assert.equal(sourceLabel(unread), "Okänt dokument");
    assert.equal(locationLabel(unread), null);
    assert.equal(sectionLabel(unread), null);
  });
});
