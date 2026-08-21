"""Tests for luaudit.plugin (studio mirror install/remove/status) + hook nag."""

from __future__ import annotations

import importlib.util
import sys
import zipfile
from pathlib import Path

import pytest

from luaudit import plugin

REPO = Path(__file__).resolve().parent.parent
PLUGIN_ENGINE = REPO / "plugins" / "luaudit" / "scripts" / "luaudit_hook.py"


def _load_hook():
    spec = importlib.util.spec_from_file_location("luaudit_hook_nag_test", PLUGIN_ENGINE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# schema key extraction
# ---------------------------------------------------------------------------

def test_bundled_artifact_resolves_and_carries_schema():
    p = plugin.bundled_plugin_path()
    assert p.is_file(), f"artifact not found at {p}"
    assert plugin._read_schema(p) == plugin.CURRENT_SCHEMA


def test_schema_regex_rejects_unreadable(tmp_path):
    junk = tmp_path / "junk.rbxmx"
    junk.write_text("not an artifact", encoding="utf-8")
    assert plugin._read_schema(junk) is None


def test_schema_key_matches_hook_engine():
    """The one shared contract: payload schema key. Drift fails here."""
    assert _load_hook().MIRROR_SCHEMA == plugin.CURRENT_SCHEMA


# ---------------------------------------------------------------------------
# install / remove against a redirected plugins dir
# ---------------------------------------------------------------------------

@pytest.fixture()
def fake_plugins_dir(tmp_path, monkeypatch):
    d = tmp_path / "Roblox" / "Plugins"
    monkeypatch.setattr(plugin, "plugins_dir", lambda: d)
    return d


def test_install_copy_is_byte_identical(fake_plugins_dir):
    out = plugin.install(yes=True)
    target = fake_plugins_dir / plugin.PLUGIN_FILENAME
    assert out["installed"] is True
    assert target.read_bytes() == plugin.bundled_plugin_path().read_bytes()


def test_install_idempotent_when_current(fake_plugins_dir):
    plugin.install(yes=True)
    again = plugin.install()  # no consent needed: nothing to write
    assert again["note"] == "already up to date"
    assert again["installed"] is True


def test_install_requires_consent_non_interactive(fake_plugins_dir):
    # stdin is not a tty under pytest => refusal, never a hang
    out = plugin.install()
    assert out["installed"] is False
    assert "--yes" in out["note"]
    assert not (fake_plugins_dir / plugin.PLUGIN_FILENAME).exists()


def test_install_survives_lying_tty_with_eof_stdin(fake_plugins_dir, monkeypatch):
    """Regression: some ssh ptys report isatty()==True even with redirected
    stdin, so input() raises EOFError mid-install. Must degrade to the
    clean refusal, never crash the hook/CLI."""
    import builtins

    class LyingStdin:
        def isatty(self):
            return True

        def read(self, *a):  # pragma: no cover
            raise OSError("stdin unreadable")

    monkeypatch.setattr(sys, "stdin", LyingStdin())

    def boom(prompt=""):
        raise EOFError("EOF when reading a line")
    monkeypatch.setattr(builtins, "input", boom)
    out = plugin.install()
    assert out["installed"] is False
    assert "--yes" in out["note"]


def test_status_detects_stale(fake_plugins_dir):
    plugin.install(yes=True)
    target = fake_plugins_dir / plugin.PLUGIN_FILENAME
    text = target.read_text(encoding="utf-8")
    target.write_text(text.replace(plugin.CURRENT_SCHEMA, "luaudit-mirror-v0"),
                      encoding="utf-8")
    st = plugin.status()
    assert st["up_to_date"] is False
    assert st["schema"] == "luaudit-mirror-v0"


def test_status_missing_plugin(fake_plugins_dir):
    st = plugin.status()
    assert st["installed"] is False
    assert st["up_to_date"] is False


def test_remove(fake_plugins_dir):
    plugin.install(yes=True)
    out = plugin.remove()
    assert out["removed"] is True
    assert not (fake_plugins_dir / plugin.PLUGIN_FILENAME).exists()
    # removing twice is fine
    assert plugin.remove()["removed"] is False


# ---------------------------------------------------------------------------
# hook-side nag behavior
# ---------------------------------------------------------------------------

@pytest.fixture()
def hook(tmp_path, monkeypatch):
    mod = _load_hook()
    monkeypatch.setattr(mod, "CACHE_DIR", tmp_path / "cache")
    return mod


def test_hook_nag_fires_once_per_interval(hook):
    t0 = 1_000_000.0
    first = hook._plugin_nag(now=t0)
    assert "[luaudit]" in first
    assert "luaudit plugin install" in first
    assert "\u2014" not in first  # no em dashes, per project rule
    # immediately after: suppressed
    assert hook._plugin_nag(now=t0 + 60) == ""
    # after the interval: fires again
    again = hook._plugin_nag(now=t0 + hook.NAG_INTERVAL + 60)
    assert "[luaudit]" in again


def test_hook_nag_silent_when_current(hook, monkeypatch):
    monkeypatch.setattr(hook, "_installed_mirror_ok", lambda: True)
    assert hook._plugin_nag(now=1_000_000.0) == ""


def test_hook_nag_never_raises(hook, monkeypatch):
    def boom():
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(hook, "_installed_mirror_ok", boom)
    assert hook._plugin_nag() == ""


# ---------------------------------------------------------------------------
# packaging guarantees
# ---------------------------------------------------------------------------

def test_pyproject_force_includes_artifact():
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert "force-include" in pyproject
    assert "plugin_data/luaudit-mirror.rbxmx" in pyproject


def test_built_wheel_carries_artifact(tmp_path):
    out = __import__("subprocess").run(
        [sys.executable, "-m", "pip", "wheel", str(REPO), "--no-deps",
         "-w", str(tmp_path)],
        capture_output=True, text=True, timeout=600,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    wheels = list(tmp_path.glob("luaudit-*.whl"))
    assert wheels, "no wheel produced"
    with zipfile.ZipFile(wheels[0]) as z:
        names = z.namelist()
        assert "luaudit/plugin_data/luaudit-mirror.rbxmx" in names
        embedded = z.read("luaudit/plugin_data/luaudit-mirror.rbxmx")
    assert embedded == plugin.bundled_plugin_path().read_bytes()


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------

def test_cli_plugin_roundtrip(fake_plugins_dir, capsys):
    from luaudit.cli import main
    assert main(["plugin", "install", "--yes"]) == 0
    assert main(["plugin", "status"]) == 0
    captured = capsys.readouterr().out
    assert "engine_version:" in captured
    assert "up_to_date: True" in captured
    assert main(["plugin", "remove"]) == 0


def test_cli_install_refuses_without_yes_non_interactive(fake_plugins_dir, capsys):
    from luaudit.cli import main
    rc = main(["plugin", "install"])
    assert rc == 2
    assert "--yes" in capsys.readouterr().out
