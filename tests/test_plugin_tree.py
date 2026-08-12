"""Tests for the shipped plugin tree (plugins/luau-check/).

These validate the artifacts that actually ship to users in Claude Code and
Codex marketplaces:
- both plugin manifests parse and carry the required fields
- hooks/hooks.json references a script that exists
- the hook .sh is executable and the .cmd wrapper is present
- the engine runs: hook mode (real event in -> contract JSON out) and check
  mode (exit code + summary), driven with fake binaries so it's offline.

The engine is stdlib-only and shares the real cache, so tests point HOME at
a tmp dir and install a fake toolchain into it.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO_ROOT / "plugins" / "luau-check"
ENGINE = PLUGIN_DIR / "scripts" / "luau_check_hook.py"

PLUGIN_FILES = [
    ".claude-plugin/plugin.json",
    ".codex-plugin/plugin.json",
    "hooks/hooks.json",
    "scripts/luau-check-hook.sh",
    "scripts/luau-check-hook.cmd",
    "scripts/luau_check_hook.py",
    "skills/luau-check/SKILL.md",
]


def _run_hook(tmp_home: Path, event: dict, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["HOME"] = str(tmp_home)
    env["PYTHON"] = sys.executable
    env.pop("LUAU_CHECK_HOME", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(ENGINE)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env=env,
    )


def _write_fake_bin(dirpath: Path, name: str, content: str) -> Path:
    """Write a fake executable (a shell script) that behaves like the tool."""
    p = dirpath / name
    p.write_text(content, encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return p


# ---------------------------------------------------------------------------
# Artifact checks
# ---------------------------------------------------------------------------

def test_plugin_files_exist():
    for rel in PLUGIN_FILES:
        assert (PLUGIN_DIR / rel).exists(), f"missing {rel}"


def test_claude_manifest_valid():
    data = json.loads((PLUGIN_DIR / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert data["name"] == "luau-check"
    assert data["version"] == "3.0.0"
    assert "description" in data


def test_codex_manifest_valid():
    data = json.loads((PLUGIN_DIR / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert data["name"] == "luau-check"
    assert data["version"] == "3.0.0"
    # codex loads ./hooks/hooks.json by default; manifest may omit "hooks"
    assert "interface" in data


def test_hooks_json_references_existing_script():
    data = json.loads((PLUGIN_DIR / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    post = data["hooks"]["PostToolUse"]
    assert isinstance(post, list) and post
    group = post[0]
    assert "Write|Edit" in group.get("matcher", "")
    hook = group["hooks"][0]
    cmd = hook["command"]
    # command uses ${CLAUDE_PLUGIN_ROOT} (set by both codex and claude)
    assert "${CLAUDE_PLUGIN_ROOT}" in cmd
    # referenced script exists in the plugin
    ref = cmd.split('"/', 1)[1].rstrip('"') if '"/' in cmd else ""
    assert ref, f"could not parse script path from {cmd}"
    assert (PLUGIN_DIR / ref).exists(), f"hooks.json references missing {ref}"


def test_hook_script_executable_on_posix():
    sh = PLUGIN_DIR / "scripts" / "luau-check-hook.sh"
    mode = sh.stat().st_mode
    assert mode & stat.S_IXUSR, "hook .sh must be executable"
    # the .cmd wrapper is only used on Windows, but must be present
    assert (PLUGIN_DIR / "scripts" / "luau-check-hook.cmd").exists()


def test_marketplace_files():
    for rel in (".claude-plugin/marketplace.json", ".agents/plugins/marketplace.json"):
        data = json.loads((REPO_ROOT / rel).read_text(encoding="utf-8"))
        assert data["name"] == "luau-check"
        plugins = data["plugins"]
        assert len(plugins) == 1
        entry = plugins[0]
        assert entry["name"] == "luau-check"
        assert entry["source"].startswith("./plugins/luau-check"), entry["source"]
        # the plugin the marketplace points to exists
        assert (REPO_ROOT / entry["source"]).exists(), entry["source"]


# ---------------------------------------------------------------------------
# Engine behavior (offline, fake toolchain in a temp HOME)
# ---------------------------------------------------------------------------

@pytest.fixture()
def fake_toolchain(tmp_path):
    """Install fake luau-lsp/selene/stylua + defs into a temp ~/.luau-check.

    Fake luau-lsp emits a real-style diagnostic line for a 'bad' marker.
    Fake selene emits JSON diagnostics. Fake stylua exits 1 with a diff line.
    """
    home = tmp_path / "home"
    bin_dir = home / ".luau-check" / "bin"
    defs_dir = home / ".luau-check" / "defs"
    config_dir = home / ".luau-check" / "config"
    bin_dir.mkdir(parents=True)
    defs_dir.mkdir(parents=True)
    config_dir.mkdir(parents=True)
    (defs_dir / "globalTypes.d.luau").write_text("declare global game: any\n")
    (config_dir / "selene.toml").write_text('std = "roblox"\n')
    (config_dir / ".luaurc").write_text('{"languageMode": "strict"}')

    suffix = ".exe" if os.name == "nt" else ""

    _write_fake_bin(bin_dir, f"luau-lsp{suffix}", """#!/bin/sh
for f in "$@"; do
  case "$f" in
    *bad*) echo "/path/to/bad.luau:1:1-5: (W0) TypeError: Expected this to be 'number', got 'string'";;
  esac
done
exit 0
""")
    _write_fake_bin(bin_dir, f"selene{suffix}", """#!/bin/sh
JSON='{"primary_label":{"filename":"/path/to/bad.luau","span":{"start_line":2,"start_column":1,"end_line":2,"end_column":5}},"severity":"Warning","code":"unused_variable","message":"unused variable"}'
for f in "$@"; do
  case "$f" in
    *bad*) echo "$JSON";;
  esac
done
exit 0
""")
    _write_fake_bin(bin_dir, f"stylua{suffix}", """#!/bin/sh
for f in "$@"; do
  case "$f" in
    *bad*) echo "Diff in /path/to/bad.luau:1:1";;
  esac
done
exit 1
""")
    return home


def _write_sample(root: Path, name: str, content: str) -> Path:
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def test_engine_hook_bad_file_emits_contract(fake_toolchain, tmp_path):
    """A bad .luau file produces the exact PostToolUse JSON contract."""
    f = _write_sample(tmp_path, "bad.luau", "local x: number = 's'\nlocal y = 1\n")
    r = _run_hook(fake_toolchain, {"tool_name": "Write", "tool_input": {"file_path": str(f)}})
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "luau-check diagnostics" in ctx
    assert "[ERROR]" in ctx and "[WARNING]" in ctx


def test_engine_hook_clean_file_silent(fake_toolchain, tmp_path):
    """A clean .luau file produces NO output (the documented silent contract)."""
    f = _write_sample(tmp_path, "clean.luau", "local x: number = 1\n")
    r = _run_hook(fake_toolchain, {"tool_name": "Write", "tool_input": {"file_path": str(f)}})
    assert r.returncode == 0
    assert r.stdout.strip() == "", f"expected silent, got: {r.stdout!r}"


def test_engine_hook_non_luau_silent(fake_toolchain, tmp_path):
    """Non-Luau files and missing files are ignored entirely."""
    f = _write_sample(tmp_path, "notes.md", "# hi\n")
    r = _run_hook(fake_toolchain, {"tool_name": "Write", "tool_input": {"file_path": str(f)}})
    assert r.stdout.strip() == ""
    r2 = _run_hook(fake_toolchain, {"tool_name": "Write", "tool_input": {"file_path": "/no/such/bad.luau"}})
    assert r2.stdout.strip() == ""


def test_engine_check_mode(fake_toolchain, tmp_path):
    """check mode prints diagnostics text and exits non-zero on errors."""
    f = _write_sample(tmp_path, "bad.luau", "local x: number = 's'\n")
    r = subprocess.run(
        [sys.executable, str(ENGINE), "check", str(f)],
        capture_output=True, text=True,
        env={**os.environ, "HOME": str(fake_toolchain), "PYTHON": sys.executable},
    )
    assert r.returncode == 1
    assert "TypeError" in r.stdout
    assert "summary:" in r.stdout


def test_engine_check_mode_json(fake_toolchain, tmp_path):
    f = _write_sample(tmp_path, "bad.luau", "local x: number = 's'\n")
    r = subprocess.run(
        [sys.executable, str(ENGINE), "check", "--json", str(f)],
        capture_output=True, text=True,
        env={**os.environ, "HOME": str(fake_toolchain), "PYTHON": sys.executable},
    )
    assert r.returncode == 1
    data = json.loads(r.stdout)
    assert data["summary"]["errors"] >= 1
    assert data["summary"]["warnings"] >= 1


def test_engine_check_clean_ok(fake_toolchain, tmp_path):
    f = _write_sample(tmp_path, "clean.luau", "local x: number = 1\n")
    r = subprocess.run(
        [sys.executable, str(ENGINE), "check", str(f)],
        capture_output=True, text=True,
        env={**os.environ, "HOME": str(fake_toolchain), "PYTHON": sys.executable},
    )
    assert r.returncode == 0
    assert r.stdout.strip() == "", f"clean check should be silent, got: {r.stdout!r}"


def test_engine_check_nonexistent_target_error(fake_toolchain):
    r = subprocess.run(
        [sys.executable, str(ENGINE), "check", "/no/such/dir"],
        capture_output=True, text=True,
        env={**os.environ, "HOME": str(fake_toolchain), "PYTHON": sys.executable},
    )
    assert r.returncode == 1
    assert "NoSuchFile" in r.stdout


def test_engine_hook_ignores_other_tools(fake_toolchain, tmp_path):
    """Hooks on non-write tools are ignored even if a file path is set."""
    f = _write_sample(tmp_path, "bad.luau", "local x: number = 's'\n")
    for tool in ("Read", "Glob"):
        r = _run_hook(fake_toolchain, {"tool_name": tool, "tool_input": {"file_path": str(f)}})
        assert r.stdout.strip() == "", f"{tool} should be ignored"


def test_engine_hook_bash_write_redirection_emits_contract(fake_toolchain, tmp_path):
    """Codex writes via Bash commands (printf > file.luau); the hook must
    extract the written path from the command and emit diagnostics."""
    f = _write_sample(tmp_path, "bad.luau", "local x: number = 's'\n")
    cmd = f"printf 'local x = 1\\n' > {f} && cat {f}"
    r = _run_hook(fake_toolchain, {"tool_name": "Bash", "tool_input": {"command": cmd}})
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "luau-check diagnostics" in out["hookSpecificOutput"]["additionalContext"]


def test_engine_hook_bash_append_and_sed(fake_toolchain, tmp_path):
    """Append (>>) and sed -i forms also yield the written Luau path."""
    f = _write_sample(tmp_path, "bad.luau", "local x: number = 's'\n")
    r1 = _run_hook(fake_toolchain, {"tool_name": "Bash", "tool_input": {"command": f"echo x >> {f}"}})
    assert r1.stdout.strip() != "", "append should produce diagnostics"
    r2 = _run_hook(fake_toolchain, {"tool_name": "Bash", "tool_input": {"command": f"sed -i 's/a/b/' {f}"}})
    assert r2.stdout.strip() != "", "sed -i should produce diagnostics"


def test_engine_hook_bash_read_only_silent(fake_toolchain, tmp_path):
    """cat/read-only Bash commands on .luau files must stay silent."""
    f = _write_sample(tmp_path, "bad.luau", "local x: number = 's'\n")
    for cmd in (f"cat {f}", f"head -5 {f}", f"ls -la {f}"):
        r = _run_hook(fake_toolchain, {"tool_name": "Bash", "tool_input": {"command": cmd}})
        assert r.stdout.strip() == "", f"read-only {cmd!r} should be silent"


@pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("python3") is None,
    reason="bash + python3 required to exercise the shipped .sh hook",
)
def test_sh_hook_script_end_to_end(fake_toolchain, tmp_path):
    """The shipped bash hook (what hooks.json actually invokes) drives the
    engine: event in -> contract JSON out, silent on clean."""
    f = _write_sample(tmp_path, "bad.luau", "local x: number = 's'\n")
    env = {**os.environ, "HOME": str(fake_toolchain), "PYTHON": "python3"}
    r = subprocess.run(
        ["bash", str(PLUGIN_DIR / "scripts" / "luau-check-hook.sh")],
        input=json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(f)}}),
        capture_output=True, text=True, env=env,
    )
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "[ERROR]" in out["hookSpecificOutput"]["additionalContext"]

    clean = _write_sample(tmp_path, "clean.luau", "local x: number = 1\n")
    r2 = subprocess.run(
        ["bash", str(PLUGIN_DIR / "scripts" / "luau-check-hook.sh")],
        input=json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(clean)}}),
        capture_output=True, text=True, env=env,
    )
    assert r2.returncode == 0
    assert r2.stdout.strip() == "", "clean file must be silent through the .sh hook"

