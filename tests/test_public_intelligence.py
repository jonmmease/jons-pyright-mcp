"""Focused tests for public diagnostics and rename preview behavior."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from test_mcp_tools import create_mock_client, setup_mock_manager

from jons_mcp_pyright.constants import LSPMethods
from jons_mcp_pyright.tools import diagnostics, preview_rename
from jons_mcp_pyright.tools.intelligence import (
    _normalize_rename_edits,
    _rename_prewarm_candidates,
)


def _raw_diagnostic(uri: str, rule: str, message: str) -> dict[str, object]:
    """Build a raw zero-based diagnostic as Pyright would publish it."""

    return {
        "uri": uri,
        "message": message,
        "severity": 1,
        "code": rule,
        "range": {
            "start": {"line": 0, "character": 0},
            "end": {"line": 0, "character": 1},
        },
    }


@pytest.mark.asyncio
async def test_diagnostics_filters_file_env_and_aggregate_modes(tmp_path: Path):
    """Public diagnostics consistently apply member report-rule overrides."""

    package = tmp_path / "package"
    package.mkdir()
    (package / "pyproject.toml").write_text(
        '[tool.pyright]\nreportMissingImports = "none"\n'
    )
    file_path = package / "module.py"
    file_path.write_text("import missing\n")
    uri = file_path.resolve().as_uri()
    raw_diagnostic = _raw_diagnostic(
        uri,
        "reportMissingImports",
        "Import could not be resolved",
    )

    mock_client = create_mock_client()
    mock_manager = setup_mock_manager(mock_client, tmp_path)
    mock_manager.get_diagnostics_for_file = MagicMock(return_value=[raw_diagnostic])
    mock_manager.get_diagnostics_for_environment = MagicMock(
        return_value={uri: [raw_diagnostic]}
    )
    mock_manager.get_all_diagnostics = MagicMock(return_value={uri: [raw_diagnostic]})

    file_result = await diagnostics(file_path="package/module.py")
    env_result = await diagnostics(env_id=str(tmp_path))
    aggregate_result = await diagnostics()

    assert file_result["totalItems"] == 0
    assert env_result["totalItems"] == 0
    assert aggregate_result["totalItems"] == 0
    assert raw_diagnostic["severity"] == 1


@pytest.mark.asyncio
async def test_diagnostics_pagination_uses_filtered_totals(tmp_path: Path):
    """Pagination metadata is computed after member diagnostic filtering."""

    (tmp_path / "pyproject.toml").write_text(
        '[tool.pyright]\nreportMissingImports = "none"\n'
    )
    file_path = tmp_path / "module.py"
    file_path.write_text("import missing\nx: int = 'wrong'\n")
    uri = file_path.resolve().as_uri()
    suppressed = _raw_diagnostic(
        uri,
        "reportMissingImports",
        "Import could not be resolved",
    )
    kept = _raw_diagnostic(
        uri,
        "reportAssignmentType",
        "Expression of type str cannot be assigned to int",
    )

    mock_client = create_mock_client()
    mock_manager = setup_mock_manager(mock_client, tmp_path)
    mock_manager.get_all_diagnostics = MagicMock(return_value={uri: [suppressed, kept]})

    result = await diagnostics(limit=1)

    assert result["totalItems"] == 1
    assert result["hasMore"] is False
    assert [item["message"] for item in result["items"]] == [kept["message"]]


def test_preview_rename_normalizes_changes_and_sorts_one_based_ranges():
    """WorkspaceEdit.changes becomes sorted preview edits with public ranges."""
    result = _normalize_rename_edits(
        {
            "changes": {
                "file:///b.py": [
                    {
                        "range": {
                            "start": {"line": 1, "character": 1},
                            "end": {"line": 1, "character": 4},
                        },
                        "newText": "new",
                    }
                ],
                "file:///a.py": [
                    {
                        "range": {
                            "start": {"line": 0, "character": 0},
                            "end": {"line": 0, "character": 3},
                        },
                        "newText": "new",
                    }
                ],
            }
        }
    )

    assert result["totalEdits"] == 2
    assert [edit["uri"] for edit in result["edits"]] == [
        "file:///a.py",
        "file:///b.py",
    ]
    assert result["edits"][0]["range"]["start"] == {"line": 1, "character": 1}
    assert result["edits"][1]["range"]["start"] == {"line": 2, "character": 2}


def test_preview_rename_normalizes_document_changes():
    """WorkspaceEdit.documentChanges text document edits are supported."""
    result = _normalize_rename_edits(
        {
            "documentChanges": [
                {
                    "textDocument": {"uri": "file:///a.py", "version": 1},
                    "edits": [
                        {
                            "range": {
                                "start": {"line": 2, "character": 3},
                                "end": {"line": 2, "character": 6},
                            },
                            "newText": "new",
                        }
                    ],
                }
            ]
        }
    )

    assert result == {
        "edits": [
            {
                "uri": "file:///a.py",
                "range": {
                    "start": {"line": 3, "character": 4},
                    "end": {"line": 3, "character": 7},
                },
                "newText": "new",
            }
        ],
        "totalEdits": 1,
    }


def test_preview_rename_rejects_resource_operations():
    """Resource operations are intentionally not part of rename preview output."""
    result = _normalize_rename_edits(
        {"documentChanges": [{"kind": "rename", "oldUri": "a", "newUri": "b"}]}
    )

    assert result["error"]["code"] == "rename_unsupported_edit_shape"


def test_prewarm_candidates_prioritize_symbol_and_skip_ignored_paths(tmp_path: Path):
    """Prewarm discovery keeps likely symbol files first and skips unsafe paths."""
    matching_file = tmp_path / "b_match.py"
    other_file = tmp_path / "a_other.py"
    matching_file.write_text("query_sql()\n")
    other_file.write_text("value = 1\n")

    venv_dir = tmp_path / ".venv"
    venv_dir.mkdir()
    (venv_dir / "ignored.py").write_text("query_sql()\n")

    outside_file = tmp_path.parent / "outside.py"
    outside_file.write_text("query_sql()\n")
    (tmp_path / "linked.py").symlink_to(outside_file)

    invalid_file = tmp_path / "invalid.py"
    invalid_file.write_bytes(b"\xff\xfe")

    result = _rename_prewarm_candidates(tmp_path, "query_sql")

    assert result.paths[:2] == [matching_file.resolve(), other_file.resolve()]
    assert venv_dir / "ignored.py" not in result.paths
    assert result.skipped >= 2


@pytest.mark.asyncio
async def test_preview_rename_does_not_write_files(tmp_path: Path):
    """Previewing a rename returns edits while leaving disk content unchanged."""
    test_file = tmp_path / "test.py"
    test_file.write_text("old_name = 1\nprint(old_name)\n")

    mock_client = create_mock_client()
    mock_client.request = AsyncMock(
        side_effect=[
            {"range": {"start": {"line": 0, "character": 0}}},
            {
                "changes": {
                    test_file.resolve().as_uri(): [
                        {
                            "range": {
                                "start": {"line": 0, "character": 0},
                                "end": {"line": 0, "character": 8},
                            },
                            "newText": "new_name",
                        }
                    ]
                }
            },
            [],
        ]
    )
    setup_mock_manager(mock_client, tmp_path)

    result = await preview_rename(
        file_path="test.py",
        line=1,
        character=1,
        new_name="new_name",
        prewarm=False,
    )

    assert result["totalEdits"] == 1
    assert test_file.read_text() == "old_name = 1\nprint(old_name)\n"


@pytest.mark.asyncio
async def test_preview_rename_prewarms_candidates_before_rename(tmp_path: Path):
    """Cold rename previews open likely callers before rename and references."""
    declaration_file = tmp_path / "provider.py"
    caller_file = tmp_path / "consumer.py"
    declaration_file.write_text("def query_sql():\n    pass\n")
    caller_file.write_text("from provider import query_sql\nquery_sql()\n")

    declaration_uri = declaration_file.resolve().as_uri()
    caller_uri = caller_file.resolve().as_uri()

    mock_client = create_mock_client()
    mock_client.request = AsyncMock(
        side_effect=[
            {
                "range": {
                    "start": {"line": 0, "character": 4},
                    "end": {"line": 0, "character": 13},
                }
            },
            [],
            [],
            {
                "changes": {
                    declaration_uri: [
                        {
                            "range": {
                                "start": {"line": 0, "character": 4},
                                "end": {"line": 0, "character": 13},
                            },
                            "newText": "renamed_query_sql",
                        }
                    ]
                }
            },
            [
                {
                    "uri": declaration_uri,
                    "range": {
                        "start": {"line": 0, "character": 4},
                        "end": {"line": 0, "character": 13},
                    },
                },
                {
                    "uri": caller_uri,
                    "range": {
                        "start": {"line": 0, "character": 21},
                        "end": {"line": 0, "character": 30},
                    },
                },
            ],
        ]
    )
    mock_manager = setup_mock_manager(mock_client, tmp_path)
    mock_env = mock_manager.get_environment_for_file.return_value

    def mark_opened(_file_path: str, uri: str, version: int = 1) -> None:
        mock_env.opened_files.add(uri)
        mock_env.doc_versions[uri] = version

    mock_manager.mark_file_opened.side_effect = mark_opened

    result = await preview_rename(
        file_path="provider.py",
        line=1,
        character=5,
        new_name="renamed_query_sql",
    )

    requested_methods = [call.args[0] for call in mock_client.request.await_args_list]
    assert requested_methods == [
        LSPMethods.PREPARE_RENAME,
        LSPMethods.DOCUMENT_SYMBOL,
        LSPMethods.DOCUMENT_SYMBOL,
        LSPMethods.RENAME,
        LSPMethods.REFERENCES,
    ]
    assert result["totalEdits"] == 2
    assert "warnings" not in result
    assert caller_uri in mock_env.opened_files


@pytest.mark.asyncio
async def test_preview_rename_limit_zero_warns_and_skips_prewarm(tmp_path: Path):
    """A zero prewarm limit warns without issuing documentSymbol warm-up calls."""
    test_file = tmp_path / "test.py"
    test_file.write_text("old_name = 1\nprint(old_name)\n")
    uri = test_file.resolve().as_uri()

    mock_client = create_mock_client()
    mock_client.request = AsyncMock(
        side_effect=[
            {
                "range": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": 0, "character": 8},
                }
            },
            {
                "changes": {
                    uri: [
                        {
                            "range": {
                                "start": {"line": 0, "character": 0},
                                "end": {"line": 0, "character": 8},
                            },
                            "newText": "new_name",
                        }
                    ]
                }
            },
            [],
        ]
    )
    setup_mock_manager(mock_client, tmp_path)

    result = await preview_rename(
        file_path="test.py",
        line=1,
        character=1,
        new_name="new_name",
        prewarm_limit=0,
    )

    requested_methods = [call.args[0] for call in mock_client.request.await_args_list]
    assert requested_methods == [
        LSPMethods.PREPARE_RENAME,
        LSPMethods.RENAME,
        LSPMethods.REFERENCES,
    ]
    assert result["warnings"] == ["Prewarm limit was 0; unopened files may be missed."]


@pytest.mark.asyncio
async def test_preview_rename_unavailable_skips_prewarm(tmp_path: Path):
    """prepareRename failure/unavailability stops before prewarm side effects."""
    test_file = tmp_path / "test.py"
    test_file.write_text("old_name = 1\n")

    mock_client = create_mock_client()
    mock_client.request = AsyncMock(return_value=None)
    setup_mock_manager(mock_client, tmp_path)

    result = await preview_rename(
        file_path="test.py",
        line=1,
        character=1,
        new_name="new_name",
    )

    assert result["error"]["code"] == "rename_not_available"
    mock_client.request.assert_awaited_once()


@pytest.mark.asyncio
async def test_preview_rename_supplements_missing_reference_edits(tmp_path: Path):
    """Reference ranges are added when Pyright rename omits workspace callers."""
    declaration_file = tmp_path / "provider.py"
    caller_file = tmp_path / "consumer.py"
    declaration_file.write_text("def query_sql():\n    pass\n")
    caller_file.write_text("from provider import query_sql\nquery_sql()\n")

    declaration_uri = declaration_file.resolve().as_uri()
    caller_uri = caller_file.resolve().as_uri()

    mock_client = create_mock_client()
    mock_client.request = AsyncMock(
        side_effect=[
            {
                "range": {
                    "start": {"line": 0, "character": 4},
                    "end": {"line": 0, "character": 13},
                }
            },
            {
                "changes": {
                    declaration_uri: [
                        {
                            "range": {
                                "start": {"line": 0, "character": 4},
                                "end": {"line": 0, "character": 13},
                            },
                            "newText": "renamed_query_sql",
                        }
                    ]
                }
            },
            [
                {
                    "uri": declaration_uri,
                    "range": {
                        "start": {"line": 0, "character": 4},
                        "end": {"line": 0, "character": 13},
                    },
                },
                {
                    "uri": caller_uri,
                    "range": {
                        "start": {"line": 0, "character": 21},
                        "end": {"line": 0, "character": 30},
                    },
                },
                {
                    "uri": caller_uri,
                    "range": {
                        "start": {"line": 1, "character": 0},
                        "end": {"line": 1, "character": 9},
                    },
                },
            ],
        ]
    )
    setup_mock_manager(mock_client, tmp_path)

    result = await preview_rename(
        file_path="provider.py",
        line=1,
        character=5,
        new_name="renamed_query_sql",
        prewarm=False,
    )

    assert result == {
        "edits": [
            {
                "uri": caller_uri,
                "range": {
                    "start": {"line": 1, "character": 22},
                    "end": {"line": 1, "character": 31},
                },
                "newText": "renamed_query_sql",
            },
            {
                "uri": caller_uri,
                "range": {
                    "start": {"line": 2, "character": 1},
                    "end": {"line": 2, "character": 10},
                },
                "newText": "renamed_query_sql",
            },
            {
                "uri": declaration_uri,
                "range": {
                    "start": {"line": 1, "character": 5},
                    "end": {"line": 1, "character": 14},
                },
                "newText": "renamed_query_sql",
            },
        ],
        "totalEdits": 3,
        "warnings": ["Prewarm was disabled; unopened files may be missed."],
    }
    assert declaration_file.read_text() == "def query_sql():\n    pass\n"
    assert caller_file.read_text() == "from provider import query_sql\nquery_sql()\n"
    requested_methods = [call.args[0] for call in mock_client.request.await_args_list]
    assert requested_methods == [
        LSPMethods.PREPARE_RENAME,
        LSPMethods.RENAME,
        LSPMethods.REFERENCES,
    ]
