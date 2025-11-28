"""FastMCP server exposing Pyright LSP features.

This module provides backward compatibility by re-exporting
from the new package structure.
"""

from .jons_mcp_pyright import main

__all__ = ["main"]
