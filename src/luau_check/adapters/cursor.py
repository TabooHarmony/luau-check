"""Cursor adapter for luau-check.

Mechanism (verified against cursor.com/docs/hooks):
- Cursor loads hooks from `~/.cursor/hooks.json` (user level, applies to all
  projects) or `<project>/.cursor/hooks.json` (project level). The documented
  schema is:
      { "version": 1, "hooks": { "<eventName>": [ { "command": "...", ... } ] } }
- `postToolUse` fires after a tool call, for all tools (optionally filtered by
  `matcher`). Hooks are spawned processes receiving JSON on stdin and emitting
  JSON on stdout. Output schema is:
      { "updated_mcp_tool_output": ..., "additional_context": "..." }
  (snake_case; no hookSpecificOutput envelope — that is Claude/Codex, not
  Cursor).
- Exit code 0 with no output = advisory. Exit code 2 would block; we never use
  it (luau-check feedback is advisory).
- Env: `CURSOR_PROJECT_DIR` is workspace root; `CLAUDE_PROJECT_DIR` alias is
  also present.

Install strategy:
  1. Write `~/.cursor/hooks.json` as `{ "version": 1, "hooks": {...} }`,
     merging with any existing hooks under the `hooks` key (preserve user
     entries, keep the version field).
  2. Write the hook script into `~/.luau-check/cursor-hook` and reference it by
     absolute path (user-level hook paths are relative to `~/.cursor/`, but an
     absolute path works too and survives cwd changes).
  3. Idempotent: skip if a luau-check command hook already exists.
"""

from __future__ import annotations

import json
import os
import platform
import stat
import sys
from pathlib import Path

from ..version import __version__

HOOKS_FILE = Path.home() / ".cursor" / "hooks.json"
HOOK_LAUNCHER_NAME = "cursor-hook"
BIN_DIR = Path.home() / ".luau-check" / "bin"


def hooks_file_path() -> Path:
    return HOOKS_FILE


def launcher_path() -> Path:
    p = BIN_DIR / HOOK_LAUNCHER_NAME
    if platform.system() == "Windows":
        return p.with_suffix(".cmd")
    return p


def current_hooks() -> dict:
    """Load existing user hooks.json, or a fresh empty skeleton if absent.

    NOTE: on JSONDecodeError or non-dict content we return a fresh skeleton
    and leave the file untouched — we never overwrite an existing (even
    broken) file without explicit knowledge. Callers wanting to install must
    check `hooks_file_valid` first.
    """
    p = hooks_file_path()
    if not p.exists():
        return {"version": 1, "hooks": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"version": 1, "hooks": {}}
        if not isinstance(data.get("hooks"), dict):
            data["hooks"] = {}
        return data
    except (json.JSONDecodeError, OSError):
        return {"version": 1, "hooks": {}}


def hooks_file_valid() -> bool:
    """True if the existing hooks.json (if any) is valid JSON we may merge into."""
    p = hooks_file_path()
    if not p.exists():
        return True
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return isinstance(data, dict)
    except (json.JSONDecodeError, OSError):
        return False


def _luau_check_command() -> str:
    import shutil
    found = shutil.which("luau-check")
    if found and os.path.isabs(found):
        return found
    # fallback: absolute python + module. The launcher must call it as
    # `"$PY" -m luau_check.cli` (two tokens), so we need a small wrapper.
    import sys
    fallback = f"{sys.executable} -m luau_check.cli"
    if os.name == "nt":
        # git-bash needs forward slashes for Windows paths
        fallback = fallback.replace("\\", "/")
    return fallback


def hook_script_content(luau_check_cmd: str, python_path: str | None = None) -> str:
    """Bash hook script for cursor postToolUse.

    Reads cursor's JSON on stdin (toolName, toolInput), runs luau-check on the
    edited file, prints advisory output ONLY on errors (cursor's
    `additional_context` field, snake_case, no envelope). Silent on clean.
    """
    python_path = python_path or sys.executable
    if os.name == "nt":
        python_path = python_path.replace("\\", "/")
    return f'''#!/usr/bin/env bash
# luau-check cursor hook (v{__version__})
# postToolUse hook: runs luau-check on the file the agent just wrote.
# Emits advisory output ONLY when there are errors. Clean => silent.
set -uo pipefail

LUAU_CHECK="{luau_check_cmd}"

# Real interpreter for stdin parsing / JSON emission.
# (Windows git-bash has no `python3`; use the interpreter that generated us.)
PYTHON="{python_path}"

# The command may be "<python> -m luau_check.cli" (two tokens) when the CLI
# is not on PATH; run_check() handles both forms without eval.
run_check() {{
  if [[ "$LUAU_CHECK" == *" -m "* ]]; then
    # Split "<python> -m luau_check.cli" on the " -m " separator only, so a
    # python path containing spaces (Program Files) survives as one argv token.
    PY_BIN="${{LUAU_CHECK%% -m *}}"
    PY_ARGS="${{LUAU_CHECK#* -m }}"
    "$PY_BIN" -m $PY_ARGS "$@"
  else
    "$LUAU_CHECK" "$@"
  fi
}}

input="$(cat)"
tool_name="$(printf '%s' "$input" | "$PYTHON" -c 'import sys,json; d=json.load(sys.stdin); print(d.get("tool_name","") or d.get("toolName",""))' 2>/dev/null || true)"
file_path="$(printf '%s' "$input" | "$PYTHON" -c 'import sys,json; d=json.load(sys.stdin); ti=d.get("tool_input",{{}}) or d.get("toolInput",{{}}); print(ti.get("file_path","") or ti.get("filePath","") or ti.get("path","") or ti.get("file",""))' 2>/dev/null || true)"

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

# Run the check; emit advisory output only when there are diagnostics
out="$(run_check check --json "$file_path" 2>/dev/null)"
summary="$(printf '%s' "$out" | "$PYTHON" -c 'import sys,json; d=json.load(sys.stdin); print(d.get("summary",{{}}).get("total",0))' 2>/dev/null || echo 0)"
if [ "$summary" -gt 0 ]; then
  ctx="$(printf '%s' "$out" | "$PYTHON" -c '
import sys,json
d=json.load(sys.stdin)
diags=d.get("diagnostics",[])
lines=[]
for x in diags:
    sev=x.get("severity","")
    if sev in ("error","warning"):
        lines.append(f"{{x[\"file\"]}}:{{x[\"line\"]}}:{{x[\"column\"]}}: [{{sev.upper()}}] {{x[\"code\"]}} {{x[\"message\"]}}")
if not lines: sys.exit(0)
print("luau-check diagnostics:")
print("\\n".join(lines))
' 2>/dev/null || true)"
  if [ -n "$ctx" ]; then
    "$PYTHON" -c 'import json,sys; print(json.dumps({{"additional_context":sys.argv[1]}}))' "$ctx"
  fi
fi
exit 0
'''


def write_launcher(dest: Path | None = None) -> Path:
    from .launchers import write_launcher as _write_launcher

    dest = dest or launcher_path()
    return _write_launcher(hook_script_content(_luau_check_command()), dest)


def install_hooks(launcher: Path | None = None) -> bool:
    """Merge a luau-check postToolUse hook into ~/.cursor/hooks.json.

    Preserves existing hooks under the `hooks` key and keeps `version`.
    Refuses to install (returns False) if the existing file is invalid JSON,
    rather than clobbering user data.
    """
    launcher = launcher or launcher_path()
    if not launcher.exists():
        raise FileNotFoundError(f"launcher not found: {launcher}")

    if not hooks_file_valid():
        return False

    path = hooks_file_path()
    data = current_hooks()
    hooks = data.get("hooks", {})
    post = hooks.get("postToolUse", [])
    if not isinstance(post, list):
        post = []

    # Skip if our hook is already present (match by command prefix)
    for h in post:
        if isinstance(h, dict) and str(h.get("command", "")).startswith(str(launcher)):
            return False

    post.append({
        "matcher": ".*",
        "type": "command",
        "command": str(launcher),
    })
    hooks["postToolUse"] = post
    data["hooks"] = hooks
    if "version" not in data:
        data["version"] = 1

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return True


def uninstall_hooks(launcher: Path | None = None) -> bool:
    """Remove the luau-check hook from ~/.cursor/hooks.json. Returns True if removed."""
    launcher = launcher or launcher_path()
    path = hooks_file_path()
    data = current_hooks()
    hooks = data.get("hooks", {})
    post = hooks.get("postToolUse", [])
    if not isinstance(post, list):
        return False

    def is_lens(h: dict) -> bool:
        return str(h.get("command", "")).startswith(str(launcher))

    kept = [h for h in post if not (isinstance(h, dict) and is_lens(h))]
    if len(kept) == len(post):
        return False
    hooks["postToolUse"] = kept
    data["hooks"] = hooks
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return True
