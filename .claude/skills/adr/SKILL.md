---
name: adr
description: Draft an architecture decision record in docs/adr/, in this repo's Swedish template, and add it to the index. Use when a change makes a design decision the repo should record, replaces an earlier ADR, or when the user asks for an ADR.
argument-hint: "[the decision in one line]"
---

Write a new ADR for the decision in `$ARGUMENTS`. When it is empty, ask for the decision in one line.

## 1. Read first

- `docs/adr/README.md`: the index table and the template line under it. The number is the last row's plus one, four digits.
- The newest ADR, as the style example: the headline states the decision; Kontext names what drove it and what constrains it; Beslut is a numbered list of concrete choices with file paths; Konsekvenser say what is gained and what it costs (tokens, seconds, money, known limits); every rejected alternative carries the reason it lost.
- The ADRs and the `docs/steg/` file for the area the decision touches.

## 2. Write `docs/adr/NNNN-slug.md`

Swedish prose; code, paths, identifiers and tool names as in the code. The slug is lower-case ASCII with hyphens, å, ä and ö written as a, a and o (`0025-tankar-och-farre-motfragor.md`). Headline `# ADR NNNN: <the decision as a sentence>`, then in this order:

```markdown
**Status:** Föreslaget.

## Kontext

## Beslut

## Konsekvenser

## Alternativ som valts bort
```

Dates as YYYY-MM-DD. A number from a measurement names the run it comes from. A decision that replaces an earlier ADR says so in Kontext; the earlier file stays as it is, and its index row gets "ersatt av NNNN".

## 3. Index and links

Add the row to the table in `docs/adr/README.md` with status "Föreslaget"; Simon sets "Godkänt" and the PR number when he merges. Link the new ADR from the `docs/steg/` file for the area, and from the README's table of decisions when the area has a row there.

Done when every section is filled, the index row exists and the user has seen the draft. Do not commit.
