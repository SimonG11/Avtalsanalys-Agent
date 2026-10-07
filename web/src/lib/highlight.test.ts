import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { findQuote, foldText, markItem } from "./highlight.ts";

/** Text items as PDF.js returns them for a page with one clause over three lines. */
const PAGE = [
  { str: "6.21.9 Uppsägning", hasEOL: true },
  { str: "Kunden har rätt att säga upp Kontraktet med tre (3) månaders upp-", hasEOL: true },
  { str: "sägningstid. Uppsägningen ska vara skriftlig.", hasEOL: true },
  {}, // marked content: no str
  { str: "6.21.10 Övrigt", hasEOL: false },
];

function marked(items: { str?: string }[], quote: string): string[] {
  const match = findQuote(items, quote);
  return items.flatMap((item, index) => {
    const ranges = match.ranges.get(index);
    return ranges ? ranges.map(([start, end]) => (item.str ?? "").slice(start, end)) : [];
  });
}

describe("foldText", () => {
  it("drops whitespace and unifies dashes, quotation marks and ligatures", () => {
    assert.equal(foldText("a – b\n”c” ﬁ"), 'a-b"c"fi');
  });
});

describe("findQuote", () => {
  it("finds a quote inside one item", () => {
    const match = findQuote(PAGE, "säga upp Kontraktet");
    assert.equal(match.kind, "full");
    assert.deepEqual(marked(PAGE, "säga upp Kontraktet"), ["säga upp Kontraktet"]);
  });

  it("finds a quote across items regardless of line breaks and spaces", () => {
    const quote = "Kunden har rätt att säga upp Kontraktet med tre (3) månaders upp- sägningstid.";
    assert.equal(findQuote(PAGE, quote).kind, "full");
    assert.deepEqual(marked(PAGE, quote), [
      "Kunden har rätt att säga upp Kontraktet med tre (3) månaders upp-",
      "sägningstid.",
    ]);
  });

  it("drops the hyphen at a line end when the quote has the joined word", () => {
    const quote = "med tre (3) månaders uppsägningstid";
    assert.equal(findQuote(PAGE, quote).kind, "full");
    assert.deepEqual(marked(PAGE, quote), ["med tre (3) månaders upp", "sägningstid"]);
  });

  it("marks the start of a quote that continues on the next page", () => {
    const quote = "Uppsägningen ska vara skriftlig. Den skickas till Leverantörens kontaktperson.";
    const match = findQuote(PAGE, quote);
    assert.equal(match.kind, "partial");
    assert.deepEqual(marked(PAGE, quote), ["Uppsägningen ska vara skriftlig."]);
  });

  it("marks the end of a quote that started on the previous page", () => {
    const quote = "Leverantören ska meddela detta i god tid. 6.21.9 Uppsägning Kunden har rätt";
    assert.equal(findQuote(PAGE, quote).kind, "partial");
    assert.deepEqual(marked(PAGE, quote), ["6.21.9 Uppsägning", "Kunden har rätt"]);
  });

  it("does not mark the start of a misquote that is found in the middle of the page", () => {
    // The page says "tre (3) månaders"; the quote says six months.
    const quote = "Kunden har rätt att säga upp Kontraktet med sex (6) månaders uppsägningstid.";
    assert.equal(findQuote(PAGE, quote).kind, "none");
  });

  it("matches letters stored as a base letter and a combining accent", () => {
    const decomposed = [{ str: "Kunden har ra\u0308tt att sa\u0308ga upp", hasEOL: true }];
    assert.equal(findQuote(decomposed, "har rätt att säga").kind, "full");
  });

  it("drops a Unicode hyphen at a line end", () => {
    const items = [
      { str: "tre månaders upp\u2010", hasEOL: true },
      { str: "sägningstid", hasEOL: true },
    ];
    assert.equal(findQuote(items, "tre månaders uppsägningstid").kind, "full");
  });

  it("reports nothing when the quote is not on the page", () => {
    assert.equal(findQuote(PAGE, "Vite utgår med 0,5 procent per påbörjad vecka.").kind, "none");
    assert.equal(findQuote(PAGE, "   ").kind, "none");
  });
});

describe("markItem", () => {
  it("wraps the ranges in <mark> and escapes HTML", () => {
    assert.equal(
      markItem("a <b> & c", [[2, 5]]),
      'a <mark class="quote-mark">&lt;b&gt;</mark> &amp; c',
    );
  });

  it("returns escaped text when there is nothing to mark", () => {
    assert.equal(markItem("\"x\" 'y'", undefined), "&quot;x&quot; &#39;y&#39;");
  });
});
