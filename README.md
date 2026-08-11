# luau-lens

Luau diagnostics for AI coding agents. A single, dependency-free CLI that wraps
**luau-lsp** (type checking), **selene** (linting), and **stylua** (formatting)
so agents get fast, deterministic feedback without spending an LLM turn.

The v1 of this tool was an MCP server. v2 is a CLI. Agents call it through
their own terminal: it is stateless, fast, print-silent-on-clean, and works in
any harness that can run a shell command.

## Install

```bash
pip install luau-lens
# or: uv tool install luau-lens
```

First run downloads `luau-lsp`, `selene`, `stylua`, and the Roblox type
definitions into `~/.luau-lens/` automatically. No manual setup.

## Commands

```bash
# type-check and lint files or a directory
luau-lens check src/ server.luau client.luau
luau-lens check src/ --json

# format files in place
luau-lens format src/

# write default selene.toml + .luaurc into a project
luau-lens init

# verify toolchain
luau-lens doctor

# static security audit of Roblox Luau (heuristic, leads not verdicts)
luau-lens audit src/
```

`check` exits non-zero if any error is found, and prints nothing on a clean
tree, so hooks and agents can treat it as a gate.

## Agent usage

luau-lens is deliberately harness-agnostic. The CLI is the contract. Per-agent
wiring (AGENTS.md snippet, Claude Code plugin, cursor rules) is built on top.

Example AGENTS.md snippet for Codex:

```md
## Luau checks

Use `luau-lens check <path>` to type-check and lint Luau before declaring work
complete. `luau-lens check` exits non-zero when errors exist. Run it on edited
files and on the whole project before finishing a task.
```

### Install into harnesses

```bash
luau-lens install-agent
```

This auto-detects installed harnesses and wires luau-lens in:

- **Codex**: writes a `PostToolUse` hook into `~/.codex/hooks.json` (a managed
  file, never touches your `config.toml`). After a Write/Edit of a `.luau`,
  it runs `luau-lens check --json` on the edited file and injects the errors
  into codex's context as advisory feedback. Clean writes are silent.
- **Claude Code**: installs a skills-directory plugin at
  `~/.claude/skills/luau-lens/`, auto-discovered on next session as
  `luau-lens@skills-dir`. Same PostToolUse behavior via a bundled hook,
  `additionalContext` fed back only on errors.
- **Cursor / Pi**: the CLI works today from any harness that can run shell
  commands; first-class adapters are planned.

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

`luau-lens audit` is a static heuristic scanner for the exploiter patterns
that show up in AI-generated Roblox games: client-trusted remotes, missing
server validation, over-broad DataStore writes. It returns leads for review,
not security guarantees. Clean output means "no obvious pattern," not "secure."

## Config discovery

`check` walks up the directory tree and uses the nearest project's
`.luaurc`, `selene.toml`, and `.stylua.toml` if present, falling back to
bundled defaults. It never modifies your files.

## Development

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e . pytest
pytest
```

## License

MIT
