"""MCP tools for Pyright."""

from .extensions import list_environments, restart_server
from .intelligence import diagnostics, rename
from .language import (
    definition,
    document_symbols,
    implementation,
    references,
    symbol_info,
    type_definition,
    type_info,
)

__all__ = [
    # Language tools
    "symbol_info",
    "type_info",
    "definition",
    "type_definition",
    "implementation",
    "references",
    "document_symbols",
    # Intelligence tools
    "diagnostics",
    "rename",
    # Extension tools
    "list_environments",
    "restart_server",
]
