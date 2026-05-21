"""Pyright extension tools."""

import logging
from typing import Any

from fastmcp import Context

from ..exceptions import PathValidationError
from ..utils import exception_to_tool_error

logger = logging.getLogger(__name__)


async def list_environments(
    ctx: Context | None = None,
) -> dict[str, Any]:
    """List all discovered Python environments.

    Returns information about each environment including:
    - env_id: Unique identifier (usually the project root path)
    - project_root: Path to the project root
    - venv_path: Path to the virtual environment (if found)
    - is_active: Whether the pyright client is currently running
    - last_accessed: When the environment was last accessed
    - opened_files_count: Number of files currently open in this environment

    Use this to understand which environments are available and their status.
    """
    from ..server import get_manager, get_project_root

    mgr = get_manager()

    environments: list[dict[str, Any]] = []

    for env in mgr.environments.values():
        env_info = {
            "env_id": env.env_id,
            "project_root": str(env.project_root),
            "venv_path": str(env.venv_path) if env.venv_path else None,
            "is_active": env.client is not None,
            "last_accessed": (
                env.last_accessed.isoformat() if env.last_accessed else None
            ),
            "opened_files_count": len(env.opened_files),
        }
        environments.append(env_info)

    return {
        "total": len(environments),
        "active_count": sum(1 for e in environments if e["is_active"]),
        "project_root": str(get_project_root()),
        "environments": environments,
    }


async def restart_server(
    file_path: str | None = None,
    env_id: str | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Restart the pyright language server.

    Use this after modifying pyrightconfig.json or when pyright seems stuck.
    The server will re-read configuration on restart. Previously opened files
    will be automatically re-opened after restart.

    Args:
        file_path: Optional path to restart only the environment for this file.
        env_id: Optional environment ID to restart specific environment.
                file_path takes priority over env_id.
        If neither provided, restarts all environments.

    Returns status message.
    """
    from ..server import get_manager, resolve_file_for_tool

    mgr = get_manager()

    if file_path:
        # Mode 1: Restart environment for specific file
        try:
            resolved = resolve_file_for_tool(file_path)
        except PathValidationError as e:
            return exception_to_tool_error(e)
        if ctx:
            await ctx.info(f"Restarting pyright server for {resolved.display_path}...")
        env = mgr.get_environment_for_file(str(resolved.path))
        if not env:
            return {
                "status": "error",
                "message": f"No environment found for file: {resolved.display_path}",
            }
        await mgr.restart_environment(env.env_id)
        return {
            "status": "restarted",
            "scope": "environment",
            "env_id": env.env_id,
            "file": resolved.uri,
        }

    elif env_id:
        # Mode 2: Restart specific environment by ID
        if ctx:
            await ctx.info(f"Restarting pyright server for environment {env_id}...")
        try:
            await mgr.restart_environment(env_id)
            return {"status": "restarted", "scope": "environment", "env_id": env_id}
        except ValueError as e:
            return {"status": "error", "message": str(e)}

    else:
        # Mode 3: Restart all environments and re-discover
        if ctx:
            await ctx.info("Restarting all pyright servers...")
        await mgr.restart_all()
        return {"status": "restarted", "scope": "all"}
