"""Generate a luau-lsp-compatible sourcemap.json from a directory tree.

This closes the Script Sync gap: Script Sync puts real .luau files on disk
but no sourcemap, so luau-lsp can resolve types inside one file but cannot
resolve ``require()`` across files. Tools born for Rojo users get the map
for free; everyone else was stuck.

We synthesize the same rojo-style tree luau-lsp expects:

    DataModel
      └─ <your directory's structure>
           Folder      <- plain directories
           ModuleScript<- Name.luau / Name.lua
           Script      <- Name.server.luau / Name.server.lua
           LocalScript <- Name.client.luau / Name.client.lua

``init.luau`` (and friends) turn their containing folder into that class,
matching Rojo semantics. The result never touches your sources; it only
writes ``sourcemap.json`` so the existing engine picks it up through its
normal walk-up search.
"""

from __future__ import annotations

import json
from pathlib import Path

SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__"}

# Suffix -> script class. Order matters: check two-suffix names first.
SUFFIX_CLASSES = {
    ".server.luau": "Script",
    ".server.lua": "Script",
    ".client.luau": "LocalScript",
    ".client.lua": "LocalScript",
}
MODULE_EXTS = (".luau", ".lua")
INIT_NAMES = tuple("init" + sfx for sfx in SUFFIX_CLASSES) + ("init.luau", "init.lua")


def _class_and_name(path: Path) -> tuple[str, str] | None:
    name = path.name
    for sfx, cls in SUFFIX_CLASSES.items():
        if name.endswith(sfx):
            return cls, name[: -len(sfx)]
    if name.endswith(MODULE_EXTS):
        return "ModuleScript", path.stem
    return None


def build_tree(root: Path) -> dict:
    """Build the sourcemap node for root (as children of a DataModel)."""
    root = root.resolve()

    def visit(directory: Path) -> tuple[list[dict], dict | None]:
        """Return (child nodes, init node for this directory or None).

        An ``init.luau``-style file becomes the *node for its own folder*
        (Rojo semantics), so at the root it turns into the DataModel's own
        filePaths and never appears twice.
        """
        nodes: list[dict] = []
        try:
            entries = sorted(directory.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            return nodes, None

        dirs: list[Path] = []
        files: dict[str, dict] = {}
        init_node: dict | None = None
        for entry in entries:
            if entry.is_dir():
                if entry.name not in SKIP_DIRS and not entry.name.startswith("."):
                    dirs.append(entry)
                continue
            if not entry.is_file():
                continue
            parsed = _class_and_name(entry)
            if parsed is None:
                continue  # not a Luau source; Script Sync may hold other assets
            cls, inst_name = parsed
            rel = entry.relative_to(root).as_posix()
            spec = {"name": inst_name, "className": cls, "filePaths": [rel]}
            if entry.name in INIT_NAMES:
                # Promote: this file represents the containing directory.
                init_node = {"name": directory.name, "className": cls,
                             "filePaths": [rel]}
                continue
            files[entry.name] = spec

        for d in dirs:
            child_nodes, child_init = visit(d)
            if child_init is not None:
                child_init["children"] = child_nodes
                nodes.append(child_init)
            else:
                nodes.append({"name": d.name, "className": "Folder",
                              "children": child_nodes})

        for name in sorted(files):
            nodes.append(files[name])
        return nodes, init_node

    children, root_init = visit(root)
    data_model: dict = {"name": root.name, "className": "DataModel"}
    if root_init is not None:
        data_model["filePaths"] = root_init["filePaths"]
    data_model["children"] = children
    return data_model


def generate(root: str | Path, output: str | Path | None = None) -> dict:
    """Write sourcemap.json for the tree at root. Returns a small report."""
    root_path = Path(root)
    if not root_path.is_dir():
        return {"ok": False, "error": f"not a directory: {root_path}"}
    out_path = Path(output) if output else root_path / "sourcemap.json"

    def count_files(node: dict) -> int:
        n = len(node.get("filePaths", []))
        for child in node.get("children", []):
            n += count_files(child)
        return n

    tree = build_tree(root_path)
    total = count_files(tree)
    out_path.write_text(json.dumps(tree, indent=2), encoding="utf-8")
    return {"ok": True, "output": str(out_path), "scripts": total}
