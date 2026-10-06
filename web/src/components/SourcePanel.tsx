"use client";
/**
 * What: the panel beside the chat that shows one citation: where it comes from, the quote,
 * whether it was verified, and the PDF page with the quote highlighted.
 *
 * Why: the reader should never have to trust the answer blindly. One click on a source number
 * shows the exact place in the agreement.
 *
 * How: AgentApp renders the panel when a citation is opened, with a key per citation so each
 * one starts fresh. The PDF is fetched from the web app's own /api/documents/{sha256}/pdf
 * route, and PdfViewer is loaded only in the browser.
 */
import dynamic from "next/dynamic";
import { useCallback, useEffect, useRef, useState } from "react";

import type { Citation } from "@/lib/contract";
import type { QuoteMatch } from "@/lib/highlight";

import styles from "./SourcePanel.module.css";

const PdfViewer = dynamic(() => import("./PdfViewer"), {
  ssr: false,
  loading: () => <p className={styles.status}>Laddar PDF-visaren…</p>,
});

const MATCH_NOTES: Record<QuoteMatch["kind"], string | null> = {
  full: null,
  partial: "Bara en del av citatet finns på den här sidan. Det kan fortsätta på nästa sida.",
  none: "Citatet hittades inte i sidans text.",
};

export function SourcePanel({ citation, onClose }: { citation: Citation; onClose: () => void }) {
  const [match, setMatch] = useState<QuoteMatch["kind"] | null>(null);
  const pageArea = useRef<HTMLDivElement>(null);
  const width = useWidth(pageArea);
  const onMatch = useCallback((kind: QuoteMatch["kind"]) => setMatch(kind), []);

  const note = match ? MATCH_NOTES[match] : null;

  return (
    <aside className={styles.panel} aria-label="Källa" data-testid="source-panel">
      <header className={styles.header}>
        <div>
          <p className={styles.eyebrow}>Källa {citation.id}</p>
          <h2 className={styles.title}>{citation.file_title}</h2>
          <p className={styles.meta}>{citation.page_title}</p>
          <p className={styles.meta}>
            Avsnitt {citation.section_number} {citation.section_title} · sida {citation.page}
          </p>
        </div>
        <button type="button" className={styles.close} onClick={onClose} aria-label="Stäng källan">
          ×
        </button>
      </header>

      <blockquote className={styles.quote}>
        ”{citation.quote}”
        <footer className={citation.verified ? styles.verified : styles.unverified}>
          {citation.verified
            ? "Kontrollerat mot avtalstexten"
            : "Kunde inte kontrolleras mot avtalstexten"}
        </footer>
      </blockquote>

      {note && (
        <p className={styles.note} data-testid="match-note">
          {note}
        </p>
      )}

      <div className={styles.page} ref={pageArea}>
        {width > 0 && (
          <PdfViewer
            url={`/api/documents/${citation.sha256}/pdf`}
            page={citation.page}
            quote={citation.quote}
            width={width}
            onMatch={onMatch}
          />
        )}
      </div>
    </aside>
  );
}

/** The element's width, kept up to date when the window is resized. */
function useWidth(ref: React.RefObject<HTMLElement | null>): number {
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)));
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref]);
  return width;
}
