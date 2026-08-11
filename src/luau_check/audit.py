"""Static security audit rules for Roblox Luau.

A focused, opinionated set of heuristics that flag the exploiter patterns
that show up in AI-generated games: client-trusted remotes, unauthorized
writes, missing validation, and over-broad handlers. This is a heuristic
scanner, not a guarantee: every finding is a lead for a human or agent to
review, and clean output means "no obvious pattern", not "secure".

Findings are JSON-serializable dicts:
    {rule, severity (info|warning|error), file, line, message}
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class Finding:
    rule: str
    severity: str
    file: str
    line: int
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Rule definitions
# ---------------------------------------------------------------------------

RULES: list[dict] = [
    {
        "name": "client-trust",
        "severity": "warning",
        "pattern": re.compile(
            r"OnServerEvent\s*[:.]\s*Connect|RemoteEvent|RemoteFunction",
            re.IGNORECASE,
        ),
        "message": "Server listens on a Remote; verify args are validated (range, type, cooldown).",
    },
    {
        "name": "unauthorized-write",
        "severity": "warning",
        "pattern": re.compile(
            r"(game|workspace)\.[A-Za-z0-9_.]+\.Value\s*=|DataStore|UpdateAsync|SetAsync",
            re.IGNORECASE,
        ),
        "message": "Write to game state or DataStore; ensure it is server-authorized and rate-limited.",
    },
    {
        "name": "player-owned-data",
        "severity": "info",
        "pattern": re.compile(
            r"player:\s*\[?getplayers?\]?|game\.Players|Players\.LocalPlayer",
            re.IGNORECASE,
        ),
        "message": "Player data access; confirm ownership checks before trusting values.",
    },
    {
        "name": "missing-service",
        "severity": "error",
        "pattern": re.compile(r"GetService\(\s*[\"']([A-Za-z]+)[\"']\s*\)"),
        "message": "Service obtained; ensure it exists and is used in the correct context (server vs client).",
    },
]


def _line_of(text: str, pat: re.Pattern) -> int | None:
    for i, line in enumerate(text.splitlines(), 1):
        if pat.search(line):
            return i
    return None


def audit_file(path: Path) -> list[Finding]:
    text = path.read_text(encoding="utf-8", errors="replace")
    findings: list[Finding] = []
    for rule in RULES:
        line = _line_of(text, rule["pattern"])
        if line is not None:
            findings.append(Finding(
                rule=rule["name"],
                severity=rule["severity"],
                file=str(path),
                line=line,
                message=rule["message"],
            ))
    return findings


def audit_project(directory: Path, patterns: tuple[str, ...] = (".luau", ".lua")) -> dict:
    directory = directory.resolve()
    findings: list[dict] = []
    files = 0
    if directory.is_file():
        candidates = [directory]
    else:
        candidates = [p for p in directory.rglob("*") if p.is_file() and p.suffix in patterns]
    for f in candidates:
        files += 1
        findings.extend(x.to_dict() for x in audit_file(f))
    return {"files": files, "findings": findings}
