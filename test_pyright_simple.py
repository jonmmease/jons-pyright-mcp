#!/usr/bin/env python3
"""Simple test to see if pyright is responding at all."""

import subprocess
import json
import sys
import threading

def read_stderr(proc):
    """Read stderr in a thread."""
    for line in proc.stderr:
        print(f"STDERR: {line.strip()}")

def test_simple():
    """Very simple test."""
    print("Starting pyright...")
    
    # Start pyright
    cmd = [sys.executable, "-m", "pyright.langserver", "--stdio"]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    # Start stderr reader
    stderr_thread = threading.Thread(target=read_stderr, args=(proc,))
    stderr_thread.daemon = True
    stderr_thread.start()
    
    # Send a simple initialize
    message = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "processId": None,
            "rootUri": None,
            "capabilities": {}
        }
    }
    
    content = json.dumps(message)
    header = f"Content-Length: {len(content)}\r\n\r\n"
    
    print("Sending initialize...")
    proc.stdin.write(header + content)
    proc.stdin.flush()
    
    print("Waiting for response...")
    
    # Try to read any output
    import select
    import time
    
    start_time = time.time()
    timeout = 5.0
    
    while time.time() - start_time < timeout:
        readable, _, _ = select.select([proc.stdout], [], [], 0.1)
        if readable:
            # Try to read headers
            line = proc.stdout.readline()
            print(f"Got line: {repr(line)}")
            
            if "Content-Length:" in line:
                # Parse content length
                content_length = int(line.split(':', 1)[1].strip())
                
                # Read rest of headers
                while True:
                    line = proc.stdout.readline()
                    if line in ['\r\n', '\n']:
                        break
                
                # Read content
                content = proc.stdout.read(content_length)
                print(f"Got response: {content}")
                try:
                    data = json.loads(content)
                    print(f"Parsed response: {json.dumps(data, indent=2)}")
                except:
                    pass
                break
    else:
        print("Timeout waiting for response")
    
    # Kill process
    proc.terminate()
    proc.wait()

if __name__ == "__main__":
    test_simple()