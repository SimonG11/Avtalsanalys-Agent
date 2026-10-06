"use client";
/**
 * What: shows one page of a PDF with the cited quote highlighted in its text layer.
 *
 * Why: a citation is only convincing when the reader sees it in the agreement itself, on the
 * page the agent named. The highlight makes the quote easy to find on a dense page.
 *
 * How: react-pdf (PDF.js) draws the page and an invisible text layer on top of it. When the
 * text layer's items are loaded, lib/highlight.ts finds the quote among them, and
 * `customTextRenderer` wraps the matching characters in <mark>. PDF.js needs a browser, so
 * SourcePanel loads this module without server rendering.
 */
import { useCallback, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/TextLayer.css";
import "react-pdf/dist/Page/AnnotationLayer.css";

import { findQuote, markItem } from "@/lib/highlight";
import type { QuoteMatch, TextItemLike } from "@/lib/highlight";

import styles from "./SourcePanel.module.css";

// react-pdf asks for the worker to be set in the module that renders <Document>.
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/legacy/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

export interface PdfViewerProps {
  url: string;
  page: number;
  quote: string;
  width: number;
  /** Called with how well the quote was found, so the panel can say so. */
  onMatch: (kind: QuoteMatch["kind"]) => void;
}

export default function PdfViewer({ url, page, quote, width, onMatch }: PdfViewerProps) {
  const [match, setMatch] = useState<QuoteMatch | null>(null);

  const onGetTextSuccess = useCallback(
    ({ items }: { items: TextItemLike[] }) => {
      const found = findQuote(items, quote);
      setMatch(found);
      onMatch(found.kind);
    },
    [quote, onMatch],
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
      error={<p className={styles.error}>Dokumentet kunde inte hämtas från API:t.</p>}
    >
      <Page
        pageNumber={page}
        width={width}
        suspense={false}
        loading={<p className={styles.status}>Ritar sidan…</p>}
        error={<p className={styles.error}>Sidan {page} finns inte i dokumentet.</p>}
        onGetTextSuccess={onGetTextSuccess}
        customTextRenderer={customTextRenderer}
        onRenderTextLayerSuccess={scrollToMark}
      />
    </Document>
  );
}

/** Brings the first highlighted line into view once the text layer is drawn. */
function scrollToMark(): void {
  document.querySelector(".quote-mark")?.scrollIntoView({ block: "center", behavior: "smooth" });
}
