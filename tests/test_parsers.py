"""Tests for luau-check parsers (luau-lsp plain, selene JSON, merge)."""

from __future__ import annotations

from luau_check.parsers import (
    Diagnostic,
    merge_diagnostics,
    parse_luau_lsp,
    parse_selene,
    summary_of,
    to_dict,
)


class TestParseLuauLsp:
    def test_parses_type_error(self):
        out = "test.luau:1:1-25: (W0) TypeError: Expected this to be 'number', but got 'string'\n"
        diags = parse_luau_lsp(out)
        assert len(diags) == 1
        d = diags[0]
        assert d.file == "test.luau"
        assert d.line == 1
        assert d.column == 1
        assert d.end_column == 25
        assert d.code == "TypeError"
        assert d.severity == "error"
        assert "number" in d.message

    def test_parses_unused_warning(self):
        out = "test.luau:5:7-12: (W0) LocalUnused: Variable 'result' is never used; prefix with '_' to silence\n"
        diags = parse_luau_lsp(out)
        assert len(diags) == 1
        d = diags[0]
        assert d.line == 5
        assert d.column == 7
        assert d.code == "LocalUnused"
        assert d.severity == "warning"

    def test_skips_noise_lines(self):
        out = "[INFO] load stuff\nWARNING: deprecation\nAnalyzing 3 files\n"
        assert parse_luau_lsp(out) == []

    def test_reads_stderr_too(self):
        out = ""
        err = "test.luau:2:3-4: (W0) TypeError: boom\n"
        diags = parse_luau_lsp(out, err)
        assert len(diags) == 1
        assert diags[0].line == 2

    def test_ignores_unmatched_garbage(self):
        assert parse_luau_lsp("random line without prefix\n") == []


class TestParseSelene:
    def _line(self):
        return (
            '{"severity":"Warning","code":"unused","message":"x unused",'
            '"primary_label":{"filename":"a.luau","span":{"start_line":2,"start_column":3,'
            '"end_line":2,"end_column":8}}}'
        )

    def test_parses_and_converts_to_one_indexed(self):
        diags = parse_selene(self._line() + "\n")
        assert len(diags) == 1
        d = diags[0]
        assert d.file == "a.luau"
        assert d.line == 3
        assert d.column == 4
        assert d.end_column == 9
        assert d.severity == "warning"
        assert d.source == "selene"

    def test_maps_error_severity(self):
        line = self._line().replace('"Warning"', '"Error"')
        diags = parse_selene(line)
        assert diags[0].severity == "error"

    def test_ignores_results_summary(self):
        diags = parse_selene(self._line() + "\nResults: 1 warning\n")
        assert len(diags) == 1

    def test_skips_non_json(self):
        assert parse_selene("not json\n") == []


class TestMergeAndSummary:
    def test_merge_dedupes_and_sorts(self):
        a = Diagnostic("f.luau", 2, 1, None, None, "X", "warning", "m", "selene")
        b = Diagnostic("f.luau", 1, 1, None, None, "Y", "error", "m", "luau-lsp")
        dup = Diagnostic("f.luau", 2, 1, None, None, "X", "warning", "m", "selene")
        merged = merge_diagnostics([a, dup, b])
        assert len(merged) == 2
        assert [d.line for d in merged] == [1, 2]

    def test_to_dict_and_summary(self):
        diags = [
            Diagnostic("a.luau", 1, 1, None, None, "TypeError", "error", "m", "luau-lsp"),
            Diagnostic("a.luau", 2, 1, None, None, "W", "warning", "m", "selene"),
        ]
        d = to_dict(diags)
        assert d["summary"]["errors"] == 1
        assert d["summary"]["warnings"] == 1
        assert d["summary"]["total"] == 2
        assert d["diagnostics"][0]["source"] == "luau-lsp"

    def test_summary_of_empty(self):
        assert summary_of([]) == {"errors": 0, "warnings": 0, "total": 0}
