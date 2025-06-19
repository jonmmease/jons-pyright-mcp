#!/usr/bin/env python3
"""Test with a potential fix for the hanging issue."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from pyright_mcp import PyrightClient

# Monkey patch the notification handler to fix async issue
original_handle_message = PyrightClient._handle_message

async def fixed_handle_message(self, message):
    """Fixed message handler that properly handles sync callbacks."""
    if 'id' in message:
        # Response - use original handler
        await original_handle_message(self, message)
    else:
        # Notification - handle sync callbacks
        method = message.get('method', '')
        params = message.get('params', {})
        
        handler = self.notification_handlers.get(method)
        if handler:
            try:
                # Check if handler is async
                if asyncio.iscoroutinefunction(handler):
                    await handler(params)
                else:
                    # Run sync handler in thread to avoid blocking
                    handler(params)
            except Exception as e:
                print(f"Error in notification handler for {method}: {e}")
        else:
            print(f"Unhandled notification: {method}")

PyrightClient._handle_message = fixed_handle_message

async def test_with_fix():
    """Test with the fix."""
    print("Testing with fixed message handler...")
    
    # Simple test
    test_dir = Path("test_workspace")
    test_dir.mkdir(exist_ok=True)
    test_file = test_dir / "test.py"
    test_file.write_text("""
def greet(name: str) -> str:
    return f"Hello, {name}!"
""")
    
    client = PyrightClient(test_dir)
    client.request_timeout = 10.0
    
    try:
        await client.start()
        print("Client started")
        
        # Open file
        file_uri = f"file://{test_file.absolute()}"
        await client.notify("textDocument/didOpen", {
            "textDocument": {
                "uri": file_uri,
                "languageId": "python",
                "version": 1,
                "text": test_file.read_text()
            }
        })
        
        await asyncio.sleep(1.0)
        
        # Test hover
        print("Testing hover...")
        response = await client.request("textDocument/hover", {
            "textDocument": {"uri": file_uri},
            "position": {"line": 1, "character": 4}
        })
        
        print(f"Success! Response: {response}")
        
        # Test on hex-sl
        if "--hex-sl" in sys.argv:
            await client.shutdown()
            
            print("\nTesting on hex-sl...")
            hex_sl_dir = Path("/Users/jonmmease/VegaFusion/projects/hex-sl")
            client2 = PyrightClient(hex_sl_dir)
            client2.request_timeout = 30.0
            
            await client2.start()
            print("Started for hex-sl")
            
            # Wait for analysis
            await asyncio.sleep(3.0)
            
            # Test hover
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
            
            print("Testing hover on hex-sl...")
            response = await client2.request("textDocument/hover", {
                "textDocument": {"uri": file_uri},
                "position": {"line": 6, "character": 20}  # On __version__
            })
            
            print(f"Hex-sl hover response: {response}")
            
            await client2.shutdown()
        
    finally:
        await client.shutdown()
        test_file.unlink()
        test_dir.rmdir()

if __name__ == "__main__":
    asyncio.run(test_with_fix())