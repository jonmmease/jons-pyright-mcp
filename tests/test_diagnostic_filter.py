"""Tests for public diagnostic filtering from member Pyright config."""

from __future__ import annotations

from pathlib import Path

import pytest

from jons_mcp_pyright.diagnostic_filter import (
    clear_diagnostic_filter_cache,
    diagnostic_rule_name,
    filter_diagnostics_by_member_config,
)


@pytest.fixture(autouse=True)
def clear_filter_cache() -> None:
    """Keep config-cache state isolated between tests."""

    clear_diagnostic_filter_cache()


def _diagnostic(
    uri: str,
    *,
    rule: str | None = "reportMissingImports",
    code: str | int | None = None,
    severity: int = 1,
) -> dict[str, object]:
    diagnostic: dict[str, object] = {
        "uri": uri,
        "message": "diagnostic",
        "severity": severity,
        "range": {
            "start": {"line": 0, "character": 0},
            "end": {"line": 0, "character": 1},
        },
    }
    if code is not None:
        diagnostic["code"] = code
    elif rule is not None:
        diagnostic["code"] = rule
    return diagnostic


def test_pyrightconfig_beats_pyproject_in_same_directory(tmp_path: Path) -> None:
    """pyrightconfig.json takes precedence over [tool.pyright]."""

    (tmp_path / "pyproject.toml").write_text(
        '[tool.pyright]\nreportMissingImports = "none"\n'
    )
    (tmp_path / "pyrightconfig.json").write_text('{"reportMissingImports": "warning"}')
    file_path = tmp_path / "module.py"
    file_path.write_text("import missing\n")

    result = filter_diagnostics_by_member_config(
        [_diagnostic(file_path.resolve().as_uri())],
        tmp_path,
    )

    assert len(result) == 1
    assert result[0]["severity"] == 2


def test_root_rules_apply_to_descendants(tmp_path: Path) -> None:
    """A root diagnostic override applies to files below the root."""

    (tmp_path / "pyproject.toml").write_text(
        '[tool.pyright]\nreportMissingImports = "none"\n'
    )
    package = tmp_path / "package"
    package.mkdir()
    file_path = package / "module.py"
    file_path.write_text("import missing\n")

    result = filter_diagnostics_by_member_config(
        [_diagnostic(file_path.resolve().as_uri())],
        tmp_path,
    )

    assert result == []


def test_nearest_member_config_overrides_root_rules(tmp_path: Path) -> None:
    """A member config can override an inherited root report rule."""

    (tmp_path / "pyproject.toml").write_text(
        '[tool.pyright]\nreportMissingImports = "none"\n'
    )
    package = tmp_path / "package"
    package.mkdir()
    (package / "pyproject.toml").write_text(
        '[tool.pyright]\nreportMissingImports = "error"\n'
    )
    file_path = package / "module.py"
    file_path.write_text("import missing\n")

    result = filter_diagnostics_by_member_config(
        [_diagnostic(file_path.resolve().as_uri(), severity=2)],
        tmp_path,
    )

    assert len(result) == 1
    assert result[0]["severity"] == 1


@pytest.mark.parametrize(
    ("config_name", "contents"),
    [
        ("pyrightconfig.json", "{invalid json"),
        ("pyproject.toml", "[tool.pyright\ninvalid"),
    ],
)
def test_invalid_config_does_not_fail_diagnostics(
    tmp_path: Path,
    config_name: str,
    contents: str,
) -> None:
    """Bad config files fail open and leave diagnostics visible."""

    (tmp_path / config_name).write_text(contents)
    file_path = tmp_path / "module.py"
    file_path.write_text("import missing\n")
    diagnostic = _diagnostic(file_path.resolve().as_uri())

    result = filter_diagnostics_by_member_config([diagnostic], tmp_path)

    assert result == [diagnostic]


def test_code_rule_and_data_rule_are_recognized(tmp_path: Path) -> None:
    """Exact rule identities can come from code, rule, or data.rule."""

    (tmp_path / "pyproject.toml").write_text(
        "\n".join(
            [
                "[tool.pyright]",
                'reportMissingImports = "none"',
                'reportGeneralTypeIssues = "warning"',
                'reportUnknownMemberType = "information"',
            ]
        )
    )
    file_path = tmp_path / "module.py"
    file_path.write_text("x = 1\n")
    uri = file_path.resolve().as_uri()

    result = filter_diagnostics_by_member_config(
        [
            _diagnostic(uri, code="reportMissingImports"),
            {**_diagnostic(uri, rule=None), "rule": "reportGeneralTypeIssues"},
            {
                **_diagnostic(uri, rule=None),
                "data": {"rule": "reportUnknownMemberType"},
            },
        ],
        tmp_path,
    )

    assert [item["severity"] for item in result] == [2, 3]


def test_true_absent_numeric_and_message_only_diagnostics_are_preserved(
    tmp_path: Path,
) -> None:
    """Only exact supported report-rule overrides affect diagnostics."""

    (tmp_path / "pyproject.toml").write_text(
        "\n".join(
            [
                "[tool.pyright]",
                "reportMissingImports = true",
                "reportUnknownArgumentType = 123",
            ]
        )
    )
    file_path = tmp_path / "module.py"
    file_path.write_text("x = 1\n")
    uri = file_path.resolve().as_uri()
    diagnostics = [
        _diagnostic(uri, code="reportMissingImports", severity=2),
        _diagnostic(uri, code=123, severity=1),
        {
            "uri": uri,
            "message": "message-only diagnostic",
            "severity": 3,
            "range": {
                "start": {"line": 0, "character": 0},
                "end": {"line": 0, "character": 1},
            },
        },
        _diagnostic(uri, code="reportUnknownArgumentType", severity=2),
    ]

    result = filter_diagnostics_by_member_config(diagnostics, tmp_path)

    assert result == diagnostics


def test_outside_diagnostic_uri_is_not_filtered_by_outside_config(
    tmp_path: Path,
) -> None:
    """Unsafe diagnostic URIs are left alone and do not use outside config."""

    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "pyproject.toml").write_text(
        '[tool.pyright]\nreportMissingImports = "none"\n'
    )
    outside_file = outside / "module.py"
    outside_file.write_text("import missing\n")
    diagnostic = _diagnostic(outside_file.resolve().as_uri())

    result = filter_diagnostics_by_member_config([diagnostic], root)

    assert result == [diagnostic]


def test_rule_extraction_prefers_report_code_over_other_fields() -> None:
    """A report* code is the most specific rule identity."""

    assert (
        diagnostic_rule_name(
            {
                "code": "reportMissingImports",
                "rule": "reportGeneralTypeIssues",
                "data": {"rule": "reportUnknownMemberType"},
            }
        )
        == "reportMissingImports"
    )
