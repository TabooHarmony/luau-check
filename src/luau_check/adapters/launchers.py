"""Shared launcher helpers across harness adapters.

On Windows, the bash hook scripts cannot execute directly (no bash in
cmd/PowerShell unless git-bash is installed). The adapters write a `.cmd`
wrapper that dispatches to `bash.exe` (git-bash) on the `.sh` hook. This
module provides the cross-platform "write the hook launcher" logic used by
the three bash-hook adapters (codex, cursor, claude).
"""

from __future__ import annotations

import os
import platform
import stat
from pathlib import Path


def _git_bash() -> str | None:
    """Return the git-bash bash.exe path if present."""
    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Git" / "bin" / "bash.exe",
        Path.home() / "AppData" / "Local" / "Programs" / "Git" / "bin" / "bash.exe",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    # last resort: anything on PATH named bash.exe
    import shutil
    return shutil.which("bash")


def launcher_cmd_content(sh_path: Path) -> str:
    """Content of the .cmd wrapper that runs a bash hook via git-bash."""
    bash = _git_bash()
    if bash is None:
        raise RuntimeError(
            "git-bash not found on Windows; install Git for Windows "
            "(https://git-scm.com) so harness hooks can run bash scripts"
        )
    return f'@echo off\r\n"{bash}" "{sh_path}" %*\r\n'


def write_launcher(hook_script: str, dest: Path) -> Path:
    """Write hook_script to dest (POSIX) or a `.cmd` wrapper (Windows).

    Returns the path the harness should invoke (the .cmd on Windows).
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if platform.system() == "Windows":
        # Write the .sh, then a .cmd that runs it via git-bash.
        sh_path = dest.with_name(dest.name + ".sh")
        sh_path.write_text(hook_script, encoding="utf-8")
        cmd_path = dest.with_suffix(".cmd")
        cmd_path.write_text(launcher_cmd_content(sh_path), encoding="utf-8")
        return cmd_path
    # POSIX: write the script and mark it executable
    dest.write_text(hook_script, encoding="utf-8")
    dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return dest
