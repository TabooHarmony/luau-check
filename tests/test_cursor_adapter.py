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


def test_install_hooks_writes_correct_schema(fake_cursor_home, tmp_path):
    # pre-existing user hook in the documented shape
    (fake_cursor_home / "hooks.json").write_text(json.dumps({
        "version": 1,
        "hooks": {
            "afterFileEdit": [{"command": "/usr/bin/other"}]
        }
    }), encoding="utf-8")

    launcher = tmp_path / "cursor-hook"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o755)

    assert cursor.install_hooks(launcher) is True
    data = json.loads((fake_cursor_home / "hooks.json").read_text())
    # documented schema: version + hooks wrapper
    assert data.get("version") == 1
    assert "hooks" in data
    hooks = data["hooks"]
    # user entry preserved
    assert hooks.get("afterFileEdit") == [{"command": "/usr/bin/other"}]
    # luau-check added to postToolUse as a flat entry
    post = hooks.get("postToolUse", [])
    assert any(
        isinstance(h, dict) and h.get("command") and str(h["command"]).startswith(str(launcher))
        for h in post
    )
    # no top-level event key (the bug the auditor caught)
    assert "postToolUse" not in data


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
    data["hooks"]["afterFileEdit"] = [{"command": "/bin/true"}]
    (fake_cursor_home / "hooks.json").write_text(json.dumps(data), encoding="utf-8")

    assert cursor.uninstall_hooks(launcher) is True
    remaining = json.loads((fake_cursor_home / "hooks.json").read_text())
    post = remaining["hooks"]["postToolUse"]
    commands = [h.get("command") for h in post if isinstance(h, dict)]
    assert not any(c and c.startswith(str(launcher)) for c in commands)
    assert remaining["hooks"]["afterFileEdit"] == [{"command": "/bin/true"}]


def test_invalid_json_not_clobbered(fake_cursor_home, tmp_path):
    # the auditor's #4: broken user hooks.json must NOT be overwritten
    (fake_cursor_home / "hooks.json").write_text(
        "{ INVALID JSON user data that must be preserved", encoding="utf-8"
    )
    launcher = tmp_path / "cursor-hook"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o755)

    assert cursor.hooks_file_valid() is False
    assert cursor.install_hooks(launcher) is False
    # original content preserved byte-for-byte
    assert (fake_cursor_home / "hooks.json").read_text(encoding="utf-8").startswith(
        "{ INVALID JSON"
    )


def test_hook_script_emits_cursor_output_shape(tmp_path):
    launcher = cursor.write_launcher(tmp_path / "cursor-hook")
    content = launcher.read_text(encoding="utf-8")
    # cursor output is snake_case additional_context, no hookSpecificOutput envelope
    assert '"additional_context"' in content
    assert "hookSpecificOutput" not in content
    # cursor hook entries are flat {command, ...}, not the codex {matcher, hooks:[...]} shape
    assert "MATCHER" not in content or "hooks\": [" not in content
