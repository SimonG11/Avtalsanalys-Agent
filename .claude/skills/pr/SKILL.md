---
name: pr
description: The shape of a pull request in this repo. Use when writing or editing a PR title or body, or when asked to open a PR.
---

Title and body in Swedish. Code, paths, identifiers and tool names as written in the code.

**Title:** one line that says what the reader gets, as the merged PRs do ("Visa avtalssidan på källkorten", "Rätta fem texter som inte stämmer med koden"). The milestone or ADR number goes in the body, not the title.

**Body**, in this order, each label in bold:

```markdown
**Före:** what a user or reader saw before, concretely: the screen, the output, the sentence in the docs. For a milestone, what was missing.

**Efter:** what they see now. A bulleted list when the change has parts.

**Hur:** the mechanism, file by file, one line each: `agent/mcp_tools.py` does X. Name the ADR and the `docs/steg/` file the change adds or updates, and what was deliberately not done.

**Verifierat:** the checks that ran and their result, as `/verify` reports them: ruff, ruff format and mypy; the test counts and what was skipped and why; the integration tests, or that they did not run; what was tried by hand (a question in the web app, a run against the real database). "Inga anrop till OpenAI" when that holds.
```

Optional last block, when the work turned up stale text elsewhere that this PR leaves alone: **Andra ställen med gammal text, inte ändrade här:** with `file:line` per item.

Every number in the body comes from a check that ran on this branch or from a measurement the body names. Write the body to a file and pass it with `gh pr create --body-file`, so the Swedish characters and the line breaks survive.
