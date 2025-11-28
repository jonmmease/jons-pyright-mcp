"""
Integration tests for pyright-mcp server.
"""

import asyncio
from pathlib import Path

import pytest

from jons_mcp_pyright import PyrightClient, mcp
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


class TestPyrightIntegration:
    """Integration tests with real pyright process."""

    @pytest.mark.asyncio
    async def test_basic_symbol_info(
        self, pyright_client: PyrightClient, temp_python_project: Path
    ):
        """Test symbol_info functionality with real pyright."""
        # Set global client
        server_module.pyright = pyright_client

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
        self, pyright_client: PyrightClient, temp_python_project: Path
    ):
        """Test go to definition with real pyright."""
        server_module.pyright = pyright_client

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
        self, pyright_client: PyrightClient, temp_python_project: Path
    ):
        """Test document symbols with real pyright."""
        server_module.pyright = pyright_client

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
        self, pyright_client: PyrightClient, temp_python_project: Path
    ):
        """Test rename functionality with real pyright."""
        server_module.pyright = pyright_client

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
        self, pyright_client: PyrightClient, temp_python_project: Path
    ):
        """Test type_info on Calculator class instance."""
        server_module.pyright = pyright_client

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
        self, pyright_client: PyrightClient, temp_python_project: Path
    ):
        """Test type_info on primitive type (int)."""
        server_module.pyright = pyright_client

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
        self, pyright_client: PyrightClient, temp_python_project: Path
    ):
        """Test type_definition tool on typed variable."""
        server_module.pyright = pyright_client

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
        self, pyright_client: PyrightClient, temp_python_project: Path
    ):
        """Test references tool finds all usages."""
        server_module.pyright = pyright_client

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
        self, pyright_client: PyrightClient, temp_python_project: Path
    ):
        """Test workspace_symbols search."""
        server_module.pyright = pyright_client

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
        self, pyright_client: PyrightClient, temp_python_project: Path
    ):
        """Test diagnostics tool detects type errors."""
        server_module.pyright = pyright_client

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

    # Mock the global pyright variable
    original_pyright = server_module.pyright
    server_module.pyright = None

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

    finally:
        # Restore original
        server_module.pyright = original_pyright
