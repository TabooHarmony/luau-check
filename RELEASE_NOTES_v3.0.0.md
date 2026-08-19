## v3.0.0

luau-check is now a plugin-first release: a marketplace plugin for Claude Code and Codex, dropping the old adapters/install-agent split. It installs from a marketplace, loads its skill, and wires its post-edit hook automatically.

### What you get

- **Post-edit diagnostics**: after each Write/Edit on a Luau file, luau-check type-checks, lints, and checks formatting, then feeds findings back to the agent as advisory context. Clean edits stay silent; errors and warnings come back for the agent to fix. Never blocks work.
- **A skill**: bundled `luau-check` skill tells the agent to verify Luau code before declaring work complete, with or without the hook.
- **A minimal CLI inside the plugin**: `luau-check check` remains the engine for on-demand checks, scripts, and CI.

### Install

Claude Code:
```bash
/plugin marketplace add TabooHarmony/luau-check
/plugin install luau-check
```

Codex:
```bash
codex plugin marketplace add TabooHarmony/luau-check
codex plugin add luau-check@luau-check
```

### Windows support (verified on codex 0.147, WINDEV VM)

- Cross-platform hook dispatch via extensionless name (PATHEXT -> .cmd on Windows, shebang on POSIX)
- Robust .cmd launcher: resolves python by absolute path (harness hook envs sanitize PATH), falls back to git-bash
- Whole-path quoting in hooks.json (cmd concat breaks POSIX-style quotes) + UTF-16 transcode in engine
- Hook execution confirmed end-to-end: `hook: PostToolUse -> Completed` on every tool use after hook trust

### Reliability

- Atomic checksummed toolchain downloads; empty-defs repair
- Bootstrap failures report InternalError (no silent clean)
- Timeout raised 30s -> 60s for slow toolchain fetch
- Support for noclobber, all-stream writes, .venv-* ignores, quoted/spaced paths, backslash zip member rejection
- Encoding fixes: UTF-8/UTF-16 handled correctly through PowerShell Set-Content

### Correctness

- No bare .lua sed false-positives; script-token skip in sed
- GetService/DataStore audit precision tightened; nonexistent target = error
- Warnings surfaced through hooks + --warnings gate for strict CI
