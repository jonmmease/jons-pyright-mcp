#!/usr/bin/env python3
"""Minimal async test to isolate the issue."""

import asyncio
import subprocess
import sys

async def test_minimal():
    """Test minimal async subprocess communication."""
    print("Starting pyright subprocess...")
    
    # Start process
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "pyright.langserver", "--stdio",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    
    print("Process started, testing readline...")
    
    # Send initialize
    message = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'
    header = f"Content-Length: {len(message)}\r\n\r\n"
    proc.stdin.write(header.encode() + message.encode())
    await proc.stdin.drain()
    print("Sent initialize")
    
    # Try to read response
    try:
        print("Reading first line...")
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=2.0)
        print(f"Got line: {line}")
        
        # Read more lines
        while line and b"Content-Length" not in line:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=1.0)
            print(f"Got line: {line}")
            
    except asyncio.TimeoutError:
        print("Timeout reading from stdout")
        
        # Check stderr
        try:
            stderr = await asyncio.wait_for(proc.stderr.read(1000), timeout=0.5)
            print(f"Stderr: {stderr.decode()}")
        except:
            pass
    
    # Cleanup
    proc.terminate()
    await proc.wait()
    print("Done")

if __name__ == "__main__":
    asyncio.run(test_minimal())