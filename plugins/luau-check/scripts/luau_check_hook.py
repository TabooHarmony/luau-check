#!/usr/bin/env python3
"""Standalone luau-check engine bundled inside the luau-check plugin.

Self-contained (stdlib only) so the plugin works after being copied into a
harness's plugin cache with no pip install. Shares the toolchain cache
(~/.luau-check) with the full luau-check CLI.

Two modes:
- hook (default): read a PostToolUse hook event JSON on stdin
  ({tool_name, tool_input:{file_path|file|path}}), check the edited Luau file,
  and print the harness contract
  {"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":...}}
  ONLY when there are errors/warnings. Clean => empty stdout, exit 0.
- check:  luau_check_hook.py check [--json] <paths...>
  Plain CLI semantics (text or JSON), exit non-zero on errors.
  This is the "minimal CLI": it lives inside the plugin, zero install.

Generated from the luau-check package so versions and URLs never drift.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

PLUGIN_VERSION = "3.0.0"

# ---------------------------------------------------------------------------
# Toolchain (mirror of luau_check.bootstrap)
# ---------------------------------------------------------------------------

CACHE_DIR = Path(os.environ.get("LUAU_CHECK_HOME", Path.home() / ".luau-check"))
BIN_DIR = CACHE_DIR / "bin"
DEFS_DIR = CACHE_DIR / "defs"
CONFIG_DIR = CACHE_DIR / "config"

DEFS_URL = "https://luau-lsp.pages.dev/type-definitions/globalTypes.d.luau"
DEFS_FILENAME = "globalTypes.d.luau"

LUAU_LSP_VERSION = "1.68.1"
SELENE_VERSION = "0.31.0"
STYLUA_VERSION = "2.5.2"
DEFS_MAX_AGE = 7 * 24 * 60 * 60


def _platform() -> tuple[str, str]:
    os_name = platform.system().lower()
    machine = platform.machine().lower()
    if os_name == "windows":
        return "windows", "x86_64"
    if os_name == "darwin":
        return ("macos", "arm64") if ("arm" in machine or "aarch" in machine) else ("macos", "x86_64")
    return ("linux", "arm64") if ("arm" in machine or "aarch" in machine) else ("linux", "x86_64")


def _urls() -> dict[str, str]:
    os_name, arch = _platform()
    base = {
        "luau-lsp": {
            ("windows", "x86_64"): f"https://github.com/JohnnyMorganz/luau-lsp/releases/download/{LUAU_LSP_VERSION}/luau-lsp-win64.zip",
            ("macos", "arm64"): f"https://github.com/JohnnyMorganz/luau-lsp/releases/download/{LUAU_LSP_VERSION}/luau-lsp-macos.zip",
            ("macos", "x86_64"): f"https://github.com/JohnnyMorganz/luau-lsp/releases/download/{LUAU_LSP_VERSION}/luau-lsp-macos.zip",
            ("linux", "x86_64"): f"https://github.com/JohnnyMorganz/luau-lsp/releases/download/{LUAU_LSP_VERSION}/luau-lsp-linux-x86_64.zip",
            ("linux", "arm64"): f"https://github.com/JohnnyMorganz/luau-lsp/releases/download/{LUAU_LSP_VERSION}/luau-lsp-linux-arm64.zip",
        },
        "selene": {
            ("windows", "x86_64"): f"https://github.com/Kampfkarren/selene/releases/download/{SELENE_VERSION}/selene-{SELENE_VERSION}-windows.zip",
            ("macos", "arm64"): f"https://github.com/Kampfkarren/selene/releases/download/{SELENE_VERSION}/selene-{SELENE_VERSION}-macos.zip",
            ("macos", "x86_64"): f"https://github.com/Kampfkarren/selene/releases/download/{SELENE_VERSION}/selene-{SELENE_VERSION}-macos.zip",
            ("linux", "x86_64"): f"https://github.com/Kampfkarren/selene/releases/download/{SELENE_VERSION}/selene-{SELENE_VERSION}-linux.zip",
            ("linux", "arm64"): f"https://github.com/Kampfkarren/selene/releases/download/{SELENE_VERSION}/selene-{SELENE_VERSION}-linux.zip",
        },
        "stylua": {
            ("windows", "x86_64"): f"https://github.com/JohnnyMorganz/StyLua/releases/download/v{STYLUA_VERSION}/stylua-windows-x86_64.zip",
            ("macos", "arm64"): f"https://github.com/JohnnyMorganz/StyLua/releases/download/v{STYLUA_VERSION}/stylua-macos-aarch64.zip",
            ("macos", "x86_64"): f"https://github.com/JohnnyMorganz/StyLua/releases/download/v{STYLUA_VERSION}/stylua-macos-x86_64.zip",
            ("linux", "x86_64"): f"https://github.com/JohnnyMorganz/StyLua/releases/download/v{STYLUA_VERSION}/stylua-linux-x86_64.zip",
            ("linux", "arm64"): f"https://github.com/JohnnyMorganz/StyLua/releases/download/v{STYLUA_VERSION}/stylua-linux-aarch64.zip",
        },
    }
    return {k: v[(os_name, arch)] for k, v in base.items()}


def _exe(name: str) -> str:
    return f"{name}.exe" if platform.system() == "Windows" else name


def _download(url: str, dest: Path, timeout: int = 60, min_size: int = 1024) -> None:
    """Download URL to dest atomically (tmp + os.replace).

    min_size is a cheap integrity sanity check: a successful download smaller
    than min_size bytes is treated as a failure (error page / empty body),
    since real tool binaries and defs are always >1KB.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "luau-check/plugin"})
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + f".tmp-{os.getpid()}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            with open(tmp, "wb") as f:
                size = 0
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    size += len(chunk)
        if size < min_size:
            raise ValueError(f"download too small ({size} bytes < {min_size})")
        os.replace(tmp, dest)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _download_and_extract_zip(url: str, dest_dir: Path) -> None:
    """Download a zip and extract it atomically into dest_dir.

    Extraction goes into a temp sibling dir first, then entries are moved into
    dest_dir, so a crash or concurrent second caller never sees a half-written
    tree. Individual file moves are atomic on the same filesystem.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    stage = dest_dir.parent / f".stage-{dest_dir.name}-{os.getpid()}"
    try:
        _download(url, tmp_path)
        with zipfile.ZipFile(tmp_path) as zf:
            # Reject zip-slip (members escaping the target dir) outright.
            for info in zf.infolist():
                name = info.filename
                # Reject zip-slip outright: absolute members, `..` traversal,
                # and backslash separators (zip spec uses `/`; a `\` member
                # name is either a Windows path-escape attempt or malformed).
                if (
                    name.startswith(("/", "\\\\"))
                    or ".." in Path(name).parts
                    or "\\" in name
                ):
                    raise ValueError(f"unsafe zip member: {name!r}")
            zf.extractall(stage)
        if not stage.exists():
            stage.mkdir(parents=True)
        for p in stage.iterdir():
            target = dest_dir / p.name
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            os.replace(p, target)
        if platform.system() != "Windows":
            for p in dest_dir.iterdir():
                if p.is_file() and not p.suffix:
                    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    finally:
        tmp_path.unlink(missing_ok=True)
        shutil.rmtree(stage, ignore_errors=True)


def ensure_tools() -> bool:
    """Download tools that are missing. Returns True if usable, False if any
    hard requirement (luau-lsp or defs) is missing."""
    urls = _urls()
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    DEFS_DIR.mkdir(parents=True, exist_ok=True)

    luau_lsp = BIN_DIR / _exe("luau-lsp")
    if not luau_lsp.exists():
        try:
            _download_and_extract_zip(urls["luau-lsp"], BIN_DIR)
        except Exception:
            return False
        if not luau_lsp.exists():
            return False

    selene = BIN_DIR / _exe("selene")
    if not selene.exists():
        try:
            _download_and_extract_zip(urls["selene"], BIN_DIR)
        except Exception:
            pass

    stylua = BIN_DIR / _exe("stylua")
    if not stylua.exists():
        try:
            _download_and_extract_zip(urls["stylua"], BIN_DIR)
        except Exception:
            pass

    defs = DEFS_DIR / DEFS_FILENAME
    defs_ok = defs.exists() and defs.stat().st_size > 0
    need_defs = not defs_ok
    if defs_ok and time.time() - defs.stat().st_mtime > DEFS_MAX_AGE:
        need_defs = True
    if need_defs:
        try:
            _download(defs_url(), defs)
            if not defs.exists() or defs.stat().st_size == 0:
                return False
        except Exception:
            return False

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    selene_toml = CONFIG_DIR / "selene.toml"
    if not selene_toml.exists():
        selene_toml.write_text('std = "roblox"\n', encoding="utf-8")
    luaurc = CONFIG_DIR / ".luaurc"
    if not luaurc.exists():
        luaurc.write_text('{\n  "languageMode": "strict"\n}\n', encoding="utf-8")
    return True


def defs_url() -> str:
    return DEFS_URL


def _find_sourcemap(project_root: str) -> str | None:
    """Walk up from project_root for a usable sourcemap.

    Returns the path to a sourcemap.json if one exists:
    - an existing sourcemap.json is used as-is
    - a default.project.json (rojo project) is turned into a sourcemap by
      running rojo if installed
    Returns None if neither is found (per-file fallback mode).

    The sourcemap filePaths are relative to the sourcemap file's directory
    (rojo emits paths relative to the project file), so callers must run
    luau-lsp with cwd set to that directory.
    """
    current = Path(project_root).resolve()
    while True:
        sm = current / "sourcemap.json"
        if sm.exists():
            return str(sm)
        proj = current / "default.project.json"
        if proj.exists():
            rojo = shutil.which("rojo")
            if rojo:
                try:
                    out = subprocess.run(
                        [rojo, "sourcemap", "--output", "sourcemap.json", str(proj)],
                        capture_output=True, text=True, timeout=60, cwd=str(current),
                    )
                    if out.returncode == 0 and sm.exists():
                        return str(sm)
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    pass
            # rojo missing: fall through to check parents, then None
        if current.parent == current:
            break
        current = current.parent
    return None


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


# ---------------------------------------------------------------------------
# Parsers (mirror of luau_check.parsers)
# ---------------------------------------------------------------------------

_LUAU_LSP_RE = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+):(?P<col>\d+)(?:-(?P<endcol>\d+))?"
    r":\s+\(W0\)\s+(?P<category>\w+):\s+(?P<message>.+)$"
)
# With --sourcemap, plain-formatter lines gain a virtual-instance path after
# the file: `path [game/ServerScriptService/Utils]:8:27-29: (W0) TypeError: ...`
_LUAU_LSP_SOURCEMAP_RE = re.compile(
    r"^(?P<file>.+?) \[[^\]]+\]:(?P<line>\d+):(?P<col>\d+)(?:-(?P<endcol>\d+))?"
    r":\s+\(W0\)\s+(?P<category>\w+):\s+(?P<message>.+)$"
)
_SKIP_PREFIXES = ("[INFO]", "[WARN]", "[DEBUG]", "WARNING:", "Analyzing")
_SELENE_SEVERITY = {"Error": "error", "Warning": "warning"}


def _parse_luau_lsp(output: str, stderr: str = "") -> list[dict]:
    diags: list[dict] = []
    for line in (output + "\n" + stderr).splitlines():
        line = line.strip()
        if not line or any(line.startswith(p) for p in _SKIP_PREFIXES):
            continue
        m = _LUAU_LSP_SOURCEMAP_RE.match(line) or _LUAU_LSP_RE.match(line)
        if not m:
            continue
        category = m.group("category")
        diags.append({
            "file": m.group("file"),
            "line": int(m.group("line")),
            "column": int(m.group("col")),
            "code": category,
            "severity": "error" if "Error" in category else "warning",
            "message": m.group("message"),
            "source": "luau-lsp",
        })
    return diags


def _parse_selene(output: str) -> list[dict]:
    diags: list[dict] = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("Results:"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        label = obj.get("primary_label", {})
        span = label.get("span", {})
        diags.append({
            "file": label.get("filename", "unknown"),
            "line": span.get("start_line", 0) + 1,
            "column": span.get("start_column", 0) + 1,
            "code": obj.get("code", "unknown"),
            "severity": _SELENE_SEVERITY.get(obj.get("severity", ""), "warning"),
            "message": obj.get("message", ""),
            "source": "selene",
        })
    return diags


def _merge(diags: list[dict]) -> list[dict]:
    seen: set[tuple[str, int, int, str]] = set()
    merged: list[dict] = []
    for d in diags:
        key = (d["file"], d["line"], d["column"], d["code"])
        if key not in seen:
            seen.add(key)
            merged.append(d)
    merged.sort(key=lambda d: (d["file"], d["line"], d["column"]))
    return merged


# ---------------------------------------------------------------------------
# Runners (mirror of luau_check.runners)
# ---------------------------------------------------------------------------

def _run(cmd: list[str], cwd: str | None = None, timeout: int = 60) -> tuple[str, str, int]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired:
        return "", f"Command timed out after {timeout}s", -1
    except FileNotFoundError:
        return "", f"Binary not found: {cmd[0]}", -1


def _ensure_utf8(filepath: str) -> tuple[str, str | None]:
    """Return (path_to_check, temp_to_cleanup). If the file is UTF-16 (BOM
    sniff), write a UTF-8 transcode to a temp file next to it so the native
    tools (luau-lsp/selene/stylua) can parse it. PowerShell's Set-Content
    writes UTF-16LE by default, so Windows-checked files are often UTF-16."""
    with open(filepath, "rb") as f:
        head = f.read(4)
    if head.startswith(b"\xff\xfe") or head.startswith(b"\xfe\xff"):
        data = open(filepath, "rb").read()
        enc = "utf-16-le" if head.startswith(b"\xff\xfe") else "utf-16-be"
        text = data.decode(enc, errors="replace").lstrip("\ufeff")
        tmp = filepath + ".lc-utf8"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        return tmp, tmp
    return filepath, None


def check_file(filepath: str) -> list[dict]:
    """Run all three tools on one file, return merged diagnostics with
    absolute paths. A broken toolchain surfaces as an InternalError diagnostic
    (never silently 'clean')."""
    ensure_tools()
    # NOTE: no early return on ensure_tools() failure. If luau-lsp/defs are
    # missing, the InternalError branch below reports it instead of hiding it.

    luau_lsp = BIN_DIR / _exe("luau-lsp")
    selene = BIN_DIR / _exe("selene")
    stylua = BIN_DIR / _exe("stylua")
    defs = DEFS_DIR / DEFS_FILENAME
    base_luaurc = CONFIG_DIR / ".luaurc"
    base_selene = CONFIG_DIR / "selene.toml"

    project_root = os.path.dirname(filepath)
    all_diags: list[dict] = []

    # Transcode UTF-16 sources so the native tools can consume them.
    check_target, tmp_target = _ensure_utf8(filepath)
    check_target = check_target or filepath

    if not luau_lsp.exists() or not defs.exists():
        all_diags.append({
            "file": filepath, "line": 1, "column": 1, "code": "InternalError",
            "severity": "error", "source": "luau-check",
            "message": "luau-check toolchain missing (luau-lsp or defs); run the luau-check CLI once to bootstrap",
        })
        return all_diags

    project_luaurc = _find_config(project_root, (".luaurc",))
    sourcemap = _find_sourcemap(project_root)
    analyze_cwd = os.path.dirname(sourcemap) if sourcemap else None
    cmd = [
        str(luau_lsp), "analyze", "--platform", "roblox", "--formatter", "plain",
        f"--definitions=@roblox={defs}", f"--base-luaurc={project_luaurc or base_luaurc}",
    ]
    if sourcemap:
        cmd.append(f"--sourcemap={sourcemap}")
    cmd.append(check_target)
    stdout, stderr, code = _run(cmd, cwd=analyze_cwd, timeout=60)
    if (code == -1 and not stdout) or (code != 0 and not stdout.strip()):
        all_diags.append({
            "file": filepath, "line": 1, "column": 1, "code": "InternalError",
            "severity": "error", "source": "luau-lsp",
            "message": f"luau-lsp failed (exit {code}): {stderr.strip() or 'no output'}",
        })
    else:
        all_diags.extend(_parse_luau_lsp(stdout, stderr))

    if selene.exists():
        project_selene = _find_config(project_root, ("selene.toml", "selene.yml"))
        cmd = [str(selene), "--display-style", "json", "--no-summary",
               f"--config={project_selene or base_selene}", check_target]
        stdout2, stderr2, code2 = _run(cmd, timeout=60)
        if (code2 == -1 and not stdout2) or (code2 != 0 and not stdout2.strip()):
            all_diags.append({
                "file": filepath, "line": 1, "column": 1, "code": "InternalError",
                "severity": "error", "source": "selene",
                "message": f"selene failed (exit {code2}): {stderr2.strip() or 'no output'}",
            })
        else:
            all_diags.extend(_parse_selene(stdout2))

    if stylua.exists():
        cmd = [str(stylua), "--check"]
        project_stylua = _find_config(project_root, (".stylua.toml", "stylua.toml"))
        if project_stylua:
            cmd.append(f"--config-path={project_stylua}")
        cmd.append(check_target)
        stdout3, stderr3, code3 = _run(cmd, timeout=30)
        if code3 != 0:
            if code3 == -1 and not stdout3:
                all_diags.append({
                    "file": filepath, "line": 1, "column": 1, "code": "InternalError",
                    "severity": "error", "source": "stylua", "message": f"stylua failed: {stderr3}",
                })
            else:
                for line in stdout3.splitlines():
                    line = line.strip()
                    if line.startswith("Diff in "):
                        diff_file = line.replace("Diff in ", "").rstrip(":")
                        all_diags.append({
                            "file": diff_file, "line": 1, "column": 1,
                            "code": "StyLuaFormat", "severity": "warning",
                            "source": "stylua",
                            "message": "Code is not formatted",
                        })

    result: list[dict] = []
    resolve_base = analyze_cwd or project_root
    for d in all_diags:
        p = d["file"]
        if not os.path.isabs(p):
            # On POSIX hosts, luau-lsp can still emit Windows-style absolute
            # paths (c:/proj/...) when the sourcemap came from a Windows
            # workspace. Treat a drive-letter prefix as absolute too.
            if re.match(r"^[A-Za-z]:[\\/]", p):
                p = os.path.normpath(p)
            else:
                p = os.path.abspath(os.path.join(resolve_base, p))
        d["file"] = p
        result.append(d)
    if tmp_target:
        try:
            os.unlink(tmp_target)
        except OSError:
            pass
    return _merge(result)


def check_paths(paths: list[str], cwd: str = ".") -> dict:
    """CLI-mode check: files or directories. Returns diagnostics dict."""
    files: list[str] = []
    missing: list[str] = []
    for t in paths:
        p = t if os.path.isabs(t) else os.path.join(cwd, t)
        p = os.path.abspath(p)
        if os.path.isfile(p):
            files.append(p)
        elif os.path.isdir(p):
            for root, _, fs in os.walk(p):
                for f in fs:
                    if f.endswith((".luau", ".lua")):
                        files.append(os.path.join(root, f))
        else:
            missing.append(p)
    if missing:
        diags = [{
            "file": t, "line": 0, "column": 0, "code": "NoSuchFile",
            "severity": "error", "source": "luau-check",
            "message": f"path does not exist: {t}",
        } for t in missing]
        return _result(diags)
    if not files:
        return _result([])
    all_diags: list[dict] = []
    for f in files:
        all_diags.extend(check_file(f))
    return _result(_merge(all_diags))


def _result(diags: list[dict]) -> dict:
    errors = sum(1 for d in diags if d["severity"] == "error")
    warnings = sum(1 for d in diags if d["severity"] == "warning")
    return {"diagnostics": diags, "summary": {"errors": errors, "warnings": warnings, "total": len(diags)}}


# ---------------------------------------------------------------------------
# Hook mode
# ---------------------------------------------------------------------------

def _hook_event_file(event: dict) -> str | None:
    """Extract the written Luau file from a harness hook event.

    Claude: Write|Edit|MultiEdit carry tool_input.file_path.
    Codex:  writes arrive as Bash commands. Only shell write-redirections
            (`> path`, `>> path`, `sed -i ... path`) count; stderr
            redirections (`2>`) and read-only commands are NOT writes.
    """
    tool_name = event.get("tool_name", "")
    ti = event.get("tool_input") or {}
    if isinstance(ti, str):
        try:
            ti = json.loads(ti)
        except json.JSONDecodeError:
            ti = {}
    if not isinstance(ti, dict):
        return None

    # Claude tools: file path is a first-class field.
    if not tool_name or re.search(r"^(Write|Edit|MultiEdit)$", tool_name):
        fp = ti.get("file_path") or ti.get("file") or ti.get("path")
        return fp if isinstance(fp, str) else None

    # Codex: file writes arrive as Bash commands (printf > x.luau, cat > x.luau,
    # sed -i ... x.luau). Pull the written Luau path out of the command text.
    if tool_name == "Bash":
        cmd = ti.get("command")
        if not isinstance(cmd, str):
            return None

        def _is_luau(p: str) -> bool:
            return p.endswith((".luau", ".lua"))

        # 1. shell write redirections: `> path` / `>> path` / `>| path`, optionally
        #    quoted. Exclude fd-number redirections (`2> err`, `1> out`) but
        #    KEEP all-stream `&>` (writes stdout+stderr to the target).
        for m in re.finditer(r"(?<![0-9])(?:>>|>\||>)\s*('(?:[^']*)'|\"(?:[^\"]*)\"|\S+)", cmd):
            raw = m.group(1)
            if raw.startswith(("'", '"')) and len(raw) >= 2:
                p = raw[1:-1]
            else:
                p = raw
            if _is_luau(p):
                return p
        # 2. sed -i ... path (in-place edit): the guard is mandatory BEFORE the
        #    path; skip the sed script/flag tokens so `'s/foo.lua/bar/'` is not
        #    mistaken for the target; a bare `.lua` token elsewhere is a read.
        for m in re.finditer(
            r"\bsed\s+-i(?:\S*)\s+(?:(?:'[^']*'|\"[^\"]*\"|s/.*?/[^/]*/[a-z]*)\s+)*([^\s;|&]+\.(?:luau|lua))\b",
            cmd,
        ):
            p = m.group(1).strip("'\"")
            if p and _is_luau(p):
                return p
        return None

    return None


def run_hook() -> int:
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    filepath = _hook_event_file(event)
    if not filepath or not filepath.endswith((".luau", ".lua")):
        return 0
    if not os.path.isfile(filepath):
        return 0

    result = check_paths([filepath], cwd=os.getcwd())
    diags = [d for d in result["diagnostics"] if d["severity"] in ("error", "warning")]
    if not diags:
        return 0  # clean => silent, the documented contract
    lines = [
        f"{d['file']}:{d['line']}:{d['column']}: [{d['severity'].upper()}] {d['code']} {d['message']}"
        for d in diags
    ]
    ctx = "luau-check diagnostics:\n" + "\n".join(lines)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": ctx,
        }
    }))
    return 0


# ---------------------------------------------------------------------------
# CLI mode
# ---------------------------------------------------------------------------

def run_cli(argv: list[str]) -> int:
    args = list(argv)
    as_json = False
    if args and args[0] == "--json":
        as_json = True
        args = args[1:]
    if not args:
        print("usage: luau_check_hook.py check [--json] <file|dir> ...", file=sys.stderr)
        return 2
    result = check_paths(args, cwd=os.getcwd())
    if as_json:
        print(json.dumps(result, indent=2))
    else:
        for d in result["diagnostics"]:
            print(f"{d['file']}:{d['line']}:{d['column']}: {d['severity'].upper()} [{d['source']}/{d['code']}] {d['message']}")
        s = result["summary"]
        if s["total"]:
            print(f"summary: {s['errors']} errors, {s['warnings']} warnings, {s['total']} total")
    return 1 if result["summary"]["errors"] > 0 else 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "check":
        return run_cli(argv[1:])
    if argv and argv[0] in ("--help", "-h", "help"):
        print("luau-check plugin engine: hook mode (stdin event) or 'check [--json] <paths>'")
        return 0
    return run_hook()


if __name__ == "__main__":
    sys.exit(main())
