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
wiring (AGENTS.md snippet, Claude Code plugin, cursor rules) is built on top
and planned as separate adapters.

Example AGENTS.md snippet for Codex:

```md
## Luau checks

Use `luau-lens check <path>` to type-check and lint Luau before declaring work
complete. `luau-lens check` exits non-zero when errors exist. Run it on edited
files and on the whole project before finishing a task.
```

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
