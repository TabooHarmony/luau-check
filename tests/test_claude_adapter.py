"""Tests for the claude adapter: plugin tree, hooks.json, idempotence, uninstall."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from luau_lens.adapters import claude


@pytest.fixture
def fake_claude_home(tmp_path: Path, monkeypatch):
    """Point the skills dir at a temp dir."""
    skills = tmp_path / ".claude" / "skills"
    monkeypatch.setattr(claude, "SKILLS_ROOT", skills)
    monkeypatch.setattr(claude, "PLUGIN_DIR", skills / claude.PLUGIN_NAME)
    return skills


def test_install_plugin_creates_tree(fake_claude_home):
    plugin_dir = claude.install_plugin(luau_lens_cmd="/venv/bin/luau-lens")
    assert plugin_dir.exists()
    assert (plugin_dir / ".claude-plugin" / "plugin.json").exists()
    assert (plugin_dir / "hooks" / "hooks.json").exists()
    script = plugin_dir / "scripts" / "luau-lens-hook.sh"
    assert script.exists()
    assert os.access(script, os.X_OK)


def test_plugin_json_shape(fake_claude_home):
    claude.install_plugin(luau_lens_cmd="/venv/bin/luau-lens")
    data = json.loads((fake_claude_home / claude.PLUGIN_NAME / ".claude-plugin" / "plugin.json").read_text())
    assert data["name"] == "luau-lens"
    assert "version" in data
    assert "description" in data


def test_hooks_json_matches_write_edit(fake_claude_home):
    claude.install_plugin(luau_lens_cmd="/venv/bin/luau-lens")
    data = json.loads((fake_claude_home / claude.PLUGIN_NAME / "hooks" / "hooks.json").read_text())
    post = data["hooks"]["PostToolUse"]
    assert post[0]["matcher"] == claude.HOOK_MATCHER
    cmd = post[0]["hooks"][0]
    assert cmd["type"] == "command"
    # must reference the plugin root so it works in-place
    assert "${CLAUDE_PLUGIN_ROOT}" in cmd["command"]
    assert "luau-lens-hook.sh" in cmd["command"]


def test_hook_script_has_lens_cmd_and_silent_on_clean(fake_claude_home):
    claude.install_plugin(luau_lens_cmd="/venv/bin/luau-lens")
    script = (fake_claude_home / claude.PLUGIN_NAME / "scripts" / "luau-lens-hook.sh").read_text()
    assert "/venv/bin/luau-lens" in script
    assert "additionalContext" in script
    assert "summary" in script


def test_is_installed_and_uninstall(fake_claude_home):
    assert claude.is_installed() is False
    claude.install_plugin(luau_lens_cmd="/venv/bin/luau-lens")
    assert claude.is_installed() is True
    assert claude.uninstall_plugin() is True
    assert claude.is_installed() is False
    assert claude.uninstall_plugin() is False  # already gone


def test_install_idempotent_overwrites(fake_claude_home):
    first = claude.install_plugin(luau_lens_cmd="/venv/bin/luau-lens")
    second = claude.install_plugin(luau_lens_cmd="/new/path/luau-lens")
    assert first == second
    # updated command path is reflected
    script = (first / "scripts" / "luau-lens-hook.sh").read_text()
    assert "/new/path/luau-lens" in script
    # exactly one plugin dir
    assert len(list(fake_claude_home.iterdir())) == 1
