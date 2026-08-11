"""Shared diagnostic model and parsers for luau-lens output.

Same parsing logic as luau-lens v1 (proven against real luau-lsp and selene
output), kept dependency-free so every harness can consume it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass


@dataclass
class Diagnostic:
    file: str
    line: int
    column: int
    end_line: int | None
    end_column: int | None
    code: str
    severity: str  # "error" | "warning"
    message: str
    source: str  # "luau-lsp" | "selene" | "stylua"


# ---------------------------------------------------------------------------
# luau-lsp plain formatter parser
# ---------------------------------------------------------------------------
# Format: file:line:col-endcol: (W0) CategoryName: message
_LUAU_LSP_RE = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+):(?P<col>\d+)(?:-(?P<endcol>\d+))?"
    r":\s+\(W0\)\s+(?P<category>\w+):\s+(?P<message>.+)$"
)

_SKIP_PREFIXES = ("[INFO]", "[WARN]", "[DEBUG]", "WARNING:", "Analyzing")


def parse_luau_lsp(output: str, stderr: str = "") -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for line in (output + "\n" + stderr).splitlines():
        line = line.strip()
        if not line:
            continue
        if any(line.startswith(p) for p in _SKIP_PREFIXES):
            continue
        m = _LUAU_LSP_RE.match(line)
        if not m:
            continue
        category = m.group("category")
        severity = "error" if "Error" in category else "warning"
        end_col = m.group("endcol")
        diagnostics.append(Diagnostic(
            file=m.group("file"),
            line=int(m.group("line")),
            column=int(m.group("col")),
            end_line=None,
            end_column=int(end_col) if end_col else None,
            code=category,
            severity=severity,
            message=m.group("message"),
            source="luau-lsp",
        ))
    return diagnostics


# ---------------------------------------------------------------------------
# selene JSON parser
# ---------------------------------------------------------------------------
# selene --display-style json emits one JSON object per line (0-indexed spans).
_SELENE_SEVERITY_MAP = {"Error": "error", "Warning": "warning"}


def parse_selene(output: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
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
        diagnostics.append(Diagnostic(
            file=label.get("filename", "unknown"),
            line=span.get("start_line", 0) + 1,
            column=span.get("start_column", 0) + 1,
            end_line=span.get("end_line", 0) + 1,
            end_column=span.get("end_column", 0) + 1,
            code=obj.get("code", "unknown"),
            severity=_SELENE_SEVERITY_MAP.get(obj.get("severity", ""), "warning"),
            message=obj.get("message", ""),
            source="selene",
        ))
    return diagnostics


def merge_diagnostics(*lists: list[Diagnostic]) -> list[Diagnostic]:
    """Merge and deduplicate by (file, line, column, code), sorted."""
    seen: set[tuple[str, int, int, str]] = set()
    merged: list[Diagnostic] = []
    for lst in lists:
        for d in lst:
            key = (d.file, d.line, d.column, d.code)
            if key not in seen:
                seen.add(key)
                merged.append(d)
    merged.sort(key=lambda d: (d.file, d.line, d.column))
    return merged


def summary_of(diagnostics: list[Diagnostic]) -> dict:
    errors = sum(1 for d in diagnostics if d.severity == "error")
    warnings = sum(1 for d in diagnostics if d.severity == "warning")
    return {"errors": errors, "warnings": warnings, "total": len(diagnostics)}


def to_dict(diagnostics: list[Diagnostic]) -> dict:
    return {
        "diagnostics": [
            {
                "file": d.file,
                "line": d.line,
                "column": d.column,
                "endLine": d.end_line,
                "endColumn": d.end_column,
                "code": d.code,
                "severity": d.severity,
                "message": d.message,
                "source": d.source,
            }
            for d in diagnostics
        ],
        "summary": summary_of(diagnostics),
    }
