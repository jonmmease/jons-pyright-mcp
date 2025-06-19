#!/usr/bin/env python3
"""Test pyright incrementally to find where it hangs."""

import asyncio
import sys
import os
import json
from pathlib import Path

# Enable debug logging
os.environ["LOG_LEVEL"] = "DEBUG"

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from pyright_mcp import PyrightClient

async def test_incremental():
    """Test pyright step by step."""
    # Start with a minimal test in current directory
    test_file = Path("test_sample.py")
    test_file.write_text("""
def hello(name: str) -> str:
    return f"Hello, {name}!"
    
result = hello("World")
""")
    
    print("Step 1: Testing on local file first...")
    client = PyrightClient(Path.cwd())
    client.request_timeout = 5.0
    
    # Track notifications
    notifications = []
    def track_notification(method, params):
        notifications.append((method, params))
        print(f"  Notification: {method}")
        if method == "textDocument/publishDiagnostics":
            print(f"    Diagnostics for: {params.get('uri', 'unknown')}")
            
    client.on_notification("textDocument/publishDiagnostics", track_notification)
    client.on_notification("window/logMessage", track_notification)
    
    try:
        await client.start()
        print("  ✓ Client started")
        
        # Open document
        file_uri = f"file://{test_file.absolute()}"
        await client.notify("textDocument/didOpen", {
            "textDocument": {
                "uri": file_uri,
                "languageId": "python",
                "version": 1,
                "text": test_file.read_text()
            }
        })
        print("  ✓ Document opened")
        
        # Wait for diagnostics
        await asyncio.sleep(1.0)
        
        # Try hover
        print("  Requesting hover...")
        try:
            response = await client.request("textDocument/hover", {
                "textDocument": {"uri": file_uri},
                "position": {"line": 1, "character": 4}  # On "hello"
            })
            print(f"  ✓ Hover response: {response}")
        except asyncio.TimeoutError:
            print("  ✗ Hover timed out")
            
        await client.shutdown()
        print("  ✓ Client shutdown")
        
        # Now test on hex-sl with analysis limiting
        print("\nStep 2: Testing on hex-sl with limited analysis...")
        
        # Create a more restrictive config temporarily
        hex_sl_dir = Path("/Users/jonmmease/VegaFusion/projects/hex-sl")
        temp_config = hex_sl_dir / "pyrightconfig.temp.json"
        temp_config.write_text(json.dumps({
            "include": ["src/hex_sl/__init__.py"],  # Only analyze one file
            "exclude": ["**/*"],
            "typeCheckingMode": "off",  # Disable type checking
            "analysis": {
                "autoSearchPaths": False,
                "useLibraryCodeForTypes": False,
                "diagnosticMode": "openFilesOnly",
                "logLevel": "Warning"
            }
        }, indent=2))
        
        # Set config path
        os.environ["PYRIGHT_CONFIG"] = str(temp_config)
        
        client2 = PyrightClient(hex_sl_dir)
        client2.request_timeout = 10.0
        
        notifications.clear()
        client2.on_notification("textDocument/publishDiagnostics", track_notification)
        
        await client2.start()
        print("  ✓ Client started for hex-sl")
        
        # Open just the __init__.py file
        init_file = hex_sl_dir / "src/hex_sl/__init__.py"
        file_uri = f"file://{init_file.absolute()}"
        
        await client2.notify("textDocument/didOpen", {
            "textDocument": {
                "uri": file_uri,
                "languageId": "python",
                "version": 1,
                "text": init_file.read_text()
            }
        })
        print("  ✓ __init__.py opened")
        
        # Wait a bit
        await asyncio.sleep(2.0)
        
        # Try hover
        print("  Requesting hover on hex-sl...")
        try:
            response = await client2.request("textDocument/hover", {
                "textDocument": {"uri": file_uri},
                "position": {"line": 7, "character": 20}  # On "__version__"
            })
            print(f"  ✓ Hover response: {response}")
        except asyncio.TimeoutError:
            print("  ✗ Hover timed out on hex-sl")
            print(f"  Total notifications received: {len(notifications)}")
            
        await client2.shutdown()
        
        # Cleanup
        temp_config.unlink()
        test_file.unlink()
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_incremental())