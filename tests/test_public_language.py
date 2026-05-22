"""Focused tests for public language tool response behavior."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from test_mcp_tools import create_mock_client, setup_mock_manager

from jons_mcp_pyright.tools import document_symbols


@pytest.mark.asyncio
async def test_document_symbols_normalizes_symbol_information(tmp_path: Path):
    """SymbolInformation responses expose uri/range without raw LSP location."""
    (tmp_path / "test.py").write_text("def f():\n    pass\n")
    mock_client = create_mock_client()
    mock_client.request = AsyncMock(
        return_value=[
            {
                "name": "f",
                "kind": 12,
                "location": {
                    "uri": (tmp_path / "test.py").resolve().as_uri(),
                    "range": {
                        "start": {"line": 0, "character": 4},
                        "end": {"line": 0, "character": 5},
                    },
                },
            }
        ]
    )
    setup_mock_manager(mock_client, tmp_path)

    result = await document_symbols(file_path="test.py")

    assert result["totalItems"] == 1
    assert result["items"][0] == {
        "name": "f",
        "kind": 12,
        "range": {
            "start": {"line": 1, "character": 5},
            "end": {"line": 1, "character": 6},
        },
        "uri": (tmp_path / "test.py").resolve().as_uri(),
    }
