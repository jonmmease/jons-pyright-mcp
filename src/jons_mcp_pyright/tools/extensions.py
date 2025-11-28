"""Pyright extension tools."""

import logging

from fastmcp import Context

from ..lsp_client import read_pyright_config

logger = logging.getLogger(__name__)


async def restart_server(ctx: Context | None = None) -> str:
    """Restart the pyright language server.

    Use this after modifying pyrightconfig.json or when pyright seems stuck.
    The server will re-read configuration on restart.

    Returns status message.
    """
    from ..server import handle_diagnostics, pyright as current_pyright

    # Import the module to modify globals
    from .. import server as server_module

    if not current_pyright:
        return "pyright server is not running"

    if ctx:
        await ctx.info("Restarting pyright server...")

    # Shutdown existing server
    await current_pyright.shutdown()

    # Re-read configuration before starting new server
    project_root = current_pyright.project_root
    pyright_config = read_pyright_config(project_root)

    # Import PyrightClient here to avoid circular import
    from ..lsp_client import PyrightClient

    # Start new server with updated config
    new_client = PyrightClient(project_root, pyright_config)
    new_client.on_notification("textDocument/publishDiagnostics", handle_diagnostics)

    await new_client.start()

    # Update the global reference
    server_module.pyright = new_client

    return "pyright server restarted successfully"
