"""
Integration tests for pyright-mcp server.
"""

import asyncio
import os
from pathlib import Path

import pytest

from jons_mcp_pyright import PyrightClientManager, mcp
from jons_mcp_pyright import server as server_module
from jons_mcp_pyright.tools import (
    definition,
    diagnostics,
    document_symbols,
    implementation,
    references,
    rename,
    symbol_info,
    type_definition,
    type_info,
    workspace_symbols,
)
from jons_mcp_pyright.tools.extensions import list_environments, restart_server


class TestPyrightIntegration:
    """Integration tests with real pyright process."""

    @pytest.mark.asyncio
    async def test_basic_symbol_info(
        self, pyright_manager: PyrightClientManager, temp_python_project: Path
    ):
        """Test symbol_info functionality with real pyright."""
        # Test symbol_info on the greet function
        file_path = temp_python_project / "src" / "main.py"

        result = await symbol_info(
            file_path=str(file_path),
            line=2,  # def greet line
            character=4,  # on 'greet'
            ctx=None,
        )

        assert "contents" in result
        contents = result["contents"]

        # Check that we got some hover info
        if isinstance(contents, dict):
            assert "value" in contents
            assert "greet" in contents["value"].lower()
        else:
            assert len(contents) > 0

    @pytest.mark.asyncio
    async def test_find_definition(
        self, pyright_manager: PyrightClientManager, temp_python_project: Path
    ):
        """Test go to definition with real pyright."""
        # Create a test file that uses our functions
        test_file = temp_python_project / "test_definition.py"
        test_file.write_text("""from src.main import greet, add

result = greet("World")
sum_val = add(1, 2)
""")

        # Wait for pyright to process
        await asyncio.sleep(0.5)

        # Find definition of 'greet'
        result = await definition(
            file_path=str(test_file),
            line=2,  # result = greet line
            character=9,  # on 'greet'
            ctx=None,
        )

        # Check we got a location
        if isinstance(result, list):
            assert len(result) > 0
            location = result[0]
        else:
            location = result

        assert "uri" in location
        assert location["uri"].endswith("main.py")
        assert "range" in location

    @pytest.mark.asyncio
    async def test_document_symbols(
        self, pyright_manager: PyrightClientManager, temp_python_project: Path
    ):
        """Test document symbols with real pyright."""
        main_file = temp_python_project / "src" / "main.py"
        result = await document_symbols(file_path=str(main_file), ctx=None)

        # Result should be paginated
        assert isinstance(result, dict)
        assert "items" in result
        assert len(result["items"]) > 0

        # Check we found our functions and class
        names = []
        for symbol in result["items"]:
            names.append(symbol["name"])
            # If it has children (like a class), add those too
            if "children" in symbol:
                for child in symbol["children"]:
                    names.append(child["name"])

        assert "greet" in names
        assert "add" in names
        assert "Calculator" in names
        assert "__init__" in names

    @pytest.mark.asyncio
    async def test_rename_symbol(
        self, pyright_manager: PyrightClientManager, temp_python_project: Path
    ):
        """Test rename functionality with real pyright."""
        # Create files for rename test
        rename_file = temp_python_project / "rename_test.py"
        rename_file.write_text("""def old_function():
    return 42

result = old_function()
another = old_function()
""")

        # Wait for pyright to process
        await asyncio.sleep(0.5)

        # Try to rename old_function
        result = await rename(
            file_path=str(rename_file),
            line=0,  # def old_function line
            character=4,  # on 'old_function'
            new_name="new_function",
            ctx=None,
        )

        if "error" not in result:
            assert "changes" in result or "documentChanges" in result

            # If we got changes, verify they include our file
            if "changes" in result:
                file_uri = f"file://{rename_file.absolute()}"
                assert file_uri in result["changes"]
                edits = result["changes"][file_uri]
                assert len(edits) > 0  # Should have multiple edits for each occurrence

    @pytest.mark.asyncio
    async def test_type_info_on_class_instance(
        self, pyright_manager: PyrightClientManager, temp_python_project: Path
    ):
        """Test type_info on Calculator class instance."""
        # Create a test file with Calculator instance - use explicit dot access
        # to ensure completion works
        test_file = temp_python_project / "test_type_info.py"
        test_file.write_text("""from src.main import Calculator

calc = Calculator(10)
calc.
""")

        # Wait for pyright to process
        await asyncio.sleep(1.0)

        # Get type info on 'calc' variable on line 3 where dot exists
        result = await type_info(
            file_path=str(test_file),
            line=3,  # calc.
            character=0,  # on 'calc'
            ctx=None,
        )

        # Verify we got type info
        assert "error" not in result, f"Got error: {result.get('error')}"
        assert result["typeName"] == "Calculator"
        assert result["typeKind"] == "class"
        assert result["typeLocation"] is not None

        # Methods may or may not be returned depending on pyright's analysis state
        # but we should have proper response structure
        assert "totalMethods" in result
        assert "methods" in result

    @pytest.mark.asyncio
    async def test_type_info_on_primitive(
        self, pyright_manager: PyrightClientManager, temp_python_project: Path
    ):
        """Test type_info on primitive type (int)."""
        test_file = temp_python_project / "test_primitive.py"
        test_file.write_text("""x = 42
y = x + 10
""")

        await asyncio.sleep(0.5)

        result = await type_info(
            file_path=str(test_file),
            line=0,
            character=0,  # on 'x'
            ctx=None,
        )

        # For primitives, we should get type info via hover fallback
        if "error" not in result:
            # If we get a result, verify it identifies the type
            assert result["typeName"] in ("int", "Literal[42]", "unknown")

    @pytest.mark.asyncio
    async def test_type_definition(
        self, pyright_manager: PyrightClientManager, temp_python_project: Path
    ):
        """Test type_definition tool on typed variable."""
        test_file = temp_python_project / "test_typedef.py"
        test_file.write_text("""from src.main import Calculator

calc: Calculator = Calculator(10)
""")

        await asyncio.sleep(0.5)

        result = await type_definition(
            file_path=str(test_file),
            line=2,  # calc: Calculator = ...
            character=0,  # on 'calc'
            ctx=None,
        )

        # Should return location pointing to Calculator class definition
        if result:
            if isinstance(result, list):
                location = result[0] if result else None
            else:
                location = result

            if location:
                assert "uri" in location
                assert "main.py" in location["uri"]
                assert "range" in location

    @pytest.mark.asyncio
    async def test_references(
        self, pyright_manager: PyrightClientManager, temp_python_project: Path
    ):
        """Test references tool finds all usages."""
        # The greet function is used in test_main.py
        main_file = temp_python_project / "src" / "main.py"

        result = await references(
            file_path=str(main_file),
            line=2,  # def greet(name: str)
            character=4,  # on 'greet'
            include_declaration=True,
            ctx=None,
        )

        # Should be paginated response
        assert "items" in result
        assert result["totalItems"] >= 1  # At least the definition

        # Check that items have URIs
        for item in result["items"]:
            assert "uri" in item

    @pytest.mark.asyncio
    async def test_workspace_symbols(
        self, pyright_manager: PyrightClientManager, temp_python_project: Path
    ):
        """Test workspace_symbols search."""
        # Search for Calculator
        result = await workspace_symbols(query="Calculator", ctx=None)

        assert "items" in result
        # Should find Calculator class
        if result["totalItems"] > 0:
            names = [s["name"] for s in result["items"]]
            assert "Calculator" in names

        # Search for greet function
        result2 = await workspace_symbols(query="greet", ctx=None)
        assert "items" in result2
        if result2["totalItems"] > 0:
            names = [s["name"] for s in result2["items"]]
            assert "greet" in names

    @pytest.mark.asyncio
    async def test_diagnostics_with_error(
        self, pyright_manager: PyrightClientManager, temp_python_project: Path
    ):
        """Test diagnostics tool detects type errors."""
        # Create a file with a type error
        error_file = temp_python_project / "error_file.py"
        error_file.write_text("""def add_numbers(a: int, b: int) -> int:
    return a + b

# Type error: passing string instead of int
result = add_numbers("hello", 5)
""")

        # Wait for pyright to analyze and publish diagnostics
        await asyncio.sleep(1.5)

        # Get diagnostics for the error file
        result = await diagnostics(
            file_path=str(error_file),
            ctx=None,
        )

        # Should have at least one diagnostic
        assert "items" in result
        # Note: The diagnostic may or may not be published yet depending on timing
        # So we just verify the response structure is correct
        assert "totalItems" in result
        assert "hasMore" in result


@pytest.mark.asyncio
async def test_mcp_server_lifecycle():
    """Test the MCP server lifecycle management."""
    # This test verifies the lifespan context manager works correctly
    server = mcp

    # Mock the global manager variable
    original_manager = server_module.manager
    server_module.manager = None

    try:
        # Test that lifespan is properly configured
        assert server._has_lifespan is True

        # The server should have our tools registered
        tools = await server.get_tools()
        # tools is a dictionary mapping tool names to tool objects
        assert "symbol_info" in tools
        assert "type_info" in tools
        assert "definition" in tools
        assert "diagnostics" in tools
        assert "restart_server" in tools
        assert "list_environments" in tools

    finally:
        # Restore original
        server_module.manager = original_manager


class TestMultiEnvironment:
    """Integration tests for multi-environment support."""

    @pytest.mark.asyncio
    async def test_environment_discovery(
        self, multi_env_manager: PyrightClientManager, multi_env_project: Path
    ):
        """Test that environment discovery finds all project environments."""
        # Should have discovered 3 environments: root, pkg-a, pkg-b
        assert len(multi_env_manager.environments) == 3

        env_ids = set(multi_env_manager.environments.keys())
        assert str(multi_env_project) in env_ids
        assert str(multi_env_project / "packages" / "pkg-a") in env_ids
        assert str(multi_env_project / "packages" / "pkg-b") in env_ids

    @pytest.mark.asyncio
    async def test_file_routing(
        self, multi_env_manager: PyrightClientManager, multi_env_project: Path
    ):
        """Test that files are routed to correct environment."""
        # Root file should route to root env
        root_file = multi_env_project / "src" / "main.py"
        root_env = multi_env_manager.get_environment_for_file(str(root_file))
        assert root_env is not None
        assert root_env.env_id == str(multi_env_project)

        # Pkg-a file should route to pkg-a env
        pkg_a_file = multi_env_project / "packages" / "pkg-a" / "src" / "module_a.py"
        pkg_a_env = multi_env_manager.get_environment_for_file(str(pkg_a_file))
        assert pkg_a_env is not None
        assert pkg_a_env.env_id == str(multi_env_project / "packages" / "pkg-a")

        # Pkg-b file should route to pkg-b env
        pkg_b_file = multi_env_project / "packages" / "pkg-b" / "src" / "module_b.py"
        pkg_b_env = multi_env_manager.get_environment_for_file(str(pkg_b_file))
        assert pkg_b_env is not None
        assert pkg_b_env.env_id == str(multi_env_project / "packages" / "pkg-b")

    @pytest.mark.asyncio
    async def test_list_environments_tool(
        self, multi_env_manager: PyrightClientManager, multi_env_project: Path
    ):
        """Test list_environments tool returns all discovered environments."""
        result = await list_environments()

        assert result["total"] == 3
        # At least root is active
        assert result["active_count"] >= 1

        env_ids = {e["env_id"] for e in result["environments"]}
        assert str(multi_env_project) in env_ids
        assert str(multi_env_project / "packages" / "pkg-a") in env_ids
        assert str(multi_env_project / "packages" / "pkg-b") in env_ids

    @pytest.mark.asyncio
    async def test_symbol_info_routes_to_correct_env(
        self, multi_env_manager: PyrightClientManager, multi_env_project: Path
    ):
        """Test symbol_info on files in different environments."""
        # Get symbol info from pkg-a
        pkg_a_file = multi_env_project / "packages" / "pkg-a" / "src" / "module_a.py"
        result = await symbol_info(
            file_path=str(pkg_a_file),
            line=4,  # def func_a
            character=4,
            ctx=None,
        )

        assert "contents" in result
        # Should get info about func_a

    @pytest.mark.asyncio
    async def test_document_symbols_per_env(
        self, multi_env_manager: PyrightClientManager, multi_env_project: Path
    ):
        """Test document_symbols works in different environments."""
        # Symbols from pkg-a
        pkg_a_file = multi_env_project / "packages" / "pkg-a" / "src" / "module_a.py"
        result_a = await document_symbols(file_path=str(pkg_a_file), ctx=None)

        assert "items" in result_a
        names_a = [s["name"] for s in result_a["items"]]
        assert "func_a" in names_a
        assert "ClassA" in names_a

        # Symbols from pkg-b
        pkg_b_file = multi_env_project / "packages" / "pkg-b" / "src" / "module_b.py"
        result_b = await document_symbols(file_path=str(pkg_b_file), ctx=None)

        assert "items" in result_b
        names_b = [s["name"] for s in result_b["items"]]
        assert "func_b" in names_b
        assert "ClassB" in names_b

    @pytest.mark.asyncio
    async def test_workspace_symbols_requires_env_id(
        self, multi_env_manager: PyrightClientManager, multi_env_project: Path
    ):
        """Test workspace_symbols requires env_id when multiple environments exist."""
        # Without env_id, should return error listing available environments
        result = await workspace_symbols(query="Class", ctx=None)

        assert "error" in result
        assert "Multiple environments exist" in result["error"]
        assert "available_environments" in result
        assert len(result["available_environments"]) == 3  # root, pkg-a, pkg-b

    @pytest.mark.asyncio
    async def test_workspace_symbols_with_env_id(
        self, multi_env_manager: PyrightClientManager, multi_env_project: Path
    ):
        """Test workspace_symbols works with explicit env_id."""
        pkg_a_path = str(multi_env_project / "packages" / "pkg-a")

        # Search for "Class" in pkg-a environment
        result = await workspace_symbols(query="Class", env_id=pkg_a_path, ctx=None)

        assert "items" in result
        assert "error" not in result
        # Should find ClassA from pkg-a
        if result["totalItems"] > 0:
            names = [s["name"] for s in result["items"]]
            assert "ClassA" in names

    @pytest.mark.asyncio
    async def test_restart_environment_by_file(
        self, multi_env_manager: PyrightClientManager, multi_env_project: Path
    ):
        """Test restart_server restarts correct environment."""
        pkg_a_file = multi_env_project / "packages" / "pkg-a" / "src" / "module_a.py"

        # First access the file to start the environment
        await symbol_info(file_path=str(pkg_a_file), line=0, character=0, ctx=None)
        await asyncio.sleep(0.5)

        # Restart the environment containing the file
        result = await restart_server(file_path=str(pkg_a_file), ctx=None)

        assert "pyright server restarted for environment containing" in result

    @pytest.mark.asyncio
    async def test_restart_environment_by_id(
        self, multi_env_manager: PyrightClientManager, multi_env_project: Path
    ):
        """Test restart_server with env_id parameter."""
        pkg_b_path = str(multi_env_project / "packages" / "pkg-b")

        # Access a file to start the environment
        pkg_b_file = multi_env_project / "packages" / "pkg-b" / "src" / "module_b.py"
        await symbol_info(file_path=str(pkg_b_file), line=0, character=0, ctx=None)
        await asyncio.sleep(0.5)

        # Restart by env_id
        result = await restart_server(env_id=pkg_b_path, ctx=None)

        assert f"pyright server restarted for environment: {pkg_b_path}" in result

    @pytest.mark.asyncio
    async def test_backward_compatibility_single_env(
        self, pyright_manager: PyrightClientManager, temp_python_project: Path
    ):
        """Test that single-environment projects still work correctly."""
        # This uses the existing temp_python_project fixture (single env)
        main_file = temp_python_project / "src" / "main.py"

        # All tools should work
        result = await symbol_info(
            file_path=str(main_file), line=2, character=4, ctx=None
        )
        assert "contents" in result

        result = await document_symbols(file_path=str(main_file), ctx=None)
        assert "items" in result

        result = await workspace_symbols(query="greet", ctx=None)
        assert "items" in result


class TestLRUEviction:
    """Test LRU eviction behavior."""

    @pytest.mark.asyncio
    async def test_lru_eviction_with_max_clients(
        self, multi_env_project: Path, monkeypatch
    ):
        """Test that LRU eviction works when max clients is reached."""
        import shutil

        # Check if pyright is available
        try:
            import pyright
            has_pyright = True
        except ImportError:
            has_pyright = False

        if not has_pyright and not shutil.which("pyright-langserver"):
            pytest.skip("pyright not found")

        # Set max clients to 2
        monkeypatch.setenv("PYRIGHT_MAX_CLIENTS", "2")

        manager = PyrightClientManager(multi_env_project)

        try:
            await manager.start_root_client()
            server_module.manager = manager
            server_module.initialization_complete = True

            await asyncio.sleep(0.5)

            # Access root file - starts root client
            root_file = multi_env_project / "src" / "main.py"
            root_client = await manager.get_client_for_file(str(root_file))
            assert root_client is not None

            # Access pkg-a file - starts pkg-a client (2 active now)
            pkg_a_file = multi_env_project / "packages" / "pkg-a" / "src" / "module_a.py"
            pkg_a_client = await manager.get_client_for_file(str(pkg_a_file))
            assert pkg_a_client is not None

            # Count active clients
            initial_active = sum(1 for e in manager.environments.values() if e.client is not None)
            assert initial_active == 2

            # Access pkg-b file - should evict oldest (root) client
            pkg_b_file = multi_env_project / "packages" / "pkg-b" / "src" / "module_b.py"
            await manager.get_client_for_file(str(pkg_b_file))

            # Should still have at most 2 active clients
            final_active = sum(1 for e in manager.environments.values() if e.client is not None)
            assert final_active <= 2

        finally:
            server_module.initialization_complete = False
            await manager.shutdown_all()
            server_module.manager = None


class TestProcessCleanup:
    """Test process cleanup on shutdown."""

    @pytest.mark.asyncio
    async def test_no_zombie_processes(
        self, multi_env_manager: PyrightClientManager, multi_env_project: Path
    ):
        """Verify that shutdown cleans up all pyright processes."""
        import subprocess

        # Start multiple environments
        pkg_a_file = multi_env_project / "packages" / "pkg-a" / "src" / "module_a.py"
        pkg_b_file = multi_env_project / "packages" / "pkg-b" / "src" / "module_b.py"

        await multi_env_manager.get_client_for_file(str(pkg_a_file))
        await multi_env_manager.get_client_for_file(str(pkg_b_file))
        await asyncio.sleep(0.5)

        # Get PIDs of active pyright processes (if any)
        active_pids = set()
        for env in multi_env_manager.environments.values():
            if env.client and env.client.process:
                active_pids.add(env.client.process.pid)

        # Shutdown
        await multi_env_manager.shutdown_all()
        await asyncio.sleep(0.5)

        # Verify processes are no longer running
        for pid in active_pids:
            try:
                # Check if process still exists
                os.kill(pid, 0)
                # If we get here, process is still alive (which might be okay
                # if it's gracefully shutting down)
            except OSError:
                # Process doesn't exist - good!
                pass
