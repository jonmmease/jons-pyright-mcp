"""
Unit tests for MCP tools exposed by pyright-mcp.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from jons_mcp_pyright import ensure_file_uri, ensure_pyright
from jons_mcp_pyright import server as server_module
from jons_mcp_pyright.environment import EnvironmentState
from jons_mcp_pyright.manager import PyrightClientManager
from jons_mcp_pyright.tools import (
    definition,
    diagnostics,
    document_symbols,
    preview_rename,
    references,
    restart_server,
    symbol_info,
    type_definition,
    type_info,
)
from jons_mcp_pyright.tools.language import _get_methods_via_completion


def create_mock_client():
    """Create a mock pyright client with common setup."""
    mock_client = AsyncMock()
    mock_client._initialized = True
    mock_client.is_initialized = MagicMock(return_value=True)
    mock_client.notify = AsyncMock()  # For file open notifications
    mock_client.project_root = Path("/test/project")
    return mock_client


def setup_mock_manager(mock_client, tmp_path=None):
    """Set up a mock manager with the given client.

    Args:
        mock_client: The mock PyrightClient to use
        tmp_path: Optional tmp_path for creating environment state

    Returns:
        The mock manager
    """
    project_root = tmp_path if tmp_path else Path("/test/project")
    project_root.mkdir(parents=True, exist_ok=True)
    default_file = project_root / "test.py"
    if not default_file.exists():
        default_file.write_text("# test\n")
    mock_client.project_root = project_root

    # Create a mock environment state
    mock_env = MagicMock(spec=EnvironmentState)
    mock_env.env_id = str(project_root)
    mock_env.project_root = project_root
    mock_env.venv_path = None
    mock_env.last_accessed = None
    mock_env.client = mock_client
    mock_env.opened_files = set()
    mock_env.doc_versions = {}
    mock_env.diagnostics = {}

    # Create a mock manager
    mock_manager = MagicMock(spec=PyrightClientManager)
    mock_manager.root = project_root
    mock_manager.root_environment = mock_env
    mock_manager.environments = {str(project_root): mock_env}
    mock_manager.get_environment_for_file = MagicMock(return_value=mock_env)
    mock_manager.get_client_for_file = AsyncMock(return_value=mock_client)
    mock_manager.is_file_opened = MagicMock(
        side_effect=lambda _file_path, uri: uri in mock_env.opened_files
    )
    mock_manager.mark_file_opened = MagicMock()
    mock_manager.increment_doc_version = MagicMock(return_value=1)
    mock_manager.get_all_diagnostics = MagicMock(return_value={})
    mock_manager.get_diagnostics_for_file = MagicMock(return_value=[])
    mock_manager.get_diagnostics_for_environment = MagicMock(return_value={})
    mock_manager.get_all_active_clients = MagicMock(
        return_value=[(str(project_root), mock_client)]
    )
    mock_manager._start_client = AsyncMock()
    mock_manager.is_file_stale = MagicMock(return_value=False)
    mock_manager.register_diagnostic_waiter = MagicMock(return_value=asyncio.Event())
    mock_manager.wait_for_diagnostics = AsyncMock(return_value=True)
    mock_manager.get_environment = MagicMock(return_value=mock_env)
    mock_manager.get_all_environments = MagicMock(return_value=[mock_env])

    server_module.manager = mock_manager
    server_module.initialization_complete = True

    return mock_manager


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
        """Test ensure_pyright when manager is not initialized."""
        server_module.manager = None
        with pytest.raises(Exception, match="Manager is not initialized"):
            ensure_pyright()

    def test_ensure_pyright_initialized(self, tmp_path: Path):
        """Test ensure_pyright with initialized client."""
        mock_client = create_mock_client()
        setup_mock_manager(mock_client, tmp_path)

        result = ensure_pyright()
        assert result == mock_client


class TestCoreLanguageFeatures:
    """Test core language feature tools."""

    @pytest.mark.asyncio
    async def test_relative_paths_resolve_from_project_root(
        self, tmp_path: Path, monkeypatch
    ):
        """Relative tool paths should ignore the MCP process cwd."""
        project_root = tmp_path / "project"
        other_cwd = tmp_path / "other"
        project_root.mkdir()
        other_cwd.mkdir()
        test_file = project_root / "test.py"
        test_file.write_text("# project file\n")
        (other_cwd / "test.py").write_text("# wrong file\n")
        monkeypatch.chdir(other_cwd)

        mock_client = create_mock_client()
        mock_client.request = AsyncMock(return_value={"contents": "ok"})
        setup_mock_manager(mock_client, project_root)

        result = await symbol_info(file_path="test.py", line=1, character=1)

        assert result == {"content": "ok"}
        mock_client.request.assert_called_once_with(
            "textDocument/hover",
            {
                "textDocument": {"uri": test_file.resolve().as_uri()},
                "position": {"line": 0, "character": 0},
            },
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "tool_call",
        [
            lambda path: symbol_info(path, 0, 0),
            lambda path: type_info(path, 0, 0),
            lambda path: definition(path, 0, 0),
            lambda path: type_definition(path, 0, 0),
            lambda path: references(path, 0, 0),
            lambda path: document_symbols(path),
            lambda path: diagnostics(file_path=path),
            lambda path: preview_rename(path, 1, 1, "new_name"),
            lambda path: restart_server(file_path=path),
        ],
    )
    @pytest.mark.parametrize(
        "path_case",
        [
            "relative_escape",
            "absolute_outside",
            "file_uri_outside",
            "symlink_escape",
            "missing",
            "directory",
        ],
    )
    async def test_file_tools_reject_unsafe_paths(
        self, tmp_path: Path, tool_call, path_case: str
    ):
        """Every file-taking public tool validates paths before LSP/filesystem work."""
        root = tmp_path / "project"
        root.mkdir()
        outside = tmp_path / "outside.py"
        outside.write_text("x = 1\n")
        (root / "test.py").write_text("x = 1\n")
        symlink = root / "linked.py"
        symlink.symlink_to(outside)

        paths = {
            "relative_escape": "../outside.py",
            "absolute_outside": str(outside),
            "file_uri_outside": outside.resolve().as_uri(),
            "symlink_escape": str(symlink),
            "missing": str(root / "missing.py"),
            "directory": str(root),
        }

        mock_client = create_mock_client()
        setup_mock_manager(mock_client, root)

        result = await tool_call(paths[path_case])

        assert result["error"]["code"] == "path_validation_error"
        mock_client.request.assert_not_called()
        mock_client.notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_symbol_info(self, tmp_path: Path, monkeypatch):
        """Test symbol_info tool."""
        monkeypatch.chdir(tmp_path)
        test_file = tmp_path / "test.py"
        test_file.write_text("# test")

        mock_client = create_mock_client()
        mock_client.request = AsyncMock(
            return_value={"contents": {"kind": "markdown", "value": "Test hover info"}}
        )

        setup_mock_manager(mock_client, tmp_path)

        mock_ctx = AsyncMock()
        result = await symbol_info(
            file_path="test.py", line=11, character=6, ctx=mock_ctx
        )

        assert result["content"] == "Test hover info"
        mock_client.request.assert_called_once_with(
            "textDocument/hover",
            {
                "textDocument": {"uri": f"file://{test_file.absolute()}"},
                "position": {"line": 10, "character": 5},
            },
        )

    @pytest.mark.asyncio
    async def test_symbol_info_no_info(self, tmp_path: Path):
        """Test symbol_info tool with no information."""
        mock_client = create_mock_client()
        mock_client.request = AsyncMock(return_value=None)

        setup_mock_manager(mock_client, tmp_path)

        mock_ctx = AsyncMock()
        result = await symbol_info(
            file_path="test.py", line=11, character=6, ctx=mock_ctx
        )

        assert result == {"content": "No symbol information available"}

    @pytest.mark.asyncio
    async def test_symbol_info_still_initializing(self, tmp_path: Path):
        """Test symbol_info tool when pyright is still initializing."""
        mock_client = create_mock_client()
        mock_client.is_initialized = MagicMock(return_value=False)

        setup_mock_manager(mock_client, tmp_path)
        server_module.initialization_complete = False

        mock_ctx = AsyncMock()
        result = await symbol_info(
            file_path="test.py", line=11, character=6, ctx=mock_ctx
        )

        assert result["error"]["code"] == "pyright_initializing"
        assert result["error"]["retryable"] is True

    @pytest.mark.asyncio
    async def test_definition(self, tmp_path: Path):
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

        setup_mock_manager(mock_client, tmp_path)

        mock_ctx = AsyncMock()
        result = await definition(
            file_path="test.py", line=11, character=6, ctx=mock_ctx
        )

        assert result == {
            "items": [
                {
                    "uri": "file:///test.py",
                    "range": {
                        "start": {"line": 1, "character": 1},
                        "end": {"line": 6, "character": 11},
                    },
                }
            ],
            "totalItems": 1,
        }

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

        setup_mock_manager(mock_client, tmp_path)

        mock_ctx = AsyncMock()
        result = await type_definition(
            file_path="test.py", line=11, character=6, ctx=mock_ctx
        )

        assert result == {
            "items": [
                {
                    "uri": "file:///test.py",
                    "range": {
                        "start": {"line": 1, "character": 1},
                        "end": {"line": 6, "character": 11},
                    },
                }
            ],
            "totalItems": 1,
        }

    @pytest.mark.asyncio
    async def test_references(self, tmp_path: Path, monkeypatch):
        """Test references tool."""
        monkeypatch.chdir(tmp_path)
        test_file = tmp_path / "test.py"
        test_file.write_text("# test")

        mock_refs = [
            {
                "uri": "file:///test1.py",
                "range": {"start": {"line": 1, "character": 0}},
            },
            {
                "uri": "file:///test2.py",
                "range": {"start": {"line": 5, "character": 10}},
            },
        ]

        mock_client = create_mock_client()
        mock_client.request = AsyncMock(return_value=mock_refs)

        setup_mock_manager(mock_client, tmp_path)

        result = await references(
            file_path="test.py", line=11, character=6, include_declaration=False
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
    async def test_document_symbols(self, tmp_path: Path):
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

        setup_mock_manager(mock_client, tmp_path)

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


class TestCodeIntelligence:
    """Test code intelligence tools."""

    @pytest.mark.asyncio
    async def test_diagnostics_all(self, tmp_path: Path):
        """Test diagnostics tool for all files."""
        mock_diagnostics = {
            "file:///test1.py": [{"severity": 1, "message": "Error 1"}],
            "file:///test2.py": [{"severity": 2, "message": "Warning 1"}],
        }

        mock_client = create_mock_client()
        mock_manager = setup_mock_manager(mock_client, tmp_path)
        mock_manager.get_all_diagnostics = MagicMock(return_value=mock_diagnostics)

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
    async def test_diagnostics_single_file(self, tmp_path: Path):
        """Test diagnostics tool for single file."""
        mock_client = create_mock_client()
        mock_manager = setup_mock_manager(mock_client, tmp_path)

        # Set up diagnostics for the environment
        mock_manager.get_diagnostics_for_file = MagicMock(
            return_value=[{"severity": 1, "message": "Error 1"}]
        )

        test_file = tmp_path / "test1.py"
        test_file.write_text("# test\n")
        result = await diagnostics(file_path=str(test_file))

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
    async def test_preview_rename(self, tmp_path: Path):
        """Test preview_rename tool."""
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
            [],  # references supplement
            mock_edit,  # rename
        ]

        setup_mock_manager(mock_client, tmp_path)

        mock_ctx = AsyncMock()
        result = await preview_rename(
            file_path="test.py",
            line=11,
            character=6,
            new_name="new_name",
            ctx=mock_ctx,
        )

        assert result == {
            "edits": [
                {
                    "uri": "file:///test.py",
                    "range": {
                        "start": {"line": 11, "character": 1},
                        "end": {"line": 11, "character": 1},
                    },
                    "newText": "new_name",
                }
            ],
            "totalEdits": 1,
        }

    @pytest.mark.asyncio
    async def test_preview_rename_not_allowed(self, tmp_path: Path):
        """Test preview_rename tool when rename is not allowed."""
        mock_client = create_mock_client()
        mock_client.request = AsyncMock(return_value=None)

        setup_mock_manager(mock_client, tmp_path)

        mock_ctx = AsyncMock()
        result = await preview_rename(
            file_path="test.py",
            line=11,
            character=6,
            new_name="new_name",
            ctx=mock_ctx,
        )

        assert result["error"]["code"] == "rename_not_available"
        assert result["error"]["message"] == "Cannot rename at this position"


class TestPyrightExtensions:
    """Test pyright-specific extension tools."""

    @pytest.mark.asyncio
    async def test_restart_server_all(self, tmp_path: Path):
        """Test restart_server tool restarts all environments."""
        mock_client = create_mock_client()
        mock_manager = setup_mock_manager(mock_client, tmp_path)

        # Set up the mock manager for restart_all
        mock_manager.restart_all = AsyncMock()

        mock_ctx = AsyncMock()
        result = await restart_server(ctx=mock_ctx)

        assert result == {"status": "restarted", "scope": "all"}
        mock_manager.restart_all.assert_called_once()

    @pytest.mark.asyncio
    async def test_restart_server_single_environment(self, tmp_path: Path):
        """Test restart_server tool for a specific file."""
        mock_client = create_mock_client()
        mock_manager = setup_mock_manager(mock_client, tmp_path)

        # Set up the mock manager for restart_environment
        mock_manager.restart_environment = AsyncMock()

        mock_ctx = AsyncMock()
        result = await restart_server(file_path="test.py", ctx=mock_ctx)

        assert result["status"] == "restarted"
        assert result["scope"] == "environment"
        assert result["env_id"] == str(tmp_path)
        # restart_environment is called with the env_id (project root), not the file path
        mock_manager.restart_environment.assert_called_once_with(str(tmp_path))

    @pytest.mark.asyncio
    async def test_restart_server_by_env_id(self, tmp_path: Path):
        """Test restart_server tool with env_id parameter."""
        mock_client = create_mock_client()
        mock_manager = setup_mock_manager(mock_client, tmp_path)

        # Set up the mock manager for restart_environment
        mock_manager.restart_environment = AsyncMock()

        mock_ctx = AsyncMock()
        result = await restart_server(env_id=str(tmp_path), ctx=mock_ctx)

        assert result == {
            "status": "restarted",
            "scope": "environment",
            "env_id": str(tmp_path),
        }
        mock_manager.restart_environment.assert_called_once_with(str(tmp_path))

    @pytest.mark.asyncio
    async def test_restart_server_env_id_not_found(self, tmp_path: Path):
        """Test restart_server tool with non-existent env_id."""
        mock_client = create_mock_client()
        mock_manager = setup_mock_manager(mock_client, tmp_path)

        # Set up the mock manager to raise ValueError for unknown env_id
        mock_manager.restart_environment = AsyncMock(
            side_effect=ValueError("No environment found with ID: /nonexistent")
        )

        mock_ctx = AsyncMock()
        result = await restart_server(env_id="/nonexistent", ctx=mock_ctx)

        assert result["error"]["code"] == "environment_not_found"
        assert "No environment found" in result["error"]["message"]

    @pytest.mark.asyncio
    async def test_restart_server_not_running(self):
        """Test restart_server tool when server not running."""
        from jons_mcp_pyright.exceptions import PyrightNotInitializedError

        server_module.manager = None

        mock_ctx = AsyncMock()
        with pytest.raises(PyrightNotInitializedError):
            await restart_server(ctx=mock_ctx)


class TestListEnvironments:
    """Test list_environments tool."""

    @pytest.mark.asyncio
    async def test_list_environments(self, tmp_path: Path):
        """Test listing environments."""
        from jons_mcp_pyright.tools.extensions import list_environments

        mock_client = create_mock_client()
        setup_mock_manager(mock_client, tmp_path)

        result = await list_environments()

        assert "total" in result
        assert "active_count" in result
        assert "environments" in result
        assert result["total"] == 1  # Root environment
        assert result["active_count"] == 1
        assert len(result["environments"]) == 1

        env = result["environments"][0]
        assert env["env_id"] == str(tmp_path)
        assert env["project_root"] == str(tmp_path)
        assert env["is_active"] is True
        assert "opened_files_count" in env

    @pytest.mark.asyncio
    async def test_list_environments_not_running(self):
        """Test list_environments when server not running."""
        from jons_mcp_pyright.exceptions import PyrightNotInitializedError
        from jons_mcp_pyright.tools.extensions import list_environments

        server_module.manager = None

        with pytest.raises(PyrightNotInitializedError):
            await list_environments()


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

        # Completion response for fields and methods
        completion_response = {
            "items": [
                {"label": "value", "kind": 5, "detail": "int"},
                {"label": "name", "kind": 5, "detail": "str"},
                {"label": "__init__", "kind": 2, "detail": "(self) -> None"},
                {"label": "__str__", "kind": 2, "detail": "(self) -> str"},
            ]
        }

        mock_client.request = AsyncMock(
            side_effect=[
                {"contents": {"kind": "markdown", "value": "obj: MyClass"}},  # hover
                type_def_response,  # typeDefinition
                completion_response,  # completion
                {"label": "value", "kind": 5, "detail": "int"},  # resolve
                {"label": "name", "kind": 5, "detail": "str"},  # resolve
                {"label": "__init__", "kind": 2, "detail": "(self) -> None"},  # resolve
                {"label": "__str__", "kind": 2, "detail": "(self) -> str"},  # resolve
            ]
        )

        setup_mock_manager(mock_client, tmp_path)

        mock_ctx = AsyncMock()
        result = await type_info(
            file_path=str(test_file), line=5, character=1, ctx=mock_ctx
        )

        assert result["typeName"] == "MyClass"
        assert result["kind"] == "class"
        assert result["sourceLocation"] is not None
        assert len(result["fields"]) == 2
        assert {field["name"] for field in result["fields"]} == {"value", "name"}
        assert result["methods"]["totalItems"] >= 2

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
                hover_response,  # hover fallback
                type_def_response,  # typeDefinition (None)
                completion_response,  # completion
                {"label": "bit_length", "kind": 2, "detail": "(self) -> int"},
                {"label": "to_bytes", "kind": 2, "detail": "(self, ...) -> bytes"},
            ]
        )

        setup_mock_manager(mock_client, tmp_path)

        mock_ctx = AsyncMock()
        result = await type_info(
            file_path=str(test_file), line=1, character=1, ctx=mock_ctx
        )

        assert result["typeName"] == "int"
        assert "kind" not in result
        assert "sourceLocation" not in result
        assert result["fields"] == []
        assert result["methods"]["totalItems"] >= 2

    @pytest.mark.asyncio
    async def test_type_info_no_type_found(self, tmp_path: Path, monkeypatch):
        """Test error when neither typeDefinition nor hover returns useful info."""
        monkeypatch.chdir(tmp_path)
        test_file = tmp_path / "test.py"
        test_file.write_text("# empty\n")

        mock_client = create_mock_client()
        mock_client.request = AsyncMock(
            side_effect=[
                None,  # hover
            ]
        )

        setup_mock_manager(mock_client, tmp_path)

        mock_ctx = AsyncMock()
        result = await type_info(
            file_path=str(test_file), line=1, character=1, ctx=mock_ctx
        )

        assert "error" in result
        assert result["error"]["code"] == "type_not_found"
        assert "Could not determine type" in result["error"]["message"]

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
            {"label": f"method{i}", "kind": 2, "detail": "(self) -> int"}
            for i in range(25)
        ]
        completion_response = {"items": methods}

        # Build side effects with resolve responses
        side_effects = [
            hover_response,  # hover
            None,  # typeDefinition
            completion_response,  # completion
        ]
        # Add resolve responses for each method
        for m in methods:
            side_effects.append(m)

        mock_client.request = AsyncMock(side_effect=side_effects)

        setup_mock_manager(mock_client, tmp_path)

        mock_ctx = AsyncMock()

        # First page: offset=0, limit=10
        result = await type_info(
            file_path=str(test_file),
            line=1,
            character=1,
            limit=10,
            offset=0,
            ctx=mock_ctx,
        )

        assert result["methods"]["totalItems"] == 25
        assert len(result["methods"]["items"]) == 10
        assert result["methods"]["offset"] == 0
        assert result["methods"]["limit"] == 10
        assert result["methods"]["hasMore"] is True
        assert result["methods"]["nextOffset"] == 10

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
            "documentation": {
                "kind": "markdown",
                "value": "Return a copy of the string converted to uppercase.",
            },
        }

        mock_client.request = AsyncMock(
            side_effect=[
                hover_response,  # hover
                None,  # typeDefinition
                completion_response,  # completion
                resolve_response,  # resolve with documentation
            ]
        )

        setup_mock_manager(mock_client, tmp_path)

        mock_ctx = AsyncMock()
        result = await type_info(
            file_path=str(test_file),
            line=1,
            character=1,
            include_documentation=True,
            ctx=mock_ctx,
        )

        assert result["typeName"] == "str"
        assert len(result["methods"]["items"]) >= 1
        # Check that at least one method has documentation
        upper_method = next(
            (m for m in result["methods"]["items"] if m["name"] == "upper"), None
        )
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
                {
                    "label": "some_method",
                    "kind": 2,
                    "detail": "(self) -> int",
                },  # resolve
            ]
        )

        # Mock the manager for version tracking
        mock_manager = MagicMock()
        mock_manager.increment_doc_version.side_effect = [
            2,
            3,
        ]  # First for insert, second for restore
        monkeypatch.setattr("jons_mcp_pyright.server.manager", mock_manager)

        methods = await _get_methods_via_completion(
            mock_client, file_uri, str(test_file), line=0, character=0
        )

        assert len(methods) == 1
        assert methods[0]["name"] == "some_method"
        # Should have called notify twice (insert dot, restore original)
        assert mock_client.notify.call_count == 2
        # Verify versions were incremented
        assert mock_manager.increment_doc_version.call_count == 2

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
                {
                    "label": "a_property",
                    "kind": 10,
                    "detail": "int",
                },  # Property - excluded
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


class TestDiagnosticWaiter:
    """Test the diagnostic waiter mechanism on PyrightClientManager."""

    def _make_manager(self, tmp_path: Path) -> PyrightClientManager:
        """Create a real PyrightClientManager for testing waiter methods."""
        mgr = PyrightClientManager.__new__(PyrightClientManager)
        mgr.root = tmp_path
        mgr.max_active_clients = 1
        mgr.notification_handler = None
        mgr.environments = {}
        mgr._active_count = 0
        mgr._diagnostic_waiters = {}
        return mgr

    def test_register_and_signal_waiter(self, tmp_path: Path):
        """Test that registering a waiter and signaling it via _handle_diagnostics works."""
        mgr = self._make_manager(tmp_path)
        uri = "file:///test.py"

        event = mgr.register_diagnostic_waiter(uri)
        assert not event.is_set()
        assert uri in mgr._diagnostic_waiters
        assert len(mgr._diagnostic_waiters[uri]) == 1

        # Simulate diagnostics arriving
        env = MagicMock(spec=EnvironmentState)
        env.diagnostics = {}
        env.env_id = "test-env"
        mgr._handle_diagnostics(env, {"uri": uri, "diagnostics": []})

        assert event.is_set()
        # Waiter should be popped after signaling
        assert uri not in mgr._diagnostic_waiters

    @pytest.mark.asyncio
    async def test_wait_for_diagnostics_success(self, tmp_path: Path):
        """Test wait_for_diagnostics returns True when events are set."""
        mgr = self._make_manager(tmp_path)
        event = asyncio.Event()
        event.set()

        result = await mgr.wait_for_diagnostics([event], timeout=0.1)
        assert result is True

    @pytest.mark.asyncio
    async def test_wait_for_diagnostics_timeout(self, tmp_path: Path):
        """Test wait_for_diagnostics returns False on timeout and cleans up."""
        mgr = self._make_manager(tmp_path)
        uri = "file:///test.py"
        event = mgr.register_diagnostic_waiter(uri)

        result = await mgr.wait_for_diagnostics([event], timeout=0.05)
        assert result is False
        # Event should be cleaned up
        assert uri not in mgr._diagnostic_waiters

    @pytest.mark.asyncio
    async def test_wait_for_diagnostics_no_events(self, tmp_path: Path):
        """Test wait_for_diagnostics with empty list returns True immediately."""
        mgr = self._make_manager(tmp_path)
        result = await mgr.wait_for_diagnostics([], timeout=0.1)
        assert result is True

    def test_multiple_waiters_same_uri(self, tmp_path: Path):
        """Test multiple waiters on same URI both get signaled."""
        mgr = self._make_manager(tmp_path)
        uri = "file:///test.py"

        event1 = mgr.register_diagnostic_waiter(uri)
        event2 = mgr.register_diagnostic_waiter(uri)
        assert len(mgr._diagnostic_waiters[uri]) == 2

        # Signal via diagnostics
        env = MagicMock(spec=EnvironmentState)
        env.diagnostics = {}
        env.env_id = "test-env"
        mgr._handle_diagnostics(env, {"uri": uri, "diagnostics": []})

        assert event1.is_set()
        assert event2.is_set()
        assert uri not in mgr._diagnostic_waiters

    def test_cleanup_removes_only_specified_events(self, tmp_path: Path):
        """Test _cleanup_waiters removes only the specified events."""
        mgr = self._make_manager(tmp_path)
        uri = "file:///test.py"

        event1 = mgr.register_diagnostic_waiter(uri)
        event2 = mgr.register_diagnostic_waiter(uri)

        # Only clean up event1
        mgr._cleanup_waiters([event1])

        assert uri in mgr._diagnostic_waiters
        assert len(mgr._diagnostic_waiters[uri]) == 1
        assert mgr._diagnostic_waiters[uri][0] is event2

    def test_cleanup_deletes_empty_uri_entries(self, tmp_path: Path):
        """Test _cleanup_waiters removes URI key when list becomes empty."""
        mgr = self._make_manager(tmp_path)
        uri = "file:///test.py"

        event = mgr.register_diagnostic_waiter(uri)
        mgr._cleanup_waiters([event])

        assert uri not in mgr._diagnostic_waiters


class TestDiagnosticsWaiterIntegration:
    """Test the diagnostics tool correctly uses the waiter mechanism."""

    @pytest.mark.asyncio
    async def test_diagnostics_waits_on_new_file(self, tmp_path: Path):
        """When file is not yet opened, diagnostics should register waiter and wait."""
        mock_client = create_mock_client()
        mock_manager = setup_mock_manager(mock_client, tmp_path)

        # File not yet opened
        mock_manager.is_file_opened = MagicMock(return_value=False)
        mock_manager.get_diagnostics_for_file = MagicMock(
            return_value=[{"severity": 1, "message": "Error 1"}]
        )

        result = await diagnostics(file_path=str(tmp_path / "test.py"))

        mock_manager.register_diagnostic_waiter.assert_called_once()
        mock_manager.wait_for_diagnostics.assert_called_once()
        assert len(result["items"]) == 1

    @pytest.mark.asyncio
    async def test_diagnostics_waits_on_stale_file(self, tmp_path: Path):
        """When file is opened but stale, diagnostics should register waiter and wait."""
        test_file = tmp_path / "test.py"
        test_file.write_text("x = 1\n")

        mock_client = create_mock_client()
        mock_manager = setup_mock_manager(mock_client, tmp_path)

        # File opened but stale
        mock_manager.is_file_opened = MagicMock(return_value=True)
        mock_manager.is_file_stale = MagicMock(return_value=True)
        mock_manager.get_diagnostics_for_file = MagicMock(
            return_value=[{"severity": 1, "message": "Error 1"}]
        )

        result = await diagnostics(file_path=str(test_file))

        mock_manager.register_diagnostic_waiter.assert_called_once()
        mock_manager.wait_for_diagnostics.assert_called_once()
        assert len(result["items"]) == 1

    @pytest.mark.asyncio
    async def test_diagnostics_no_wait_when_current(self, tmp_path: Path):
        """When file is opened and fresh, no waiter should be registered."""
        mock_client = create_mock_client()
        mock_manager = setup_mock_manager(mock_client, tmp_path)

        # File opened and current
        mock_manager.is_file_opened = MagicMock(return_value=True)
        mock_manager.is_file_stale = MagicMock(return_value=False)
        mock_manager.get_diagnostics_for_file = MagicMock(
            return_value=[{"severity": 1, "message": "Error 1"}]
        )

        result = await diagnostics(file_path=str(tmp_path / "test.py"))

        mock_manager.register_diagnostic_waiter.assert_not_called()
        mock_manager.wait_for_diagnostics.assert_not_called()
        assert len(result["items"]) == 1

    @pytest.mark.asyncio
    async def test_diagnostics_timeout_returns_cached(self, tmp_path: Path):
        """When waiter times out, cached diagnostics should still be returned."""
        mock_client = create_mock_client()
        mock_manager = setup_mock_manager(mock_client, tmp_path)

        # File not opened (needs refresh)
        mock_manager.is_file_opened = MagicMock(return_value=False)
        # Timeout
        mock_manager.wait_for_diagnostics = AsyncMock(return_value=False)
        mock_manager.get_diagnostics_for_file = MagicMock(
            return_value=[{"severity": 2, "message": "Cached warning"}]
        )

        result = await diagnostics(file_path=str(tmp_path / "test.py"))

        mock_manager.register_diagnostic_waiter.assert_called_once()
        mock_manager.wait_for_diagnostics.assert_called_once()
        # Should still return cached diagnostics
        assert len(result["items"]) == 1
        assert result["items"][0]["message"] == "Cached warning"

    @pytest.mark.asyncio
    async def test_diagnostics_env_mode_refreshes_stale(self, tmp_path: Path):
        """env_id mode should refresh stale files before returning."""
        mock_client = create_mock_client()
        mock_manager = setup_mock_manager(mock_client, tmp_path)

        # Set up environment with opened files
        mock_env = mock_manager.get_environment.return_value
        mock_env.client = mock_client
        file_a = tmp_path / "a.py"
        file_b = tmp_path / "b.py"
        file_a.write_text("a = 1\n")
        file_b.write_text("b = 1\n")
        uri_a = file_a.resolve().as_uri()
        uri_b = file_b.resolve().as_uri()
        mock_env.opened_files = {uri_a, uri_b}

        # a.py is stale, b.py is not
        def is_stale(fp, uri):
            return uri == uri_a

        mock_manager.is_file_stale = MagicMock(side_effect=is_stale)
        mock_manager.get_diagnostics_for_environment = MagicMock(
            return_value={uri_a: [{"severity": 1, "message": "E1"}]}
        )

        result = await diagnostics(env_id=str(tmp_path))

        # Should register waiter only for a.py (stale), not b.py
        mock_manager.register_diagnostic_waiter.assert_called_once_with(uri_a)
        mock_manager.wait_for_diagnostics.assert_called_once()
        assert len(result["items"]) == 1

    @pytest.mark.asyncio
    async def test_diagnostics_aggregate_refreshes_stale(self, tmp_path: Path):
        """Aggregate mode should refresh stale files across all environments."""
        mock_client = create_mock_client()
        mock_manager = setup_mock_manager(mock_client, tmp_path)

        # Set up environment with opened files
        mock_env = mock_manager.get_all_environments.return_value[0]
        mock_env.client = mock_client
        file_a = tmp_path / "a.py"
        file_a.write_text("a = 1\n")
        uri_a = file_a.resolve().as_uri()
        mock_env.opened_files = {uri_a}

        mock_manager.is_file_stale = MagicMock(return_value=True)
        mock_manager.get_all_diagnostics = MagicMock(
            return_value={uri_a: [{"severity": 1, "message": "E1"}]}
        )

        result = await diagnostics()

        mock_manager.register_diagnostic_waiter.assert_called_once_with(uri_a)
        mock_manager.wait_for_diagnostics.assert_called_once()
        assert len(result["items"]) == 1
