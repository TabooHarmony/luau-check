"""Tests for the pi/oh-my-pi adapter: extension file, install, uninstall."""

from __future__ import annotations

from pathlib import Path

import pytest

from luau_check.adapters import ohmpi


@pytest.fixture
def fake_pi_home(tmp_path: Path, monkeypatch):
    ext_dir = tmp_path / ".omp" / "agent" / "extensions"
    ext_dir.mkdir(parents=True)
    monkeypatch.setattr(ohmpi, "agent_extensions_dir", lambda: ext_dir)
    return ext_dir


def test_extension_written(fake_pi_home):
    path = ohmpi.install_extension(luau_check_cmd="/venv/bin/luau-check")
    assert path == fake_pi_home / "luau-check.ts"
    assert path.exists()
    content = path.read_text()
    assert "/venv/bin/luau-check" in content
    assert "tool_result" in content
    # tool_result patching content (ToolResultEventResult), not additionalContext
    assert "type: \"text\"" in content
    assert "additionalContext:" not in content
    assert "content: [" in content


def test_idempotent(fake_pi_home):
    p1 = ohmpi.install_extension(luau_check_cmd="/a")
    p2 = ohmpi.install_extension(luau_check_cmd="/b")
    assert p1 == p2
    assert "/b" in p1.read_text()


def test_is_installed_and_uninstall(fake_pi_home):
    assert ohmpi.is_installed() is False
    ohmpi.install_extension(luau_check_cmd="/x")
    assert ohmpi.is_installed() is True
    assert ohmpi.uninstall_extension() is True
    assert ohmpi.is_installed() is False
    assert ohmpi.uninstall_extension() is False


def test_generated_extension_shape(fake_pi_home):
    content = ohmpi.extension_ts_content("/venv/bin/luau-check")
    assert "export default function" in content
    assert "pi.on" in content
    assert "tool_result" in content
