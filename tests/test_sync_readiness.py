"""Focused tests for fail-closed sync and readiness gates."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from test_mcp_tools import create_mock_client, setup_mock_manager

from jons_mcp_pyright import server as server_module
from jons_mcp_pyright.exceptions import DocumentSyncError
from jons_mcp_pyright.tools import references, symbol_info


@pytest.mark.asyncio
async def test_document_sync_error_prevents_lsp_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Tools fail closed when document sync fails before making LSP requests."""
    (tmp_path / "test.py").write_text("x = 1\n")
    mock_client = create_mock_client()
    setup_mock_manager(mock_client, tmp_path)

    async def fail_sync(*_args, **_kwargs):
        raise DocumentSyncError("could not sync test.py")

    monkeypatch.setattr(server_module, "ensure_file_open_and_ready", fail_sync)

    result = await symbol_info(file_path="test.py", line=1, character=1)

    assert result["error"]["code"] == "document_sync_error"
    assert result["error"]["retryable"] is True
    mock_client.request.assert_not_called()


@pytest.mark.asyncio
async def test_project_wide_reference_waits_for_readiness(tmp_path: Path):
    """Project-wide reference lookup waits for diagnostics when sync refreshes."""
    (tmp_path / "test.py").write_text("name = 1\nprint(name)\n")
    mock_client = create_mock_client()
    mock_client.request = AsyncMock(return_value=[])
    mock_manager = setup_mock_manager(mock_client, tmp_path)
    mock_manager.is_file_opened = MagicMock(return_value=False)

    result = await references(file_path="test.py", line=1, character=1)

    assert result["totalItems"] == 0
    mock_manager.register_diagnostic_waiter.assert_called_once()
    mock_manager.wait_for_diagnostics.assert_called_once()
    mock_client.request.assert_called_once()
