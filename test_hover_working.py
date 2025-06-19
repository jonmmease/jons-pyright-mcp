#!/usr/bin/env python3
"""Test hover with proper message handling."""

import asyncio
import sys
import os
from pathlib import Path

# Set timeout for testing
os.environ["PYRIGHT_TIMEOUT"] = "10"

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

import pyright_mcp
from pyright_mcp import PyrightClient, handle_diagnostics

async def test_hover_on_hex_sl():
    """Test hover on hex-sl project."""
    project_dir = Path("/Users/jonmmease/VegaFusion/projects/hex-sl")
    
    print(f"Starting pyright for project: {project_dir}")
    print("Note: First initialization of a large project can take 30-60 seconds")
    
    # Create client with shorter timeout for testing
    pyright_mcp.pyright = PyrightClient(project_dir)
    pyright_mcp.pyright.request_timeout = 10.0  # 10 second timeout
    pyright_mcp.pyright.on_notification("textDocument/publishDiagnostics", handle_diagnostics)
    
    try:
        # Start pyright
        print("Initializing pyright...")
        await pyright_mcp.pyright.start()
        
        # Mark as initialized
        pyright_mcp.initialization_complete = True
        
        # Give it time to analyze (hex-sl is a large project)
        print("Waiting for initial project analysis (this may take a while)...")
        await asyncio.sleep(5.0)
        
        # Test file
        test_file = project_dir / "src" / "hex_sl" / "__init__.py"
        file_uri = f"file://{test_file.absolute()}"
        
        # Read the file to see what we're hovering on
        with open(test_file) as f:
            lines = f.readlines()
            print(f"\nFile content around line 2:")
            for i in range(max(0, 1), min(len(lines), 5)):
                print(f"  {i}: {lines[i].rstrip()}")
        
        # Open the file first
        print(f"\nOpening file: {test_file}")
        await pyright_mcp.pyright.notify("textDocument/didOpen", {
            "textDocument": {
                "uri": file_uri,
                "languageId": "python", 
                "version": 1,
                "text": open(test_file).read()
            }
        })
        pyright_mcp.opened_files.add(file_uri)
        
        # Give pyright a moment to process the file
        await asyncio.sleep(1.0)
        
        # Try hover on line 2 (HqlQuery import)
        print(f"\nRequesting hover at line 2, character 20...")
        try:
            response = await pyright_mcp.pyright.request("textDocument/hover", {
                "textDocument": {"uri": file_uri},
                "position": {"line": 2, "character": 20}  # On "HqlQuery"
            })
            
            if response:
                print(f"\nHover response received!")
                if isinstance(response, dict) and "contents" in response:
                    contents = response["contents"]
                    if isinstance(contents, dict) and "value" in contents:
                        print(f"Hover info:\n{contents['value']}")
                    else:
                        print(f"Hover contents: {contents}")
                else:
                    print(f"Response: {response}")
            else:
                print("No hover information available")
                
        except asyncio.TimeoutError:
            print("ERROR: Hover request timed out after 10 seconds")
            print("This suggests pyright is still analyzing the project or having issues")
            
        # Try a simpler hover on a built-in
        print(f"\nTrying hover on '__all__' at line 9...")
        try:
            response = await pyright_mcp.pyright.request("textDocument/hover", {
                "textDocument": {"uri": file_uri},
                "position": {"line": 9, "character": 0}  # On "__all__"
            })
            
            if response:
                print(f"Hover response: {response}")
            else:
                print("No hover information")
                
        except asyncio.TimeoutError:
            print("ERROR: Second hover request also timed out")
            
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
    asyncio.run(test_hover_on_hex_sl())