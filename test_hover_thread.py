#!/usr/bin/env python3
"""Test hover with thread implementation."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from pyright_mcp import PyrightClient, ensure_file_uri

async def test():
    """Test hover functionality."""
    # Create test file
    test_dir = Path("test_workspace")
    test_dir.mkdir(exist_ok=True)
    test_file = test_dir / "test.py"
    test_file.write_text("""
def greet(name: str) -> str:
    '''Greet someone by name.'''
    return f"Hello, {name}!"

message = greet("World")
print(message)
""")
    
    print("Creating client...")
    client = PyrightClient(test_dir)
    
    try:
        print("Starting client...")
        await client.start()
        print("✓ Client started successfully")
        
        # Wait for analysis
        print("Waiting for analysis...")
        await asyncio.sleep(2.0)
        
        # Open the file
        file_uri = ensure_file_uri(str(test_file))
        print(f"Opening file: {file_uri}")
        
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
        
        # Wait a moment
        await asyncio.sleep(0.5)
        
        # Test hover
        print("Testing hover on 'greet' function...")
        response = await client.request("textDocument/hover", {
            "textDocument": {"uri": file_uri},
            "position": {"line": 1, "character": 4}  # On "greet"
        })
        
        print(f"✓ Hover response: {response}")
        
        if response and "contents" in response:
            contents = response["contents"]
            if isinstance(contents, dict) and "value" in contents:
                print(f"\nHover text:\n{contents['value']}")
                
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("\nShutting down...")
        await client.shutdown()
        print("Shutdown complete")
        
        # Cleanup
        test_file.unlink()
        test_dir.rmdir()

if __name__ == "__main__":
    asyncio.run(test())