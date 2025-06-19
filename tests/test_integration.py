"""
Integration tests for pyright-mcp server.
"""

import asyncio
import json
from pathlib import Path
import pytest

from pyright_mcp import PyrightClient, mcp
import pyright_mcp


class TestPyrightIntegration:
    """Integration tests with real pyright process."""
    
    @pytest.mark.asyncio
    async def test_basic_hover(self, pyright_client: PyrightClient, temp_python_project: Path):
        """Test hover functionality with real pyright."""
        # Set global client
        pyright_mcp.pyright = pyright_client
        
        # Test hover on the greet function
        file_path = temp_python_project / "src" / "main.py"
        
        result = await pyright_mcp.hover.fn(
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
    async def test_basic_completion(self, pyright_client: PyrightClient, temp_python_project: Path):
        """Test completion functionality with real pyright."""
        pyright_mcp.pyright = pyright_client
        
        # Create a test file that imports from our module
        test_file = temp_python_project / "test_completion.py"
        test_file.write_text("""from src.main import 

calc = Calculator()
calc.""")
        
        # Wait for pyright to process the file
        await asyncio.sleep(0.5)
        
        # Test completion after 'calc.'
        result = await pyright_mcp.completion.fn(
            file_path=str(test_file),
            line=2,  # calc. line
            character=5,  # after the dot
            ctx=None
        )
        
        assert isinstance(result, list)
        assert len(result) > 0
        
        # Check that we get Calculator methods
        labels = [item["label"] for item in result]
        assert "add" in labels
        assert "multiply" in labels
        assert "get_value" in labels
    
    @pytest.mark.asyncio
    async def test_find_definition(self, pyright_client: PyrightClient, temp_python_project: Path):
        """Test go to definition with real pyright."""
        pyright_mcp.pyright = pyright_client
        
        # Create a test file that uses our functions
        test_file = temp_python_project / "test_definition.py"
        test_file.write_text("""from src.main import greet, add

result = greet("World")
sum_val = add(1, 2)
""")
        
        # Wait for pyright to process
        await asyncio.sleep(0.5)
        
        # Find definition of 'greet'
        result = await pyright_mcp.definition.fn(
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
    async def test_find_references(self, pyright_client: PyrightClient, temp_python_project: Path):
        """Test find references with real pyright."""
        pyright_mcp.pyright = pyright_client
        
        # Create test files that reference our Calculator class
        test_file1 = temp_python_project / "use_calc1.py"
        test_file1.write_text("""from src.main import Calculator

calc = Calculator()
""")
        
        test_file2 = temp_python_project / "use_calc2.py"
        test_file2.write_text("""from src.main import Calculator

def make_calculator():
    return Calculator(10)
""")
        
        # Wait for pyright to process
        await asyncio.sleep(0.5)
        
        # Find references to Calculator in main.py
        main_file = temp_python_project / "src" / "main.py"
        result = await pyright_mcp.references.fn(
            file_path=str(main_file),
            line=28,  # class Calculator line
            character=6,  # on 'Calculator'
            include_declaration=True
        )
        
        assert isinstance(result, list)
        # Should find at least the declaration and our two uses
        assert len(result) >= 3
        
        # Check that we found references in our test files
        uris = [ref["uri"] for ref in result]
        assert any("use_calc1.py" in uri for uri in uris)
        assert any("use_calc2.py" in uri for uri in uris)
    
    @pytest.mark.asyncio
    async def test_document_symbols(self, pyright_client: PyrightClient, temp_python_project: Path):
        """Test document symbols with real pyright."""
        pyright_mcp.pyright = pyright_client
        
        main_file = temp_python_project / "src" / "main.py"
        result = await pyright_mcp.document_symbols.fn(
            file_path=str(main_file),
            ctx=None
        )
        
        assert isinstance(result, list)
        assert len(result) > 0
        
        # Check we found our functions and class
        names = []
        for symbol in result:
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
    async def test_diagnostics(self, pyright_client: PyrightClient, temp_python_project: Path):
        """Test diagnostics with real pyright."""
        pyright_mcp.pyright = pyright_client
        
        # Create a file with type errors
        error_file = temp_python_project / "errors.py"
        error_file.write_text('''"""File with intentional errors."""

def bad_function(x: int) -> str:
    return x  # Type error: returning int instead of str

undefined_variable = unknown_var  # Name error

def missing_return() -> int:
    pass  # Missing return statement
''')
        
        # Wait for pyright to analyze
        await asyncio.sleep(1.0)
        
        # Get diagnostics
        result = await pyright_mcp.diagnostics.fn()
        
        # Find diagnostics for our error file
        error_uri = f"file://{error_file.absolute()}"
        assert error_uri in result
        
        diagnostics = result[error_uri]
        assert len(diagnostics) > 0
        
        # Check we found our errors
        messages = [d["message"] for d in diagnostics]
        assert any("Cannot return value of type" in msg or "incompatible return value" in msg.lower() for msg in messages)
        assert any("not defined" in msg for msg in messages)
    
    @pytest.mark.asyncio
    async def test_signature_help(self, pyright_client: PyrightClient, temp_python_project: Path):
        """Test signature help with real pyright."""
        pyright_mcp.pyright = pyright_client
        
        # Create a file that calls our functions
        test_file = temp_python_project / "test_signature.py"
        test_file.write_text("""from src.main import greet, add

greet(
add(1, 
""")
        
        # Wait for pyright to process
        await asyncio.sleep(0.5)
        
        # Get signature help for greet
        result = await pyright_mcp.signature_help.fn(
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
    async def test_organize_imports(self, pyright_client: PyrightClient, temp_python_project: Path):
        """Test organize imports with real pyright."""
        pyright_mcp.pyright = pyright_client
        
        # Create a file with unorganized imports
        messy_file = temp_python_project / "messy_imports.py"
        messy_file.write_text("""import sys
from typing import List
import os
from pathlib import Path
import json

def test():
    pass
""")
        
        # Wait for pyright to process
        await asyncio.sleep(0.5)
        
        # Organize imports
        result = await pyright_mcp.organize_imports.fn(
            file_path=str(messy_file),
            ctx=None
        )
        
        # Result could be empty if imports are already organized
        # or contain edits to reorganize
        assert isinstance(result, list)
    
    @pytest.mark.asyncio
    async def test_rename_symbol(self, pyright_client: PyrightClient, temp_python_project: Path):
        """Test rename functionality with real pyright."""
        pyright_mcp.pyright = pyright_client
        
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
        result = await pyright_mcp.rename.fn(
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
    original_pyright = pyright_mcp.pyright
    pyright_mcp.pyright = None
    
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
        pyright_mcp.pyright = original_pyright