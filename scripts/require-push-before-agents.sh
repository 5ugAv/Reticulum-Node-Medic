#!/bin/bash
# PUSH BEFORE AGENTS.
#
# Agent worktrees are created from the REMOTE-TRACKING ref, not from local HEAD.
# So "commit before launching an agent" is not enough: if the commits are
# unpushed, every agent gets a checkout that predates them.
#
# Proven 2026-08-16. Eight agents were launched from a clean local main at
# d9b29ca. All six new worktrees came up at 5d9c24a — exactly origin/main, eight
# commits behind. Agents sent to examine workflows/carry.py and a new screen got
# a checkout containing neither. One noticed unaided; the others would have
# reported confidently on code that no longer existed.
#
# Fails OPEN on anything unexpected (not a repo, no upstream, git missing): a
# gate that fires when it should not is a gate people learn to step over.
set -uo pipefail

cd "${CLAUDE_PROJECT_DIR:-$PWD}" 2>/dev/null || exit 0
command -v git >/dev/null 2>&1 || exit 0
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

# No upstream configured -> nothing to compare against. Allow.
upstream="$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null)" || exit 0
[ -n "$upstream" ] || exit 0

ahead="$(git rev-list --count "${upstream}..HEAD" 2>/dev/null)" || exit 0
[ "${ahead:-0}" -gt 0 ] 2>/dev/null || exit 0

branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
head_short="$(git rev-parse --short HEAD 2>/dev/null)"
up_short="$(git rev-parse --short "$upstream" 2>/dev/null)"

reason="PUSH FIRST — agents would read stale code.

${branch} is ${ahead} commit(s) ahead of ${upstream}.
  local  HEAD  ${head_short}
  ${upstream}  ${up_short}

Agent worktrees branch from ${upstream}, NOT from local HEAD, so every agent
launched now gets a checkout WITHOUT those ${ahead} commit(s) — and will report
confidently on code that no longer exists. This happened on 2026-08-16.

Do one of:
  1. git push                      (then relaunch — the normal fix)
  2. give each agent the absolute path to the main checkout and tell it to read
     files with cat; plain reads work inside a worktree, git operations do not
  3. set worktree.baseRef to \"head\" in .claude/settings.local.json so worktrees
     branch from local HEAD instead

Whichever you choose, ask the agent to report the HEAD it is working from. A
stale base is invisible in the findings otherwise."

python3 - "$reason" <<'PY' 2>/dev/null || printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"Unpushed commits: agent worktrees branch from origin and would miss them. Push first."}}'
import json, sys
print(json.dumps({"hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "ask",
    "permissionDecisionReason": sys.argv[1],
}}))
PY
exit 0
