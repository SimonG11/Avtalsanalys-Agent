/**
 * What: reads a tool call's arguments while they are still streaming, as far as they have come.
 *
 * Why: the model writes a tool call's arguments token by token, so the timeline gets
 * `{"query": "uppsägn` before it gets the whole object. Showing what has arrived makes the step
 * live, instead of empty until the call is complete.
 *
 * How: JSON that parses is returned as it is. Arguments sent twice are read once: when a run
 * resumes after ask_user, the backend streams that call's arguments again under the same id, and
 * the AG-UI client adds them to the ones it has (`{…}{…`), so a complete object followed by more
 * text is read on its own. Otherwise the text is closed: an open string gets its quote, a key
 * without a value is dropped, a trailing comma or colon is dropped, and open objects and arrays
 * get their brackets, in reverse order. What still does not parse gives undefined.
 */

export function parsePartialJson(text: string): unknown {
  const trimmed = text.trim();
  if (trimmed === "") return undefined;
  try {
    return JSON.parse(trimmed);
  } catch {
    // Not complete yet, or more than one value: see below.
  }
  const end = firstValueEnd(trimmed);
  if (end > 0) {
    try {
      return JSON.parse(trimmed.slice(0, end));
    } catch {
      // Not JSON after all.
    }
  }
  try {
    return JSON.parse(closeJson(trimmed));
  } catch {
    return undefined;
  }
}

/** Where the first complete object or array in the text ends, or -1 if none is complete. */
function firstValueEnd(text: string): number {
  if (text[0] !== "{" && text[0] !== "[") return -1;
  let depth = 0;
  let inString = false;
  let escaped = false;
  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    if (inString) {
      if (escaped) escaped = false;
      else if (char === "\\") escaped = true;
      else if (char === '"') inString = false;
    } else if (char === '"') inString = true;
    else if (char === "{" || char === "[") depth++;
    else if ((char === "}" || char === "]") && --depth === 0) return i + 1;
  }
  return -1;
}

function closeJson(text: string): string {
  const open: string[] = [];
  let inString = false;
  let escaped = false;
  for (const char of text) {
    if (inString) {
      if (escaped) escaped = false;
      else if (char === "\\") escaped = true;
      else if (char === '"') inString = false;
      continue;
    }
    if (char === '"') inString = true;
    else if (char === "{") open.push("}");
    else if (char === "[") open.push("]");
    else if (char === "}" || char === "]") open.pop();
  }

  let closed = text;
  if (inString) {
    // A lone backslash at the end would escape the closing quote.
    if (escaped) closed = closed.slice(0, -1);
    closed += '"';
  }
  // A dangling comma or colon is left out, and in an object so is a key with no value yet
  // ({"a": 1, "b"). In an array, a string after a comma is a value and stays.
  closed = closed.replace(/[,:]\s*$/, "");
  if (open.at(-1) === "}") closed = closed.replace(/([{,])\s*"(?:[^"\\]|\\.)*"\s*$/, "$1");
  closed = closed.replace(/,\s*$/, "");
  return closed + open.reverse().join("");
}
