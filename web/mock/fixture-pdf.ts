/**
 * What: builds the PDF the mock agent cites: a short, made-up agreement in Swedish.
 *
 * Why: the source panel has to be tested against a real PDF with a text layer, but the
 * repository must not contain documents from avropa.se (their reuse rights are still open).
 * So the mock writes its own document, marked as fictitious on every page.
 *
 * How: pdf-lib draws the lines of `PAGES` with a standard font, which gives PDF.js a text
 * layer to search. The scenarios quote these lines, so a quote in an answer is always on the
 * page it names. The dates are fixed so the file, and its SHA-256, are the same on every run.
 */
import { createHash } from "node:crypto";

import { PDFDocument, StandardFonts, rgb } from "pdf-lib";

export const DOCUMENT_TITLE = "Exempelavtal Allmänna villkor";
export const PAGE_TITLE = "Exempelområde IT-drift (fiktivt)";

const NOTICE = "Fiktivt testdokument för webbappens mock. Inte ett avtal från avropa.se.";

/** One entry per page, one string per line. Lines starting with a number are headings. */
export const PAGES: string[][] = [
  [
    "Exempelavtal Allmänna villkor",
    "",
    "1 Inledning",
    "1.1 Dessa allmänna villkor gäller för avrop som Kunden gör från",
    "ramavtalet. Villkoren gäller i den mån inget annat har avtalats",
    "i Kontraktet.",
    "",
    "1.2 Med Kund avses den organisation som gör ett avrop.",
  ],
  [
    "6.21 Kontraktets upphörande",
    "",
    "6.21.9 Uppsägning",
    "Kunden har rätt att säga upp Kontraktet med tre (3) månaders",
    "uppsägningstid. Uppsägningen ska vara skriftlig och skickas till",
    "Leverantörens kontaktperson.",
    "",
    "6.21.10 Uppsägning vid väsentligt avtalsbrott",
    "Part har rätt att säga upp Kontraktet med omedelbar verkan om",
    "den andra Parten gör sig skyldig till väsentligt avtalsbrott.",
  ],
  [
    "7 Vite",
    "",
    "7.2 Vite vid försenad leverans",
    "Om Leverantören inte levererar i tid har Kunden rätt till vite",
    "med 0,5 procent av det avropade värdet för varje påbörjad",
    "vecka, dock högst tio (10) procent.",
  ],
  // Section 7.2 goes on here, so a quote from it is on a later page than the section's start.
  [
    "Vitet ska betalas inom trettio (30) dagar från det att Kunden",
    "har framställt krav på vite.",
    "",
    "7.3 Hävning vid dröjsmål",
    "Kunden har rätt att häva avropet om förseningen överstiger",
    "åtta (8) veckor.",
  ],
];

export interface FixturePdf {
  bytes: Uint8Array;
  sha256: string;
}

const FIXED_DATE = new Date("2026-10-01T00:00:00Z");

export async function buildFixturePdf(): Promise<FixturePdf> {
  const document = await PDFDocument.create();
  document.setTitle(DOCUMENT_TITLE);
  document.setCreationDate(FIXED_DATE);
  document.setModificationDate(FIXED_DATE);
  const regular = await document.embedFont(StandardFonts.Helvetica);
  const bold = await document.embedFont(StandardFonts.HelveticaBold);

  PAGES.forEach((lines, index) => {
    const page = document.addPage([595, 842]); // A4 in points
    let y = 780;
    for (const line of lines) {
      const isHeading = /^\d+(\.\d+)* \S/.test(line) && line.length < 50;
      page.drawText(line, { x: 60, y, size: 11, font: isHeading ? bold : regular });
      y -= 18;
    }
    page.drawText(NOTICE, { x: 60, y: 50, size: 8, font: regular, color: rgb(0.45, 0.45, 0.45) });
    page.drawText(`Sida ${index + 1} av ${PAGES.length}`, {
      x: 480,
      y: 50,
      size: 8,
      font: regular,
    });
  });

  const bytes = await document.save({ useObjectStreams: false });
  const sha256 = createHash("sha256").update(bytes).digest("hex");
  return { bytes, sha256 };
}
