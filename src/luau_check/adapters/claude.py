"""Claude Code adapter for luau-check.

Mechanism (verified against code.claude.com hooks + plugins-reference):
- Claude Code loads "skills-directory plugins" from `~/.claude/skills/<name>/`
  automatically on the next session as `<name>@skills-dir`. No install step,
  no marketplace, no cache copy. A plugin is any folder under a skills dir
  containing `.claude-plugin/plugin.json`.
- Plugins can bundle hooks via `hooks/hooks.json`. A PostToolUse command hook
  with matcher `Write|Edit` reads JSON on stdin (tool_name, tool_input) and
  returns JSON on stdout:
      {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                              "additionalContext": "..."}}
  additionalContext is wrapped in a system reminder and injected into Claude's
  context. Clean output (no JSON) means no feedback. Advisory, non-blocking.
- `${CLAUDE_PLUGIN_ROOT}` resolves to the plugin install dir, so hook commands
  reference the bundled script relatively. The script itself calls the
  absolute luau-check path (venv or installed), because claude hook processes
  have their own env.

Install strategy:
  1. Create `~/.claude/skills/luau-check/` with plugin.json, hooks/hooks.json,
     and scripts/luau-check-hook.sh.
  2. Never touch user CLAUDE.md or user-level hook config. The plugin is fully
     self-contained and discovered in place.
  3. Idempotent: re-install overwrites the plugin folder contents; uninstall
     removes the folder.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from ..version import __version__

PLUGIN_NAME = "luau-check"
SKILLS_ROOT = Path.home() / ".claude" / "skills"
PLUGIN_DIR = SKILLS_ROOT / PLUGIN_NAME
HOOK_MATCHER = r"Write|Edit"

PLUGIN_JSON = {
    "name": PLUGIN_NAME,
    "version": __version__,
    "description": "Luau diagnostics for AI coding agents (luau-lsp + selene + stylua). Runs after Write/Edit and feeds errors back as advisory context.",
}


def plugin_dir() -> Path:
    return PLUGIN_DIR


def _luau_check_command() -> str:
    """Absolute path to the luau-check CLI."""
    import shutil
    found = shutil.which("luau-check")
    if found and os.path.isabs(found):
        return found
    import sys
    return f"{sys.executable} -m luau_check.cli"


def hook_script_content(luau_check_cmd: str) -> str:
    """Bash hook script. Reads claude hook JSON on stdin, runs luau-check on the
    edited file, emits additionalContext only when there are errors."""
    return f'''#!/usr/bin/env bash
# luau-check claude hook (v{__version__})
# PostToolUse hook: runs luau-check on the file the agent just wrote.
# Emits additionalContext ONLY when there are errors. Clean => silent.
set -uo pipefail

LUAU_LENS="{luau_check_cmd}"

# The command may be "<python> -m luau_check.cli" (two tokens) when the CLI
# is not on PATH; run_check() handles both forms without eval.
run_check() {{
  if [[ "$LUAU_LENS" == *" -m "* ]]; then
    # split the generated "<python> -m luau_check.cli" string into argv tokens,
    # save them, restore positional params, then call with the check args.
    # Safe: the split string is generated and contains no user input.
    local cmd1 cmd2 cmd3
    set -- $LUAU_LENS
    cmd1="$1"; cmd2="$2"; cmd3="$3"
    shift 3
    "$cmd1" "$cmd2" "$cmd3" "$@"
  else
    "$LUAU_LENS" "$@"
  fi
}}

input="$(cat)"
tool_name="$(printf '%s' "$input" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("tool_name",""))' 2>/dev/null || true)"
tool_input="$(printf '%s' "$input" | python3 -c 'import sys,json; d=json.load(sys.stdin); ti=d.get("tool_input",{{}}); print(ti.get("file_path","") or ti.get("file","") or ti.get("path",""))' 2>/dev/null || true)"

# Only react to write/edit tools
case "$tool_name" in
  Write|Edit|MultiEdit) ;;
  *) exit 0 ;;
esac

if [ -z "$tool_input" ] || [ ! -f "$tool_input" ]; then
  exit 0
fi

# Only luau files
case "$tool_input" in
  *.luau|*.lua) ;;
  *) exit 0 ;;
esac

# Run the check; emit additionalContext only when there are diagnostics
out="$(run_check check --json "$tool_input" 2>/dev/null)"
summary="$(printf '%s' "$out" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("summary",{{}}).get("total",0))' 2>/dev/null || echo 0)"
if [ "$summary" -gt 0 ]; then
  ctx="$(printf '%s' "$out" | python3 -c '
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
    python3 -c 'import json,sys; print(json.dumps({{"hookSpecificOutput":{{"hookEventName":"PostToolUse","additionalContext":sys.argv[1]}}}}))' "$ctx"
  fi
fi
exit 0
'''


def hooks_json_content(script_rel: str) -> dict:
    """plugin hooks.json: PostToolUse matcher on Write|Edit running the bundled script."""
    return {
        "hooks": {
            "PostToolUse": [
                {
                    "matcher": HOOK_MATCHER,
                    "hooks": [
                        {
                            "type": "command",
                            "command": f'"${{CLAUDE_PLUGIN_ROOT}}"/{script_rel}',
                        }
                    ],
                }
            ]
        }
    }


def install_plugin(luau_check_cmd: str | None = None) -> Path:
    """Write the complete plugin tree into ~/.claude/skills/luau-check/.

    Returns the plugin dir. Idempotent (overwrites the plugin's own files).
    """
    luau_check_cmd = luau_check_cmd or _luau_check_command()
    plugin_dir = PLUGIN_DIR
    dot_plugin = plugin_dir / ".claude-plugin"
    hooks_dir = plugin_dir / "hooks"
    scripts_dir = plugin_dir / "scripts"

    dot_plugin.mkdir(parents=True, exist_ok=True)
    hooks_dir.mkdir(parents=True, exist_ok=True)
    scripts_dir.mkdir(parents=True, exist_ok=True)

    (dot_plugin / "plugin.json").write_text(
        json.dumps(PLUGIN_JSON, indent=2) + "\n", encoding="utf-8"
    )

    script = scripts_dir / "luau-check-hook.sh"
    script.write_text(hook_script_content(luau_check_cmd), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    (hooks_dir / "hooks.json").write_text(
        json.dumps(hooks_json_content("scripts/luau-check-hook.sh"), indent=2) + "\n",
        encoding="utf-8",
    )

    return plugin_dir


def is_installed() -> bool:
    return (PLUGIN_DIR / ".claude-plugin" / "plugin.json").exists()


def uninstall_plugin() -> bool:
    """Remove the plugin folder. Returns True if it existed."""
    if not PLUGIN_DIR.exists():
        return False
    import shutil
    shutil.rmtree(PLUGIN_DIR)
    return True


def claude_cli_available() -> bool:
    import shutil
    return shutil.which("claude") is not None
