"""Studio mirror plugin management.

The Studio mirror ships inside the wheel as
``luaudit/plugin_data/luaudit-mirror.rbxmx``. ``luaudit plugin install``
copies it into the user's Roblox plugins folder; ``luaudit plugin remove``
deletes it; ``luaudit plugin status`` reports what is installed and whether
it matches this engine build.

The contract between engine and plugin is the payload schema key
(``luaudit-mirror-v1``) embedded in the artifact's Source. The key appears
exactly once per artifact (inside the CDATA-wrapped source), so a bare
regex over the raw XML reads it from both bundled and installed files.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

from .version import __version__

PLUGIN_FILENAME = "luaudit-mirror.rbxmx"
CURRENT_SCHEMA = "luaudit-mirror-v1"

_SCHEMA_RE = re.compile(r"(luaudit-mirror-v\d+)")


def bundled_plugin_path() -> Path:
    # Wheels carry the artifact under plugin_data/ (see pyproject
    # force-include). Source checkouts fall back to the plugin tree.
    p = Path(__file__).parent / "plugin_data" / PLUGIN_FILENAME
    if p.is_file():
        return p
    root = Path(__file__).resolve().parents[2]
    cand = root / "plugins" / "luaudit" / "studio" / PLUGIN_FILENAME
    return cand if cand.is_file() else p


def plugins_dir() -> Path:
    """The Roblox Studio plugins folder (same paths rojo's roblox_install uses)."""
    home = Path.home()
    if sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local")))
        return local / "Roblox" / "Plugins"
    if sys.platform == "darwin":
        return home / "Documents" / "Roblox" / "Plugins"
    # Linux has no first-class Studio; keep a conventional location so the
    # command stays deterministic under Vinegar-style setups.
    return home / ".local" / "share" / "Roblox" / "Plugins"


def installed_plugin_path() -> Path:
    """Fixed filename so upgrades overwrite instead of accumulating copies."""
    return plugins_dir() / PLUGIN_FILENAME


def _read_schema(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = _SCHEMA_RE.search(text)
    return m.group(1) if m else None


def status() -> dict:
    """Compare the installed mirror artifact against this engine build."""
    bundled = bundled_plugin_path()
    installed = installed_plugin_path()
    out: dict = {
        "engine_version": __version__,
        "engine_schema": CURRENT_SCHEMA,
        "bundled_present": bundled.is_file(),
        "plugins_dir": str(plugins_dir()),
        "installed": installed.is_file(),
        "up_to_date": False,
        "schema": None,
        "note": "",
    }
    if not installed.is_file():
        out["note"] = "mirror plugin not installed (studio-side checks unavailable)"
        return out
    schema = _read_schema(installed)
    out["schema"] = schema
    if not out["bundled_present"]:
        # Dev/checkout install without wheel data: compare against known key.
        out["up_to_date"] = schema == CURRENT_SCHEMA
        if not out["up_to_date"]:
            out["note"] = f"installed plugin schema {schema!r} != engine {CURRENT_SCHEMA!r}"
        return out
    up_to_date = schema == _read_schema(bundled)
    out["up_to_date"] = up_to_date
    if not up_to_date:
        out["note"] = (
            f"installed mirror plugin is stale for engine {__version__}"
            if schema
            else "installed mirror plugin is unreadable or from an unknown version"
        )
    return out


def install(yes: bool = False) -> dict:
    """Copy the bundled artifact into the plugins folder.

    Idempotent: an already-current install is left untouched. When a write
    is actually needed, consent applies: ``yes=True`` proceeds silently
    (the flag agents pass after the user approves in chat); an interactive
    terminal gets a y/N prompt; a non-interactive shell without the flag
    is refused rather than hanging.
    """
    bundled = bundled_plugin_path()
    target = installed_plugin_path()
    if not bundled.is_file():
        return {"installed": False, "path": str(target),
                "note": "bundled luaudit-mirror.rbxmx missing from this install (pip data files stripped?)"}
    st = status()
    if st["installed"] and st["up_to_date"]:
        return {"installed": True, "path": str(target), "note": "already up to date"}
    verb = "update" if st["installed"] else "install"
    if not yes:
        # Ask only on a readable interactive terminal. isatty() lies on some
        # setups (ssh ptys with redirected handles), so an unreadable stdin
        # must degrade to refusal, never crash or hang.
        reply: str | None
        try:
            if sys.stdin.isatty():
                reply = input(f"{verb} the Studio mirror plugin at {target}? [y/N] ")
            else:
                reply = None
        except (EOFError, OSError):
            reply = None
        if reply is None:
            return {"installed": False, "path": str(target), "needs_yes": True,
                    "note": f"non-interactive shell: re-run with --yes to {verb} "
                            "(or run it yourself in a terminal)"}
        if reply.strip().lower() not in ("y", "yes"):
            return {"installed": False, "path": str(target), "note": "declined"}
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    shutil.copyfile(bundled, tmp)
    os.replace(tmp, target)
    return {"installed": True, "path": str(target),
            "note": f"{verb}ed",
            "restart_note": "restart Roblox Studio to load it"}


def remove() -> dict:
    target = installed_plugin_path()
    if not target.is_file():
        return {"removed": False, "path": str(target), "note": "not installed"}
    target.unlink()
    return {"removed": True, "path": str(target), "note": "removed"}
