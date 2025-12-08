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
from .lsp_client import PyrightClient
from .manager import PyrightClientManager
from .tools import (
    definition,
    diagnostics,
    document_symbols,
    implementation,
    list_environments,
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

# Global manager instance (replaces single pyright client)
manager: PyrightClientManager | None = None

# Track initialization state
initialization_complete = False

# Project root (set via CLI or cwd)
_project_root: Path | None = None


def handle_diagnostics_notification(method: str, params: dict[str, Any]) -> None:
    """Handle diagnostics notification from any pyright client.

    This is called by the manager when any client publishes diagnostics.
    The diagnostics are already stored per-environment in the manager.
    """
    uri = params.get("uri", "")
    diags = params.get("diagnostics", [])
    env_id = params.get("_env_id", "unknown")
    logger.info(f"Received {len(diags)} diagnostics for {uri} (env: {env_id})")


@asynccontextmanager
async def lifespan(mcp: FastMCP) -> AsyncIterator[None]:
    """Manage the lifecycle of the pyright client manager."""
    global manager, initialization_complete

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

    # Create manager and discover environments
    manager = PyrightClientManager(
        project_root,
        notification_handler=handle_diagnostics_notification,
    )

    try:
        # Start the root environment's client for backward compatibility
        await manager.start_root_client()

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
    if manager:
        await manager.shutdown_all()
        manager = None


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
| list_environments | List discovered Python environments and their status |
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


def get_manager() -> PyrightClientManager:
    """Get the global manager instance.

    Raises:
        PyrightNotInitializedError: If manager is not initialized
    """
    if not manager:
        raise PyrightNotInitializedError("Manager is not initialized")
    return manager


def ensure_pyright() -> PyrightClient:
    """Ensure pyright is initialized and return the root client.

    This is for backward compatibility with tools that don't yet
    support multi-environment routing.

    Raises:
        PyrightNotInitializedError: If pyright is not initialized or ready
    """
    mgr = get_manager()
    root_env = mgr.root_environment
    if not root_env:
        raise PyrightNotInitializedError("No root environment found")
    if not root_env.client:
        raise PyrightNotInitializedError("Root environment client not started")
    if not root_env.client.is_initialized():
        if initialization_complete:
            raise PyrightNotInitializedError(
                "pyright client is not properly initialized"
            )
        else:
            raise PyrightNotInitializedError("pyright is still initializing")
    return root_env.client


async def ensure_pyright_indexed(file_path: str | None = None) -> PyrightClient:
    """Ensure pyright is initialized and return the appropriate client.

    If file_path is provided, returns the client for that file's environment.
    Otherwise, returns the root environment client.

    Args:
        file_path: Optional path to route to the correct environment

    Raises:
        PyrightNotInitializedError: If pyright is not initialized
    """
    mgr = get_manager()

    if file_path:
        # Route to the correct environment for this file
        client = await mgr.get_client_for_file(file_path)
        if not client.is_initialized():
            if initialization_complete:
                raise PyrightNotInitializedError(
                    f"Client for {file_path} is not properly initialized"
                )
            else:
                raise PyrightNotInitializedError("pyright is still initializing")
        return client
    else:
        # Fall back to root environment
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
    mgr = get_manager()

    # Check if already opened in this environment
    if mgr.is_file_opened(file_path, file_uri):
        return True

    try:
        # Read file content
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Get document version
        version = mgr.increment_doc_version(file_path, file_uri)

        # Send didOpen notification
        await client.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": file_uri,
                    "languageId": "python",
                    "version": version,
                    "text": content,
                }
            },
        )

        # Track the file as opened
        mgr.mark_file_opened(file_path, file_uri, version)
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
mcp.tool(list_environments)
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
