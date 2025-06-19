#!/usr/bin/env python3
"""Debug where the async/thread integration fails."""

import asyncio
import os
import sys
from pathlib import Path

os.environ["LOG_LEVEL"] = "DEBUG"

sys.path.insert(0, str(Path(__file__).parent))

from pyright_mcp import PyrightClient

async def test():
    """Test with debug."""
    print("Creating client...")
    client = PyrightClient(Path.cwd())
    
    print("Starting client...")
    await client.start()
    print("Client started")
    
    print("Waiting 2 seconds...")
    await asyncio.sleep(2.0)
    
    print("Shutting down...")
    await client.shutdown()
    print("Done")

if __name__ == "__main__":
    asyncio.run(test())