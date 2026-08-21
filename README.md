# luaudit

Luau diagnostics for AI coding agents. After every edit, luaudit
type-checks and lints the change and feeds errors and warnings back to
the agent so it can fix them itself.

First check downloads the toolchain into `~/.luaudit`. After that it just works.

## Install

Claude Code:

```
/plugin marketplace add TabooHarmony/luaudit
/plugin install luaudit
```

Codex:

```
codex plugin marketplace add TabooHarmony/luaudit
codex plugin add luaudit@luaudit
```

That's it. The plugin loads its skill and its post-edit hook on its own.

## Updating

Codex and Claude Code copy the plugin into their own cache at install time,
so a `git pull` alone won't update a running plugin. After pulling new
versions, reinstall it:

```
codex plugin remove luaudit@luaudit
codex plugin add luaudit@luaudit
```

(Claude Code: `/plugin remove luaudit` then `/plugin install luaudit`.)
If the hook stops reporting diagnostics after an update, this is the first
thing to check — the cache can hold an older copy.

## What it does

- Type-checks, lints, and checks formatting after every Write/Edit
- Hands errors and warnings back to the agent as context
- Stays silent on clean edits
- Never blocks your work
- Uses your existing `.luaurc`, `selene.toml`, and `.stylua.toml` if it finds
  them, so existing configs keep working
- Resolves `require()`s across files when it finds a sourcemap (a
  `sourcemap.json`, or a Rojo `default.project.json` it can generate one from),
  so cross-file type errors are caught too. Without a sourcemap it falls back
  to per-file checking.

## What an agent sees

An agent writes a broken file:

```luau
local x: number = "boom"
local unused = 1
```

luaudit fires on its own and hands this back as context (verbatim output
from a real codex run):

```
broken2.luau:1:1: ERROR [luau-lsp/TypeError] Expected this to be 'number', but got 'string'
broken2.luau:1:7: WARNING [luau-lsp/LocalUnused] Variable 'x' is never used; prefix with '_' to silence
broken2.luau:1:7: WARNING [selene/unused_variable] x is assigned a value, but never used
broken2.luau:2:7: WARNING [luau-lsp/LocalUnused] Variable 'unused' is never used; prefix with '_' to silence
broken2.luau:2:7: WARNING [selene/unused_variable] unused is assigned a value, but never used
summary: 1 errors, 4 warnings, 5 total
```

The agent follows luaudit's own hints — fixes the type, renames the dead
variables to `_x` and `_unused`, re-checks, gets silence, and only then
reports done. Nobody ran a linter by hand at any point.

## Studio mirror

For MCP-only workflows where scripts live only inside Studio, the repo ships
a silent Studio plugin: `plugins/luaudit/studio/luaudit-mirror.luau`. Copy it into
`%APPDATA%\Roblox\Plugins\` and restart Studio. It mirrors the script tree to
disk every few seconds, and the hook checks against the mirror when an MCP
bridge edits a script. One-way: it never writes back into Studio.

## Credit

luaudit depends on the following projects:

- [luau-lsp](https://github.com/JohnnyMorganz/luau-lsp) by
  [JohnnyMorganz](https://github.com/JohnnyMorganz): Luau language server and
  type checker. MIT.
- [selene](https://github.com/Kampfkarren/selene) by
  [Kampfkarren](https://github.com/Kampfkarren): Luau linting. MPL-2.0.
- [StyLua](https://github.com/JohnnyMorganz/StyLua) by
  [JohnnyMorganz](https://github.com/JohnnyMorganz): Luau formatting. MPL-2.0.

Type checking also relies on the Roblox type definitions generated from
Roblox's API dumps and hosted at
[luau-lsp.pages.dev](https://luau-lsp.pages.dev).

## License

MIT. Independent project, not affiliated with or endorsed by Roblox or the
projects above or their maintainers. Luau is a trademark of Roblox
Corporation.
