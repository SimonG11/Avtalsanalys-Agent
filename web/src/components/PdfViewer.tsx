"use client";
/**
 * What: shows one page of a PDF with the cited quote highlighted in its text layer.
 *
 * Why: a citation is only convincing when the reader sees it in the agreement itself, on the
 * page the agent named. The highlight makes the quote easy to find on a dense page.
 *
 * How: react-pdf (PDF.js) draws the page and an invisible text layer on top of it. The cited
 * page is the page the section starts on, so when the document has loaded, lib/quotePage.ts
 * reads that page and the following ones and picks the page that has the quote. When that
 * page's text layer is loaded, lib/highlight.ts finds the quote among its items, and
 * `customTextRenderer` wraps the matching characters in <mark>. A citation without a page (a
 * Word file) opens the document at its first page. A document the API has no PDF of (Word files
 * again, where the API answers 404) gets a note instead of the viewer. PDF.js needs a browser,
 * so SourcePanel loads this module without server rendering.
 */
import { useCallback, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/TextLayer.css";
import "react-pdf/dist/Page/AnnotationLayer.css";

import { findQuote, markItem } from "@/lib/highlight";
import type { QuoteMatch, TextItemLike } from "@/lib/highlight";
import { locateQuote } from "@/lib/quotePage";

import styles from "./SourcePanel.module.css";

// react-pdf asks for the worker to be set in the module that renders <Document>.
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/legacy/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

export interface PdfViewerProps {
  url: string;
  /** The cited page, or null when it is not known. */
  page: number | null;
  quote: string;
  width: number;
  /** Called with how well the quote was found and on which page, so the panel can say so. */
  onMatch: (kind: QuoteMatch["kind"], page: number) => void;
}

/** The parts of a loaded PDF.js document that are used here. */
interface LoadedPdf {
  numPages: number;
  getPage(page: number): Promise<{ getTextContent(): Promise<{ items: TextItemLike[] }> }>;
}

export default function PdfViewer({ url, page, quote, width, onMatch }: PdfViewerProps) {
  const [match, setMatch] = useState<QuoteMatch | null>(null);
  const [noPdf, setNoPdf] = useState(false);
  // The page shown: the one with the quote, null while it is being looked for.
  const [pageNumber, setPageNumber] = useState<number | null>(page === null ? 1 : null);

  const onLoadSuccess = useCallback(
    (pdf: LoadedPdf) => {
      if (page === null) return;
      const readPage = async (number: number) =>
        (await (await pdf.getPage(number)).getTextContent()).items;
      locateQuote(quote, page, pdf.numPages, readPage)
        .catch(() => page)
        .then(setPageNumber);
    },
    [page, quote],
  );

  const onGetTextSuccess = useCallback(
    ({ items }: { items: TextItemLike[] }) => {
      const found = findQuote(items, quote);
      setMatch(found);
      if (pageNumber !== null) onMatch(found.kind, pageNumber);
    },
    [quote, onMatch, pageNumber],
  );

  const customTextRenderer = useCallback(
    ({ str, itemIndex }: { str: string; itemIndex: number }) =>
      markItem(str, match?.ranges.get(itemIndex)),
    [match],
  );

  return (
    <Document
      file={url}
      suspense={false}
      loading={<p className={styles.status}>Hämtar dokumentet…</p>}
      onLoadSuccess={onLoadSuccess}
      onLoadError={(error) => setNoPdf(isMissing(error))}
      error={
        noPdf ? (
          <p className={styles.status} data-testid="no-pdf">
            Det finns ingen PDF av dokumentet, till exempel för en Word-fil. Citatet står ovan.
          </p>
        ) : (
          <p className={styles.error}>Dokumentet kunde inte hämtas från API:t.</p>
        )
      }
    >
      {page === null && (
        <p className={styles.status}>Källan anger ingen sida, så dokumentet visas från början.</p>
      )}
      {pageNumber === null ? (
        <p className={styles.status}>Letar efter citatet…</p>
      ) : (
        <Page
          pageNumber={pageNumber}
          width={width}
          suspense={false}
          loading={<p className={styles.status}>Ritar sidan…</p>}
          error={<p className={styles.error}>Sidan {pageNumber} finns inte i dokumentet.</p>}
          onGetTextSuccess={onGetTextSuccess}
          customTextRenderer={customTextRenderer}
          onRenderTextLayerSuccess={scrollToMark}
        />
      )}
    </Document>
  );
}

/** Whether PDF.js failed because the file does not exist (the API answered 404). */
function isMissing(error: Error): boolean {
  const status = (error as { status?: unknown }).status;
  const missing = (error as { missing?: unknown }).missing;
  return status === 404 || missing === true;
}

/** Brings the first highlighted line into view once the text layer is drawn. */
function scrollToMark(): void {
  document.querySelector(".quote-mark")?.scrollIntoView({ block: "center", behavior: "smooth" });
}
