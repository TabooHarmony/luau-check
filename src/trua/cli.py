"""CLI entry point for trua v2.

The v2 contract is a plain, fast, deterministic CLI that agents call through
their own terminal. No MCP server, no always-on process, no LLM turn needed.

Commands:
    trua check FILE|DIR ...   run luau-lsp + selene, print diagnostics
    trua format FILE ...      format files in place with stylua
    trua init                 write default selene/luaurc configs
    trua doctor               verify toolchain (default no-op happy path)

Distribution is plugin-first: install the plugin for Claude Code or Codex
from the trua marketplace (see README). The CLI remains the engine
for on-demand checks.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import bootstrap
from .audit import audit_project
from .runners import check_files
from .version import __version__


def _print_plain(result: dict, cwd: str) -> None:
    for d in result.get("diagnostics", []):
        rel = d["file"]
        try:
            rel = str(Path(d["file"]).resolve().relative_to(Path(cwd).resolve()))
        except (ValueError, OSError):
            pass
        sev = d["severity"].upper()
        print(f"{rel}:{d['line']}:{d['column']}: {sev} [{d['source']}/{d['code']}] {d['message']}")


def _cmd_check(args: argparse.Namespace) -> int:
    bootstrap.ensure_tools()
    targets = args.paths
    if not targets:
        targets = ["./"]
    result = check_files(targets, cwd=args.cwd)
    summary = result.get("summary", {})
    if args.json:
        print(json.dumps(result, indent=2))
    elif summary.get("total", 0) == 0:
        pass  # silent on clean: exit 0, no output (the documented contract)
    else:
        for d in result.get("diagnostics", []):
            rel = d["file"]
            try:
                rel = str(Path(d["file"]).resolve().relative_to(Path(args.cwd).resolve()))
            except (ValueError, OSError):
                pass
            print(f"{rel}:{d['line']}:{d['column']}: {d['severity'].upper()} [{d['source']}/{d['code']}] {d['message']}")
        print(f"summary: {summary['errors']} errors, {summary['warnings']} warnings, {summary['total']} total")
    if summary.get("errors", 0) > 0:
        return 1
    if getattr(args, "warnings", False) and summary.get("warnings", 0) > 0:
        return 1
    return 0


def _cmd_format(args: argparse.Namespace) -> int:
    bootstrap.ensure_tools()
    if not bootstrap.has_stylua():
        print("stylua unavailable; cannot format", file=sys.stderr)
        return 2
    changed = bootstrap.format_files(args.paths, cwd=args.cwd)
    for f in changed:
        print(f"formatted {f}")
    if not changed:
        print("nothing to format")
    return 0


def _cmd_init(args: argparse.Namespace) -> int:
    bootstrap.ensure_tools()
    wrote = bootstrap.init_configs(Path(args.dir))
    if wrote:
        print(f"wrote configs to {args.dir}")
    else:
        print(f"configs already present in {args.dir}")
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    bootstrap.ensure_tools()
    paths = bootstrap.get_paths()
    problems: list[str] = []
    for name in ("luau_lsp", "selene", "stylua"):
        p = paths.get(name)
        ok = p is not None and p.exists()
        print(f"{name}: {'ok' if ok else 'missing'}")
        if not ok:
            problems.append(name)
    defs = paths.get("defs")
    ok_defs = defs is not None and defs.exists()
    print(f"defs: {'ok' if ok_defs else 'missing'}")
    if not ok_defs:
        problems.append("defs")
    if problems:
        print(f"problems: {', '.join(problems)}", file=sys.stderr)
        return 1
    return 0


def _cmd_audit(args: argparse.Namespace) -> int:
    result = audit_project(Path(args.dir))
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for item in result.get("findings", []):
            print(f"{item['severity'].upper()} [{item['rule']}] {item['file']}:{item['line']}: {item['message']}")
        print(f"audit: {len(result.get('findings', []))} findings")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="trua",
        description="Luau diagnostics for AI coding agents (luau-lsp + selene + stylua).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="type-check and lint files or a directory")
    p_check.add_argument("paths", nargs="*")
    p_check.add_argument("--json", action="store_true", help="emit JSON")
    p_check.add_argument("--warnings", action="store_true", help="exit non-zero if warnings are present (strict gate)")
    p_check.add_argument("--cwd", default=".")

    p_fmt = sub.add_parser("format", help="format files in place")
    p_fmt.add_argument("paths", nargs="+")
    p_fmt.add_argument("--cwd", default=".")

    sub.add_parser("init", help="write default selene.toml and .luaurc")
    sub.add_parser("doctor", help="verify toolchain")
    sub.add_parser("version", help="print version")

    p_audit = sub.add_parser("audit", help="static security audit of Roblox Luau code")
    p_audit.add_argument("dir", default=".")
    p_audit.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "check":
        return _cmd_check(args)
    if args.command == "format":
        return _cmd_format(args)
    if args.command == "init":
        return _cmd_init(args)
    if args.command == "doctor":
        return _cmd_doctor(args)
    if args.command == "audit":
        return _cmd_audit(args)
    if args.command == "version":
        print(__version__)
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
