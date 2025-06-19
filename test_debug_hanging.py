#!/usr/bin/env python3
"""Debug where the hanging occurs."""

import asyncio
import sys
import os
from pathlib import Path

# Enable maximum debug logging
os.environ["LOG_LEVEL"] = "DEBUG"

sys.path.insert(0, str(Path(__file__).parent))

from pyright_mcp import PyrightClient

async def test_debug():
    """Debug test."""
    print("Creating test file...")
    test_dir = Path("test_workspace")
    test_dir.mkdir(exist_ok=True)
    test_file = test_dir / "test.py"
    test_file.write_text("def hello(): pass")
    
    client = PyrightClient(test_dir)
    
    # Override _read_loop to add debugging
    original_read_loop = client._read_loop
    
    async def debug_read_loop(self):
        print("[DEBUG] Read loop started")
        reader = self.process.stdout
        message_count = 0
        
        while self.process and reader and not self._shutting_down:
            try:
                print(f"[DEBUG] Waiting for headers (message #{message_count + 1})...")
                
                # Read headers
                headers = []
                while True:
                    print("[DEBUG] Reading header line...")
                    line = await asyncio.wait_for(reader.readline(), timeout=2.0)
                    if not line:
                        print("[DEBUG] EOF!")
                        return
                    
                    line = line.decode('utf-8').rstrip('\r\n')
                    print(f"[DEBUG] Header line: {repr(line)}")
                    
                    if not line:
                        break
                    headers.append(line)
                
                # Get content length
                content_length = None
                for header in headers:
                    if header.startswith('Content-Length: '):
                        content_length = int(header[16:])
                        print(f"[DEBUG] Content-Length: {content_length}")
                        break
                
                if content_length is None:
                    print("[DEBUG] No Content-Length!")
                    continue
                
                # Read content
                print(f"[DEBUG] Reading {content_length} bytes of content...")
                content = await asyncio.wait_for(reader.readexactly(content_length), timeout=2.0)
                print(f"[DEBUG] Got content: {content[:100]}...")
                
                # Parse
                import json
                message = json.loads(content.decode('utf-8'))
                message_count += 1
                print(f"[DEBUG] Message #{message_count}: {message.get('method', 'response')} (id={message.get('id')})")
                
                await self._handle_message(message)
                
            except asyncio.TimeoutError:
                print("[DEBUG] Timeout in read loop!")
                break
            except Exception as e:
                print(f"[DEBUG] Error: {e}")
                break
    
    # Replace method
    client._read_loop = lambda: debug_read_loop(client)
    
    try:
        print("Starting client...")
        await client.start()
        print("Client started")
        
        # Open document
        file_uri = f"file://{test_file.absolute()}"
        print(f"Opening document: {file_uri}")
        await client.notify("textDocument/didOpen", {
            "textDocument": {
                "uri": file_uri,
                "languageId": "python",
                "version": 1,
                "text": "def hello(): pass"
            }
        })
        
        print("Sending hover request...")
        response = await asyncio.wait_for(
            client.request("textDocument/hover", {
                "textDocument": {"uri": file_uri},
                "position": {"line": 0, "character": 4}
            }),
            timeout=5.0
        )
        
        print(f"Response: {response}")
        
    except asyncio.TimeoutError:
        print("Hover request timed out!")
        print(f"Pending requests: {list(client.pending_requests.keys())}")
    finally:
        await client.shutdown()
        test_file.unlink()
        test_dir.rmdir()

if __name__ == "__main__":
    asyncio.run(test_debug())