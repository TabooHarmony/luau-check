"""Tests for the cursor adapter: hooks.json merge, launcher, uninstall."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from luau_check.adapters import cursor


@pytest.fixture
def fake_cursor_home(tmp_path: Path, monkeypatch):
    home_dir = tmp_path / ".cursor"
    home_dir.mkdir()
    monkeypatch.setattr(cursor, "HOOKS_FILE", home_dir / "hooks.json")
    monkeypatch.setattr(cursor, "BIN_DIR", tmp_path / ".luau-check" / "bin")
    return home_dir


def test_launcher_path(fake_cursor_home, tmp_path):
    assert cursor.launcher_path() == tmp_path / ".luau-check" / "bin" / "cursor-hook"


def test_write_launcher_creates_executable(tmp_path):
    launcher = cursor.write_launcher(tmp_path / "cursor-hook")
    assert launcher.exists()
    assert os.access(launcher, os.X_OK)
    content = launcher.read_text(encoding="utf-8")
    assert "luau-check" in content
    assert "postToolUse" in content


def test_install_hooks_writes_and_merges(fake_cursor_home, tmp_path):
    (fake_cursor_home / "hooks.json").write_text(json.dumps({
        "postToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "/usr/bin/other"}]}]
    }), encoding="utf-8")

    launcher = tmp_path / "cursor-hook"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o755)

    assert cursor.install_hooks(launcher) is True
    data = json.loads((fake_cursor_home / "hooks.json").read_text())
    post = data["postToolUse"]
    # user group preserved (matcher + hooks list shape)
    assert any(g.get("matcher") == "Bash" for g in post)
    # luau-check added
    assert any(
        g.get("matcher") == ".*"
        and any(str(h.get("command", "")).startswith(str(launcher)) for h in g.get("hooks", []))
        for g in post
    )


def test_install_hooks_idempotent(fake_cursor_home, tmp_path):
    launcher = tmp_path / "cursor-hook"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o755)
    assert cursor.install_hooks(launcher) is True
    assert cursor.install_hooks(launcher) is False


def test_uninstall_removes_only_luau_check(fake_cursor_home, tmp_path):
    launcher = tmp_path / "cursor-hook"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o755)
    cursor.install_hooks(launcher)
    data = json.loads((fake_cursor_home / "hooks.json").read_text())
    data["postToolUse"].append({"matcher": "Bash", "hooks": [{"type": "command", "command": "/bin/true"}]})
    (fake_cursor_home / "hooks.json").write_text(json.dumps(data), encoding="utf-8")

    assert cursor.uninstall_hooks(launcher) is True
    remaining = json.loads((fake_cursor_home / "hooks.json").read_text())
    post = remaining["postToolUse"]
    commands = [
        h.get("command")
        for g in post
        for h in g.get("hooks", [])
        if isinstance(h, dict)
    ]
    assert not any(c and c.startswith(str(launcher)) for c in commands)
    assert "/bin/true" in commands
