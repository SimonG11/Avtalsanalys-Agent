#!/bin/bash
# PreToolUse hook on Bash, registered in .claude/settings.json: stops git commands that throw
# work away before Claude Code runs them. Adapted from mattpocock/skills,
# skills/misc/git-guardrails-claude-code/scripts/block-dangerous-git.sh (MIT), with plain
# `git push` allowed: the agent opens pull requests and Simon merges them.
#
# Try it:
#   echo '{"tool_input":{"command":"git push --force origin main"}}' | .claude/hooks/block-dangerous-git.sh
# Exit 2 with a BLOCKED line on stderr stops the command; exit 0 lets it run.

INPUT=$(cat)
if command -v jq >/dev/null 2>&1; then
  COMMAND=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty')
else
  COMMAND=$(printf '%s' "$INPUT" | python3 -c 'import json, sys; print(json.load(sys.stdin).get("tool_input", {}).get("command", ""))')
fi

# Extended regular expressions, each with the reason it is here.
DANGEROUS_PATTERNS=(
  "git push.*( -f( |$)| --force)"                              # a force push rewrites a branch others may have pulled
  "git push.* \+"                                               # a +refspec is a force push too
  "git push.*( --delete| -d( |$))"                              # deletes a remote branch
  "git( -C [^ ]+)? reset.* --hard"                              # throws away uncommitted work
  "git clean.*( -[a-zA-Z]*f| --force)"                          # deletes untracked files
  "git branch.*( -D( |$)| --delete.* --force| --force.* --delete)"  # deletes a branch, merged or not
  "git checkout.* \.( |$)"                                      # discards every change in the tree
  "git restore.* \.( |$)"                                       # same, in the newer spelling
  "git stash (drop|clear)"                                      # deletes stashed work
)

for pattern in "${DANGEROUS_PATTERNS[@]}"; do
  if printf '%s' "$COMMAND" | grep -qE "$pattern"; then
    echo "BLOCKED: '$COMMAND' matches '$pattern'. This repo's hook stops git commands that throw work away; ask Simon to run it himself." >&2
    exit 2
  fi
done

exit 0
