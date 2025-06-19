#!/usr/bin/env python3
"""Debug hover functionality with detailed logging."""

import asyncio
import time
import sys
import os
from pathlib import Path

# Enable debug logging
os.environ["LOG_LEVEL"] = "DEBUG"

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

import pyright_mcp
from pyright_mcp import PyrightClient, handle_diagnostics

async def test_hover_debug():
    """Test hover with debug info."""
    # Test on a smaller file first
    project_dir = Path("/Users/jonmmease/VegaFusion/projects/hex-sl")
    
    print(f"Starting pyright for project: {project_dir}")
    print(f"Project size check:")
    
    # Count Python files
    py_files = list(project_dir.rglob("*.py"))
    print(f"  Found {len(py_files)} Python files")
    
    # Check for pyright config
    pyright_config = project_dir / "pyrightconfig.json"
    if pyright_config.exists():
        print(f"  pyrightconfig.json exists")
        with open(pyright_config) as f:
            print(f"  Config: {f.read()[:200]}...")
    
    # Create and start pyright client
    pyright_mcp.pyright = PyrightClient(project_dir)
    pyright_mcp.pyright.on_notification("textDocument/publishDiagnostics", handle_diagnostics)
    
    try:
        start_time = time.time()
        await pyright_mcp.pyright.start()
        init_time = time.time() - start_time
        print(f"\nPyright initialized in {init_time:.2f} seconds")
        
        # Don't wait for full analysis, just try hover immediately
        pyright_mcp.initialization_complete = True
        
        # Test on a simple file
        file_path = str(project_dir / "src/hex_sl/datatype.py")
        print(f"\nTesting hover on a simpler file: {file_path}")
        
        # Clear opened files
        pyright_mcp.opened_files.clear()
        
        # Open and read first few lines to know what to hover on
        with open(file_path) as f:
            lines = f.readlines()[:20]
            for i, line in enumerate(lines):
                if line.strip() and not line.strip().startswith("#"):
                    print(f"  Line {i}: {line.rstrip()}")
        
        # Try hover on line 5 (should have some code)
        hover_start = time.time()
        result = await pyright_mcp.hover.fn(
            file_path=file_path,
            line=5,
            character=10,
            ctx=None
        )
        hover_time = time.time() - hover_start
        
        print(f"\nHover completed in {hover_time:.2f} seconds")
        print(f"Result: {result}")
        
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
    asyncio.run(test_hover_debug())