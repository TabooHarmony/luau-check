"""Subprocess wrappers for luau-lsp analyze, selene, stylua, plus audit rules.

v2 changes: pure CLI semantics. check_files accepts file or directory paths,
prints nothing on clean output (hooks/agents rely on exit code + silence).
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from . import bootstrap
from .parsers import Diagnostic, merge_diagnostics, to_dict


def _run(cmd: list[str], cwd: str | None = None, timeout: int = 60,
         stdin_input: str | None = None) -> tuple[str, str, int]:
    try:
        proc = subprocess.run(
            cmd,
            input=stdin_input,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
        return proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired:
        return "", f"Command timed out after {timeout}s: {' '.join(cmd)}", -1
    except FileNotFoundError:
        return "", f"Binary not found: {cmd[0]}", -1


# ---------------------------------------------------------------------------
# Config discovery (walks up the tree)
# ---------------------------------------------------------------------------

def _find_config(start_dir: str, config_names: tuple[str, ...]) -> str | None:
    current = Path(start_dir).resolve()
    while True:
        for name in config_names:
            candidate = current / name
            if candidate.exists():
                return str(candidate)
        if current.parent == current:
            break
        current = current.parent
    return None


def _find_luaurc(start_dir: str) -> str | None:
    return _find_config(start_dir, (".luaurc",))


def _find_selene_toml(start_dir: str) -> str | None:
    return _find_config(start_dir, ("selene.toml", "selene.yml"))


def _find_stylua_toml(start_dir: str) -> str | None:
    return _find_config(start_dir, (".stylua.toml", "stylua.toml"))


def _normalize_paths(diagnostics: list[Diagnostic], base_dir: str) -> None:
    for d in diagnostics:
        if not os.path.isabs(d.file):
            d.file = os.path.normpath(os.path.join(base_dir, d.file))


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------

def run_luau_lsp(filepath: str, project_root: str | None = None,
                 cwd: str | None = None) -> list[Diagnostic]:
    paths = bootstrap.get_paths()
    luau_lsp = str(paths["luau_lsp"])
    defs = str(paths["defs"])
    luaurc = str(paths["luaurc"])

    cmd = [
        luau_lsp, "analyze",
        "--platform", "roblox",
        "--formatter", "plain",
        f"--definitions=@roblox={defs}",
    ]
    target_dir = project_root if project_root else os.path.dirname(filepath)
    project_luaurc = _find_luaurc(target_dir) if target_dir else None
    cmd.append(f"--base-luaurc={project_luaurc or luaurc}")
    cmd.append(filepath)

    stdout, stderr, exit_code = _run(cmd, timeout=60, cwd=cwd)
    if exit_code == -1 and not stdout:
        return [Diagnostic(
            file=filepath, line=1, column=1, end_line=None, end_column=None,
            code="InternalError", severity="error",
            message=f"luau-lsp failed: {stderr}", source="luau-lsp",
        )]
    return parse_luau_lsp_safe(stdout, stderr)


def parse_luau_lsp_safe(stdout: str, stderr: str) -> list[Diagnostic]:
    # import inside to avoid circular import of the module that wraps parsers
    from .parsers import parse_luau_lsp
    return parse_luau_lsp(stdout, stderr)


def run_selene(filepath: str, project_root: str | None = None,
               cwd: str | None = None) -> list[Diagnostic]:
    if not bootstrap.has_selene():
        return []
    paths = bootstrap.get_paths()
    selene = str(paths["selene"])
    selene_toml = str(paths["selene_toml"])

    cmd = [selene, "--display-style", "json", "--no-summary"]
    target_dir = project_root if project_root else os.path.dirname(filepath)
    project_selene = _find_selene_toml(target_dir) if target_dir else None
    cmd.append(f"--config={project_selene or selene_toml}")
    cmd.append(filepath)

    stdout, stderr, exit_code = _run(cmd, timeout=60, cwd=cwd)
    if exit_code == -1 and not stdout:
        return [Diagnostic(
            file=filepath, line=1, column=1, end_line=None, end_column=None,
            code="InternalError", severity="error",
            message=f"selene failed: {stderr}", source="selene",
        )]
    from .parsers import parse_selene
    return parse_selene(stdout)


def run_stylua_check(filepath: str, project_root: str | None = None,
                     cwd: str | None = None) -> list[Diagnostic]:
    if not bootstrap.has_stylua():
        return []
    paths = bootstrap.get_paths()
    stylua = str(paths["stylua"])

    cmd = [stylua, "--check"]
    target_dir = project_root if project_root else os.path.dirname(filepath)
    if target_dir:
        project_stylua = _find_stylua_toml(target_dir)
        if project_stylua:
            cmd.append(f"--config-path={project_stylua}")
    cmd.append(filepath)

    stdout, stderr, exit_code = _run(cmd, timeout=30, cwd=cwd)
    if exit_code == -1 and not stdout:
        return [Diagnostic(
            file=filepath, line=1, column=1, end_line=None, end_column=None,
            code="InternalError", severity="error",
            message=f"stylua failed: {stderr}", source="stylua",
        )]
    diagnostics: list[Diagnostic] = []
    if exit_code != 0:
        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith("Diff in "):
                diff_file = line.replace("Diff in ", "").rstrip(":")
                diagnostics.append(Diagnostic(
                    file=diff_file, line=1, column=1, end_line=None, end_column=None,
                    code="StyLuaFormat", severity="warning",
                    message="Code is not formatted (run luau-check format to fix)",
                    source="stylua",
                ))
    return diagnostics


# ---------------------------------------------------------------------------
# check_files: public entry used by CLI
# ---------------------------------------------------------------------------

def check_files(targets: list[str], cwd: str = ".") -> dict:
    """Check files or directories. Returns MCP-style diagnostics dict.

    If a target is a directory, walks it for .luau/.lua files. On a
    completely clean tree, returns summary total 0 (no diagnostics).
    """
    abs_targets: list[str] = []
    for t in targets:
        p = t if os.path.isabs(t) else os.path.join(cwd, t)
        abs_targets.append(os.path.abspath(p))

    files: list[str] = []
    for t in abs_targets:
        if os.path.isfile(t):
            files.append(t)
        elif os.path.isdir(t):
            for root, _, fs in os.walk(t):
                for f in fs:
                    if f.endswith((".luau", ".lua")):
                        files.append(os.path.join(root, f))

    if not files:
        return {"diagnostics": [], "summary": {"errors": 0, "warnings": 0, "total": 0},
                "note": "No .luau or .lua files found"}

    all_diags: list[Diagnostic] = []
    for f in files:
        project_root = os.path.dirname(f)
        luau_results = run_luau_lsp(f, project_root=project_root)
        selene_results = run_selene(f, project_root=project_root)
        stylua_results = run_stylua_check(f, project_root=project_root)
        for d in luau_results + selene_results + stylua_results:
            if not os.path.isabs(d.file):
                d.file = os.path.abspath(os.path.join(project_root, d.file))
        all_diags.extend(luau_results + selene_results + stylua_results)

    merged = merge_diagnostics(all_diags)
    return to_dict(merged)


# ---------------------------------------------------------------------------
# Deprecated/kept small: check_code via stdin (useful for agents)
# ---------------------------------------------------------------------------

def check_code(code: str, filename: str = "snippet.luau") -> dict:
    """Type-check a code string by writing a temp file and running checks."""
    if not bootstrap.is_ready():
        return {"error": bootstrap.last_error() or "setup incomplete"}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".luau", prefix="luau_lens_",
                                     delete=False, encoding="utf-8") as tmp:
        tmp.write(code)
        tmp_path = tmp.name
    try:
        luau_results = run_luau_lsp(tmp_path, project_root=os.path.dirname(tmp_path))
        selene_results = run_selene(tmp_path, project_root=os.path.dirname(tmp_path))
        stylua_results = run_stylua_check(tmp_path, project_root=os.path.dirname(tmp_path))
        for d in luau_results + selene_results + stylua_results:
            d.file = filename
        return to_dict(merge_diagnostics(luau_results, selene_results, stylua_results))
    finally:
        os.unlink(tmp_path)
