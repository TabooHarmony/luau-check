"""Tests for the codex adapter: hooks.json shaping, merge, launcher."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from luau_check.adapters import codex


@pytest.fixture
def fake_codex_home(tmp_path: Path, monkeypatch):
    """Point codex_home at a temp dir."""
    home = tmp_path / ".codex"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    return home


def test_hooks_file_path_uses_codex_home(fake_codex_home):
    assert codex.hooks_file_path() == fake_codex_home / "hooks.json"


def test_current_hooks_missing(fake_codex_home):
    assert codex.current_hooks() == {}


def test_current_hooks_invalid_json(fake_codex_home):
    (fake_codex_home / "hooks.json").write_text("not json{{{")
    assert codex.current_hooks() == {}


def test_write_launcher_creates_executable(tmp_path):
    launcher = codex.write_launcher(tmp_path / "bin" / "codex-hook")
    assert launcher.exists()
    assert os.access(launcher, os.X_OK)
    content = launcher.read_text(encoding="utf-8")
    assert "luau-check" in content
    assert "PostToolUse" in content


def test_install_hooks_writes_and_merges(fake_codex_home, tmp_path):
    # pre-existing user hooks must be preserved
    (fake_codex_home / "hooks.json").write_text(json.dumps({
        "PostToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "/usr/bin/other"}]}]
    }), encoding="utf-8")

    launcher = tmp_path / "codex-hook"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o755)

    changed = codex.install_hooks(launcher)
    assert changed is True

    data = json.loads((fake_codex_home / "hooks.json").read_text(encoding="utf-8"))
    post = data["PostToolUse"]
    # user group preserved
    assert any(g.get("matcher") == "Bash" for g in post)
    # luau-check group added
    ll = [g for g in post if g.get("matcher") == codex.HOOK_MATCHER_TOOLS]
    assert len(ll) == 1
    assert ll[0]["hooks"][0]["command"] == str(launcher)
    assert ll[0]["hooks"][0]["type"] == "command"
    assert "additionalContextLimit" in ll[0]["hooks"][0]


def test_install_hooks_idempotent(fake_codex_home, tmp_path):
    launcher = tmp_path / "codex-hook"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o755)

    assert codex.install_hooks(launcher) is True
    assert codex.install_hooks(launcher) is False  # already there


def test_install_hooks_requires_launcher(fake_codex_home, tmp_path):
    missing = tmp_path / "nope"
    with pytest.raises(FileNotFoundError):
        codex.install_hooks(missing)


def test_uninstall_hooks_removes_only_luau_lens(fake_codex_home, tmp_path):
    launcher = tmp_path / "codex-hook"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o755)
    codex.install_hooks(launcher)

    # add a user hook after
    data = json.loads((fake_codex_home / "hooks.json").read_text(encoding="utf-8"))
    data["PostToolUse"].append({"matcher": "Bash", "hooks": [{"type": "command", "command": "/bin/true"}]})
    (fake_codex_home / "hooks.json").write_text(json.dumps(data), encoding="utf-8")

    assert codex.uninstall_hooks(launcher) is True
    remaining = json.loads((fake_codex_home / "hooks.json").read_text(encoding="utf-8"))
    matchers = [g.get("matcher") for g in remaining["PostToolUse"]]
    assert codex.HOOK_MATCHER_TOOLS not in matchers
    assert "Bash" in matchers


def test_install_refuses_invalid_json(fake_codex_home, tmp_path):
    (fake_codex_home / "hooks.json").write_text(
        "{ INVALID JSON user data that must be preserved", encoding="utf-8"
    )
    launcher = tmp_path / "codex-hook"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o755)
    assert codex.hooks_file_valid() is False
    assert codex.install_hooks(launcher) is False
    assert (fake_codex_home / "hooks.json").read_text(encoding="utf-8").startswith(
        "{ INVALID JSON"
    )


def test_agents_md_snippet_mentions_check_and_exit_code():
    snippet = codex.agents_md_snippet()
    assert "luau-check check" in snippet
    assert "Exit code 0" in snippet
    assert "AGENTS.md" not in snippet  # it's content for AGENTS.md, doesn't self-reference
