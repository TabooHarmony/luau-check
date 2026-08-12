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
# Portable interpreter: git-bash on Windows has only `python`, POSIX has
# `python3`. Prefer an explicit PYTHON (harness may set it), else python3,
# else python.
if [ -n "${PYTHON:-}" ]; then
  PY_BIN="$PYTHON"
elif command -v python3 >/dev/null 2>&1; then
  PY_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PY_BIN="python"
else
  echo "luau-check: no python interpreter found" >&2
  exit 1
fi

input="$(cat)"
printf '%s' "$input" | "$PY_BIN" "$HOOK_DIR/luau_check_hook.py"
exit 0
