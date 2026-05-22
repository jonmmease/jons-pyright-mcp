"""Focused tests for public diagnostics and rename preview behavior."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from test_mcp_tools import create_mock_client, setup_mock_manager

from jons_mcp_pyright.constants import LSPMethods
from jons_mcp_pyright.tools import preview_rename
from jons_mcp_pyright.tools.intelligence import _normalize_rename_edits


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


@pytest.mark.asyncio
async def test_preview_rename_does_not_write_files(tmp_path: Path):
    """Previewing a rename returns edits while leaving disk content unchanged."""
    test_file = tmp_path / "test.py"
    test_file.write_text("old_name = 1\nprint(old_name)\n")

    mock_client = create_mock_client()
    mock_client.request = AsyncMock(
        side_effect=[
            {"range": {"start": {"line": 0, "character": 0}}},
            [],
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
        ]
    )
    setup_mock_manager(mock_client, tmp_path)

    result = await preview_rename(
        file_path="test.py",
        line=1,
        character=1,
        new_name="new_name",
    )

    assert result["totalEdits"] == 1
    assert test_file.read_text() == "old_name = 1\nprint(old_name)\n"


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
        ]
    )
    setup_mock_manager(mock_client, tmp_path)

    result = await preview_rename(
        file_path="provider.py",
        line=1,
        character=5,
        new_name="renamed_query_sql",
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
    }
    assert declaration_file.read_text() == "def query_sql():\n    pass\n"
    assert caller_file.read_text() == "from provider import query_sql\nquery_sql()\n"
    requested_methods = [call.args[0] for call in mock_client.request.await_args_list]
    assert requested_methods == [
        LSPMethods.PREPARE_RENAME,
        LSPMethods.REFERENCES,
        LSPMethods.RENAME,
    ]
