#!/usr/bin/env python3
"""Test using threads for I/O to avoid asyncio issues."""

import subprocess
import sys
import json
import threading
import queue
import time

def test_with_threads():
    """Test using threads for I/O."""
    print("Starting pyright with thread-based I/O...")
    
    proc = subprocess.Popen(
        [sys.executable, "-m", "pyright.langserver", "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0  # Unbuffered
    )
    
    # Message queue
    response_queue = queue.Queue()
    
    def reader_thread():
        """Read messages in a thread."""
        buffer = b""
        
        while True:
            try:
                # Read one byte at a time to avoid blocking
                byte = proc.stdout.read(1)
                if not byte:
                    break
                    
                buffer += byte
                
                # Check for complete message
                if b"\r\n\r\n" in buffer:
                    header_end = buffer.find(b"\r\n\r\n")
                    header = buffer[:header_end].decode('utf-8')
                    
                    # Parse content length
                    content_length = None
                    for line in header.split('\r\n'):
                        if line.startswith('Content-Length: '):
                            content_length = int(line[16:])
                            break
                            
                    if content_length:
                        # Read rest of header
                        buffer = buffer[header_end + 4:]
                        
                        # Read content
                        while len(buffer) < content_length:
                            chunk = proc.stdout.read(content_length - len(buffer))
                            if not chunk:
                                break
                            buffer += chunk
                            
                        if len(buffer) >= content_length:
                            content = buffer[:content_length]
                            buffer = buffer[content_length:]
                            
                            message = json.loads(content.decode('utf-8'))
                            print(f"Received: {message.get('method', 'response')} (id={message.get('id')})")
                            response_queue.put(message)
                            
            except Exception as e:
                print(f"Reader error: {e}")
                break
                
    # Start reader thread
    reader = threading.Thread(target=reader_thread, daemon=True)
    reader.start()
    
    def send_message(method, params=None, msg_id=None):
        """Send a message."""
        message = {"jsonrpc": "2.0", "method": method}
        if params:
            message["params"] = params
        if msg_id is not None:
            message["id"] = msg_id
            
        content = json.dumps(message).encode('utf-8')
        header = f"Content-Length: {len(content)}\r\n\r\n".encode('utf-8')
        
        proc.stdin.write(header + content)
        proc.stdin.flush()
        print(f"Sent: {method}")
    
    try:
        # Initialize
        send_message("initialize", {"processId": None, "rootUri": None, "capabilities": {}}, msg_id=1)
        
        # Wait for response
        start = time.time()
        while time.time() - start < 5:
            try:
                msg = response_queue.get(timeout=0.1)
                if msg.get('id') == 1:
                    print("Got initialize response")
                    
                    # Send initialized
                    send_message("initialized")
                    
                    # Open document
                    send_message("textDocument/didOpen", {
                        "textDocument": {
                            "uri": "file:///tmp/test.py",
                            "languageId": "python",
                            "version": 1,
                            "text": "def hello(): pass"
                        }
                    })
                    
                    # Send hover
                    send_message("textDocument/hover", {
                        "textDocument": {"uri": "file:///tmp/test.py"},
                        "position": {"line": 0, "character": 4}
                    }, msg_id=2)
                    
                elif msg.get('id') == 2:
                    print(f"Got hover response: {msg.get('result')}")
                    break
                    
            except queue.Empty:
                continue
                
    finally:
        proc.terminate()
        proc.wait()

if __name__ == "__main__":
    test_with_threads()