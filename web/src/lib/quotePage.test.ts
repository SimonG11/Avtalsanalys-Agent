import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { LOOKAHEAD_PAGES, locateQuote } from "./quotePage.ts";

/** A document whose pages have these lines, read the way PDF.js gives a page's text. */
function reader(pages: string[][]) {
  const read: number[] = [];
  const readPage = async (page: number) => {
    read.push(page);
    return pages[page - 1].map((str) => ({ str, hasEOL: true }));
  };
  return { readPage, read };
}

const QUOTE = "Uppsägningen ska vara skriftlig och skickas till Leverantörens kontaktperson.";

describe("locateQuote", () => {
  it("keeps the cited page when the quote is on it", async () => {
    const { readPage, read } = reader([["Inledning"], [QUOTE], [QUOTE]]);
    assert.equal(await locateQuote(QUOTE, 2, 3, readPage), 2);
    assert.deepEqual(read, [2]);
  });

  it("finds a quote further down a section on a later page", async () => {
    const { readPage } = reader([["6.21.8 Avtalstid", "Kontraktet gäller i två år."], [QUOTE]]);
    assert.equal(await locateQuote(QUOTE, 1, 2, readPage), 2);
  });

  it("takes the first page with part of the quote when no page has all of it", async () => {
    const start = "Uppsägningen ska vara skriftlig och skickas till";
    const { readPage } = reader([["Inledning", start], ["Leverantörens kontaktperson."]]);
    assert.equal(await locateQuote(QUOTE, 1, 2, readPage), 1);
  });

  it("keeps the cited page when the quote is nowhere, and reads only so far", async () => {
    const pages = Array.from({ length: LOOKAHEAD_PAGES + 5 }, () => ["Annan text."]);
    const { readPage, read } = reader(pages);
    assert.equal(await locateQuote(QUOTE, 2, pages.length, readPage), 2);
    assert.equal(read.at(-1), 2 + LOOKAHEAD_PAGES);
  });

  it("does not read past the last page", async () => {
    const { readPage, read } = reader([["Annan text."], ["Annan text."]]);
    assert.equal(await locateQuote(QUOTE, 2, 2, readPage), 2);
    assert.deepEqual(read, [2]);
  });
});
