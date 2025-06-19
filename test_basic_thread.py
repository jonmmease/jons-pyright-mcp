#!/usr/bin/env python3
"""Basic test of threaded communication."""

import subprocess
import threading
import queue
import json
import time

def test_basic():
    """Test basic thread communication."""
    print("Starting pyright...")
    
    proc = subprocess.Popen(
        ["python", "-m", "pyright.langserver", "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0
    )
    
    response_queue = queue.Queue()
    
    def reader():
        """Read thread."""
        buffer = b""
        while True:
            try:
                byte = proc.stdout.read(1)
                if not byte:
                    break
                buffer += byte
                
                # Look for complete message
                if b"\r\n\r\n" in buffer:
                    header_end = buffer.find(b"\r\n\r\n")
                    header = buffer[:header_end].decode('utf-8')
                    
                    # Get content length
                    content_length = None
                    for line in header.split('\r\n'):
                        if line.startswith('Content-Length: '):
                            content_length = int(line[16:])
                            break
                            
                    if content_length:
                        # Read content
                        content_start = header_end + 4
                        while len(buffer) < content_start + content_length:
                            chunk = proc.stdout.read(min(4096, content_start + content_length - len(buffer)))
                            if not chunk:
                                return
                            buffer += chunk
                            
                        # Extract message
                        content = buffer[content_start:content_start + content_length]
                        buffer = buffer[content_start + content_length:]
                        
                        message = json.loads(content.decode('utf-8'))
                        print(f"Received: {message.get('method', 'response')} (id={message.get('id')})")
                        response_queue.put(message)
                        
            except Exception as e:
                print(f"Reader error: {e}")
                break
                
    # Start reader
    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    
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
        print(f"Sent: {method} (id={msg_id})")
    
    # Initialize
    send_message("initialize", {"processId": None, "rootUri": None, "capabilities": {}}, msg_id=1)
    
    # Wait for response
    start = time.time()
    while time.time() - start < 5:
        try:
            msg = response_queue.get(timeout=0.1)
            if msg.get('id') == 1:
                print("✓ Got initialize response")
                
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
                
                # Wait a bit
                time.sleep(0.5)
                
                # Send hover
                send_message("textDocument/hover", {
                    "textDocument": {"uri": "file:///tmp/test.py"},
                    "position": {"line": 0, "character": 4}
                }, msg_id=2)
                
                # Wait for hover response
                hover_start = time.time()
                while time.time() - hover_start < 3:
                    try:
                        msg = response_queue.get(timeout=0.1)
                        if msg.get('id') == 2:
                            print(f"✓ Got hover response: {msg.get('result')}")
                            break
                    except queue.Empty:
                        continue
                        
                break
                
        except queue.Empty:
            continue
            
    # Cleanup
    proc.terminate()
    proc.wait()

if __name__ == "__main__":
    test_basic()