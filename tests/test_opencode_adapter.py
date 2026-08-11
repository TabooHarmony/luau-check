"""Tests for the opencode adapter: plugin file generation, install, uninstall."""

from __future__ import annotations

from pathlib import Path

import pytest

from luau_check.adapters import opencode


@pytest.fixture
def fake_opencode_home(tmp_path: Path, monkeypatch):
    cfg = tmp_path / ".config" / "opencode"
    cfg.mkdir(parents=True)
    monkeypatch.setattr(opencode, "opencode_home", lambda: cfg)
    return cfg


def test_plugin_written(fake_opencode_home):
    path = opencode.install_plugin(luau_check_cmd="/venv/bin/luau-check")
    assert path == fake_opencode_home / "plugin" / "luau-check.ts"
    assert path.exists()
    content = path.read_text()
    assert "/venv/bin/luau-check" in content
    assert "event" in content  # uses event hook


def test_idempotent(fake_opencode_home):
    p1 = opencode.install_plugin(luau_check_cmd="/a")
    p2 = opencode.install_plugin(luau_check_cmd="/b")
    assert p1 == p2
    assert "/b" in p1.read_text()  # updated in place


def test_is_installed_and_uninstall(fake_opencode_home):
    assert opencode.is_installed() is False
    opencode.install_plugin(luau_check_cmd="/x")
    assert opencode.is_installed() is True
    assert opencode.uninstall_plugin() is True
    assert opencode.is_installed() is False
    assert opencode.uninstall_plugin() is False


def test_generated_module_valid_ts_shape(fake_opencode_home):
    content = opencode.plugin_ts_content("/venv/bin/luau-check")
    # must default-export an object {id, server} returning hooks with event
    assert "export default plugin" in content
    assert 'id: "luau-check"' in content
    assert "server:" in content
    assert "event:" in content
    assert "execFileSync" in content
    # no shell-based execSync (injection surface)
    assert "execSync" not in content
