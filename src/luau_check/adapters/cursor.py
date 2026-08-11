"""Cursor adapter for luau-check.

Mechanism (verified against cursor.com/docs/hooks):
- Cursor loads hooks from `hooks.json` at user level (`~/.cursor/hooks.json`)
  or project level (`<root>/.cursor/hooks.json`). User-level works across
  projects; paths in the file are relative to `~/.cursor/`.
- `postToolUse` fires after a tool call succeeds, for all tools. Hooks are
  spawned processes communicating JSON over stdio (input on stdin, optional
  JSON output on stdout).
- Exit code 0 with no output = no decision, advisory. Exit code 2 would block
  (we never use it; luau-check feedback is advisory).
- Env: `CURSOR_PROJECT_DIR` is workspace root; `CLAUDE_PROJECT_DIR` alias
  present for Claude-compat.

Install strategy:
  1. Write `~/.cursor/hooks.json`, merging with any existing hooks (preserve
     user entries).
  2. Write the hook script into `~/.luau-check/cursor-hook` (same bin dir as
     the codex hook), and reference it by absolute path.
  3. Idempotent: skip if a luau-check command hook already exists.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from ..version import __version__

HOOKS_FILE = Path.home() / ".cursor" / "hooks.json"
HOOK_LAUNCHER_NAME = "cursor-hook"
BIN_DIR = Path.home() / ".luau-check" / "bin"


def hooks_file_path() -> Path:
    return HOOKS_FILE


def launcher_path() -> Path:
    return BIN_DIR / HOOK_LAUNCHER_NAME


def current_hooks() -> dict:
    """Load existing user hooks.json, or {} if absent/invalid. Never raises."""
    p = hooks_file_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _luau_check_command() -> str:
    import shutil
    found = shutil.which("luau-check")
    if found and os.path.isabs(found):
        return found
    import sys
    return f"{sys.executable} -m luau_check.cli"


def hook_script_content(luau_check_cmd: str) -> str:
    """Bash hook script for cursor postToolUse.

    Reads cursor's JSON on stdin (toolName, toolInput), runs luau-check on the
    edited file, prints advisory JSON only on errors. Silent on clean.
    """
    return f'''#!/usr/bin/env bash
# luau-check cursor hook (v{__version__})
# postToolUse hook: runs luau-check on the file the agent just wrote.
# Emits advisory output ONLY when there are errors. Clean => silent.
set -uo pipefail

LUAU_CHECK="{luau_check_cmd}"

input="$(cat)"
tool_name="$(printf '%s' "$input" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("tool_name","") or d.get("toolName",""))' 2>/dev/null || true)"
file_path="$(printf '%s' "$input" | python3 -c 'import sys,json; d=json.load(sys.stdin); ti=d.get("tool_input",{{}}) or d.get("toolInput",{{}}); print(ti.get("file_path","") or ti.get("filePath","") or ti.get("path","") or ti.get("file",""))' 2>/dev/null || true)"

# Only react to write/edit tools (cursor tool names: Edit, Write, applyPatch, etc)
case "$tool_name" in
  Edit|Write|MultiEdit|applyPatch|ApplyPatch) ;;
  *) exit 0 ;;
esac

if [ -z "$file_path" ] || [ ! -f "$file_path" ]; then
  exit 0
fi

case "$file_path" in
  *.luau|*.lua) ;;
  *) exit 0 ;;
esac

# Run the check; emit advisory output only when there are errors
out="$("$LUAU_CHECK" check --json "$file_path" 2>/dev/null)"
summary="$(printf '%s' "$out" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("summary",{{}}).get("errors",0))' 2>/dev/null || echo 0)"
if [ "$summary" -gt 0 ]; then
  ctx="$(printf '%s' "$out" | python3 -c '
import sys,json
d=json.load(sys.stdin)
diags=d.get("diagnostics",[])
lines=[]
for x in diags:
    if x.get("severity")=="error":
        lines.append(f"{{x[\"file\"]}}:{{x[\"line\"]}}:{{x[\"column\"]}}: {{x[\"code\"]}} {{x[\"message\"]}}")
if not lines: sys.exit(0)
print("luau-check diagnostics (errors):")
print("\\n".join(lines))
' 2>/dev/null || true)"
  if [ -n "$ctx" ]; then
    python3 -c 'import json,sys; print(json.dumps({{"hookSpecificOutput":{{"eventName":"postToolUse","additionalContext":sys.argv[1]}}}}))' "$ctx"
  fi
fi
exit 0
'''


def write_launcher(dest: Path | None = None) -> Path:
    dest = dest or launcher_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(hook_script_content(_luau_check_command()), encoding="utf-8")
    dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return dest


def install_hooks(launcher: Path | None = None) -> bool:
    """Merge a luau-check postToolUse hook into ~/.cursor/hooks.json.

    Preserves existing hooks. Returns True if written/changed, False if the
    luau-check hook already exists.
    """
    launcher = launcher or launcher_path()
    if not launcher.exists():
        raise FileNotFoundError(f"launcher not found: {launcher}")

    path = hooks_file_path()
    hooks = current_hooks()

    post = hooks.get("postToolUse", [])
    if not isinstance(post, list):
        post = []

    # Skip if our hook is already present (match by command prefix,
    # supporting both cursor's group shape and flat entries)
    for h in post:
        if isinstance(h, dict):
            if str(h.get("command", "")).startswith(str(launcher)):
                return False
            for sub in h.get("hooks", []):
                if isinstance(sub, dict) and str(sub.get("command", "")).startswith(str(launcher)):
                    return False

    post.append({
        "matcher": ".*",
        "hooks": [
            {
                "type": "command",
                "command": str(launcher),
            }
        ],
    })
    hooks["postToolUse"] = post

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(hooks, indent=2) + "\n", encoding="utf-8")
    return True


def uninstall_hooks(launcher: Path | None = None) -> bool:
    """Remove the luau-check hook from ~/.cursor/hooks.json. Returns True if removed."""
    launcher = launcher or launcher_path()
    path = hooks_file_path()
    hooks = current_hooks()
    post = hooks.get("postToolUse", [])
    if not isinstance(post, list):
        return False

    def is_lens(h: dict) -> bool:
        if str(h.get("command", "")).startswith(str(launcher)):
            return True
        return any(
            isinstance(sub, dict) and str(sub.get("command", "")).startswith(str(launcher))
            for sub in h.get("hooks", [])
        )

    kept = [h for h in post if not (isinstance(h, dict) and is_lens(h))]
    if len(kept) == len(post):
        return False
    hooks["postToolUse"] = kept
    path.write_text(json.dumps(hooks, indent=2) + "\n", encoding="utf-8")
    return True
