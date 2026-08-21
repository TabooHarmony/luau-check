---
name: luaudit
description: Run Luau type checking and linting (luau-lsp + selene) on Luau files when working on Roblox or Luau code. Use after editing .luau or .lua files to catch type errors and lint issues early.
---

# luaudit

Advisory Luau diagnostics for Roblox and Luau development. After editing
Luau code, verify it before declaring work complete.

## How to use

Run on a file or directory:

```bash
python3 "<PLUGIN_ROOT>/scripts/luaudit_hook.py" check <file-or-directory>
```

Or, if the `luaudit` CLI is installed:

```bash
luaudit check <file-or-directory>
```

- Exit 0 with no output means the code is clean.
- Non-zero exit (or diagnostics printed) means errors that should be fixed.
- Add `--warnings` to also fail on warnings (strict mode).

## Hook

This plugin also installs a PostToolUse hook that runs automatically after
Write/Edit on `.luau`/`.lua` files and feeds diagnostics back as context.
Clean edits stay silent.

## What it checks

- Type errors and warnings via luau-lsp (Roblox platform, strict mode)
- Lint issues via selene (Roblox standard)
- Formatting via StyLua (advisory warning)

The toolchain is downloaded once on first use into `~/.luaudit/`.
