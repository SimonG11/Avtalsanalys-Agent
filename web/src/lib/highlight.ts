/**
 * What: finds a citation's quote among the text items of a PDF page and marks it.
 *
 * Why: the source panel opens the PDF at the cited page and highlights the quote, so the reader
 * can check the answer against the agreement itself. PDF.js splits a page into many small text
 * items (often one per line or less), and a quote usually spans several of them, so a plain
 * string search on one item is not enough.
 *
 * How: every character of every item is folded (whitespace dropped, dashes and quotation marks
 * unified, ligatures expanded) into one long string that remembers where each character came
 * from. The folded quote is searched in that string. If the quote is not found, a second pass
 * also drops hyphens at line ends ("upp-" + "sägning"), and a third pass marks the longest
 * found start or end of the quote (a quote can continue on the next page). PdfViewer turns the
 * result into <mark> elements through `markItem`.
 */

/**
 * A PDF.js text content item. Text items have `str` and `hasEOL`; marked-content items have
 * neither and count as empty text, so item indexes stay the same as PDF.js's.
 */
export type TextItemLike = object;

function textOf(item: TextItemLike): { str: string; hasEOL: boolean } {
  const str = "str" in item && typeof item.str === "string" ? item.str : "";
  const hasEOL = "hasEOL" in item && item.hasEOL === true;
  return { str, hasEOL };
}

/** Character ranges [start, end) to mark, per item index. */
export type ItemRanges = Map<number, [number, number][]>;

export interface QuoteMatch {
  /** "full": the whole quote was found; "partial": only its start or end; "none": nothing. */
  kind: "full" | "partial" | "none";
  ranges: ItemRanges;
}

/** A partial match shorter than this many folded characters is not shown. */
const MIN_PARTIAL_LENGTH = 24;

const FOLD: Record<string, string> = {
  "­": "", // soft hyphen
  "‐": "-",
  "‑": "-",
  "‒": "-",
  "–": "-",
  "—": "-",
  "−": "-",
  "‘": "'",
  "’": "'",
  "“": '"',
  "”": '"',
  "„": '"',
  "«": '"',
  "»": '"',
  ﬀ: "ff",
  ﬁ: "fi",
  ﬂ: "fl",
  ﬃ: "ffi",
  ﬄ: "ffl",
};

function fold(char: string): string {
  if (/\s/u.test(char)) return "";
  return FOLD[char] ?? char.normalize("NFC");
}

export function foldText(text: string): string {
  return Array.from(text, fold).join("");
}

interface Haystack {
  text: string;
  /** For every character of `text`: [item index, offset in that item's str, source length]. */
  origin: [number, number, number][];
}

function buildHaystack(items: readonly TextItemLike[], dropLineEndHyphens: boolean): Haystack {
  let text = "";
  const origin: [number, number, number][] = [];
  items.forEach((item, itemIndex) => {
    const { str, hasEOL } = textOf(item);
    const lastVisible = str.trimEnd().length - 1;
    // Offsets are counted in UTF-16 code units, the same unit PdfViewer slices item.str with.
    let offset = 0;
    for (const char of str) {
      const isLineEndHyphen =
        dropLineEndHyphens && hasEOL && offset === lastVisible && char === "-";
      const folded = isLineEndHyphen ? "" : fold(char);
      for (const out of folded) {
        text += out;
        origin.push([itemIndex, offset, char.length]);
      }
      offset += char.length;
    }
  });
  return { text, origin };
}

function rangesFor(haystack: Haystack, start: number, end: number): ItemRanges {
  const ranges: ItemRanges = new Map();
  for (let k = start; k < end; k++) {
    const [itemIndex, offset, length] = haystack.origin[k];
    const existing = ranges.get(itemIndex);
    if (existing) existing[0][1] = Math.max(existing[0][1], offset + length);
    else ranges.set(itemIndex, [[offset, offset + length]]);
  }
  return ranges;
}

/** Length of the longest prefix (or suffix) of `needle` that occurs in `text`, by bisection. */
function longestFound(text: string, needle: string, fromEnd: boolean): number {
  const part = (length: number) =>
    fromEnd ? needle.slice(needle.length - length) : needle.slice(0, length);
  let low = 0;
  let high = needle.length;
  while (low < high) {
    const mid = Math.ceil((low + high) / 2);
    if (text.includes(part(mid))) low = mid;
    else high = mid - 1;
  }
  return low;
}

export function findQuote(items: readonly TextItemLike[], quote: string): QuoteMatch {
  const needle = foldText(quote);
  if (needle.length === 0) return { kind: "none", ranges: new Map() };

  for (const dropLineEndHyphens of [false, true]) {
    const haystack = buildHaystack(items, dropLineEndHyphens);
    const start = haystack.text.indexOf(needle);
    if (start >= 0) {
      return { kind: "full", ranges: rangesFor(haystack, start, start + needle.length) };
    }
  }

  const haystack = buildHaystack(items, true);
  const prefix = longestFound(haystack.text, needle, false);
  const suffix = longestFound(haystack.text, needle, true);
  const length = Math.max(prefix, suffix);
  if (length < Math.min(MIN_PARTIAL_LENGTH, needle.length)) {
    return { kind: "none", ranges: new Map() };
  }
  const part = prefix >= suffix ? needle.slice(0, length) : needle.slice(needle.length - length);
  const start = haystack.text.indexOf(part);
  return { kind: "partial", ranges: rangesFor(haystack, start, start + length) };
}

function escapeHtml(text: string): string {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

/** The HTML for one text item: escaped text with the marked ranges wrapped in <mark>. */
export function markItem(str: string, ranges: [number, number][] | undefined): string {
  if (!ranges || ranges.length === 0) return escapeHtml(str);
  let html = "";
  let last = 0;
  for (const [start, end] of [...ranges].sort((a, b) => a[0] - b[0])) {
    html += escapeHtml(str.slice(last, start));
    html += `<mark class="quote-mark">${escapeHtml(str.slice(start, end))}</mark>`;
    last = end;
  }
  return html + escapeHtml(str.slice(last));
}
