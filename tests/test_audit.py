"""Tests for the static security audit rules."""

from __future__ import annotations

from pathlib import Path

from luau_lens.audit import audit_file, audit_project


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def test_client_trust_remote_flags(tmp_path: Path):
    p = _write(tmp_path, "server.luau", "remote.OnServerEvent:Connect(function(player, dmg)\nend)\n")
    findings = audit_file(p)
    rules = {f.rule for f in findings}
    assert "client-trust" in rules


def test_clean_code_no_findings(tmp_path: Path):
    p = _write(tmp_path, "clean.luau", "local function helper(x: number): number\n\treturn x + 1\nend\nreturn helper\n")
    assert audit_file(p) == []


def test_datastore_write_flags(tmp_path: Path):
    p = _write(tmp_path, "save.luau", "store:SetAsync('key', data)\n")
    findings = audit_file(p)
    assert any(f.rule == "unauthorized-write" for f in findings)


def test_audit_project_counts_and_paths(tmp_path: Path):
    _write(tmp_path, "a.luau", "remote.OnServerEvent:Connect(function() end)\n")
    _write(tmp_path, "b.luau", "print('hi')\n")
    result = audit_project(tmp_path)
    assert result["files"] == 2
    assert len(result["findings"]) == 1
    assert result["findings"][0]["rule"] == "client-trust"
    assert result["findings"][0]["file"].endswith("a.luau")


def test_finding_shape_serializable(tmp_path: Path):
    p = _write(tmp_path, "x.luau", "game.Players.LocalPlayer\n")
    findings = audit_file(p)
    assert findings
    d = findings[0].to_dict()
    assert set(d) == {"rule", "severity", "file", "line", "message"}
    assert d["severity"] in ("info", "warning", "error")
