#!/usr/bin/env python3
"""Test the threaded implementation."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from pyright_mcp import PyrightClient, ensure_file_uri

async def test_threaded():
    """Test the threaded implementation."""
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
    
    print("Testing threaded pyright client...")
    client = PyrightClient(test_dir)
    
    try:
        print("Starting client...")
        await client.start()
        print("✓ Client started successfully")
        
        # Wait a moment
        await asyncio.sleep(1.0)
        print("After sleep")
        
        # Test hover
        file_uri = ensure_file_uri(str(test_file))
        print(f"\nTesting hover on 'greet' function...")
        
        # Open the file first
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
        
        # Wait a moment for pyright to process
        await asyncio.sleep(0.5)
        
        response = await client.request("textDocument/hover", {
            "textDocument": {"uri": file_uri},
            "position": {"line": 1, "character": 4}  # On "greet"
        })
        
        print(f"✓ Got hover response: {response}")
        
        if response and "contents" in response:
            contents = response["contents"]
            if isinstance(contents, dict) and "value" in contents:
                print(f"\nHover text:\n{contents['value']}")
                
        # Test on hex-sl if requested
        if "--hex-sl" in sys.argv:
            print("\n" + "="*60)
            print("Testing on hex-sl project...")
            
            await client.shutdown()
            
            hex_sl_dir = Path("/Users/jonmmease/VegaFusion/projects/hex-sl")
            client2 = PyrightClient(hex_sl_dir)
            
            await client2.start()
            print("✓ Started for hex-sl")
            
            # Wait for analysis
            await asyncio.sleep(3.0)
            
            # Test hover
            init_file = hex_sl_dir / "src/hex_sl/__init__.py"
            file_uri = ensure_file_uri(str(init_file))
            
            # Open the file first
            with open(init_file) as f:
                content = f.read()
                
            await client2.notify("textDocument/didOpen", {
                "textDocument": {
                    "uri": file_uri,
                    "languageId": "python",
                    "version": 1,
                    "text": content
                }
            })
            
            print("Testing hover on hex-sl...")
            response = await client2.request("textDocument/hover", {
                "textDocument": {"uri": file_uri},
                "position": {"line": 6, "character": 20}  # On __version__
            })
            
            print(f"✓ Hex-sl hover response: {response}")
            
            await client2.shutdown()
            
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await client.shutdown()
        
        # Cleanup
        test_file.unlink()
        test_dir.rmdir()

if __name__ == "__main__":
    asyncio.run(test_threaded())