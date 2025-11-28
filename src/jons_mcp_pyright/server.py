"""FastMCP server for Pyright LSP features.

This module provides the main server setup, lifespan management,
and tool registration for the MCP Pyright server.
"""

import argparse
import asyncio
import logging
import os
import signal
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastmcp import FastMCP

from .exceptions import PyrightNotInitializedError
from .lsp_client import PyrightClient, read_pyright_config
from .tools import (
    definition,
    diagnostics,
    document_symbols,
    implementation,
    references,
    rename,
    restart_server,
    symbol_info,
    type_definition,
    type_info,
    workspace_symbols,
)

logger = logging.getLogger(__name__)

# Configure logging
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

# Global pyright client instance
pyright: PyrightClient | None = None

# Store diagnostics from pyright
current_diagnostics: dict[str, list[dict[str, Any]]] = {}

# Track opened files
opened_files: set[str] = set()

# Track initialization state
initialization_complete = False

# Project root (set via CLI or cwd)
_project_root: Path | None = None


async def handle_diagnostics(params: dict[str, Any]):
    """Handle diagnostics notification from pyright."""
    uri = params.get("uri", "")
    diagnostics = params.get("diagnostics", [])
    current_diagnostics[uri] = diagnostics
    logger.info(f"Received {len(diagnostics)} diagnostics for {uri}")


@asynccontextmanager
async def lifespan(mcp: FastMCP) -> AsyncIterator[None]:
    """Manage the lifecycle of the pyright client."""
    global pyright, initialization_complete

    # Startup
    project_root = _project_root or Path.cwd()
    logger.info(f"Starting MCP server in project: {project_root}")

    # Check if this is a Python project
    if not any(
        (project_root / f).exists()
        for f in ["setup.py", "pyproject.toml", "requirements.txt", "pyrightconfig.json"]
    ):
        logger.warning(
            "No Python project files found. "
            "Consider creating pyrightconfig.json for better results."
        )

    # Read pyright configuration
    pyright_config = read_pyright_config(project_root)

    pyright = PyrightClient(project_root, pyright_config)
    pyright.on_notification("textDocument/publishDiagnostics", handle_diagnostics)

    try:
        await pyright.start()
        # Give pyright more time to analyze the project initially
        logger.info("Waiting for pyright to analyze the project...")
        await asyncio.sleep(2.0)
        initialization_complete = True
        logger.info("Pyright initialization complete")
    except Exception as e:
        logger.error(f"Failed to start pyright: {e}")
        raise

    yield

    # Shutdown
    initialization_complete = False
    if pyright:
        await pyright.shutdown()
        pyright = None


# Create FastMCP server instance with lifespan
mcp = FastMCP(
    name="pyright-mcp",
    lifespan=lifespan,
    instructions="""
MCP server providing Pyright LSP features for Python code intelligence.

## Navigation & Discovery
| Tool | Purpose |
|------|---------|
| workspace_symbols | Search for types/functions across the project by name |
| document_symbols | List all symbols defined in a file |
| definition | Jump to where a symbol is defined |
| type_definition | Jump to the type definition of a symbol |
| implementation | Find implementations of protocols/abstract classes |
| references | Find all usages of a symbol |

## Understanding Code
| Tool | Purpose |
|------|---------|
| type_info | Get type name, fields, and methods for a value (primary tool) |
| symbol_info | Get type signature and docs for any symbol (via hover) |

## Code Intelligence
| Tool | Purpose |
|------|---------|
| diagnostics | Get type errors and warnings |

## Refactoring
| Tool | Purpose |
|------|---------|
| rename | Safely rename a symbol across the project |

## Server Management
| Tool | Purpose |
|------|---------|
| restart_server | Restart Pyright after config changes |

## Typical Workflow
1. Use workspace_symbols or document_symbols to find code
2. Call type_info on a variable to discover its type, fields, and methods
3. Use definition to navigate to source, references to find usages
4. Check diagnostics after making changes

## Pagination
Tools returning lists (references, document_symbols, workspace_symbols, diagnostics,
type_info methods) return max 20 items. Use limit/offset parameters and check
hasMore for additional results.
""",
)


def ensure_pyright() -> PyrightClient:
    """Ensure pyright is initialized and return the client.

    Raises:
        PyrightNotInitializedError: If pyright is not initialized or ready
    """
    if not pyright:
        raise PyrightNotInitializedError("pyright is not initialized")
    if not pyright.is_initialized():
        if initialization_complete:
            raise PyrightNotInitializedError(
                "pyright client is not properly initialized"
            )
        else:
            raise PyrightNotInitializedError("pyright is still initializing")
    return pyright


async def ensure_pyright_indexed() -> PyrightClient:
    """Ensure pyright is initialized and has indexed the project.

    This is a convenience function for tools that need to wait for
    pyright to be fully ready.

    Raises:
        PyrightNotInitializedError: If pyright is not initialized
    """
    return ensure_pyright()


async def ensure_file_open(
    client: PyrightClient, file_path: str, file_uri: str
) -> bool:
    """Ensure file is open in pyright.

    Args:
        client: The pyright client
        file_path: Path to the file
        file_uri: URI of the file

    Returns:
        True if file is open, False if opening failed
    """
    if file_uri in opened_files:
        return True

    try:
        # Read file content
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Send didOpen notification
        await client.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": file_uri,
                    "languageId": "python",
                    "version": 1,
                    "text": content,
                }
            },
        )

        opened_files.add(file_uri)
        return True
    except Exception as e:
        logger.error(f"Failed to open file {file_path}: {e}")
        return False


# Register all tools with the MCP server
mcp.tool(symbol_info)
mcp.tool(type_info)
mcp.tool(definition)
mcp.tool(type_definition)
mcp.tool(implementation)
mcp.tool(references)
mcp.tool(document_symbols)
mcp.tool(workspace_symbols)
mcp.tool(diagnostics)
mcp.tool(rename)
mcp.tool(restart_server)


# Signal handling for graceful shutdown
def signal_handler(signum: int, frame: Any) -> None:
    """Handle shutdown signals.

    Args:
        signum: Signal number
        frame: Current stack frame
    """
    # Don't log in signal handlers to avoid reentrant logging issues
    sys.exit(0)


def main() -> None:
    """Main entry point for the MCP server."""
    global _project_root

    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="MCP server for Pyright LSP features"
    )
    parser.add_argument(
        "project_path",
        nargs="?",
        help="Path to the Python project (defaults to current directory)",
    )
    args = parser.parse_args()

    # Set project root from CLI argument
    if args.project_path:
        _project_root = Path(args.project_path).resolve()
        if not _project_root.exists():
            print(f"Error: Project path does not exist: {_project_root}", file=sys.stderr)
            sys.exit(1)

    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Run the MCP server
    mcp.run()


if __name__ == "__main__":
    main()
