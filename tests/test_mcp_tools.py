"""
Unit tests for MCP tools exposed by pyright-mcp.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jons_mcp_pyright import ensure_file_uri, ensure_pyright
from jons_mcp_pyright import server as server_module
from jons_mcp_pyright.tools import (
    definition,
    diagnostics,
    document_symbols,
    implementation,
    references,
    rename,
    restart_server,
    symbol_info,
    type_definition,
    type_info,
    workspace_symbols,
)
from jons_mcp_pyright.tools.language import _get_methods_via_completion


def create_mock_client():
    """Create a mock pyright client with common setup."""
    mock_client = AsyncMock()
    mock_client._initialized = True
    mock_client.is_initialized = MagicMock(return_value=True)
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
        server_module.pyright = None
        with pytest.raises(Exception, match="pyright is not initialized"):
            ensure_pyright()

    def test_ensure_pyright_initialized(self):
        """Test ensure_pyright with initialized client."""
        mock_client = MagicMock()
        mock_client.is_initialized = MagicMock(return_value=True)
        server_module.pyright = mock_client
        server_module.initialization_complete = True

        result = ensure_pyright()
        assert result == mock_client


class TestCoreLanguageFeatures:
    """Test core language feature tools."""

    @pytest.mark.asyncio
    async def test_symbol_info(self, tmp_path: Path, monkeypatch):
        """Test symbol_info tool."""
        monkeypatch.chdir(tmp_path)
        test_file = tmp_path / "test.py"
        test_file.write_text("# test")

        mock_client = create_mock_client()
        mock_client.request = AsyncMock(
            return_value={
                "contents": {"kind": "markdown", "value": "Test hover info"}
            }
        )
        mock_client.notify = AsyncMock()  # Mock notify for file open

        server_module.pyright = mock_client
        server_module.initialization_complete = True

        mock_ctx = AsyncMock()
        result = await symbol_info(
            file_path="test.py", line=10, character=5, ctx=mock_ctx
        )

        assert result["contents"]["value"] == "Test hover info"
        mock_client.request.assert_called_once_with(
            "textDocument/hover",
            {
                "textDocument": {"uri": f"file://{test_file.absolute()}"},
                "position": {"line": 10, "character": 5},
            },
        )

    @pytest.mark.asyncio
    async def test_symbol_info_no_info(self):
        """Test symbol_info tool with no information."""
        mock_client = create_mock_client()
        mock_client.request = AsyncMock(return_value=None)
        mock_client.notify = AsyncMock()

        server_module.pyright = mock_client
        server_module.initialization_complete = True

        mock_ctx = AsyncMock()
        result = await symbol_info(
            file_path="test.py", line=10, character=5, ctx=mock_ctx
        )

        assert result == {"contents": "No symbol information available"}

    @pytest.mark.asyncio
    async def test_symbol_info_still_initializing(self):
        """Test symbol_info tool when pyright is still initializing."""
        mock_client = create_mock_client()
        mock_client.is_initialized = MagicMock(return_value=False)

        server_module.pyright = mock_client
        server_module.initialization_complete = False

        mock_ctx = AsyncMock()
        result = await symbol_info(
            file_path="test.py", line=10, character=5, ctx=mock_ctx
        )

        assert result == {
            "error": "Pyright is still initializing. Please try again in a few seconds."
        }

    @pytest.mark.asyncio
    async def test_definition(self):
        """Test definition tool."""
        mock_location = {
            "uri": "file:///test.py",
            "range": {
                "start": {"line": 0, "character": 0},
                "end": {"line": 5, "character": 10},
            },
        }

        mock_client = create_mock_client()
        mock_client.request = AsyncMock(return_value=mock_location)

        server_module.pyright = mock_client

        mock_ctx = AsyncMock()
        result = await definition(
            file_path="test.py", line=10, character=5, ctx=mock_ctx
        )

        assert result == mock_location

    @pytest.mark.asyncio
    async def test_type_definition(self, tmp_path: Path, monkeypatch):
        """Test type_definition tool."""
        monkeypatch.chdir(tmp_path)
        test_file = tmp_path / "test.py"
        test_file.write_text("# test")

        mock_location = {
            "uri": "file:///test.py",
            "range": {
                "start": {"line": 0, "character": 0},
                "end": {"line": 5, "character": 10},
            },
        }

        mock_client = create_mock_client()
        mock_client.request = AsyncMock(return_value=mock_location)

        server_module.pyright = mock_client

        mock_ctx = AsyncMock()
        result = await type_definition(
            file_path="test.py", line=10, character=5, ctx=mock_ctx
        )

        assert result == mock_location

    @pytest.mark.asyncio
    async def test_implementation(self, tmp_path: Path, monkeypatch):
        """Test implementation tool."""
        monkeypatch.chdir(tmp_path)
        test_file = tmp_path / "test.py"
        test_file.write_text("# test")

        mock_locations = [
            {
                "uri": "file:///test.py",
                "range": {"start": {"line": 10, "character": 0}},
            }
        ]

        mock_client = create_mock_client()
        mock_client.request = AsyncMock(return_value=mock_locations)

        server_module.pyright = mock_client

        mock_ctx = AsyncMock()
        result = await implementation(
            file_path="test.py", line=5, character=4, ctx=mock_ctx
        )

        # implementation returns raw result, not paginated
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["uri"] == "file:///test.py"

    @pytest.mark.asyncio
    async def test_references(self, tmp_path: Path, monkeypatch):
        """Test references tool."""
        monkeypatch.chdir(tmp_path)
        test_file = tmp_path / "test.py"
        test_file.write_text("# test")

        mock_refs = [
            {"uri": "file:///test1.py", "range": {"start": {"line": 1, "character": 0}}},
            {"uri": "file:///test2.py", "range": {"start": {"line": 5, "character": 10}}},
        ]

        mock_client = create_mock_client()
        mock_client.request = AsyncMock(return_value=mock_refs)

        server_module.pyright = mock_client

        result = await references(
            file_path="test.py", line=10, character=5, include_declaration=False
        )

        # Result should be paginated response
        assert "items" in result
        assert "totalItems" in result
        assert "offset" in result
        assert "limit" in result
        assert "hasMore" in result

        # Check that the items have the expected URIs
        assert len(result["items"]) == len(mock_refs)
        uris = [item["uri"] for item in result["items"]]
        expected_uris = [item["uri"] for item in mock_refs]
        assert set(uris) == set(expected_uris)
        mock_client.request.assert_called_once_with(
            "textDocument/references",
            {
                "textDocument": {"uri": f"file://{test_file.absolute()}"},
                "position": {"line": 10, "character": 5},
                "context": {"includeDeclaration": False},
            },
        )

    @pytest.mark.asyncio
    async def test_document_symbols(self):
        """Test document_symbols tool."""
        mock_symbols = [
            {
                "name": "Calculator",
                "kind": 5,  # Class
                "children": [
                    {"name": "__init__", "kind": 9},  # Constructor
                    {"name": "add", "kind": 6},  # Method
                ],
            }
        ]

        mock_client = create_mock_client()
        mock_client.request = AsyncMock(return_value=mock_symbols)

        server_module.pyright = mock_client

        mock_ctx = AsyncMock()
        result = await document_symbols(file_path="test.py", ctx=mock_ctx)

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
            {"name": "compute", "location": {"uri": "file:///src/main.py"}},
        ]

        mock_client = create_mock_client()
        mock_client.request = AsyncMock(return_value=mock_symbols)

        server_module.pyright = mock_client

        mock_ctx = AsyncMock()
        result = await workspace_symbols(query="calc", ctx=mock_ctx)

        # Result should be paginated response
        assert "items" in result
        assert "totalItems" in result
        assert "offset" in result
        assert "limit" in result
        assert "hasMore" in result

        # Check that the items have the expected symbols
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
            "file:///test1.py": [{"severity": 1, "message": "Error 1"}],
            "file:///test2.py": [{"severity": 2, "message": "Warning 1"}],
        }

        server_module.current_diagnostics = mock_diagnostics

        result = await diagnostics()

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
        server_module.pyright = mock_client

        server_module.current_diagnostics = {
            "file:///test1.py": [{"severity": 1, "message": "Error 1"}],
            "file:///test2.py": [{"severity": 2, "message": "Warning 1"}],
        }

        result = await diagnostics(file_path="/test1.py")

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
            mock_edit,  # rename
        ]

        server_module.pyright = mock_client

        mock_ctx = AsyncMock()
        result = await rename(
            file_path="test.py",
            line=10,
            character=5,
            new_name="new_name",
            ctx=mock_ctx,
        )

        assert result == mock_edit

    @pytest.mark.asyncio
    async def test_rename_not_allowed(self):
        """Test rename tool when rename is not allowed."""
        mock_client = create_mock_client()
        mock_client.request = AsyncMock(return_value=None)

        server_module.pyright = mock_client

        mock_ctx = AsyncMock()
        result = await rename(
            file_path="test.py",
            line=10,
            character=5,
            new_name="new_name",
            ctx=mock_ctx,
        )

        assert result == {"error": "Cannot rename at this position"}


class TestPyrightExtensions:
    """Test pyright-specific extension tools."""

    @pytest.mark.asyncio
    async def test_restart_server(self):
        """Test restart_server tool."""
        # Mock existing client
        old_client = AsyncMock()
        old_client.shutdown = AsyncMock()
        old_client.project_root = Path("/test/project")
        server_module.pyright = old_client

        # Mock new client creation
        new_client = AsyncMock()
        new_client.start = AsyncMock()
        new_client.on_notification = MagicMock()

        mock_ctx = AsyncMock()

        # PyrightClient is imported lazily inside the function from ..lsp_client
        with patch(
            "jons_mcp_pyright.lsp_client.PyrightClient", return_value=new_client
        ):
            with patch(
                "jons_mcp_pyright.lsp_client.read_pyright_config",
                return_value={},
            ):
                result = await restart_server(ctx=mock_ctx)

        assert result == "pyright server restarted successfully"
        old_client.shutdown.assert_called_once()
        new_client.start.assert_called_once()
        assert server_module.pyright == new_client

    @pytest.mark.asyncio
    async def test_restart_server_not_running(self):
        """Test restart_server tool when server not running."""
        server_module.pyright = None

        mock_ctx = AsyncMock()
        result = await restart_server(ctx=mock_ctx)

        assert result == "pyright server is not running"


class TestTypeInfo:
    """Test type_info tool and _get_methods_via_completion helper."""

    @pytest.mark.asyncio
    async def test_type_info_class(self, tmp_path: Path, monkeypatch):
        """Test getting type info for a class instance."""
        monkeypatch.chdir(tmp_path)
        test_file = tmp_path / "test.py"
        test_file.write_text("""class MyClass:
    value: int
    name: str

obj = MyClass()
""")

        mock_client = create_mock_client()

        # Mock responses in sequence
        type_def_response = {
            "uri": f"file://{test_file.absolute()}",
            "range": {"start": {"line": 0, "character": 0}},
        }

        # Document symbols for the class
        doc_symbols_response = [
            {
                "name": "MyClass",
                "kind": 5,  # Class
                "range": {"start": {"line": 0, "character": 0}},
                "selectionRange": {"start": {"line": 0, "character": 6}},
                "children": [
                    {
                        "name": "value",
                        "kind": 8,  # Field
                        "selectionRange": {"start": {"line": 1, "character": 4}},
                    },
                    {
                        "name": "name",
                        "kind": 8,  # Field
                        "selectionRange": {"start": {"line": 2, "character": 4}},
                    },
                ],
            }
        ]

        # Hover responses for fields
        hover_value = {"contents": {"kind": "markdown", "value": "value: int"}}
        hover_name = {"contents": {"kind": "markdown", "value": "name: str"}}

        # Completion response for methods
        completion_response = {
            "items": [
                {"label": "__init__", "kind": 2, "detail": "(self) -> None"},
                {"label": "__str__", "kind": 2, "detail": "(self) -> str"},
            ]
        }

        mock_client.request = AsyncMock(
            side_effect=[
                type_def_response,  # typeDefinition
                doc_symbols_response,  # documentSymbol
                hover_value,  # hover for value field
                hover_name,  # hover for name field
                completion_response,  # completion
                {"label": "__init__", "kind": 2, "detail": "(self) -> None"},  # resolve
                {"label": "__str__", "kind": 2, "detail": "(self) -> str"},  # resolve
            ]
        )

        server_module.pyright = mock_client
        server_module.initialization_complete = True

        mock_ctx = AsyncMock()
        result = await type_info(
            file_path=str(test_file), line=4, character=0, ctx=mock_ctx
        )

        assert result["typeName"] == "MyClass"
        assert result["typeKind"] == "class"
        assert result["typeLocation"] is not None
        assert len(result["fields"]) == 2
        assert result["fields"][0]["name"] == "value"
        assert result["fields"][1]["name"] == "name"
        assert result["totalMethods"] >= 0

    @pytest.mark.asyncio
    async def test_type_info_primitive(self, tmp_path: Path, monkeypatch):
        """Test fallback to hover for primitive types."""
        monkeypatch.chdir(tmp_path)
        test_file = tmp_path / "test.py"
        test_file.write_text("x = 42\n")

        mock_client = create_mock_client()

        # No type definition for primitives
        type_def_response = None

        # Hover response shows the type
        hover_response = {"contents": {"kind": "markdown", "value": "x: int"}}

        # Completion for int methods
        completion_response = {
            "items": [
                {"label": "bit_length", "kind": 2, "detail": "(self) -> int"},
                {"label": "to_bytes", "kind": 2, "detail": "(self, ...) -> bytes"},
            ]
        }

        mock_client.request = AsyncMock(
            side_effect=[
                type_def_response,  # typeDefinition (None)
                hover_response,  # hover fallback
                completion_response,  # completion
                {"label": "bit_length", "kind": 2, "detail": "(self) -> int"},
                {"label": "to_bytes", "kind": 2, "detail": "(self, ...) -> bytes"},
            ]
        )

        server_module.pyright = mock_client
        server_module.initialization_complete = True

        mock_ctx = AsyncMock()
        result = await type_info(
            file_path=str(test_file), line=0, character=0, ctx=mock_ctx
        )

        assert result["typeName"] == "int"
        assert result["typeKind"] == "primitive"
        assert result["typeLocation"] is None
        assert result["fields"] == []
        assert result["totalMethods"] >= 2

    @pytest.mark.asyncio
    async def test_type_info_no_type_found(self, tmp_path: Path, monkeypatch):
        """Test error when neither typeDefinition nor hover returns useful info."""
        monkeypatch.chdir(tmp_path)
        test_file = tmp_path / "test.py"
        test_file.write_text("# empty\n")

        mock_client = create_mock_client()
        mock_client.request = AsyncMock(
            side_effect=[
                None,  # typeDefinition
                None,  # hover
            ]
        )

        server_module.pyright = mock_client
        server_module.initialization_complete = True

        mock_ctx = AsyncMock()
        result = await type_info(
            file_path=str(test_file), line=0, character=0, ctx=mock_ctx
        )

        assert "error" in result
        assert "Could not determine type" in result["error"]

    @pytest.mark.asyncio
    async def test_type_info_pagination(self, tmp_path: Path, monkeypatch):
        """Test method pagination with offset/limit parameters."""
        monkeypatch.chdir(tmp_path)
        test_file = tmp_path / "test.py"
        test_file.write_text("x = [1, 2, 3]\n")

        mock_client = create_mock_client()

        # Primitive type via hover
        hover_response = {"contents": {"kind": "markdown", "value": "x: list[int]"}}

        # Many methods for list type
        methods = [
            {"label": f"method{i}", "kind": 2, "detail": f"(self) -> int"}
            for i in range(25)
        ]
        completion_response = {"items": methods}

        # Build side effects with resolve responses
        side_effects = [
            None,  # typeDefinition
            hover_response,  # hover
            completion_response,  # completion
        ]
        # Add resolve responses for each method
        for m in methods:
            side_effects.append(m)

        mock_client.request = AsyncMock(side_effect=side_effects)

        server_module.pyright = mock_client
        server_module.initialization_complete = True

        mock_ctx = AsyncMock()

        # First page: offset=0, limit=10
        result = await type_info(
            file_path=str(test_file), line=0, character=0, limit=10, offset=0, ctx=mock_ctx
        )

        assert result["totalMethods"] == 25
        assert len(result["methods"]) == 10
        assert result["offset"] == 0
        assert result["limit"] == 10
        assert result["hasMore"] is True
        assert result["nextOffset"] == 10

    @pytest.mark.asyncio
    async def test_type_info_with_documentation(self, tmp_path: Path, monkeypatch):
        """Test include_documentation=True includes method docs."""
        monkeypatch.chdir(tmp_path)
        test_file = tmp_path / "test.py"
        test_file.write_text("x = 'hello'\n")

        mock_client = create_mock_client()

        hover_response = {"contents": {"kind": "markdown", "value": "x: str"}}

        completion_response = {
            "items": [
                {"label": "upper", "kind": 2, "detail": "(self) -> str"},
            ]
        }

        # Resolve with documentation
        resolve_response = {
            "label": "upper",
            "kind": 2,
            "detail": "(self) -> str",
            "documentation": {"kind": "markdown", "value": "Return a copy of the string converted to uppercase."},
        }

        mock_client.request = AsyncMock(
            side_effect=[
                None,  # typeDefinition
                hover_response,  # hover
                completion_response,  # completion
                resolve_response,  # resolve with documentation
            ]
        )

        server_module.pyright = mock_client
        server_module.initialization_complete = True

        mock_ctx = AsyncMock()
        result = await type_info(
            file_path=str(test_file), line=0, character=0, include_documentation=True, ctx=mock_ctx
        )

        assert result["typeName"] == "str"
        assert len(result["methods"]) >= 1
        # Check that at least one method has documentation
        upper_method = next((m for m in result["methods"] if m["name"] == "upper"), None)
        assert upper_method is not None
        assert "documentation" in upper_method


class TestGetMethodsViaCompletion:
    """Test _get_methods_via_completion helper function."""

    @pytest.mark.asyncio
    async def test_dot_already_exists(self, tmp_path: Path, monkeypatch):
        """Test when dot already exists after variable (no document modification needed)."""
        monkeypatch.chdir(tmp_path)
        test_file = tmp_path / "test.py"
        # Line has "obj." - dot already exists
        test_file.write_text("obj.method()\n")
        file_uri = f"file://{test_file.absolute()}"

        mock_client = create_mock_client()
        completion_response = {
            "items": [
                {"label": "method", "kind": 2, "detail": "(self) -> None"},
            ]
        }
        mock_client.request = AsyncMock(
            side_effect=[
                completion_response,  # completion
                {"label": "method", "kind": 2, "detail": "(self) -> None"},  # resolve
            ]
        )

        # Position at 'o' in 'obj'
        methods = await _get_methods_via_completion(
            mock_client, file_uri, str(test_file), line=0, character=0
        )

        assert len(methods) == 1
        assert methods[0]["name"] == "method"
        # Should NOT have called notify (no document modification)
        mock_client.notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_dot_needs_insertion(self, tmp_path: Path, monkeypatch):
        """Test when dot needs to be inserted (document modification via didChange)."""
        monkeypatch.chdir(tmp_path)
        test_file = tmp_path / "test.py"
        # No dot after obj
        test_file.write_text("obj\nx = 1\n")
        file_uri = f"file://{test_file.absolute()}"

        mock_client = create_mock_client()
        completion_response = {
            "items": [
                {"label": "some_method", "kind": 2, "detail": "(self) -> int"},
            ]
        }
        mock_client.request = AsyncMock(
            side_effect=[
                completion_response,  # completion
                {"label": "some_method", "kind": 2, "detail": "(self) -> int"},  # resolve
            ]
        )

        methods = await _get_methods_via_completion(
            mock_client, file_uri, str(test_file), line=0, character=0
        )

        assert len(methods) == 1
        assert methods[0]["name"] == "some_method"
        # Should have called notify twice (insert dot, restore original)
        assert mock_client.notify.call_count == 2

    @pytest.mark.asyncio
    async def test_empty_completion_results(self, tmp_path: Path, monkeypatch):
        """Test empty completion results."""
        monkeypatch.chdir(tmp_path)
        test_file = tmp_path / "test.py"
        test_file.write_text("x.\n")
        file_uri = f"file://{test_file.absolute()}"

        mock_client = create_mock_client()
        mock_client.request = AsyncMock(return_value={"items": []})

        methods = await _get_methods_via_completion(
            mock_client, file_uri, str(test_file), line=0, character=0
        )

        assert methods == []

    @pytest.mark.asyncio
    async def test_filter_methods_only(self, tmp_path: Path, monkeypatch):
        """Test filtering to only methods/functions (kind 2 and 3)."""
        monkeypatch.chdir(tmp_path)
        test_file = tmp_path / "test.py"
        test_file.write_text("obj.\n")
        file_uri = f"file://{test_file.absolute()}"

        mock_client = create_mock_client()
        completion_response = {
            "items": [
                {"label": "a_method", "kind": 2, "detail": "(self) -> None"},  # Method
                {"label": "a_function", "kind": 3, "detail": "() -> int"},  # Function
                {"label": "a_property", "kind": 10, "detail": "int"},  # Property - excluded
                {"label": "a_field", "kind": 5, "detail": "str"},  # Field - excluded
            ]
        }
        mock_client.request = AsyncMock(
            side_effect=[
                completion_response,
                {"label": "a_method", "kind": 2, "detail": "(self) -> None"},
                {"label": "a_function", "kind": 3, "detail": "() -> int"},
            ]
        )

        methods = await _get_methods_via_completion(
            mock_client, file_uri, str(test_file), line=0, character=0
        )

        # Should only have method and function, not property or field
        assert len(methods) == 2
        names = [m["name"] for m in methods]
        assert "a_method" in names
        assert "a_function" in names
        assert "a_property" not in names
        assert "a_field" not in names

    @pytest.mark.asyncio
    async def test_completion_resolve_for_signatures(self, tmp_path: Path, monkeypatch):
        """Test completion item resolution to get full signatures."""
        monkeypatch.chdir(tmp_path)
        test_file = tmp_path / "test.py"
        test_file.write_text("obj.\n")
        file_uri = f"file://{test_file.absolute()}"

        mock_client = create_mock_client()

        # Initial completion has no detail
        completion_response = {
            "items": [
                {"label": "process", "kind": 2},
            ]
        }
        # Resolve provides the detail
        resolve_response = {
            "label": "process",
            "kind": 2,
            "detail": "(self, data: bytes, count: int) -> bool",
        }

        mock_client.request = AsyncMock(
            side_effect=[completion_response, resolve_response]
        )

        methods = await _get_methods_via_completion(
            mock_client, file_uri, str(test_file), line=0, character=0
        )

        assert len(methods) == 1
        assert methods[0]["name"] == "process"
        assert methods[0]["detail"] == "(self, data: bytes, count: int) -> bool"
