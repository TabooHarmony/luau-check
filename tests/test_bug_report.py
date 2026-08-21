"""Failure reporting: ~/.luaudit/luaudit.log + `luaudit doctor --bug-report`.

The contract: when luaudit fails on a user's machine there is a single
paste-ready artifact (`luaudit doctor --bug-report`) backed by an
append-only, self-truncating log that records every bootstrap and tool
failure from BOTH engines (package bootstrap and plugin hook).
"""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "plugins" / "luaudit" / "scripts" / "luaudit_hook.py"


def _load_engine():
    spec = importlib.util.spec_from_file_location("luaudit_hook_bugreport", ENGINE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def boot(tmp_path, monkeypatch):
    import luaudit.bootstrap as b
    monkeypatch.setattr(b, "CACHE_DIR", tmp_path)
    return b


def test_log_filename_matches_across_engines():
    pkg = (ROOT / "src" / "luaudit" / "bootstrap.py").read_text(encoding="utf-8")
    plug = ENGINE.read_text(encoding="utf-8")
    assert 'LOG_FILENAME = "luaudit.log"' in pkg
    assert 'LOG_FILENAME = "luaudit.log"' in plug


def test_log_event_appends_timestamped_lines(boot, tmp_path):
    boot.log_event("ERROR probe one")
    boot.log_event("WARNING probe two")
    data = (tmp_path / "luaudit.log").read_text(encoding="utf-8")
    assert "ERROR probe one" in data
    assert "WARNING probe two" in data
    for line in data.splitlines():
        assert len(line.split(" ", 1)[0]) == len("YYYY-MM-DDTHH:MM:SS")


def test_log_event_truncates_when_over_cap(boot, tmp_path):
    payload = "x" * 1024
    for i in range(600):  # ~600KB total, over the 512KB cap
        boot.log_event(f"{i} {payload}")
    path = tmp_path / "luaudit.log"
    assert path.stat().st_size <= boot.LOG_MAX_BYTES + 4096
    # newest entries survive rotation
    assert "599" in path.read_text(encoding="utf-8")


def test_log_event_never_raises_on_unwritable_cache(boot, tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    import luaudit.bootstrap as b
    monkey_target = blocker  # CACHE_DIR points at a regular file -> mkdir fails
    old = b.CACHE_DIR
    b.CACHE_DIR = monkey_target
    try:
        boot.log_event("must not explode")
    finally:
        b.CACHE_DIR = old


def test_read_log_tail_missing_file(boot, tmp_path):
    out = boot.read_log_tail()
    assert "no luaudit.log" in out


def test_plugin_engine_writes_the_same_log_file(tmp_path):
    mod = _load_engine()
    old = mod.CACHE_DIR
    mod.CACHE_DIR = tmp_path
    try:
        mod._log_event("ERROR plugin engine probe")
    finally:
        mod.CACHE_DIR = old
    data = (tmp_path / "luaudit.log").read_text(encoding="utf-8")
    assert "ERROR plugin engine probe" in data


def test_doctor_bug_report_is_paste_ready(boot, tmp_path, monkeypatch, capsys):
    import luaudit.cli as cli
    monkeypatch.setattr(cli.bootstrap, "ensure_tools", lambda: None)
    boot.log_event("ERROR historical failure for report")
    rc = cli.main(["doctor", "--bug-report"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "bug report" in out
    assert f"luaudit:" in out
    assert "python:" in out
    assert "last_error:" in out
    assert "historical failure for report" in out
