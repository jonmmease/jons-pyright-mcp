#!/usr/bin/env python3
"""Test pyright directly to isolate the issue."""

import subprocess
import json
import time

def test_pyright_direct():
    """Test pyright language server directly."""
    print("Testing pyright language server directly...")
    
    # Start pyright process
    cmd = ["python", "-m", "pyright.langserver", "--stdio"]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=0
    )
    
    def send_message(method, params=None, msg_id=None):
        """Send a JSON-RPC message."""
        message = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {}
        }
        if msg_id is not None:
            message["id"] = msg_id
            
        content = json.dumps(message)
        header = f"Content-Length: {len(content)}\r\n\r\n"
        proc.stdin.write(header + content)
        proc.stdin.flush()
        print(f"Sent: {method}")
        
    def read_message():
        """Read a JSON-RPC message."""
        # Read headers
        headers = {}
        while True:
            line = proc.stdout.readline()
            if line == '\r\n' or line == '\n':
                break
            if ': ' in line:
                key, value = line.strip().split(': ', 1)
                headers[key] = value
            
        # Read content
        if 'Content-Length' in headers:
            content_length = int(headers['Content-Length'])
            content = proc.stdout.read(content_length)
            return json.loads(content)
        return None
    
    try:
        # Initialize
        send_message("initialize", {
            "processId": None,
            "rootUri": "file:///tmp/pyright_test",
            "capabilities": {
                "textDocument": {
                    "hover": {"contentFormat": ["plaintext", "markdown"]}
                }
            }
        }, msg_id=1)
        
        # Read response
        response = read_message()
        print(f"Initialize response: {response.get('result', {}).get('capabilities', {}).keys()}")
        
        # Send initialized notification
        send_message("initialized")
        
        # Open a document
        send_message("textDocument/didOpen", {
            "textDocument": {
                "uri": "file:///tmp/pyright_test/test.py",
                "languageId": "python",
                "version": 1,
                "text": """import os

def greet(name: str) -> str:
    return f"Hello, {name}!"
"""
            }
        })
        
        print("Document opened, waiting a bit...")
        time.sleep(2)
        
        # Request hover
        print("Requesting hover...")
        send_message("textDocument/hover", {
            "textDocument": {"uri": "file:///tmp/pyright_test/test.py"},
            "position": {"line": 2, "character": 4}
        }, msg_id=2)
        
        # Try to read response with timeout
        print("Waiting for hover response...")
        import select
        readable, _, _ = select.select([proc.stdout], [], [], 5.0)
        if readable:
            response = read_message()
            print(f"Hover response: {response}")
        else:
            print("No response received within 5 seconds")
            
        # Shutdown
        send_message("shutdown", msg_id=3)
        time.sleep(0.5)
        send_message("exit")
        
    finally:
        proc.terminate()
        proc.wait()

if __name__ == "__main__":
    test_pyright_direct()