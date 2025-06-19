#!/usr/bin/env python3
"""Test LSP communication directly."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from pyright_mcp import PyrightClient

async def test_direct():
    """Test pyright LSP directly."""
    # Create a simple test file
    test_dir = Path("test_workspace")
    test_dir.mkdir(exist_ok=True)
    test_file = test_dir / "test.py"
    test_file.write_text("""
def greet(name: str) -> str:
    '''Say hello to someone.'''
    return f"Hello, {name}!"

message = greet("World")
print(message)
""")
    
    print("Starting pyright client...")
    client = PyrightClient(test_dir)
    client.request_timeout = 30.0  # Long timeout for debugging
    
    # Add detailed logging
    def log_notification(method, params):
        print(f"Notification: {method}")
        if method == "window/logMessage":
            print(f"  Message: {params.get('message', '')}")
    
    client.on_notification("window/logMessage", lambda params: log_notification("window/logMessage", params))
    client.on_notification("textDocument/publishDiagnostics", lambda params: log_notification("textDocument/publishDiagnostics", params))
    
    try:
        # Start client
        await client.start()
        print("Client initialized")
        
        # Wait for initial analysis
        print("Waiting for initial analysis...")
        await asyncio.sleep(2.0)
        
        # Open document
        file_uri = f"file://{test_file.absolute()}"
        print(f"Opening document: {file_uri}")
        await client.notify("textDocument/didOpen", {
            "textDocument": {
                "uri": file_uri,
                "languageId": "python",
                "version": 1,
                "text": test_file.read_text()
            }
        })
        
        # Wait a bit
        await asyncio.sleep(1.0)
        
        # Try hover
        print("\nRequesting hover on 'greet' function...")
        print("Sending request...")
        
        try:
            response = await client.request("textDocument/hover", {
                "textDocument": {"uri": file_uri},
                "position": {"line": 1, "character": 4}  # On "greet"
            })
            
            print(f"\nGot response: {response}")
            
            if response and "contents" in response:
                contents = response["contents"]
                if isinstance(contents, dict) and "value" in contents:
                    print(f"\nHover text:\n{contents['value']}")
                elif isinstance(contents, str):
                    print(f"\nHover text:\n{contents}")
                    
        except asyncio.TimeoutError:
            print(f"ERROR: Request timed out after {client.request_timeout} seconds")
            print("\nDebugging info:")
            print(f"- Pending requests: {list(client.pending_requests.keys())}")
            print(f"- Process running: {client.process and client.process.returncode is None}")
            
            # Try to see if pyright is responding at all
            print("\nTrying a simple request...")
            try:
                # Send shutdown request with shorter timeout
                client.request_timeout = 2.0
                response = await client.request("shutdown", {})
                print(f"Shutdown response: {response}")
            except:
                print("Shutdown also timed out - pyright may be hung")
                
        except Exception as e:
            print(f"ERROR: {type(e).__name__}: {e}")
            
    finally:
        print("\nCleaning up...")
        await client.shutdown()
        
        # Cleanup
        test_file.unlink()
        test_dir.rmdir()
        
    print("Done")

if __name__ == "__main__":
    asyncio.run(test_direct())