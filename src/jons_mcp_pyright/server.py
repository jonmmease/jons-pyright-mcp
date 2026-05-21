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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

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
)
from .utils import ResolvedFilePath, resolve_project_file

logger = logging.getLogger(__name__)

# Configure logging
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
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
        for f in [
            "setup.py",
            "pyproject.toml",
            "requirements.txt",
            "pyrightconfig.json",
        ]
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
1. Use document_symbols to find code in a file
2. Call type_info on a variable to discover its type, fields, and methods
3. Use definition to navigate to source, references to find usages
4. Check diagnostics after making changes

## Pagination
Tools returning lists (references, document_symbols, diagnostics, type_info methods)
return max 20 items. Use limit/offset parameters and check hasMore for additional results.
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


def get_project_root() -> Path:
    """Return the configured project root used for user file paths."""
    if manager:
        root = getattr(manager, "root", None)
        if root is not None:
            return Path(root).resolve()
        root_env = getattr(manager, "root_environment", None)
        if root_env is not None:
            return Path(root_env.project_root).resolve()
    return (_project_root or Path.cwd()).resolve()


def resolve_file_for_tool(file_path: str) -> ResolvedFilePath:
    """Resolve and validate a tool file path inside the project root."""
    return resolve_project_file(file_path, get_project_root())


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


async def ensure_pyright_indexed(file_path: str | Path | None = None) -> PyrightClient:
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
        client = await mgr.get_client_for_file(str(file_path))
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
    client: PyrightClient, file_path: str | Path, file_uri: str
) -> bool:
    """Ensure file is open in pyright with fresh content.

    Handles both initial file opening and stale file detection. If a file
    has been modified externally since it was opened, sends a didChange
    notification to update the LSP server.

    Args:
        client: The pyright client
        file_path: Path to the file
        file_uri: URI of the file

    Returns:
        True if file is open with current content, False if opening failed
    """
    mgr = get_manager()
    file_path = str(file_path)

    # Check if already opened in this environment
    if mgr.is_file_opened(file_path, file_uri):
        # Check if file has been modified externally
        if mgr.is_file_stale(file_path, file_uri):
            logger.debug(f"Detected stale file: {file_path}")
            try:
                return await _refresh_stale_file(client, mgr, file_path, file_uri)
            except FileNotFoundError:
                # File was deleted - send didClose and clean up
                logger.warning(f"File was deleted: {file_path}")
                await _handle_deleted_file(client, mgr, file_path, file_uri)
                return False
        return True

    # File not yet opened - open it fresh
    try:
        return await _open_file_fresh(client, mgr, file_path, file_uri)
    except FileNotFoundError:
        logger.error(f"File not found: {file_path}")
        return False
    except Exception as e:
        logger.error(f"Failed to open file {file_path}: {e}")
        return False


async def _open_file_fresh(
    client: PyrightClient,
    mgr: PyrightClientManager,
    file_path: str,
    file_uri: str,
) -> bool:
    """Open a file for the first time.

    Uses stat-read-stat pattern to handle race conditions.
    """
    # Stat-read-stat pattern to handle concurrent modifications
    mtime_before = os.stat(file_path).st_mtime_ns

    with open(file_path, encoding="utf-8") as f:
        content = f.read()

    mtime_after = os.stat(file_path).st_mtime_ns

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

    # Only cache mtime if file didn't change during read
    if mtime_before == mtime_after:
        mgr.set_file_mtime(file_path, file_uri, mtime_after)
    else:
        # File changed during read - don't cache mtime to force recheck
        logger.debug(f"File changed during read, not caching mtime: {file_path}")

    return True


async def _refresh_stale_file(
    client: PyrightClient,
    mgr: PyrightClientManager,
    file_path: str,
    file_uri: str,
) -> bool:
    """Refresh a stale file by sending didChange.

    Uses stat-read-stat pattern to handle race conditions.
    """
    # Stat-read-stat pattern
    mtime_before = os.stat(file_path).st_mtime_ns

    with open(file_path, encoding="utf-8") as f:
        content = f.read()

    mtime_after = os.stat(file_path).st_mtime_ns

    # Increment version for the change
    version = mgr.increment_doc_version(file_path, file_uri)

    # Send full content change (simplest approach)
    await client.notify(
        "textDocument/didChange",
        {
            "textDocument": {
                "uri": file_uri,
                "version": version,
            },
            "contentChanges": [{"text": content}],
        },
    )

    logger.debug(f"Refreshed stale file: {file_path} (version={version})")

    # Only cache mtime if file didn't change during read
    if mtime_before == mtime_after:
        mgr.set_file_mtime(file_path, file_uri, mtime_after)
    else:
        # File changed during read - don't cache mtime to force recheck
        logger.debug(f"File changed during refresh, not caching mtime: {file_path}")

    return True


async def _handle_deleted_file(
    client: PyrightClient,
    mgr: PyrightClientManager,
    file_path: str,
    file_uri: str,
) -> None:
    """Handle a file that was deleted after being opened."""
    # Send didClose to clean up LSP state
    await client.notify(
        "textDocument/didClose",
        {"textDocument": {"uri": file_uri}},
    )

    # Clean up our tracking
    env = mgr.get_environment_for_file(file_path)
    if env:
        normalized_uri = mgr._normalize_uri(file_uri)
        env.opened_files.discard(normalized_uri)
        env.doc_versions.pop(normalized_uri, None)
        env.file_mtimes.pop(normalized_uri, None)

    logger.debug(f"Cleaned up deleted file: {file_path}")


# Register all tools with the MCP server
mcp.tool(symbol_info)
mcp.tool(type_info)
mcp.tool(definition)
mcp.tool(type_definition)
mcp.tool(implementation)
mcp.tool(references)
mcp.tool(document_symbols)
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
    parser = argparse.ArgumentParser(description="MCP server for Pyright LSP features")
    parser.add_argument(
        "project_path",
        nargs="?",
        help="Path to the Python project (defaults to current directory)",
    )
    args = parser.parse_args()

    # Set project root from CLI argument
    if args.project_path:
        _project_root = Path(args.project_path).resolve()
        if not _project_root.exists() or not _project_root.is_dir():
            print(
                f"Error: Project path does not exist: {_project_root}", file=sys.stderr
            )
            sys.exit(1)

    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Run the MCP server
    mcp.run()


if __name__ == "__main__":
    main()
