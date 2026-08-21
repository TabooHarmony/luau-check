"""Detect whether a project already syncs Luau to disk.

The Studio mirror exists for people whose scripts live only inside Studio.
When Rojo, Argon, Azul, or Script Sync already puts .luau files on disk,
mirroring the same tree into a second folder is not redundant, it is
harmful: the agent can "fix" a mirror copy that nothing ever writes back
into the real project. So before installing (and again at status time)
we probe the workspace and pick a mode:

    external   files are managed by an existing sync tool -> mirror must
               stand down entirely
    studio     no external sync found -> mirror as designed
    unknown    nothing conclusive either way -> mirror asks once in Studio

Detection is deliberately layered and cheap; any single hit is enough
to say external. We never need to be clever about *which* tool it is.
"""

from __future__ import annotations

import os
import re
import subprocess

# Project files that mark a disk-managed workflow. Kept to names we are
# sure about; anything exotic simply stays undetected and lands in
# "unknown", which degrades to a single polite question in Studio.
MARKER_FILES = (
    "default.project.json",  # Rojo
    "*.project.json",        # Rojo, named projects
    "argon.project.json",    # Argon
    "azul.toml",             # Azul
    ".azulrc",
)

# Daemons that answer on well-known local ports. A bare open port proves
# little (anything could listen), so each port is probed with the tool's
# own HTTP surface and matched against a signature substring of its body.
_PORT_PROBES = {
    34872: b"rojo",  # Rojo serve; its index/404 bodies identify themselves
}

# Process-name fragments for running sync tools (Windows matches are
# case-insensitive because tasklist there is).
_PROCESS_FRAGMENTS = ("rojo", "argon", "azul")


def _find_markers(root: str) -> list[str]:
    hits: list[str] = []
    try:
        entries = os.listdir(root)
    except OSError:
        return hits
    lower = {e.lower() for e in entries}
    for marker in MARKER_FILES:
        if "*" in marker:
            pat = re.compile("^" + re.escape(marker).replace(r"\*", ".*") + "$")
            if any(pat.match(e) for e in lower):
                hits.append(marker)
        elif marker in lower:
            hits.append(marker)
    return hits


def _port_signature(port: int) -> str | None:
    """Return the tool name if something answering on port speaks like it."""
    import urllib.request

    try:
        with urllib.request.urlopen(  # noqa: S310 - literal localhost URL
            f"http://127.0.0.1:{port}/", timeout=1.0
        ) as resp:
            body = resp.read(2048)
    except Exception:  # socket error / timeout / non-HTTP: nothing there
        return None
    sig = _PORT_PROBES.get(port)
    if sig and sig.lower() in body.lower():
        return "rojo"
    return None


def _running_processes() -> list[str]:
    """Best-effort process scan. Empty list means unknown, never fatal."""
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["tasklist", "/fo", "csv", "/nh"],
                capture_output=True, text=True, timeout=5,
            ).stdout.lower()
        else:
            out = subprocess.run(
                ["ps", "-eo", "comm="],
                capture_output=True, text=True, timeout=5,
            ).stdout.lower()
    except Exception:
        return []
    return [frag for frag in _PROCESS_FRAGMENTS if frag in out]


def detect(root: str = ".") -> dict:
    """Classify the workspace. Never raises; worst case is 'unknown'.

    Returns {"mode": ..., "reasons": [...]} where mode is one of
    "external" | "studio" | "unknown".
    """
    root = str(os.path.abspath(root))
    reasons: list[str] = []

    markers = _find_markers(root)
    if markers:
        reasons.append("project file(s): " + ", ".join(sorted(markers)))

    for port, tool in sorted(_PORT_PROBES.items()):
        who = _port_signature(port)
        if who:
            reasons.append(f"{who} serve detected on 127.0.0.1:{port}")
            break

    procs = _running_processes()
    if procs:
        reasons.append("running process(es): " + ", ".join(procs))

    if reasons:
        return {"mode": "external", "reasons": reasons}

    # No positive signal anywhere. Now separate two very different cases:
    #   - a workspace with zero .luau/.lua files on disk has nothing the
    #     mirror could diverge from -> confident "studio", no questions;
    #   - .luau files present without any known sync tool smells like
    #     Script Sync (which leaves no marker we can read) or hand-copied
    #     trees -> honest "unknown"; the mirror asks the user exactly once
    #     instead of guessing and risking a forked copy.
    luau_count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules", ".venv")]
        for f in filenames:
            if f.endswith((".luau", ".lua")):
                luau_count += 1
                break
        if luau_count:
            break

    if luau_count == 0:
        return {"mode": "studio", "reasons": ["no luau sources found on disk"]}
    return {"mode": "unknown",
            "reasons": ["luau files exist on disk but no known sync tool was detected"]}
