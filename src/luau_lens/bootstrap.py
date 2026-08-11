"""Toolchain bootstrap for luau-lens v2.

Downloads luau-lsp, selene, stylua, and Roblox type definitions on first run
into ~/.luau-lens/, with retry on failure. The v2 CLI calls this lazily when
a command needs the toolchain; the happy path stays silent so agent output
stays clean.
"""

from __future__ import annotations

import os
import platform
import shutil
import stat
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

CACHE_DIR = Path.home() / ".luau-lens"
BIN_DIR = CACHE_DIR / "bin"
DEFS_DIR = CACHE_DIR / "defs"
CONFIG_DIR = CACHE_DIR / "config"

DEFS_URL = "https://luau-lsp.pages.dev/type-definitions/globalTypes.d.luau"
DEFS_FILENAME = "globalTypes.d.luau"

LUAU_LSP_VERSION = "1.68.1"
SELENE_VERSION = "0.31.0"
STYLUA_VERSION = "2.5.2"

DEFS_MAX_AGE = 7 * 24 * 60 * 60  # 7 days

_ready = False
_last_error: str | None = None


# ---------------------------------------------------------------------------
# Platform
# ---------------------------------------------------------------------------

def _get_platform() -> tuple[str, str]:
    os_name = platform.system().lower()
    machine = platform.machine().lower()
    if os_name == "windows":
        return "windows", "x86_64"
    if os_name == "darwin":
        return ("macos", "arm64") if ("arm" in machine or "aarch" in machine) else ("macos", "x86_64")
    return ("linux", "arm64") if ("arm" in machine or "aarch" in machine) else ("linux", "x86_64")


def _get_urls() -> dict[str, str]:
    os_name, arch = _get_platform()
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


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

def _download(url: str, dest: Path, timeout: int = 60) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "luau-lens/bootstrap"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)


def _download_and_extract_zip(url: str, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        _download(url, tmp_path)
        with zipfile.ZipFile(tmp_path) as zf:
            zf.extractall(dest_dir)
        if platform.system() != "Windows":
            for p in dest_dir.iterdir():
                if p.is_file() and not p.suffix:
                    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    finally:
        tmp_path.unlink(missing_ok=True)


def _download_file(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    _download(url, dest)


# ---------------------------------------------------------------------------
# Config generation
# ---------------------------------------------------------------------------

SELENE_TOML = 'std = "roblox"\n'
LUAURC = '{\n  "languageMode": "strict"\n}\n'


def _write_configs() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    selene_toml = CONFIG_DIR / "selene.toml"
    luaurc = CONFIG_DIR / ".luaurc"
    if not selene_toml.exists():
        selene_toml.write_text(SELENE_TOML)
    if not luaurc.exists():
        luaurc.write_text(LUAURC)


def init_configs(directory: Path) -> list[str]:
    """Write project configs into directory. Returns names written."""
    directory.mkdir(parents=True, exist_ok=True)
    wrote: list[str] = []
    selene_toml = directory / "selene.toml"
    luaurc = directory / ".luaurc"
    if not selene_toml.exists():
        selene_toml.write_text(SELENE_TOML)
        wrote.append("selene.toml")
    if not luaurc.exists():
        luaurc.write_text(LUAURC)
        wrote.append(".luaurc")
    return wrote


def format_files(paths: list[str], cwd: str = ".") -> list[str]:
    """Format files with stylua. Returns paths that changed."""
    paths = [p if os.path.isabs(p) else os.path.join(cwd, p) for p in paths]
    stylua = BIN_DIR / _exe("stylua")
    if not stylua.exists():
        return []
    changed: list[str] = []
    for p in paths:
        if not os.path.isfile(p):
            continue
        proc = __import__("subprocess").run(
            [str(stylua), p],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode == 0:
            changed.append(p)
    return changed


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_ready() -> bool:
    return _ready


def last_error() -> str | None:
    return _last_error


def ensure_tools() -> None:
    """Download all required tools if not present. Retry on failure."""
    global _ready, _last_error
    if _ready:
        return
    _last_error = None

    urls = _get_urls()
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    DEFS_DIR.mkdir(parents=True, exist_ok=True)

    luau_lsp_path = BIN_DIR / _exe("luau-lsp")
    if not luau_lsp_path.exists():
        try:
            _download_and_extract_zip(urls["luau-lsp"], BIN_DIR)
        except Exception as e:
            _last_error = f"Failed to download luau-lsp: {e}"
            print(f"[luau-lens] ERROR: {_last_error}", file=sys.stderr)
            return
        if not luau_lsp_path.exists():
            _last_error = "luau-lsp binary not found after extraction"
            print(f"[luau-lens] ERROR: {_last_error}", file=sys.stderr)
            return

    selene_path = BIN_DIR / _exe("selene")
    if not selene_path.exists():
        try:
            _download_and_extract_zip(urls["selene"], BIN_DIR)
        except Exception as e:
            print(f"[luau-lens] WARNING: selene download failed: {e}, linting skipped", file=sys.stderr)
        else:
            if not selene_path.exists():
                print("[luau-lens] WARNING: selene binary missing, linting skipped", file=sys.stderr)

    stylua_path = BIN_DIR / _exe("stylua")
    if not stylua_path.exists():
        try:
            _download_and_extract_zip(urls["stylua"], BIN_DIR)
        except Exception as e:
            print(f"[luau-lens] WARNING: stylua download failed: {e}, formatting skipped", file=sys.stderr)
        else:
            if not stylua_path.exists():
                print("[luau-lens] WARNING: stylua binary missing, formatting skipped", file=sys.stderr)

    defs_path = DEFS_DIR / DEFS_FILENAME
    need_defs = not defs_path.exists()
    if defs_path.exists():
        age = time.time() - defs_path.stat().st_mtime
        if age > DEFS_MAX_AGE:
            need_defs = True
            print("[luau-lens] refreshing Roblox type definitions (stale)...", file=sys.stderr)
    if need_defs:
        try:
            _download_file(DEFS_URL, defs_path)
        except Exception as e:
            _last_error = f"Failed to download type definitions: {e}"
            print(f"[luau-lens] ERROR: {_last_error}", file=sys.stderr)
            return

    _write_configs()

    os_name, arch = _get_platform()
    if os_name == "linux" and arch == "arm64" and selene_path.exists():
        print("[luau-lens] WARNING: selene has no native Linux arm64 build; linting may not work", file=sys.stderr)

    _ready = True
    print("[luau-lens] ready", file=sys.stderr)


def get_paths() -> dict[str, Path]:
    return {
        "luau_lsp": BIN_DIR / _exe("luau-lsp"),
        "selene": BIN_DIR / _exe("selene"),
        "stylua": BIN_DIR / _exe("stylua"),
        "defs": DEFS_DIR / DEFS_FILENAME,
        "selene_toml": CONFIG_DIR / "selene.toml",
        "luaurc": CONFIG_DIR / ".luaurc",
    }


def has_selene() -> bool:
    return (BIN_DIR / _exe("selene")).exists()


def has_stylua() -> bool:
    return (BIN_DIR / _exe("stylua")).exists()


def install_cli_binary(dest_dir: Path) -> Path | None:
    """Install the luau-lens CLI as a standalone executable.

    Ships a launcher script that re-runs the installed luau-lens package.
    Used by agent adapters so the agent can invoke the tool even when the
    package isn't on PATH. Returns the launcher path.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    launcher = dest_dir / (_exe("luau-lens"))
    if launcher.exists():
        return launcher
    # Locate this package's console script if installed (uvx/pipx style)
    # Fallback: write a launcher that invokes `python -m luau_lens.cli`.
    here = Path(__file__).resolve().parent
    launcher.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"sys.path.insert(0, {str(here.parent)!r})\n"
        "from luau_lens.cli import main\n"
        "sys.exit(main())\n",
        encoding="utf-8",
    )
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return launcher
