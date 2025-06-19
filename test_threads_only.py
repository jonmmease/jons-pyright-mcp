#!/usr/bin/env python3
"""Test just the thread startup."""

import subprocess
import threading
import time
import sys
import os

def test_threads():
    """Test thread startup."""
    print("Starting process...")
    
    proc = subprocess.Popen(
        [sys.executable, "-m", "pyright.langserver", "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0
    )
    
    print(f"Process started: PID={proc.pid}")
    
    def reader():
        print("Reader thread started")
        while True:
            byte = proc.stdout.read(1)
            if not byte:
                print("Reader: EOF")
                break
            print(f"Reader: got byte {repr(byte)}")
            
    def stderr_reader():
        print("Stderr thread started")
        while True:
            line = proc.stderr.readline()
            if not line:
                print("Stderr: EOF")
                break
            print(f"Stderr: {line.decode().strip()}")
            
    # Start threads
    t1 = threading.Thread(target=reader, daemon=True)
    t2 = threading.Thread(target=stderr_reader, daemon=True)
    
    t1.start()
    t2.start()
    
    print(f"Threads started: reader={t1.is_alive()}, stderr={t2.is_alive()}")
    
    # Send initialize
    import json
    params = {
        "processId": None,
        "rootUri": f"file://{os.getcwd()}",
        "capabilities": {}
    }
    message = json.dumps({"jsonrpc":"2.0","id":1,"method":"initialize","params":params})
    header = f"Content-Length: {len(message)}\r\n\r\n"
    
    print(f"Sending: {header.strip()}")
    proc.stdin.write(header.encode() + message.encode())
    proc.stdin.flush()
    print("Message sent")
    
    # Wait a bit
    time.sleep(2)
    
    print(f"Threads still alive: reader={t1.is_alive()}, stderr={t2.is_alive()}")
    print(f"Process still running: {proc.poll() is None}")
    
    # Cleanup
    proc.terminate()
    proc.wait()

if __name__ == "__main__":
    test_threads()