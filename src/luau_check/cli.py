"""CLI entry point for luau-check v2.

The v2 contract is a plain, fast, deterministic CLI that agents call through
their own terminal. No MCP server, no always-on process, no LLM turn needed.

Commands:
    luau-check check FILE|DIR ...   run luau-lsp + selene, print diagnostics
    luau-check format FILE ...      format files in place with stylua
    luau-check init                 write default selene/luaurc configs
    luau-check doctor               verify toolchain (default no-op happy path)
    luau-check install-agent        detect and register with installed harnesses
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
        print("clean: no diagnostics")
    else:
        for d in result.get("diagnostics", []):
            rel = d["file"]
            try:
                rel = str(Path(d["file"]).resolve().relative_to(Path(args.cwd).resolve()))
            except (ValueError, OSError):
                pass
            print(f"{rel}:{d['line']}:{d['column']}: {d['severity'].upper()} [{d['source']}/{d['code']}] {d['message']}")
        print(f"summary: {summary['errors']} errors, {summary['warnings']} warnings, {summary['total']} total")
    return 1 if summary.get("errors", 0) > 0 else 0


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


def _cmd_install_agent(args: argparse.Namespace) -> int:
    from .adapters import claude as claude_adapter
    from .adapters import codex as codex_adapter

    results: list[str] = []

    # Codex adapter
    try:
        launcher = codex_adapter.write_launcher(codex_adapter.launcher_path())
        hooks_updated = codex_adapter.install_hooks(launcher)
        results.append(f"codex: {'installed' if hooks_updated else 'already installed'} (hook: {launcher})")
    except Exception as e:  # noqa: BLE001
        results.append(f"codex: FAILED ({e})")

    # Claude Code adapter (plugin, skills-dir auto-discovery)
    try:
        claude_available = claude_adapter.claude_cli_available()
        was_installed = claude_adapter.is_installed()
        plugin_dir = claude_adapter.install_plugin()
        state = "already installed" if was_installed else "installed"
        results.append(f"claude-code: {state} (plugin: {plugin_dir})")
        if not claude_available:
            results.append("claude-code: note: `claude` CLI not found on PATH; plugin will load when Claude Code runs")
    except Exception as e:  # noqa: BLE001
        results.append(f"claude-code: FAILED ({e})")

    for line in results:
        print(line)

    # Print (never write) the AGENTS.md snippet so the user keeps ownership
    if any("FAILED" not in r for r in results):
        print()
        print("Add this to your project AGENTS.md so the agent also checks via CLI:")
        print()
        print(codex_adapter.agents_md_snippet().rstrip())

    return 0 if all("FAILED" not in r for r in results) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="luau-check",
        description="Luau diagnostics for AI coding agents (luau-lsp + selene + stylua).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="type-check and lint files or a directory")
    p_check.add_argument("paths", nargs="*")
    p_check.add_argument("--json", action="store_true", help="emit JSON")
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

    sub.add_parser("install-agent", help="register with installed harnesses (auto-detect)")

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
    if args.command == "install-agent":
        return _cmd_install_agent(args)
    if args.command == "version":
        print(__version__)
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
