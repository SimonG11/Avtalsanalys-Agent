# Agent skills

The skills Claude Code loads for this repo. `CLAUDE.md` in the repo root says when to reach for them. A skill marked "you" is reached only by typing its name; the others the model picks up on its own when the task fits, and you can type them too.

## Vendored from mattpocock/skills

Copied verbatim from [mattpocock/skills](https://github.com/mattpocock/skills) at commit `49dd158` (plugin version 1.3.1, 2026-10-09), MIT licence (the notice is at the end of this file). Each folder holds the skill's `SKILL.md` and its supporting files; the Codex metadata (`agents/openai.yaml`) is left out.

| Skill | Invoked by | What it does |
|---|---|---|
| `grill-me` | you (`/grill-me`) | Interviews you until every branch of a design is settled. Calls `grilling`. |
| `grilling` | the model, or you | The interview itself: rounds of numbered questions, each with a recommended answer. |
| `tdd` | the model, or you | Red-green loop at seams agreed up front. `tests.md` and `mocking.md` say what a good test is. |
| `diagnosing-bugs` | the model, or you | Build a feedback loop that goes red on the bug, minimise, hypothesise, instrument, fix, regression-test. |
| `handoff` | you (`/handoff`) | Compacts the conversation into a handoff document for the next session. |
| `retro` | you (`/retro`) | Suggests improvements to `CLAUDE.md`, the checks and the tooling after a session. |
| `writing-for-agents` | the model, or you | How to write `CLAUDE.md` and skills so the agent follows them. |

Not vendored: `codebase-design` and `code-review`, which `tdd` names. Claude Code has a bundled `/code-review`. The spec and ticket flow (`to-spec`, `to-tickets`, `implement`, `triage`) needs an issue tracker, which this repo does not use.

To update a skill: clone the repo (`git clone --depth 1 https://github.com/mattpocock/skills`), copy `skills/<bucket>/<name>/` over the folder here without `agents/`, and change the commit above. Matt's README says to pick one install route, so do not also install the `mattpocock-skills` plugin: every skill would then exist twice.

## Written for this repo

| Skill | Invoked by | What it does |
|---|---|---|
| `verify` | the model, or you (`/verify`) | Runs the CI gate (ruff, mypy, pytest; the web app's format, lint, typecheck and tests) and reports in the PR body's Verifierat shape. Report only. |
| `adr` | the model, or you (`/adr`) | Drafts `docs/adr/NNNN-slug.md` in the repo's Swedish template (Status, Kontext, Beslut, Konsekvenser, Alternativ som valts bort) and adds the index row. |
| `pr` | the model, or you (`/pr`) | The PR title and body in the shape the merged PRs use: Före, Efter, Hur, Verifierat. |

## Hook

`.claude/hooks/block-dangerous-git.sh`, registered as a PreToolUse hook in `.claude/settings.json`, stops git commands that throw work away before Claude Code runs them: force pushes, `reset --hard`, `clean -f`, `branch -D`, `checkout .`, `restore .`, `stash drop`. Adapted from the repo's `git-guardrails-claude-code`, with plain `git push` allowed so that the agent can open pull requests. The patterns are listed in the script with the reason for each.

## MIT licence for the vendored skills

```text
MIT License

Copyright (c) 2026 Matt Pocock

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
