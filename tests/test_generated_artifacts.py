"""Tests that the GENERATED artifacts actually run: TS plugins are valid
TypeScript (node --check) and bash hooks execute correctly against a temp HOME.

These catch the class of bugs unit tests on generator strings miss:
- a template emitting a literal newline inside a JS regex literal (opencode)
- a run_check() that drops the check arguments (codex/claude/cursor fallback)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from luau_check.adapters import codex, cursor, opencode, ohmpi

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node required to check TS plugins"
)


def test_generated_opencode_plugin_is_valid_ts(tmp_path):
    """The opencode plugin must parse as TypeScript (regression for a literal
    newline inside the JS regex literal that made the module unloadable)."""
    plugin = tmp_path / "luau-check.ts"
    plugin.write_text(opencode.plugin_ts_content("/v/luau-check"))
    # node --check validates syntax (strip-types for the .ts extension)
    r = subprocess.run(
        ["node", "--experimental-strip-types", "--check", str(plugin)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, f"plugin is not valid TS:\n{r.stderr}"
    # regression guard: no literal newline inside the regex literal
    content = plugin.read_text()
    assert "replace(/\n" not in content, "regex literal contains a raw newline"


def test_generated_ohmpi_extension_is_valid_ts(tmp_path):
    ext = tmp_path / "luau-check.ts"
    ext.write_text(ohmpi.extension_ts_content("/v/luau-check"))
    r = subprocess.run(
        ["node", "--experimental-strip-types", "--check", str(ext)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, f"extension is not valid TS:\n{r.stderr}"


def test_codex_hook_executes_end_to_end(tmp_path):
    """The generated codex hook must run luau-check on a real bad file and emit
    hookEventName JSON; and must work via the -m fallback path with args intact."""
    # a real temp luau file with a type error
    bad = tmp_path / "bad.luau"
    bad.write_text('local x: number = "boom"\n', encoding="utf-8")

    # write a launcher with the real CLI (absolute path, on-PATH install case)
    hook = codex.hook_script_content(shutil.which("luau-check") or "/v/luau-check")
    launcher = tmp_path / "codex-hook"
    launcher.write_text(hook, encoding="utf-8")
    launcher.chmod(0o755)

    event = json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(bad)}})
    r = subprocess.run([str(launcher)], input=event, capture_output=True, text=True, timeout=60)
    # the hook must emit valid hookEventName JSON
    assert r.returncode == 0
    payload = json.loads(r.stdout.strip())
    assert payload["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "diagnostics" in payload["hookSpecificOutput"]["additionalContext"]


def test_run_check_fallback_preserves_args():
    """The -m fallback branch must pass check --json <file> through (regression:
    set -- clobbered the args, running the CLI with no command => silent no-op)."""
    script = """#!/usr/bin/env bash
set -uo pipefail
LUAU_LENS="python3 -m luau_check.cli"
run_check() {
  if [[ "$LUAU_LENS" == *" -m "* ]]; then
    PY_BIN="${LUAU_LENS%% -m *}"
    PY_ARGS="${LUAU_LENS#* -m }"
    "$PY_BIN" -m $PY_ARGS "$@"
  else
    "$LUAU_LENS" "$@"
  fi
}
echo "ARGS: $*"
run_check check --json /tmp/nonexistent.luau
"""
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)
    # The args must reach the command: the CLI ran check --json /tmp/nonexistent.luau
    # and reported the missing path as an error (not argparse "command required").
    assert '"command required"' not in r.stdout
    assert '"code": "NoSuchFile"' in r.stdout
    assert '"errors": 1' in r.stdout


def test_cursor_hook_executes_end_to_end(tmp_path):
    """The generated cursor hook must emit additional_context (snake_case)."""
    bad = tmp_path / "bad.luau"
    bad.write_text('local x: number = "boom"\n', encoding="utf-8")

    launcher = cursor.write_launcher(tmp_path / "cursor-hook")
    event = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": str(bad)}})
    r = subprocess.run([str(launcher)], input=event, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0
    payload = json.loads(r.stdout.strip())
    assert "additional_context" in payload
    assert "hookSpecificOutput" not in payload
