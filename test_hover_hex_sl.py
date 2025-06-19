#!/usr/bin/env python3
"""Test hover functionality on hex-sl project."""

import asyncio
import time
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

import pyright_mcp
from pyright_mcp import PyrightClient, handle_diagnostics

async def test_hover():
    """Test hover on a variable in hex-sl project."""
    # Set project directory
    project_dir = Path("/Users/jonmmease/VegaFusion/projects/hex-sl")
    
    print(f"Starting pyright for project: {project_dir}")
    
    # Create and start pyright client
    pyright_mcp.pyright = PyrightClient(project_dir)
    pyright_mcp.pyright.on_notification("textDocument/publishDiagnostics", handle_diagnostics)
    
    try:
        start_time = time.time()
        await pyright_mcp.pyright.start()
        init_time = time.time() - start_time
        print(f"Pyright initialized in {init_time:.2f} seconds")
        
        # Wait a bit more for full analysis
        print("Waiting for pyright to analyze the project...")
        await asyncio.sleep(3.0)
        pyright_mcp.initialization_complete = True
        
        # Test hover on a variable in __init__.py
        file_path = "/Users/jonmmease/VegaFusion/projects/hex-sl/src/hex_sl/__init__.py"
        print(f"\nTesting hover on 'Dataset' at line 10 in {file_path}")
        
        # Clear opened files to test file opening
        pyright_mcp.opened_files.clear()
        
        # Test hover
        hover_start = time.time()
        result = await pyright_mcp.hover.fn(
            file_path=file_path,
            line=10,  # Line with "Dataset" in __all__
            character=5,  # On "Dataset"
            ctx=None
        )
        hover_time = time.time() - hover_start
        
        print(f"\nHover completed in {hover_time:.2f} seconds")
        print(f"Result: {result}")
        
        # Try another hover on an import
        print(f"\nTesting hover on 'SemanticProject' import at line 3")
        hover_start = time.time()
        result2 = await pyright_mcp.hover.fn(
            file_path=file_path,
            line=3,  # from hex_sl.project.semantic_project import SemanticProject
            character=48,  # On SemanticProject
            ctx=None
        )
        hover_time = time.time() - hover_start
        
        print(f"\nSecond hover completed in {hover_time:.2f} seconds")
        print(f"Result: {result2}")
        
    finally:
        if pyright_mcp.pyright:
            await pyright_mcp.pyright.shutdown()
            pyright_mcp.pyright = None

if __name__ == "__main__":
    asyncio.run(test_hover())