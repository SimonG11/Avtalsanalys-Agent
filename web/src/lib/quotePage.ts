/**
 * What: the page of the PDF that has a cited quote, from the page the citation names onwards.
 *
 * Why: a citation's `page` is the page its section starts on. A quote further down a section
 * that runs over several pages is on a later page, so the panel showed the section's first page
 * and said the quote was not there, although the check had found it in the section.
 *
 * How: `locateQuote` reads the text of the cited page and of up to LOOKAHEAD_PAGES pages after
 * it, and stops at the first page with the whole quote. Without one it takes the first page
 * with part of the quote (a quote over a page break), and else the cited page, where the panel
 * then says that the quote was not found.
 */
import { findQuote } from "./highlight.ts";
import type { TextItemLike } from "./highlight.ts";

/** How many pages after the cited one are searched; a section is rarely longer. */
export const LOOKAHEAD_PAGES = 10;

export async function locateQuote(
  quote: string,
  citedPage: number,
  pageCount: number,
  readPage: (page: number) => Promise<readonly TextItemLike[]>,
): Promise<number> {
  const lastPage = Math.min(pageCount, citedPage + LOOKAHEAD_PAGES);
  let partial: number | null = null;
  for (let page = citedPage; page <= lastPage; page++) {
    const kind = findQuote(await readPage(page), quote).kind;
    if (kind === "full") return page;
    if (kind === "partial" && partial === null) partial = page;
  }
  return partial ?? citedPage;
}
