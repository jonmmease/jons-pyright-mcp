"""
Unit tests for MCP tools exposed by pyright-mcp.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

import jons_mcp_pyright
from jons_mcp_pyright import ensure_file_uri, ensure_pyright


def create_mock_client():
    """Create a mock pyright client with common setup."""
    mock_client = AsyncMock()
    mock_client._initialized = True
    mock_client.notify = AsyncMock()  # For file open notifications
    return mock_client


class TestUtilityFunctions:
    """Test utility functions."""
    
    def test_ensure_file_uri_already_uri(self):
        """Test ensure_file_uri with existing URI."""
        uri = "file:///home/user/test.py"
        assert ensure_file_uri(uri) == uri
    
    def test_ensure_file_uri_absolute_path(self, tmp_path: Path):
        """Test ensure_file_uri with absolute path."""
        file_path = tmp_path / "test.py"
        expected = f"file://{file_path.absolute()}"
        assert ensure_file_uri(str(file_path)) == expected
    
    def test_ensure_file_uri_relative_path(self, tmp_path: Path, monkeypatch):
        """Test ensure_file_uri with relative path."""
        monkeypatch.chdir(tmp_path)
        file_path = "src/test.py"
        expected = f"file://{tmp_path.absolute()}/src/test.py"
        assert ensure_file_uri(file_path) == expected
    
    def test_ensure_pyright_not_initialized(self):
        """Test ensure_pyright when client is not initialized."""
        jons_mcp_pyright.pyright = None
        with pytest.raises(RuntimeError, match="pyright is not initialized"):
            ensure_pyright()
    
    def test_ensure_pyright_initialized(self):
        """Test ensure_pyright with initialized client."""
        mock_client = MagicMock()
        mock_client._initialized = True
        jons_mcp_pyright.pyright = mock_client
        jons_mcp_pyright.initialization_complete = True
        
        result = ensure_pyright()
        assert result == mock_client


class TestCoreLanguageFeatures:
    """Test core language feature tools."""
    
    @pytest.mark.asyncio
    async def test_hover(self, tmp_path: Path, monkeypatch):
        """Test hover tool."""
        monkeypatch.chdir(tmp_path)
        test_file = tmp_path / "test.py"
        test_file.write_text("# test")
        
        mock_client = create_mock_client()
        mock_client.request = AsyncMock(return_value={
            "contents": {"kind": "markdown", "value": "Test hover info"}
        })
        mock_client.notify = AsyncMock()  # Mock notify for file open
        
        jons_mcp_pyright.pyright = mock_client
        jons_mcp_pyright.initialization_complete = True
        
        mock_ctx = AsyncMock()
        result = await jons_mcp_pyright.hover.fn(
            file_path="test.py",
            line=10,
            character=5,
            ctx=mock_ctx
        )
        
        assert result["contents"]["value"] == "Test hover info"
        mock_client.request.assert_called_once_with("textDocument/hover", {
            "textDocument": {"uri": f"file://{test_file.absolute()}"},
            "position": {"line": 10, "character": 5}
        })
    
    @pytest.mark.asyncio
    async def test_hover_no_info(self):
        """Test hover tool with no information."""
        mock_client = create_mock_client()
        mock_client.request = AsyncMock(return_value=None)
        mock_client.notify = AsyncMock()
        
        jons_mcp_pyright.pyright = mock_client
        jons_mcp_pyright.initialization_complete = True
        
        mock_ctx = AsyncMock()
        result = await jons_mcp_pyright.hover.fn(
            file_path="test.py",
            line=10,
            character=5,
            ctx=mock_ctx
        )
        
        assert result == {"contents": "No hover information available"}
    
    @pytest.mark.asyncio
    async def test_hover_still_initializing(self):
        """Test hover tool when pyright is still initializing."""
        mock_client = create_mock_client()
        mock_client._initialized = False  # Mark as not initialized
        
        jons_mcp_pyright.pyright = mock_client
        jons_mcp_pyright.initialization_complete = False
        
        mock_ctx = AsyncMock()
        result = await jons_mcp_pyright.hover.fn(
            file_path="test.py",
            line=10,
            character=5,
            ctx=mock_ctx
        )
        
        assert result == {"error": "Pyright is still initializing. Please try again in a few seconds."}
    
    @pytest.mark.asyncio
    async def test_completion(self):
        """Test completion tool."""
        mock_items = [
            {"label": "print", "kind": 3},
            {"label": "len", "kind": 3}
        ]
        
        mock_client = create_mock_client()
        mock_client.request = AsyncMock(return_value={"items": mock_items})
        
        jons_mcp_pyright.pyright = mock_client
        
        mock_ctx = AsyncMock()
        result = await jons_mcp_pyright.completion.fn(
            file_path="test.py",
            line=10,
            character=5,
            ctx=mock_ctx
        )
        
        # Result should be paginated response
        assert "items" in result
        assert "totalItems" in result
        assert "offset" in result
        assert "limit" in result
        assert "hasMore" in result
        
        # Check that the items have the expected labels (ignoring added offset fields)
        assert len(result["items"]) == len(mock_items)
        labels = [item["label"] for item in result["items"]]
        expected_labels = [item["label"] for item in mock_items]
        assert set(labels) == set(expected_labels)
    
    @pytest.mark.asyncio
    async def test_completion_list_response(self):
        """Test completion tool with list response."""
        mock_items = [
            {"label": "print", "kind": 3},
            {"label": "len", "kind": 3}
        ]
        
        mock_client = create_mock_client()
        mock_client.request = AsyncMock(return_value=mock_items)
        
        jons_mcp_pyright.pyright = mock_client
        
        mock_ctx = AsyncMock()
        result = await jons_mcp_pyright.completion.fn(
            file_path="test.py",
            line=10,
            character=5,
            ctx=mock_ctx
        )
        
        # Result should be paginated response
        assert "items" in result
        assert "totalItems" in result
        assert "offset" in result
        assert "limit" in result
        assert "hasMore" in result
        
        # Check that the items have the expected labels (ignoring added offset fields)
        assert len(result["items"]) == len(mock_items)
        labels = [item["label"] for item in result["items"]]
        expected_labels = [item["label"] for item in mock_items]
        assert set(labels) == set(expected_labels)
    
    @pytest.mark.asyncio
    async def test_definition(self):
        """Test definition tool."""
        mock_location = {
            "uri": "file:///test.py",
            "range": {
                "start": {"line": 0, "character": 0},
                "end": {"line": 5, "character": 10}
            }
        }
        
        mock_client = create_mock_client()
        mock_client.request = AsyncMock(return_value=mock_location)
        
        jons_mcp_pyright.pyright = mock_client
        
        mock_ctx = AsyncMock()
        result = await jons_mcp_pyright.definition.fn(
            file_path="test.py",
            line=10,
            character=5,
            ctx=mock_ctx
        )
        
        assert result == mock_location
    
    @pytest.mark.asyncio
    async def test_references(self, tmp_path: Path, monkeypatch):
        """Test references tool."""
        monkeypatch.chdir(tmp_path)
        test_file = tmp_path / "test.py"
        test_file.write_text("# test")
        
        mock_refs = [
            {"uri": "file:///test1.py", "range": {"start": {"line": 1, "character": 0}}},
            {"uri": "file:///test2.py", "range": {"start": {"line": 5, "character": 10}}}
        ]
        
        mock_client = create_mock_client()
        mock_client.request = AsyncMock(return_value=mock_refs)
        
        jons_mcp_pyright.pyright = mock_client
        
        result = await jons_mcp_pyright.references.fn(
            file_path="test.py",
            line=10,
            character=5,
            include_declaration=False
        )
        
        # Result should be paginated response
        assert "items" in result
        assert "totalItems" in result
        assert "offset" in result
        assert "limit" in result
        assert "hasMore" in result
        
        # Check that the items have the expected URIs (ignoring added offset fields)
        assert len(result["items"]) == len(mock_refs)
        uris = [item["uri"] for item in result["items"]]
        expected_uris = [item["uri"] for item in mock_refs]
        assert set(uris) == set(expected_uris)
        mock_client.request.assert_called_once_with("textDocument/references", {
            "textDocument": {"uri": f"file://{test_file.absolute()}"},
            "position": {"line": 10, "character": 5},
            "context": {"includeDeclaration": False}
        })
    
    @pytest.mark.asyncio
    async def test_document_symbols(self):
        """Test document_symbols tool."""
        mock_symbols = [
            {
                "name": "Calculator",
                "kind": 5,  # Class
                "children": [
                    {"name": "__init__", "kind": 9},  # Constructor
                    {"name": "add", "kind": 6}  # Method
                ]
            }
        ]
        
        mock_client = create_mock_client()
        mock_client.request = AsyncMock(return_value=mock_symbols)
        
        jons_mcp_pyright.pyright = mock_client
        
        mock_ctx = AsyncMock()
        result = await jons_mcp_pyright.document_symbols.fn(
            file_path="test.py",
            ctx=mock_ctx
        )
        
        # Result should be paginated response
        assert "items" in result
        assert "totalItems" in result
        assert "offset" in result
        assert "limit" in result
        assert "hasMore" in result
        
        # Check that the items have the expected symbols
        # Note: The function flattens hierarchical symbols, so we should have 3 items:
        # Calculator, __init__, and add
        assert len(result["items"]) == 3
        names = [item["name"] for item in result["items"]]
        assert "Calculator" in names
        assert "__init__" in names
        assert "add" in names
    
    @pytest.mark.asyncio
    async def test_workspace_symbols(self):
        """Test workspace_symbols tool."""
        mock_symbols = [
            {"name": "Calculator", "location": {"uri": "file:///src/calc.py"}},
            {"name": "compute", "location": {"uri": "file:///src/main.py"}}
        ]
        
        mock_client = create_mock_client()
        mock_client.request = AsyncMock(return_value=mock_symbols)
        
        jons_mcp_pyright.pyright = mock_client
        
        mock_ctx = AsyncMock()
        result = await jons_mcp_pyright.workspace_symbols.fn(
            query="calc",
            ctx=mock_ctx
        )
        
        # Result should be paginated response
        assert "items" in result
        assert "totalItems" in result
        assert "offset" in result
        assert "limit" in result
        assert "hasMore" in result
        
        # Check that the items have the expected symbols (ignoring added offset fields)
        assert len(result["items"]) == len(mock_symbols)
        names = [item["name"] for item in result["items"]]
        expected_names = [item["name"] for item in mock_symbols]
        assert set(names) == set(expected_names)


class TestCodeIntelligence:
    """Test code intelligence tools."""
    
    @pytest.mark.asyncio
    async def test_diagnostics_all(self):
        """Test diagnostics tool for all files."""
        mock_diagnostics = {
            "file:///test1.py": [
                {"severity": 1, "message": "Error 1"}
            ],
            "file:///test2.py": [
                {"severity": 2, "message": "Warning 1"}
            ]
        }
        
        jons_mcp_pyright.current_diagnostics = mock_diagnostics
        
        result = await jons_mcp_pyright.diagnostics.fn()
        
        # Result should be paginated response for the flattened list
        assert "items" in result
        assert "totalItems" in result
        assert "offset" in result
        assert "limit" in result
        assert "hasMore" in result
        # Check that items contain diagnostics from both files
        assert len(result["items"]) == 2
    
    @pytest.mark.asyncio
    async def test_diagnostics_single_file(self):
        """Test diagnostics tool for single file."""
        mock_client = create_mock_client()
        jons_mcp_pyright.pyright = mock_client
        
        jons_mcp_pyright.current_diagnostics = {
            "file:///test1.py": [{"severity": 1, "message": "Error 1"}],
            "file:///test2.py": [{"severity": 2, "message": "Warning 1"}]
        }
        
        result = await jons_mcp_pyright.diagnostics.fn(file_path="/test1.py")
        
        # Result should be paginated response for single file diagnostics
        assert "items" in result
        assert "totalItems" in result
        assert "offset" in result
        assert "limit" in result
        assert "hasMore" in result
        # Should contain only diagnostics from test1.py
        assert len(result["items"]) == 1
        assert result["items"][0]["message"] == "Error 1"
    
    @pytest.mark.asyncio
    async def test_code_actions(self):
        """Test code_actions tool."""
        mock_actions = [
            {"title": "Add import 'os'", "kind": "quickfix"},
            {"title": "Organize imports", "kind": "source.organizeImports"}
        ]
        
        mock_client = create_mock_client()
        mock_client.request = AsyncMock(return_value=mock_actions)
        
        jons_mcp_pyright.pyright = mock_client
        jons_mcp_pyright.current_diagnostics = {}
        
        mock_ctx = AsyncMock()
        result = await jons_mcp_pyright.code_actions.fn(
            file_path="test.py",
            start_line=10,
            start_char=0,
            end_line=10,
            end_char=10,
            ctx=mock_ctx
        )
        
        assert result == mock_actions
    
    @pytest.mark.asyncio
    async def test_rename(self):
        """Test rename tool."""
        mock_edit = {
            "changes": {
                "file:///test.py": [
                    {"range": {"start": {"line": 10}}, "newText": "new_name"}
                ]
            }
        }
        
        mock_client = create_mock_client()
        mock_client.request = AsyncMock()
        mock_client.request.side_effect = [
            {"range": {"start": {"line": 10}}},  # prepareRename
            mock_edit  # rename
        ]
        
        jons_mcp_pyright.pyright = mock_client
        
        mock_ctx = AsyncMock()
        result = await jons_mcp_pyright.rename.fn(
            file_path="test.py",
            line=10,
            character=5,
            new_name="new_name",
            ctx=mock_ctx
        )
        
        assert result == mock_edit
    
    @pytest.mark.asyncio
    async def test_rename_not_allowed(self):
        """Test rename tool when rename is not allowed."""
        mock_client = create_mock_client()
        mock_client.request = AsyncMock(return_value=None)
        
        jons_mcp_pyright.pyright = mock_client
        
        mock_ctx = AsyncMock()
        result = await jons_mcp_pyright.rename.fn(
            file_path="test.py",
            line=10,
            character=5,
            new_name="new_name",
            ctx=mock_ctx
        )
        
        assert result == {"error": "Cannot rename at this position"}
    
    @pytest.mark.asyncio
    async def test_signature_help(self):
        """Test signature_help tool."""
        mock_signatures = {
            "signatures": [{
                "label": "def add(a: int, b: int) -> int",
                "parameters": [
                    {"label": "a: int"},
                    {"label": "b: int"}
                ]
            }],
            "activeSignature": 0,
            "activeParameter": 0
        }
        
        mock_client = create_mock_client()
        mock_client.request = AsyncMock(return_value=mock_signatures)
        
        jons_mcp_pyright.pyright = mock_client
        
        mock_ctx = AsyncMock()
        result = await jons_mcp_pyright.signature_help.fn(
            file_path="test.py",
            line=10,
            character=5,
            ctx=mock_ctx
        )
        
        assert result == mock_signatures


class TestFormattingTools:
    """Test formatting tools."""
    
    @pytest.mark.asyncio
    async def test_format_document(self, tmp_path: Path, monkeypatch):
        """Test format_document tool."""
        monkeypatch.chdir(tmp_path)
        test_file = tmp_path / "test.py"
        test_file.write_text("# test")
        
        mock_edits = [
            {"range": {"start": {"line": 0}}, "newText": "formatted code"}
        ]
        
        mock_client = create_mock_client()
        mock_client.request = AsyncMock(return_value=mock_edits)
        
        jons_mcp_pyright.pyright = mock_client
        
        result = await jons_mcp_pyright.format_document.fn(
            file_path="test.py",
            tab_size=2,
            insert_spaces=False
        )
        
        assert result == mock_edits
        mock_client.request.assert_called_once_with("textDocument/formatting", {
            "textDocument": {"uri": f"file://{test_file.absolute()}"},
            "options": {"tabSize": 2, "insertSpaces": False}
        })
    
    @pytest.mark.asyncio
    async def test_organize_imports(self):
        """Test organize_imports tool."""
        mock_edits = [
            {"range": {"start": {"line": 0}}, "newText": "import os\nimport sys"}
        ]
        
        mock_client = create_mock_client()
        mock_client.request = AsyncMock(return_value={
            "changes": {"file:///test.py": mock_edits}
        })
        
        jons_mcp_pyright.pyright = mock_client
        
        mock_ctx = AsyncMock()
        result = await jons_mcp_pyright.organize_imports.fn(
            file_path="/test.py",
            ctx=mock_ctx
        )
        
        assert result == mock_edits


class TestPyrightExtensions:
    """Test pyright-specific extension tools."""
    
    @pytest.mark.asyncio
    async def test_add_import(self):
        """Test add_import tool."""
        mock_actions = [
            {
                "kind": "quickfix",
                "title": 'Add "import os"',
                "edit": {
                    "changes": {
                        "file:///test.py": [
                            {"range": {"start": {"line": 0}}, "newText": "import os\n"}
                        ]
                    }
                }
            }
        ]
        
        mock_client = create_mock_client()
        mock_client.request = AsyncMock(return_value=mock_actions)
        
        jons_mcp_pyright.pyright = mock_client
        jons_mcp_pyright.current_diagnostics = {}
        
        mock_ctx = AsyncMock()
        result = await jons_mcp_pyright.add_import.fn(
            file_path="test.py",
            line=10,
            character=5,
            ctx=mock_ctx
        )
        
        assert result == mock_actions[0]["edit"]
    
    @pytest.mark.asyncio
    async def test_add_import_not_available(self):
        """Test add_import tool when no import action available."""
        mock_client = create_mock_client()
        mock_client.request = AsyncMock(return_value=[])
        
        jons_mcp_pyright.pyright = mock_client
        jons_mcp_pyright.current_diagnostics = {}
        
        mock_ctx = AsyncMock()
        result = await jons_mcp_pyright.add_import.fn(
            file_path="test.py",
            line=10,
            character=5,
            ctx=mock_ctx
        )
        
        assert result == {"error": "No import action available"}
    
    @pytest.mark.asyncio
    async def test_create_config(self, tmp_path: Path, monkeypatch):
        """Test create_config tool."""
        monkeypatch.chdir(tmp_path)
        
        mock_ctx = AsyncMock()
        result = await jons_mcp_pyright.create_config.fn(ctx=mock_ctx)
        
        assert result == "Created pyrightconfig.json"
        
        # Check file was created
        config_file = tmp_path / "pyrightconfig.json"
        assert config_file.exists()
        
        # Check content
        config = json.loads(config_file.read_text())
        assert config["typeCheckingMode"] == "basic"
        assert config["pythonVersion"] == "3.10"
    
    @pytest.mark.asyncio
    async def test_create_config_already_exists(self, tmp_path: Path, monkeypatch):
        """Test create_config tool when config already exists."""
        monkeypatch.chdir(tmp_path)
        
        # Create existing config
        config_file = tmp_path / "pyrightconfig.json"
        config_file.write_text("{}")
        
        mock_ctx = AsyncMock()
        result = await jons_mcp_pyright.create_config.fn(ctx=mock_ctx)
        
        assert result == "pyrightconfig.json already exists"
    
    @pytest.mark.asyncio
    async def test_restart_server(self):
        """Test restart_server tool."""
        # Mock existing client
        old_client = AsyncMock()
        old_client.shutdown = AsyncMock()
        jons_mcp_pyright.pyright = old_client
        
        # Mock new client creation
        new_client = AsyncMock()
        new_client.start = AsyncMock()
        
        mock_ctx = AsyncMock()
        
        with patch("jons_mcp_pyright.PyrightClient", return_value=new_client):
            result = await jons_mcp_pyright.restart_server.fn(ctx=mock_ctx)
        
        assert result == "pyright server restarted successfully"
        old_client.shutdown.assert_called_once()
        new_client.start.assert_called_once()
        assert jons_mcp_pyright.pyright == new_client
    
    @pytest.mark.asyncio
    async def test_restart_server_not_running(self):
        """Test restart_server tool when server not running."""
        jons_mcp_pyright.pyright = None
        
        mock_ctx = AsyncMock()
        result = await jons_mcp_pyright.restart_server.fn(ctx=mock_ctx)
        
        assert result == "pyright server is not running"