#!/usr/bin/env bash
# luau-check hook (v3.0.0)
# PostToolUse hook for Claude Code and Codex.
# Reads the harness hook event JSON on stdin, checks the edited Luau file,
# and emits the harness contract
#   {"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":...}}
# ONLY when there are errors/warnings. Clean => silent, exit 0.
set -uo pipefail

# Directory of this script, portable (no readlink -f; works in git-bash too).
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"

input="$(cat)"
printf '%s' "$input" | "$PYTHON" "$HOOK_DIR/luau_check_hook.py"
exit 0
