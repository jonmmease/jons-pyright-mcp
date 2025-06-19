#!/usr/bin/env python3
"""Test that local package imports work with pixi environment."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from pyright_mcp import PyrightClient, ensure_file_uri, read_pyright_config, get_python_interpreter

async def test_pixi_imports():
    """Test local imports in hex-sl project."""
    project_dir = Path("/Users/jonmmease/VegaFusion/projects/hex-sl")
    
    if not project_dir.exists():
        print(f"Project directory not found: {project_dir}")
        return
        
    print(f"Testing imports in project: {project_dir}")
    
    # Read config
    config = read_pyright_config(project_dir)
    python_path = get_python_interpreter(project_dir, config)
    print(f"Using Python: {python_path}")
    
    # Create test file that imports from hex_sl
    test_file = project_dir / "test_imports.py"
    test_file.write_text("""
# Test imports from the local package
from hex_sl import Visualizer, CSSVars, normalize_values
from hex_sl.colors import ColorSystem
import hex_sl

# Use the imports
viz = Visualizer()
css = CSSVars()
colors = ColorSystem()
version = hex_sl.__version__
""")
    
    # Create client
    client = PyrightClient(project_dir, config)
    
    try:
        print("\nStarting pyright...")
        await client.start()
        
        # Wait for analysis
        await asyncio.sleep(3.0)
        
        # Open the test file
        file_uri = ensure_file_uri(str(test_file))
        with open(test_file) as f:
            content = f.read()
            
        await client.notify("textDocument/didOpen", {
            "textDocument": {
                "uri": file_uri,
                "languageId": "python",
                "version": 1,
                "text": content
            }
        })
        
        await asyncio.sleep(1.0)
        
        # Test hover on Visualizer (imported from hex_sl)
        print("\nTesting hover on 'Visualizer' (local package import)...")
        response = await client.request("textDocument/hover", {
            "textDocument": {"uri": file_uri},
            "position": {"line": 2, "character": 18}  # On "Visualizer"
        })
        
        if response and "contents" in response:
            print("✓ Hover on Visualizer successful")
            contents = response["contents"]
            if isinstance(contents, dict) and "value" in contents:
                print(f"  Type: {contents['value'][:100]}...")
        else:
            print("✗ No hover info for Visualizer - imports not resolved")
            
        # Test hover on ColorSystem
        print("\nTesting hover on 'ColorSystem' (submodule import)...")
        response = await client.request("textDocument/hover", {
            "textDocument": {"uri": file_uri},
            "position": {"line": 3, "character": 28}  # On "ColorSystem"
        })
        
        if response and "contents" in response:
            print("✓ Hover on ColorSystem successful")
        else:
            print("✗ No hover info for ColorSystem")
            
        # Test hover on variable using imported class
        print("\nTesting hover on 'viz' variable...")
        response = await client.request("textDocument/hover", {
            "textDocument": {"uri": file_uri},
            "position": {"line": 7, "character": 0}  # On "viz"
        })
        
        if response and "contents" in response:
            contents = response["contents"]
            if isinstance(contents, dict) and "value" in contents:
                value = contents["value"]
                if "Visualizer" in value:
                    print("✓ Variable 'viz' correctly typed as Visualizer")
                else:
                    print(f"  Variable type: {value[:100]}...")
        else:
            print("✗ No type info for variable")
            
        # Check for import errors
        print("\nChecking for import errors...")
        from pyright_mcp import current_diagnostics
        
        diags = current_diagnostics.get(file_uri, [])
        import_errors = [d for d in diags if "import" in d.get("message", "").lower()]
        
        if import_errors:
            print(f"✗ Found {len(import_errors)} import errors:")
            for err in import_errors[:3]:
                print(f"  - {err['message']}")
        else:
            print("✓ No import errors found")
            
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Cleanup
        if test_file.exists():
            test_file.unlink()
            
        await client.shutdown()

if __name__ == "__main__":
    import os
    os.environ["LOG_LEVEL"] = "INFO"
    asyncio.run(test_pixi_imports())