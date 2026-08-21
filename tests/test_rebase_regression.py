"""Regression: relative input paths must never produce doubled output paths.

Bug history: check_files used to pass the caller's path to the tools
verbatim. luau-lsp echoes whatever it is given, so a relative input came
back as a relative diagnostic, which the rebaser then joined onto
project_root again -> .../studio/plugins/luaudit/studio/luaudit-mirror.luau.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from luaudit import bootstrap  # noqa: E402
from luaudit.runners import check_files  # noqa: E402


def _toolchain_available() -> bool:
    try:
        bootstrap.ensure_tools()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _toolchain_available(), reason="toolchain unavailable")
def test_relative_input_never_doubles_path(tmp_path):
    target = REPO / "plugins" / "luaudit" / "studio" / "luaudit-mirror.luau"
    if not target.exists():
        pytest.skip("mirror file missing")
    # Deliberately relative, from an unrelated cwd.
    rel = os.path.relpath(target, tmp_path)
    result = check_files([rel], cwd=str(tmp_path))
    for d in result["diagnostics"]:
        assert str(tmp_path) not in d["file"], f"doubled path leaked: {d['file']}"
        assert os.path.isabs(d["file"]), f"non-absolute path leaked: {d['file']}"


@pytest.mark.skipif(not _toolchain_available(), reason="toolchain unavailable")
def test_mirror_self_check_is_clean():
    """The Studio mirror must pass luaudit's own product path."""
    target = REPO / "plugins" / "luaudit" / "studio" / "luaudit-mirror.luau"
    if not target.exists():
        pytest.skip("mirror file missing")
    result = check_files([str(target)])
    errors = [d for d in result["diagnostics"] if d.get("severity") == "error"]
    assert errors == [], f"mirror has type errors: {errors}"
