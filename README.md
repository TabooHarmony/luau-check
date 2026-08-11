# luau-check

Luau diagnostics for AI coding agents.

A single, dependency-free CLI that wraps [luau-lsp] (type checking),
[selene] (linting), and [StyLua] (formatting) so agents get fast,
deterministic feedback without spending an LLM turn.

The v1 of this tool was an MCP server. v2 is a CLI. Agents call it through
their own terminal: it is stateless, fast, print-silent-on-clean, and works in
any harness that can run a shell command.

## Install

```bash
pip install luau-check
# or: uv tool install luau-check
```

First run downloads `luau-lsp`, `selene`, `stylua`, and the Roblox type
definitions into `~/.luau-check/` automatically. No manual setup.

Not on PyPI yet? Until it is, install straight from GitHub:

```bash
pip install git+https://github.com/TabooHarmony/luau-check.git
```

Then run checks, and `luau-check install-agent` to wire it into your agent.

## Commands

```bash
# type-check and lint files or a directory
luau-check check src/ server.luau client.luau
luau-check check src/ --json

# format files in place
luau-check format src/

# write default selene.toml + .luaurc into a project
luau-check init

# verify toolchain
luau-check doctor

# static security audit of Roblox Luau (heuristic, leads not verdicts)
luau-check audit src/
```

`check` exits non-zero if any error is found, and prints nothing on a clean
tree, so hooks and agents can treat it as a gate. `--warnings` makes it exit
non-zero on warnings too.

## Agent usage

luau-check is deliberately harness-agnostic. The CLI is the contract. Per-agent
wiring is built on top, and `luau-check install-agent` wires them all in one
command.

Add this to your project's AGENTS.md so agents also check via CLI:

```md
## Luau checks

Use `luau-check check <path>` to type-check and lint Luau before declaring work
complete. `luau-check check` exits non-zero when errors exist. Run it on edited
files and on the whole project before finishing a task.
```

### Install into harnesses

```bash
luau-check install-agent
```

This auto-detects installed harnesses and wires luau-check in. Every adapter
shares one design: advisory only (never blocks), silent when a file is clean,
and it never edits your AGENTS.md, CLAUDE.md, or user config files.

- **Codex**: `PostToolUse` hook in `~/.codex/hooks.json` (managed file).
  After a Write/Edit of a `.luau`, runs `luau-check check --json` on the file
  and injects errors and warnings as advisory context. Live-verified.
- **Claude Code**: skills-directory plugin at `~/.claude/skills/luau-check/`,
  auto-discovered as `luau-check@skills-dir`. Bundled PostToolUse hook,
  `additionalContext` fed back on errors and warnings. Live-verified.
- **Cursor**: user-level `postToolUse` hook in `~/.cursor/hooks.json`
  (project level also works; the adapter uses user level). Same advisory
  JSON-over-stdio contract, same silence-on-clean. Contract verified; needs a
  real Cursor session to smoke (no headless Cursor exists).
- **OpenCode**: TS plugin at `~/.config/opencode/plugin/luau-check.ts` using
  the `event` hook (file edits surface as `message.part.updated` tool parts).
  One module targets opencode v1 and v2 (identical plugin API on both
  branches). Smoke-tested once on 1.18.16; verify on your own opencode.
- **Pi / Oh-My-Pi**: TS extension at `~/.omp/agent/extensions/luau-check.ts`.
  `tool_result` handler runs the check and patches the result content with the
  diagnostics (omp's `tool_result` has no context-injection channel; content
  patching is the supported mechanism). Live-verified in omp 17.2.12.

`install-agent` never edits your AGENTS.md, CLAUDE.md, or config files. It
prints the recommended AGENTS.md snippet for you to add yourself.

### Claude Code without an Anthropic subscription

Claude Code can run against any Anthropic-Messages-compatible endpoint via
`ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN`; no login required. If your
LLM provider speaks the Messages API (e.g. many OpenAI-compatible proxies
also expose `/v1/messages`), point it there:

```bash
# scripts/claude-charm.sh in this repo (Charm Hyper example)
source scripts/claude-charm.sh && claude
```

Note: set `ANTHROPIC_BASE_URL` to the host root, not the `/v1` path. Claude
Code appends `/v1/messages` itself, so `https://host/v1` becomes a double
`/v1/v1` and fails.

## Audit

`luau-check audit` is a static heuristic scanner for the exploiter patterns
that show up in AI-generated Roblox games: client-trusted remotes, missing
server validation, over-broad DataStore writes. It returns leads for review,
not security guarantees. Clean output means "no obvious pattern," not
"secure."

## Configuration

`check` walks up the directory tree and uses the nearest project's `.luaurc`,
`selene.toml`, and `.stylua.toml` if present, falling back to bundled
defaults. It never modifies your files.

## Development

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e . pytest
pytest
```

## Third-party tools

luau-check downloads and shells out to these projects. Thanks to their
maintainers. This project is not affiliated with any of them.

| Tool | Purpose | Author | License |
| --- | --- | --- | --- |
| [luau-lsp] | Type checking | Johnny Morgan | MIT |
| [selene] | Linting | Kampfkarren | MPL-2.0 |
| [StyLua] | Formatting | Johnny Morgan | MPL-2.0 |

Roblox type definitions are downloaded from the copy hosted at
[luau-lsp.pages.dev](https://luau-lsp.pages.dev), which is generated by
[luau-lsp's scripts](https://github.com/JohnnyMorganz/luau-lsp/tree/master/scripts)
from Roblox's API dumps. luau-check itself is written against Luau (MIT).

[luau-lsp]: https://github.com/JohnnyMorganz/luau-lsp
[selene]: https://github.com/Kampfkarren/selene
[StyLua]: https://github.com/JohnnyMorganz/StyLua

## License

MIT
