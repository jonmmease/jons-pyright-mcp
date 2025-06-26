"""
Integration tests for pyright-mcp server.
"""

import asyncio
import json
from pathlib import Path
import pytest

from jons_mcp_pyright import PyrightClient, mcp
import jons_mcp_pyright


class TestPyrightIntegration:
    """Integration tests with real pyright process."""
    
    @pytest.mark.asyncio
    async def test_basic_hover(self, pyright_client: PyrightClient, temp_python_project: Path):
        """Test hover functionality with real pyright."""
        # Set global client
        jons_mcp_pyright.pyright = pyright_client
        
        # Test hover on the greet function
        file_path = temp_python_project / "src" / "main.py"
        
        result = await jons_mcp_pyright.hover.fn(
            file_path=str(file_path),
            line=2,  # def greet line
            character=4,  # on 'greet'
            ctx=None
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
    async def test_find_definition(self, pyright_client: PyrightClient, temp_python_project: Path):
        """Test go to definition with real pyright."""
        jons_mcp_pyright.pyright = pyright_client
        
        # Create a test file that uses our functions
        test_file = temp_python_project / "test_definition.py"
        test_file.write_text("""from src.main import greet, add

result = greet("World")
sum_val = add(1, 2)
""")
        
        # Wait for pyright to process
        await asyncio.sleep(0.5)
        
        # Find definition of 'greet'
        result = await jons_mcp_pyright.definition.fn(
            file_path=str(test_file),
            line=2,  # result = greet line
            character=9,  # on 'greet'
            ctx=None
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
    async def test_document_symbols(self, pyright_client: PyrightClient, temp_python_project: Path):
        """Test document symbols with real pyright."""
        jons_mcp_pyright.pyright = pyright_client
        
        main_file = temp_python_project / "src" / "main.py"
        result = await jons_mcp_pyright.document_symbols.fn(
            file_path=str(main_file),
            ctx=None
        )
        
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
    async def test_signature_help(self, pyright_client: PyrightClient, temp_python_project: Path):
        """Test signature help with real pyright."""
        jons_mcp_pyright.pyright = pyright_client
        
        # Create a file that calls our functions
        test_file = temp_python_project / "test_signature.py"
        test_file.write_text("""from src.main import greet, add

greet(
add(1, 
""")
        
        # Wait for pyright to process
        await asyncio.sleep(0.5)
        
        # Get signature help for greet
        result = await jons_mcp_pyright.signature_help.fn(
            file_path=str(test_file),
            line=2,  # greet( line
            character=6,  # after the (
            ctx=None
        )
        
        assert "signatures" in result
        assert len(result["signatures"]) > 0
        
        signature = result["signatures"][0]
        assert "label" in signature
        assert "name: str" in signature["label"]
    
    @pytest.mark.asyncio
    async def test_rename_symbol(self, pyright_client: PyrightClient, temp_python_project: Path):
        """Test rename functionality with real pyright."""
        jons_mcp_pyright.pyright = pyright_client
        
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
        result = await jons_mcp_pyright.rename.fn(
            file_path=str(rename_file),
            line=0,  # def old_function line
            character=4,  # on 'old_function'
            new_name="new_function",
            ctx=None
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
async def test_mcp_server_lifecycle():
    """Test the MCP server lifecycle management."""
    # This test verifies the lifespan context manager works correctly
    server = mcp
    
    # Mock the global pyright variable
    original_pyright = jons_mcp_pyright.pyright
    jons_mcp_pyright.pyright = None
    
    try:
        # Test that lifespan is properly configured
        assert server._has_lifespan is True
        
        # The server should have our tools registered
        tools = await server.get_tools()
        # tools is a dictionary mapping tool names to tool objects
        assert "hover" in tools
        assert "completion" in tools
        assert "definition" in tools
        assert "diagnostics" in tools
        
    finally:
        # Restore original
        jons_mcp_pyright.pyright = original_pyright