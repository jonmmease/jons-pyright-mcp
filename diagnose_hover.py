#!/usr/bin/env python3
"""Diagnose why hover is hanging."""

import asyncio
import sys
import subprocess
import json

def test_sync():
    """Test pyright synchronously to eliminate async issues."""
    print("Testing pyright with synchronous subprocess...")
    
    # Start pyright
    proc = subprocess.Popen(
        [sys.executable, "-m", "pyright.langserver", "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False  # Use bytes
    )
    
    def send_request(method, params=None, msg_id=None):
        """Send request and return response."""
        message = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params:
            message["params"] = params
        if msg_id is not None:
            message["id"] = msg_id
            
        content = json.dumps(message).encode('utf-8')
        header = f"Content-Length: {len(content)}\r\n\r\n".encode('utf-8')
        
        proc.stdin.write(header + content)
        proc.stdin.flush()
        print(f"Sent: {method} (id={msg_id})")
        
    def read_message():
        """Read one message."""
        # Read header
        header_data = b""
        while b"\r\n\r\n" not in header_data:
            byte = proc.stdout.read(1)
            if not byte:
                return None
            header_data += byte
            
        # Parse content length
        headers = header_data.decode('utf-8').strip()
        content_length = None
        for line in headers.split('\r\n'):
            if line.startswith('Content-Length: '):
                content_length = int(line[16:])
                break
                
        if content_length is None:
            return None
            
        # Read content
        content = proc.stdout.read(content_length)
        return json.loads(content.decode('utf-8'))
    
    try:
        # Initialize
        send_request("initialize", {
            "processId": None,
            "rootUri": None,
            "capabilities": {}
        }, msg_id=1)
        
        # Read messages until we get initialize response
        print("\nReading messages...")
        messages = []
        for _ in range(10):  # Read up to 10 messages
            msg = read_message()
            if msg:
                messages.append(msg)
                print(f"Received: {msg.get('method', 'response')} (id={msg.get('id')})")
                
                # Check if this is our initialize response
                if msg.get('id') == 1:
                    print("Got initialize response!")
                    break
        
        # Send initialized
        send_request("initialized")
        
        # Open a simple document
        send_request("textDocument/didOpen", {
            "textDocument": {
                "uri": "file:///tmp/test.py",
                "languageId": "python",
                "version": 1,
                "text": "def hello(): pass"
            }
        })
        
        # Send hover request
        send_request("textDocument/hover", {
            "textDocument": {"uri": "file:///tmp/test.py"},
            "position": {"line": 0, "character": 4}
        }, msg_id=2)
        
        # Try to read hover response
        print("\nWaiting for hover response...")
        import time
        start = time.time()
        
        while time.time() - start < 5:
            msg = read_message()
            if msg:
                print(f"Received: {msg}")
                if msg.get('id') == 2:
                    print("Got hover response!")
                    break
        else:
            print("Timeout waiting for hover response")
            
            # Check if process is still alive
            if proc.poll() is not None:
                print(f"Process died with code: {proc.poll()}")
                stderr = proc.stderr.read().decode()
                if stderr:
                    print(f"Stderr: {stderr}")
                    
    finally:
        proc.terminate()
        proc.wait()

if __name__ == "__main__":
    test_sync()