#!/usr/bin/env python3
"""Test with different event loop implementations."""

import asyncio
import subprocess
import sys
import platform

def test_sync_first():
    """Test sync subprocess first to ensure pyright works."""
    print("Testing sync subprocess first...")
    
    proc = subprocess.Popen(
        [sys.executable, "-m", "pyright", "--version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    stdout, stderr = proc.communicate(timeout=5)
    print(f"Pyright version: {stdout.strip()}")
    if stderr:
        print(f"Stderr: {stderr}")
    
    print(f"Return code: {proc.returncode}")
    print()

async def test_with_event_loop():
    """Test async subprocess."""
    print(f"Python version: {sys.version}")
    print(f"Platform: {platform.system()} {platform.release()}")
    print(f"Event loop: {type(asyncio.get_running_loop()).__name__}")
    print()
    
    # Try running a simple command first
    print("Testing simple async subprocess...")
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "print('hello')",
        stdout=asyncio.subprocess.PIPE
    )
    
    stdout, _ = await proc.communicate()
    print(f"Simple test output: {stdout.decode().strip()}")
    print()
    
    # Now try pyright
    print("Testing pyright langserver...")
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "pyright.langserver", "--stdio",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    
    print("Process created, sending data...")
    
    # Send initialize  
    message = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"capabilities":{}}}'
    header = f"Content-Length: {len(message)}\r\n\r\n"
    data = header.encode() + message.encode()
    
    print(f"Writing {len(data)} bytes...")
    proc.stdin.write(data)
    
    try:
        print("Draining stdin...")
        await asyncio.wait_for(proc.stdin.drain(), timeout=1.0)
        print("Drain complete")
    except asyncio.TimeoutError:
        print("Drain timed out!")
    
    # Try reading with timeout
    print("Attempting to read...")
    try:
        # Read one byte at a time to see if anything comes through
        first_byte = await asyncio.wait_for(proc.stdout.read(1), timeout=2.0)
        print(f"Got first byte: {first_byte}")
    except asyncio.TimeoutError:
        print("Read timed out!")
        
        # Check if process is alive
        if proc.returncode is None:
            print("Process is still running")
        else:
            print(f"Process died with code: {proc.returncode}")
            
        # Check stderr
        try:
            stderr_data = await asyncio.wait_for(proc.stderr.read(100), timeout=0.5)
            if stderr_data:
                print(f"Stderr: {stderr_data.decode()}")
        except:
            pass
    
    proc.terminate()
    await proc.wait()

if __name__ == "__main__":
    test_sync_first()
    
    # Try with different event loop on macOS
    if platform.system() == "Darwin":
        print("=== Testing with selector event loop ===")
        # asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    asyncio.run(test_with_event_loop())