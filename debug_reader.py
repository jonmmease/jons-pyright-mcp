#!/usr/bin/env python3
"""Debug the message reader to see where it's getting stuck."""

import asyncio
import subprocess
import sys
import json

async def debug_reader():
    """Debug the async reader."""
    print("Starting pyright process...")
    
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "pyright.langserver", "--stdio",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    
    async def send_message(method, params=None, msg_id=None):
        """Send a message."""
        message = {"jsonrpc": "2.0", "method": method}
        if params:
            message["params"] = params
        if msg_id is not None:
            message["id"] = msg_id
            
        content = json.dumps(message).encode('utf-8')
        header = f"Content-Length: {len(content)}\r\n\r\n".encode('utf-8')
        
        proc.stdin.write(header + content)
        await proc.stdin.drain()
        print(f"Sent: {method}")
    
    # Initialize
    await send_message("initialize", {"processId": None, "rootUri": None, "capabilities": {}}, msg_id=1)
    
    # Start reader task
    buffer = b""
    messages_received = []
    
    async def reader():
        nonlocal buffer
        print("Reader started")
        
        while True:
            try:
                # Read chunks
                chunk = await proc.stdout.read(1024)
                if not chunk:
                    print("No more data")
                    break
                    
                print(f"Read {len(chunk)} bytes")
                buffer += chunk
                
                # Try to parse messages
                while True:
                    # Look for header end
                    header_end = buffer.find(b"\r\n\r\n")
                    if header_end == -1:
                        print(f"No complete header yet, buffer size: {len(buffer)}")
                        break
                        
                    # Parse header
                    header = buffer[:header_end].decode('utf-8')
                    content_start = header_end + 4
                    
                    # Get content length
                    content_length = None
                    for line in header.split('\r\n'):
                        if line.startswith('Content-Length: '):
                            content_length = int(line[16:])
                            break
                            
                    if content_length is None:
                        print("No content length!")
                        break
                        
                    # Check if we have full content
                    if len(buffer) < content_start + content_length:
                        print(f"Incomplete content: need {content_length}, have {len(buffer) - content_start}")
                        break
                        
                    # Extract message
                    content = buffer[content_start:content_start + content_length]
                    message = json.loads(content.decode('utf-8'))
                    messages_received.append(message)
                    
                    print(f"Got message: {message.get('method', 'response')} (id={message.get('id')})")
                    
                    # Remove from buffer
                    buffer = buffer[content_start + content_length:]
                    
                    # Check if this is our initialize response
                    if message.get('id') == 1:
                        print("Got initialize response, sending initialized")
                        await send_message("initialized")
                        
                        # Open document
                        await send_message("textDocument/didOpen", {
                            "textDocument": {
                                "uri": "file:///tmp/test.py",
                                "languageId": "python",
                                "version": 1,
                                "text": "def hello(): pass"
                            }
                        })
                        
                        # Send hover
                        await send_message("textDocument/hover", {
                            "textDocument": {"uri": "file:///tmp/test.py"},
                            "position": {"line": 0, "character": 4}
                        }, msg_id=2)
                        
                    elif message.get('id') == 2:
                        print("Got hover response!")
                        return
                        
            except Exception as e:
                print(f"Reader error: {e}")
                import traceback
                traceback.print_exc()
                break
    
    try:
        # Run reader with timeout
        await asyncio.wait_for(reader(), timeout=10.0)
    except asyncio.TimeoutError:
        print("Reader timed out")
        print(f"Buffer size: {len(buffer)}")
        print(f"Messages received: {len(messages_received)}")
        
    finally:
        proc.terminate()
        await proc.wait()

if __name__ == "__main__":
    asyncio.run(debug_reader())