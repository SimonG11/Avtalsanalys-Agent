"use client";
/**
 * What: the panel beside the chat that shows one citation: where it comes from, the quote,
 * whether it was verified, and the PDF page with the quote highlighted.
 *
 * Why: the reader should never have to trust the answer blindly. One click on a source number
 * shows the exact place in the agreement.
 *
 * How: AgentApp renders the panel when a citation is opened, with a key per citation so each
 * one starts fresh. An agreement's PDF is fetched from the web app's own
 * /api/documents/{sha256}/pdf route, and a PDF the person uploaded from
 * /api/uploads/{upload_id}/file (lib/uploads.ts); PdfViewer is loaded only in the browser. An
 * uploaded Word or text file has no pages, so only the quote is shown. The citation's page is
 * where its section starts; when PdfViewer finds the quote on a later page, the panel says so.
 */
import dynamic from "next/dynamic";
import { useCallback, useEffect, useRef, useState } from "react";

import { documentTitle, locationLabel } from "@/lib/citation";
import type { Citation } from "@/lib/contract";
import type { QuoteMatch } from "@/lib/highlight";
import { citationPdfUrl, isUploadCitation } from "@/lib/uploads";

import { Icon } from "./icons";
import { useUploads } from "./Uploads";
import styles from "./SourcePanel.module.css";

const PdfViewer = dynamic(() => import("./PdfViewer"), {
  ssr: false,
  loading: () => <p className={styles.status}>Laddar PDF-visaren…</p>,
});

const MATCH_NOTES: Record<QuoteMatch["kind"], string | null> = {
  full: null,
  partial: "Bara en del av citatet finns på den här sidan. Resten finns på sidan före eller efter.",
  none: "Citatet hittades inte i sidans text.",
};

export function SourcePanel({ citation, onClose }: { citation: Citation; onClose: () => void }) {
  const { threadId } = useUploads();
  const [match, setMatch] = useState<{ kind: QuoteMatch["kind"]; page: number } | null>(null);
  const pageArea = useRef<HTMLDivElement>(null);
  const width = useWidth(pageArea);
  const onMatch = useCallback(
    (kind: QuoteMatch["kind"], page: number) => setMatch({ kind, page }),
    [],
  );

  const note = match ? MATCH_NOTES[match.kind] : null;
  const laterPage =
    match !== null && citation.page !== null && match.page !== citation.page ? match.page : null;
  const location = locationLabel(citation);
  const ownFile = isUploadCitation(citation);
  const url = citationPdfUrl(citation, threadId);
  const checkedAgainst = ownFile ? "filens text" : "avtalstexten";

  return (
    <aside className={styles.panel} aria-label="Källa" data-testid="source-panel">
      <header className={styles.header}>
        <div>
          <p className={styles.eyebrow}>
            Källa {citation.id}
            {ownFile && " · Din fil"}
          </p>
          <h2 className={styles.title}>{documentTitle(citation)}</h2>
          {citation.page_title && <p className={styles.meta}>{citation.page_title}</p>}
          {location && <p className={styles.meta}>{location}</p>}
        </div>
        <button type="button" className={styles.close} onClick={onClose} aria-label="Stäng källan">
          <Icon name="close" size={18} />
        </button>
      </header>

      <blockquote className={styles.quote}>
        ”{citation.quote}”
        <footer className={citation.verified ? styles.verified : styles.unverified}>
          <Icon name={citation.verified ? "shield" : "alert"} size={14} />
          {citation.verified
            ? `Kontrollerat mot ${checkedAgainst}`
            : `Kunde inte kontrolleras mot ${checkedAgainst}`}
        </footer>
      </blockquote>

      {laterPage !== null && (
        <p className={styles.meta} data-testid="page-note">
          Citatet står på sida {laterPage}. Avsnittet börjar på sida {citation.page}.
        </p>
      )}
      {note && (
        <p className={styles.note} data-testid="match-note">
          {note}
        </p>
      )}

      <div className={styles.page} ref={pageArea}>
        {url === null ? (
          <p className={styles.status} data-testid="no-pdf">
            Filen är ingen PDF, så bara citatet visas. Det står ovan.
          </p>
        ) : (
          width > 0 && (
            <PdfViewer
              url={url}
              page={citation.page}
              quote={citation.quote}
              width={width}
              onMatch={onMatch}
            />
          )
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
