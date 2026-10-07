/**
 * What: reads a tool call's arguments while they are still streaming, as far as they have come.
 *
 * Why: the model writes a tool call's arguments token by token, so the timeline gets
 * `{"query": "uppsägn` before it gets the whole object. Showing what has arrived makes the step
 * live, instead of empty until the call is complete.
 *
 * How: JSON that parses is returned as it is. Otherwise the text is closed: an open string gets
 * its quote, a key without a value is dropped, a trailing comma or colon is dropped, and open
 * objects and arrays get their brackets, in reverse order. What still does not parse gives
 * undefined.
 */

export function parsePartialJson(text: string): unknown {
  const trimmed = text.trim();
  if (trimmed === "") return undefined;
  try {
    return JSON.parse(trimmed);
  } catch {
    // Not complete yet: close it below.
  }
  try {
    return JSON.parse(closeJson(trimmed));
  } catch {
    return undefined;
  }
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
