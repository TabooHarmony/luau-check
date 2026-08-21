"""Tests for bootstrap: config writes, platform detection, init_configs."""

from __future__ import annotations

from pathlib import Path

from luaudit import bootstrap


def test_init_configs_writes_once(tmp_path: Path):
    wrote = bootstrap.init_configs(tmp_path)
    assert sorted(wrote) == [".luaurc", "selene.toml"]
    assert (tmp_path / "selene.toml").read_text() == 'std = "roblox"\n'
    assert (tmp_path / ".luaurc").read_text().strip() == '{\n  "languageMode": "strict"\n}'

    # second call writes nothing
    assert bootstrap.init_configs(tmp_path) == []


def test_init_configs_idempotent_content(tmp_path: Path):
    (tmp_path / "selene.toml").write_text("std = \"roblox\"\n")
    wrote = bootstrap.init_configs(tmp_path)
    # pre-existing file untouched; only missing .luaurc is created
    assert wrote == [".luaurc"]
    assert (tmp_path / "selene.toml").read_text() == "std = \"roblox\"\n"


def test_platform_shape():
    os_name, arch = bootstrap._get_platform()
    assert os_name in ("windows", "darwin", "linux")
    assert arch in ("x86_64", "arm64")


def test_urls_shape():
    urls = bootstrap._get_urls()
    assert set(urls) == {"luau-lsp", "selene", "stylua"}
    for u in urls.values():
        assert u.startswith("https://")
