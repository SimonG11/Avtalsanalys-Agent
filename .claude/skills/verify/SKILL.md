---
name: verify
description: Run this repo's CI gate (ruff, mypy, pytest, and the web app's format, lint, typecheck and tests) and report the result in the PR body's Verifierat shape. Use before opening a pull request, or when the user asks to verify, check or run the checks.
argument-hint: "[base branch, default main]"
allowed-tools: Bash(uv run *) Bash(npm run *) Bash(npm test*) Bash(git status*) Bash(git diff *) Bash(docker info*)
---

Run the checks CI runs (`.github/workflows/ci.yml`) from the repo root and report. Report only: fix nothing in this turn.

## 1. Scope

`git status --short` and `git diff --stat $ARGUMENTS...HEAD` (base `main` when none is given). The backend checks always run. The web checks run when anything under `web/` changed. The browser tests run only when the user asks for them: they need `npm run build` and Playwright's Chromium.

## 2. Backend

```bash
uv run ruff format --check
uv run ruff check
uv run mypy
uv run pytest -q
```

Run `docker info` first. Without Docker, run `uv run pytest -q tests/unit` instead and report that the integration tests did not run. The PDF tests skip locally when Docling's models cannot be downloaded; CI treats a missing model as an error, so name every skipped test and its reason.

## 3. Web, when `web/` changed

```bash
cd web && npm run format:check && npm run lint && npm run typecheck && npm test
```

With the user's go-ahead: `npm run build && npm run test:e2e`.

## 4. Report

Paste every failure verbatim in a code block. Then the summary in Swedish, in the shape the PR body's **Verifierat** uses, for example: "ruff, ruff format och mypy (strikt) är gröna. 2 213 enhetstester godkända, 1 hoppades över (registret är inte nedladdat). Integrationstesterna kördes inte (ingen Docker). Webbappen: format, lint, typer och 94 enhetstester gröna."

Done when every check in scope has run once and its result, or the reason it did not run, is in the report.
