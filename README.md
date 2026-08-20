# luau-check

Luau type checking and linting for Claude Code and Codex.

Runs after every edit to a `.luau` or `.lua` file, and feeds errors and
warnings back to the agent so it can fix them itself.

First check downloads the toolchain into `~/.luau-check`. After that it just
works.

## Install

Claude Code:

```
/plugin marketplace add TabooHarmony/luau-check
/plugin install luau-check
```

Codex:

```
codex plugin marketplace add TabooHarmony/luau-check
codex plugin add luau-check@luau-check
```

That's it. The plugin loads its skill and its post-edit hook on its own.

## Updating

Codex and Claude Code copy the plugin into their own cache at install time,
so a `git pull` alone won't update a running plugin. After pulling new
versions, reinstall it:

```
codex plugin remove luau-check@luau-check
codex plugin add luau-check@luau-check
```

(Claude Code: `/plugin remove luau-check` then `/plugin install luau-check`.)
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

## Credit

luau-check depends on the following projects:

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

MIT. Not affiliated with the projects above or their maintainers.