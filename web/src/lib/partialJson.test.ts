import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { parsePartialJson } from "./partialJson.ts";

describe("parsePartialJson", () => {
  it("parses complete JSON as it is", () => {
    assert.deepEqual(parsePartialJson('{"query": "vite", "limit": 5}'), {
      query: "vite",
      limit: 5,
    });
  });

  it("reads a string that is still streaming", () => {
    assert.deepEqual(parsePartialJson('{"query": "uppsägn'), { query: "uppsägn" });
  });

  it("leaves out a key that has no value yet", () => {
    assert.deepEqual(parsePartialJson('{"query": "vite", "framework_area'), { query: "vite" });
    assert.deepEqual(parsePartialJson('{"query": "vite", "framework_area":'), { query: "vite" });
    assert.deepEqual(parsePartialJson('{"query'), {});
  });

  it("drops a trailing comma and closes nested arrays and objects", () => {
    assert.deepEqual(parsePartialJson('{"query": "vite",'), { query: "vite" });
    assert.deepEqual(parsePartialJson('{"question": "Vilket?", "options": ["IT-drift", "Bem'), {
      question: "Vilket?",
      options: ["IT-drift", "Bem"],
    });
    assert.deepEqual(parsePartialJson('{"a": {"b": [1, 2'), { a: { b: [1, 2] } });
  });

  it("keeps escaped quotes and drops a lone backslash at the end", () => {
    assert.deepEqual(parsePartialJson('{"query": "\\"vite\\" och'), { query: '"vite" och' });
    assert.deepEqual(parsePartialJson('{"query": "vite\\'), { query: "vite" });
  });

  it("reads a number that may still grow", () => {
    assert.deepEqual(parsePartialJson('{"limit": 1'), { limit: 1 });
  });

  it("reads arguments that were streamed twice under the same id once", () => {
    const args = '{"question": "Vilket område?", "options": ["IT-drift", "Bemanning"]}';
    const expected = { question: "Vilket område?", options: ["IT-drift", "Bemanning"] };
    assert.deepEqual(parsePartialJson(args + args), expected);
    assert.deepEqual(parsePartialJson(args + '{"question": "Vil'), expected);
    assert.deepEqual(parsePartialJson('{"q": "a } b"}{"q"'), { q: "a } b" });
  });

  it("gives undefined for nothing or for text that cannot be completed", () => {
    assert.equal(parsePartialJson(""), undefined);
    assert.equal(parsePartialJson("{]"), undefined);
  });
});
