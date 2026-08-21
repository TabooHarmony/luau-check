"""Coexistence detection, mirror-mode baking/gating, and the Script Sync
sourcemap bridge."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from luaudit import coexist, plugin, sourcemapper

REPO = Path(__file__).resolve().parent.parent
PLUGIN_ENGINE = REPO / "plugins" / "luaudit" / "scripts" / "luaudit_hook.py"
MIRROR_LUAU = REPO / "plugins" / "luaudit" / "studio" / "luaudit-mirror.luau"


def _load_hook():
    spec = importlib.util.spec_from_file_location("luaudit_hook_coexist_test", PLUGIN_ENGINE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# coexist.detect
# ---------------------------------------------------------------------------

def test_detect_empty_dir_is_studio(tmp_path):
    v = coexist.detect(str(tmp_path))
    assert v["mode"] == "studio"


def test_detect_rojo_marker_is_external(tmp_path):
    (tmp_path / "default.project.json").write_text("{}", encoding="utf-8")
    v = coexist.detect(str(tmp_path))
    assert v["mode"] == "external"
    assert any("default.project.json" in r for r in v["reasons"])


def test_detect_azul_marker_is_external(tmp_path):
    (tmp_path / "azul.toml").write_text("", encoding="utf-8")
    assert coexist.detect(str(tmp_path))["mode"] == "external"


@pytest.mark.skipif(sys.platform == "win32", reason="ps/tasklist shape differs")
def test_detect_running_process_is_external(tmp_path, monkeypatch):
    monkeypatch.setattr(coexist, "_running_processes", lambda: ["rojo"])
    v = coexist.detect(str(tmp_path))
    assert v["mode"] == "external"
    assert any("rojo" in r for r in v["reasons"])


def test_detect_luau_files_no_tool_is_unknown(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "Main.luau").write_text("return 1", encoding="utf-8")
    v = coexist.detect(str(tmp_path))
    assert v["mode"] == "unknown"


def test_detect_skips_git_and_venv_when_scanning_for_luau(tmp_path):
    (tmp_path / ".venv" / "lib").mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "stray.luau").write_text("return 1", encoding="utf-8")
    # Only a vendored .luau exists: workspace itself counts as clean.
    assert coexist.detect(str(tmp_path))["mode"] == "studio"


# ---------------------------------------------------------------------------
# plugin.install bakes the mode; status surfaces it
# ---------------------------------------------------------------------------

@pytest.fixture()
def fake_plugins_dir(tmp_path, monkeypatch):
    d = tmp_path / "Roblox" / "Plugins"
    monkeypatch.setattr(plugin, "plugins_dir", lambda: d)
    return d


def test_install_bakes_mirror_mode_for_empty_project(fake_plugins_dir, tmp_path):
    out = plugin.install(yes=True, root=str(tmp_path))
    assert out["mode"] == "mirror"
    installed = fake_plugins_dir / plugin.PLUGIN_FILENAME
    assert "--LUAUDIT-MODE=mirror" in installed.read_text(encoding="utf-8")


def test_install_bakes_external_mode_and_stays_byte_stable(fake_plugins_dir, tmp_path):
    (tmp_path / "default.project.json").write_text("{}", encoding="utf-8")
    out = plugin.install(yes=True, root=str(tmp_path))
    assert out["mode"] == "external"
    assert out.get("standdown_note")
    text = (fake_plugins_dir / plugin.PLUGIN_FILENAME).read_text(encoding="utf-8")
    assert "--LUAUDIT-MODE=external" in text


def test_reinstall_rebakes_mode_when_workspace_changes(fake_plugins_dir, tmp_path):
    # First install on an empty project: mirror.
    plugin.install(yes=True, root=str(tmp_path))
    # A Rojo project appears afterwards: next install must rewrite the mode.
    (tmp_path / "default.project.json").write_text("{}", encoding="utf-8")
    out = plugin.install(yes=True, root=str(tmp_path))
    assert out["mode"] == "external"
    text = (fake_plugins_dir / plugin.PLUGIN_FILENAME).read_text(encoding="utf-8")
    assert "--LUAUDIT-MODE=external" in text


def test_force_mode_overrides_detection(fake_plugins_dir, tmp_path):
    (tmp_path / "default.project.json").write_text("{}", encoding="utf-8")
    out = plugin.install(yes=True, root=str(tmp_path), force_mode="mirror")
    assert out["mode"] == "mirror"
    bad = plugin.install(yes=True, force_mode="nonsense")
    assert bad["installed"] is False
    assert "invalid mode" in bad["note"]


def test_artifact_roundtrip_preserves_crlf(fake_plugins_dir, tmp_path):
    plugin.install(yes=True, root=str(tmp_path))
    raw = (fake_plugins_dir / plugin.PLUGIN_FILENAME).read_bytes()
    bundled = plugin.bundled_plugin_path().read_bytes()
    assert raw.count(b"\r\n") == bundled.count(b"\r\n")


# ---------------------------------------------------------------------------
# Studio-side gate (static checks against the .luau source)
# ---------------------------------------------------------------------------

def test_mirror_source_gate_tokens():
    src = MIRROR_LUAU.read_text(encoding="utf-8")
    assert "--LUAUDIT-MODE=mirror" in src
    assert 'src:match("%-%-LUAUDIT%-MODE=(%S+)")' in src
    assert "startMirroring()" in src
    # idle message explains why and names the escape hatch
    assert "mirror is idle" in src and "start mirror" in src


def test_mirror_source_uses_real_api_only():
    """Guard against hallucinated APIs: Prompt() does not exist on Plugin."""
    src = MIRROR_LUAU.read_text(encoding="utf-8")
    assert ":Prompt(" not in src
    assert "CreatePluginAction" in src  # verified API for the opt-in entry
    assert "Triggered" in src           # verified PluginAction event


# ---------------------------------------------------------------------------
# Script Sync bridge: sourcemapper + CLI
# ---------------------------------------------------------------------------

def _write_script(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_sourcemapper_basic_tree(tmp_path):
    _write_script(tmp_path / "init.luau", "return { }")
    _write_script(tmp_path / "Game" / "round.server.luau", "print(1)")
    _write_script(tmp_path / "Shared" / "util.luau", "return 42")
    out = sourcemapper.generate(tmp_path)
    assert out["ok"] and out["scripts"] == 3
    tree = json.loads((tmp_path / "sourcemap.json").read_text(encoding="utf-8"))
    assert tree["className"] == "DataModel"
    # Root-level init.luau becomes the DataModel's own source, not a child.
    assert tree["filePaths"] == ["init.luau"]
    by_name = {c["name"]: c for c in tree["children"]}
    assert "init" not in by_name
    assert by_name["Game"]["children"][0]["className"] == "Script"
    assert by_name["Shared"]["children"][0]["className"] == "ModuleScript"
    assert by_name["Shared"]["children"][0]["filePaths"] == ["Shared/util.luau"]


def test_sourcemapper_promotes_subfolder_init(tmp_path):
    _write_script(tmp_path / "Pkg" / "init.luau", "return {}")
    _write_script(tmp_path / "Pkg" / "inner.luau", "return 1")
    out = sourcemapper.generate(tmp_path)
    assert out["scripts"] == 2
    tree = json.loads((tmp_path / "sourcemap.json").read_text(encoding="utf-8"))
    pkg = tree["children"][0]
    assert pkg["className"] == "ModuleScript"
    assert pkg["name"] == "Pkg"
    assert sorted(c["name"] for c in pkg["children"]) == ["inner"]


def test_sourcemapper_ignores_non_sources(tmp_path):
    _write_script(tmp_path / "a.luau", "x=1")
    (tmp_path / "notes.txt").write_text("hi", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    _write_script(tmp_path / ".git" / "hidden.luau", "x=2")
    out = sourcemapper.generate(tmp_path)
    assert out["scripts"] == 1


def test_sourcemap_cli_writes_file(tmp_path, capsys):
    from luaudit.cli import main
    _write_script(tmp_path / "m.luau", "return 1")
    rc = main(["sourcemap", str(tmp_path)])
    assert rc == 0
    assert "1 scripts" in capsys.readouterr().out
    assert (tmp_path / "sourcemap.json").is_file()


def test_sourcemap_output_is_discoverable_by_engine(tmp_path):
    """The generated file must satisfy runners' walk-up search by name."""
    from luaudit.runners import _find_sourcemap
    _write_script(tmp_path / "sub" / "x.luau", "return 1")
    sourcemapper.generate(tmp_path)
    assert _find_sourcemap(str(tmp_path / "sub")) == str(tmp_path / "sourcemap.json")


# ---------------------------------------------------------------------------
# hook nag: silent for external projects and deliberately-idle mirrors
# ---------------------------------------------------------------------------

@pytest.fixture()
def hook(tmp_path, monkeypatch):
    mod = _load_hook()
    monkeypatch.setattr(mod, "CACHE_DIR", tmp_path / "cache")
    return mod


def test_nag_silent_for_rojo_project(hook, tmp_path):
    (tmp_path / "default.project.json").write_text("{}", encoding="utf-8")
    f = tmp_path / "src" / "thing.luau"
    _write_script(f, "x=1")
    assert hook._plugin_nag(now=1_000_000.0, filepath=str(f)) == ""


def test_nag_silent_when_installed_mode_is_external(hook, monkeypatch, tmp_path):
    monkeypatch.setattr(hook, "_installed_mirror_idle", lambda: True)
    assert hook._plugin_nag(now=1_000_000.0) == ""
