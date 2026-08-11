"""Codex CLI adapter for luau-check.

Mechanism (verified against openai/codex source, v0.147):
- Codex loads PostToolUse hooks from `~/.codex/hooks.json` (the managed hook
  directory / hooks.json in the codex config folder). This is a separate file
  from the user's hand-written `~/.codex/config.toml`, so we never touch or
  clobber user config.
- A PostToolUse hook handler runs a command after Write/Edit tools. Its stdout
  is parsed as JSON:
      {"hookSpecificOutput": {"eventName": "PostToolUse", "additionalContext": "..."}}
  If additionalContext is present, it is injected into the model context
  (advisory feedback). No decision/block = does not interrupt the agent.
- The command runs in codex's own process env, so it must be an absolute path.

Install strategy:
  1. Write `~/.codex/hooks.json` (merge with existing hooks.json if present,
     preserving user entries).
  2. Write a launcher script `~/.luau-check/bin/codex-hook` that runs
     `luau-check check --json <file>` for the edited file (or project if the
     file isn't a luau file) and emits the PostToolUse JSON with diagnostics
     as additionalContext ONLY when there are errors. Clean output => emit
     nothing (empty stdout) so the agent sees zero feedback.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

from ..version import __version__

HOOKS_FILE_NAME = "hooks.json"
HOOK_LAUNCHER_NAME = "codex-hook"
HOOK_MATCHER_TOOLS = r"^(Write|MultiEdit|Edit)$"


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))


def hooks_file_path() -> Path:
    return codex_home() / HOOKS_FILE_NAME


def launcher_path() -> Path:
    return Path.home() / ".luau-check" / "bin" / HOOK_LAUNCHER_NAME


def current_hooks() -> dict:
    """Load existing hooks.json, or {} if absent/invalid. Never raises."""
    p = hooks_file_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
        return {}
    except (json.JSONDecodeError, OSError):
        return {}


def _luau_check_command() -> str:
    """Absolute path to the luau-check CLI. Prefer the active venv binary."""
    # The CLI entry point installed by pip: find it via shutil.which first.
    found = shutil_which("luau-check")
    if found and os.path.isabs(found):
        return found
    # fallback: python -m luau_check.cli
    fallback = f"{sys.executable} -m luau_check.cli"
    return fallback


def shutil_which(cmd: str) -> str | None:
    import shutil
    return shutil.which(cmd)


def hook_script_content(luau_check_cmd: str) -> str:
    """Shell script emitted by the codex hook.

    Reads codex's hook JSON from stdin (codex passes tool info this way),
    extracts the edited file path from tool_input, and runs luau-check check
    on it. On errors, prints the PostToolUse JSON with diagnostics in
    additionalContext. On clean, prints nothing.
    """
    return f'''#!/usr/bin/env bash
# luau-check codex hook (v{__version__})
# PostToolUse hook: runs luau-check on the file the agent just wrote.
# Emits additionalContext ONLY when there are errors. Clean => silent.
set -uo pipefail

LUAU_LENS="{luau_check_cmd}"

input="$(cat)"
# codex sends hook input as JSON on stdin with tool_name + tool_input
tool_name="$(printf '%s' "$input" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("tool_name",""))' 2>/dev/null || true)"
tool_input="$(printf '%s' "$input" | python3 -c 'import sys,json; d=json.load(sys.stdin); ti=d.get("tool_input",{{}}); print(ti.get("file_path","") or ti.get("file","") or ti.get("path",""))' 2>/dev/null || true)"

# Only react to write/edit tools
case "$tool_name" in
  Write|MultiEdit|Edit) ;;
  *) exit 0 ;;
esac

if [ -z "$tool_input" ] || [ ! -f "$tool_input" ]; then
  exit 0
fi

# If the edited file isn't luau, skip (no feedback)
case "$tool_input" in
  *.luau|*.lua) ;;
  *) exit 0 ;;
esac

# Run the check; use --json so we can extract the summary
out="$("$LUAU_LENS" check --json "$tool_input" 2>/dev/null)"
rc=$?
if [ $rc -ne 0 ] && [ -n "$out" ]; then
  # Build additionalContext from diagnostics; only when there are errors
  summary="$(printf '%s' "$out" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("summary",{{}}).get("errors",0))' 2>/dev/null || echo 0)"
  if [ "$summary" -gt 0 ]; then
    ctx="$(printf '%s' "$out" | python3 -c '
import sys,json
d=json.load(sys.stdin)
diags=d.get("diagnostics",[])
if not diags: sys.exit(0)
lines=[]
for x in diags:
    if x.get("severity")=="error":
        lines.append(f"{{x[\"file\"]}}:{{x[\"line\"]}}:{{x[\"column\"]}}: {{x[\"code\"]}} {{x[\"message\"]}}")
if not lines: sys.exit(0)
print("luau-check diagnostics (errors):")
print("\\n".join(lines))
' 2>/dev/null || true)"
    if [ -n "$ctx" ]; then
      python3 -c 'import json,sys; print(json.dumps({{"hookSpecificOutput":{{"eventName":"PostToolUse","additionalContext":sys.argv[1]}}}}))' "$ctx"
    fi
  fi
fi
exit 0
'''


# ---------------------------------------------------------------------------
# Install logic
# ---------------------------------------------------------------------------

def write_launcher(dest: Path | None = None) -> Path:
    """Write the codex hook launcher script. Returns its path."""
    dest = dest or launcher_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    luau_check_cmd = _luau_check_command()
    dest.write_text(hook_script_content(luau_check_cmd), encoding="utf-8")
    dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return dest


def install_hooks(launcher: Path | None = None) -> bool:
    """Merge a luau-check PostToolUse hook into ~/.codex/hooks.json.

    Preserves any existing hooks. Returns True if the file was written or
    changed, False if a luau-check hook already exists with the same command.
    """
    launcher = launcher or launcher_path()
    if not launcher.exists():
        raise FileNotFoundError(f"launcher not found: {launcher}")

    path = hooks_file_path()
    hooks = current_hooks()

    # Reuse existing PostToolUse if present (merge rather than replace)
    post = hooks.get("PostToolUse", [])
    if not isinstance(post, list):
        post = []

    # Look for an existing luau-check matcher to avoid duplication
    for group in post:
        if not isinstance(group, dict):
            continue
        matcher = group.get("matcher", "")
        hooks_list = group.get("hooks")
        if not isinstance(hooks_list, list):
            continue
        if matcher == HOOK_MATCHER_TOOLS and any(
            isinstance(h, dict) and h.get("type") == "command"
            and str(h.get("command", "")).startswith(str(launcher))
            for h in hooks_list
        ):
            # Already installed
            return False

    # Append our matcher group
    post.append({
        "matcher": HOOK_MATCHER_TOOLS,
        "hooks": [
            {
                "type": "command",
                "command": str(launcher),
                "timeout": 30,
                "additionalContextLimit": 1024,
            }
        ],
    })
    hooks["PostToolUse"] = post

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(hooks, indent=2) + "\n", encoding="utf-8")
    return True


def uninstall_hooks(launcher: Path | None = None) -> bool:
    """Remove the luau-check matcher group from hooks.json. Returns True if removed."""
    launcher = launcher or launcher_path()
    path = hooks_file_path()
    hooks = current_hooks()
    post = hooks.get("PostToolUse", [])
    if not isinstance(post, list):
        return False
    kept = [
        group for group in post
        if not (
            isinstance(group, dict)
            and group.get("matcher") == HOOK_MATCHER_TOOLS
            and any(
                isinstance(h, dict) and h.get("type") == "command"
                and str(h.get("command", "")).startswith(str(launcher))
                for h in group.get("hooks", [])
                if isinstance(h, dict)
            )
        )
    ]
    if len(kept) == len(post):
        return False
    hooks["PostToolUse"] = kept
    path.write_text(json.dumps(hooks, indent=2) + "\n", encoding="utf-8")
    return True


def agents_md_snippet() -> str:
    """Instruction snippet for a project AGENTS.md.

    Returned as a string, never auto-written into user files. The human can
    paste it (or the install command prints it) so their AGENTS.md stays
    theirs.
    """
    return (
        "## Luau checks (luau-check)\n"
        "\n"
        "Before declaring a Luau task complete, run:\n"
        "\n"
        "```bash\n"
        "luau-check check <edited-file-or-directory>\n"
        "```\n"
        "\n"
        "It type-checks with luau-lsp, lints with selene, and checks "
        "formatting with stylua. Exit code 0 and no output means clean; "
        "non-zero means errors that must be fixed.\n"
    )
