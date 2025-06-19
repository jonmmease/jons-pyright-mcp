#!/usr/bin/env python3
"""Fixed version of pyright_mcp.py that handles asyncio subprocess I/O correctly."""

import asyncio
import sys
from pathlib import Path

# Add the directory to path
sys.path.insert(0, str(Path(__file__).parent))

# Import and patch the PyrightClient
from pyright_mcp import PyrightClient
import pyright_mcp

# Import json early
import json

async def fixed_reader_task(self):
    """Fixed reader that uses readline to avoid blocking on incomplete messages."""
    buffer = b""
    
    while self.process and self.process.stdout and not self._shutting_down:
        try:
            # Use readline instead of read to avoid blocking
            # This reads until we get a newline, which headers always have
            line = await self.process.stdout.readline()
            if not line:
                break
                
            buffer += line
            
            # Process complete messages
            while True:
                # Look for complete header
                header_end = buffer.find(b"\r\n\r\n")
                if header_end == -1:
                    # No complete header yet
                    break
                    
                # Parse header
                header = buffer[:header_end].decode('utf-8')
                content_start = header_end + 4
                
                # Extract content length
                content_length = None
                for header_line in header.split('\r\n'):
                    if header_line.startswith('Content-Length: '):
                        content_length = int(header_line[16:])
                        break
                        
                if content_length is None:
                    # Invalid message, skip it
                    buffer = buffer[content_start:]
                    continue
                    
                # Check if we have complete content
                while len(buffer) < content_start + content_length:
                    # Read more data until we have the full content
                    chunk = await self.process.stdout.read(min(4096, content_start + content_length - len(buffer)))
                    if not chunk:
                        return  # Process ended
                    buffer += chunk
                    
                # Extract and parse content
                content = buffer[content_start:content_start + content_length]
                try:
                    message = json.loads(content.decode('utf-8'))
                    buffer = buffer[content_start + content_length:]
                    await self._handle_message(message)
                except json.JSONDecodeError as e:
                    print(f"Failed to parse JSON: {e}")
                    buffer = buffer[content_start + content_length:]
                    
        except Exception as e:
            print(f"Error in read loop: {e}")
            break

# Apply the fix
PyrightClient._reader_task = fixed_reader_task

# Also fix the notification handler to handle non-async callbacks
original_handle_message = PyrightClient._handle_message

async def fixed_handle_message(self, message):
    """Fixed message handler."""
    import asyncio
    from pyright_mcp import logger
    
    logger.debug(f"Received: {message}")
    
    if 'id' in message:
        # Response to our request
        request_id = message['id']
        future = self.pending_requests.pop(request_id, None)
        
        if future and not future.done():
            if 'error' in message:
                error = message['error']
                from pyright_mcp import LSPRequestError
                future.set_exception(
                    LSPRequestError(f"{error.get('message', 'Unknown error')} (code: {error.get('code')})")
                )
            else:
                future.set_result(message.get('result'))
    else:
        # Server notification
        method = message.get('method', '')
        params = message.get('params', {})
        
        handler = self.notification_handlers.get(method)
        if handler:
            try:
                # Check if handler is a coroutine function
                if asyncio.iscoroutinefunction(handler):
                    await handler(params)
                else:
                    # Call sync handler directly
                    handler(params)
            except Exception as e:
                logger.error(f"Error in notification handler for {method}: {e}")
        else:
            logger.debug(f"Unhandled notification: {method}")

PyrightClient._handle_message = fixed_handle_message

# Test the fix
async def test_fixed():
    """Test the fixed client."""
    import json
    
    print("Testing fixed pyright client...")
    
    # Test on hex-sl
    hex_sl_dir = Path("/Users/jonmmease/VegaFusion/projects/hex-sl")
    print(f"Project: {hex_sl_dir}")
    
    client = PyrightClient(hex_sl_dir)
    client.request_timeout = 30.0  # Give it time for large project
    
    # Simple notification handler
    def log_notification(method, params):
        if method == "textDocument/publishDiagnostics":
            uri = params.get('uri', '').replace('file://', '')
            count = len(params.get('diagnostics', []))
            if count > 0:
                print(f"  Diagnostics: {count} issues in {Path(uri).name}")
    
    client.on_notification("textDocument/publishDiagnostics", 
                          lambda params: log_notification("textDocument/publishDiagnostics", params))
    
    try:
        print("Starting client...")
        await client.start()
        print("Client started successfully")
        
        # Give it time to analyze
        print("Waiting for initial analysis...")
        await asyncio.sleep(3.0)
        
        # Test file
        test_file = hex_sl_dir / "src/hex_sl/__init__.py"
        file_uri = f"file://{test_file.absolute()}"
        
        # Open file
        print(f"Opening {test_file.name}...")
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
        print("Testing hover on '__version__'...")
        start = asyncio.get_event_loop().time()
        
        response = await client.request("textDocument/hover", {
            "textDocument": {"uri": file_uri},
            "position": {"line": 6, "character": 20}  # On __version__
        })
        
        elapsed = asyncio.get_event_loop().time() - start
        print(f"Hover completed in {elapsed:.2f} seconds")
        
        if response and 'contents' in response:
            contents = response['contents']
            if isinstance(contents, dict) and 'value' in contents:
                print(f"Hover text: {contents['value'][:100]}...")
            else:
                print(f"Hover response: {contents}")
        else:
            print("No hover information")
            
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("Shutting down...")
        await client.shutdown()

if __name__ == "__main__":
    asyncio.run(test_fixed())