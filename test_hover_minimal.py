#!/usr/bin/env python3
"""Minimal test of hover functionality."""

import asyncio
import time
import sys
import os
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

import pyright_mcp
from pyright_mcp import PyrightClient, handle_diagnostics, ensure_file_uri, ensure_file_open

async def test_hover_minimal():
    """Test hover with minimal setup."""
    # Create a minimal test project
    test_dir = Path("/tmp/pyright_test")
    test_dir.mkdir(exist_ok=True)
    
    # Create a simple Python file
    test_file = test_dir / "test.py"
    test_file.write_text("""# Simple test file
import os

def greet(name: str) -> str:
    '''Greet someone by name.'''
    return f"Hello, {name}!"

result = greet("World")
print(result)
""")
    
    print(f"Testing hover on simple project: {test_dir}")
    
    # Create and start pyright client
    pyright_mcp.pyright = PyrightClient(test_dir)
    pyright_mcp.pyright.on_notification("textDocument/publishDiagnostics", handle_diagnostics)
    
    try:
        start_time = time.time()
        await pyright_mcp.pyright.start()
        init_time = time.time() - start_time
        print(f"Pyright initialized in {init_time:.2f} seconds")
        
        # Mark as initialized
        pyright_mcp.initialization_complete = True
        
        # Wait a moment for analysis
        await asyncio.sleep(1.0)
        
        # Test hover on the greet function
        file_path = str(test_file)
        print(f"\nTesting hover on 'greet' function at line 3")
        
        # Ensure file is open
        file_uri = ensure_file_uri(file_path)
        await ensure_file_open(pyright_mcp.pyright, file_path, file_uri)
        
        # Direct LSP request
        hover_start = time.time()
        response = await pyright_mcp.pyright.request("textDocument/hover", {
            "textDocument": {"uri": file_uri},
            "position": {"line": 3, "character": 4}  # On "greet"
        })
        hover_time = time.time() - hover_start
        
        print(f"\nHover completed in {hover_time:.2f} seconds")
        print(f"Response: {response}")
        
        # Now test on hex-sl if requested
        if "--hex-sl" in sys.argv:
            print("\n" + "="*60)
            print("Testing on hex-sl project...")
            
            # Shutdown current client
            await pyright_mcp.pyright.shutdown()
            
            # Start new client for hex-sl
            hex_sl_dir = Path("/Users/jonmmease/VegaFusion/projects/hex-sl")
            pyright_mcp.pyright = PyrightClient(hex_sl_dir)
            pyright_mcp.pyright.on_notification("textDocument/publishDiagnostics", handle_diagnostics)
            
            start_time = time.time()
            await pyright_mcp.pyright.start()
            init_time = time.time() - start_time
            print(f"Pyright initialized for hex-sl in {init_time:.2f} seconds")
            
            # Wait for analysis
            print("Waiting for project analysis...")
            await asyncio.sleep(5.0)
            
            # Test hover
            file_path = str(hex_sl_dir / "src/hex_sl/__init__.py")
            file_uri = ensure_file_uri(file_path)
            
            await ensure_file_open(pyright_mcp.pyright, file_path, file_uri)
            
            hover_start = time.time()
            response = await pyright_mcp.pyright.request("textDocument/hover", {
                "textDocument": {"uri": file_uri},
                "position": {"line": 2, "character": 30}  # On "Dataset" in import
            })
            hover_time = time.time() - hover_start
            
            print(f"\nHover on hex-sl completed in {hover_time:.2f} seconds")
            print(f"Response: {response}")
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if pyright_mcp.pyright:
            print("\nShutting down pyright...")
            await pyright_mcp.pyright.shutdown()
            pyright_mcp.pyright = None

if __name__ == "__main__":
    asyncio.run(test_hover_minimal())