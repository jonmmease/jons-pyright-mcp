#!/usr/bin/env python3
"""Minimal test of thread implementation."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from pyright_mcp import PyrightClient

async def test():
    """Test minimal functionality."""
    print("Creating client...")
    client = PyrightClient(Path.cwd())
    
    print("Starting client...")
    await client.start()
    print("Client started successfully")
    
    print("Sleeping for 1 second...")
    await asyncio.sleep(1.0)
    print("Sleep completed")
    
    print("Shutting down...")
    await client.shutdown()
    print("Shutdown complete")

if __name__ == "__main__":
    asyncio.run(test())