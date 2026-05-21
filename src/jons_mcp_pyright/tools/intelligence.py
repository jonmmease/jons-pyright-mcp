"""Code intelligence tools."""

import asyncio
import logging
from typing import Any

from fastmcp import Context

from ..constants import DEFAULT_PAGINATION_LIMIT, DEFAULT_PAGINATION_OFFSET, LSPMethods
from ..exceptions import (
    LSPRequestError,
    PathValidationError,
    PyrightNotInitializedError,
)
from ..utils import (
    apply_pagination,
    diagnostic_sort_key,
    exception_to_tool_error,
    file_uri_to_path,
    resolve_project_file,
    tool_error,
)

logger = logging.getLogger(__name__)


async def diagnostics(
    file_path: str | None = None,
    env_id: str | None = None,
    limit: int = DEFAULT_PAGINATION_LIMIT,
    offset: int = DEFAULT_PAGINATION_OFFSET,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Get current diagnostics (errors, warnings) for file(s).

    This is the primary tool for type checking. Returns type errors, warnings,
    and other issues detected by Pyright.

    Args:
        file_path: Optional path to specific file. Takes priority over env_id.
        env_id: Optional environment ID to get diagnostics from.
        limit: Maximum items to return (default: 20)
        offset: Number of items to skip for pagination (default: 0)

    Query modes:
    - file_path provided: Returns diagnostics for that file's environment only
    - env_id provided: Returns all diagnostics for that specific environment
    - Neither provided: Aggregates diagnostics from all active environments

    Returns {items, totalItems, hasMore, nextOffset} where each item has:
    - uri: File URI
    - severity: 1=Error, 2=Warning, 3=Info, 4=Hint
    - range: Location in file
    - message: Description of the issue
    - source: "Pyright"
    - environment: (optional) Environment ID when aggregating multiple envs

    Paginated: use limit/offset, check hasMore for more results.
    """
    from ..server import (
        ensure_file_open,
        ensure_pyright_indexed,
        get_manager,
        get_project_root,
    )

    mgr = get_manager()
    project_root = get_project_root()

    # Collect all diagnostics
    all_diagnostics: list[dict[str, Any]] = []
    events: list[asyncio.Event] = []

    if file_path:
        # Mode 1: Get diagnostics for specific file
        try:
            resolved = resolve_project_file(file_path, project_root)
            file_uri = resolved.uri
            client = await ensure_pyright_indexed(resolved.path)
        except (PathValidationError, ValueError) as e:
            return exception_to_tool_error(e)
        except PyrightNotInitializedError as e:
            return tool_error("pyright_not_initialized", str(e), retryable=True)

        # Check if file needs refresh BEFORE calling ensure_file_open
        needs_refresh = not mgr.is_file_opened(
            str(resolved.path), file_uri
        ) or mgr.is_file_stale(str(resolved.path), file_uri)

        # Register waiter BEFORE refresh to prevent race condition
        if needs_refresh:
            events.append(mgr.register_diagnostic_waiter(file_uri))

        await ensure_file_open(client, resolved.path, file_uri)

        # Wait for fresh diagnostics if we triggered a refresh
        if events:
            await mgr.wait_for_diagnostics(events)

        file_diags = mgr.get_diagnostics_for_file(str(resolved.path))
        for diag in file_diags:
            all_diagnostics.append({**diag, "uri": file_uri})

    elif env_id:
        # Mode 2: Get all diagnostics for specific environment
        try:
            env = mgr.get_environment(env_id)
            if not env:
                return tool_error(
                    "environment_not_found",
                    f"Environment not found: {env_id}",
                    items=[],
                    totalItems=0,
                    hasMore=False,
                )

            # Refresh stale files and register waiters
            if env.client:
                for uri in list(env.opened_files):
                    try:
                        file_path_from_uri = file_uri_to_path(uri)
                        resolved = resolve_project_file(
                            str(file_path_from_uri), project_root
                        )
                    except PathValidationError:
                        logger.warning("Skipping unsafe opened file URI: %s", uri)
                        continue
                    if mgr.is_file_stale(str(resolved.path), uri):
                        events.append(mgr.register_diagnostic_waiter(uri))
                        await ensure_file_open(env.client, resolved.path, uri)

                if events:
                    await mgr.wait_for_diagnostics(events)

            env_diagnostics = mgr.get_diagnostics_for_environment(env_id)
            for uri, diags in env_diagnostics.items():
                for diag in diags:
                    all_diagnostics.append(
                        {**diag, "uri": uri, "environment": env_id}
                    )
        except ValueError as e:
            return tool_error(
                "environment_not_found",
                str(e),
                items=[],
                totalItems=0,
                hasMore=False,
            )

    else:
        # Mode 3: Aggregate diagnostics from all active environments
        for env in mgr.get_all_environments():
            if env.client:
                for uri in list(env.opened_files):
                    try:
                        file_path_from_uri = file_uri_to_path(uri)
                        resolved = resolve_project_file(
                            str(file_path_from_uri), project_root
                        )
                    except PathValidationError:
                        logger.warning("Skipping unsafe opened file URI: %s", uri)
                        continue
                    if mgr.is_file_stale(str(resolved.path), uri):
                        events.append(mgr.register_diagnostic_waiter(uri))
                        await ensure_file_open(env.client, resolved.path, uri)

        if events:
            await mgr.wait_for_diagnostics(events)

        all_env_diagnostics = mgr.get_all_diagnostics()
        for uri, diags in all_env_diagnostics.items():
            for diag in diags:
                all_diagnostics.append({**diag, "uri": uri})

    all_diagnostics.sort(key=diagnostic_sort_key)
    paginated_items, metadata = apply_pagination(all_diagnostics, offset, limit)

    return {"items": paginated_items, **metadata}


async def rename(
    file_path: str,
    line: int,
    character: int,
    new_name: str,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Rename a symbol and all its references across the project.

    Args:
        file_path: Path to the Python file
        line: Zero-based line number
        character: Zero-based character offset in the line
        new_name: New name for the symbol

    Returns WorkspaceEdit with all changes needed, or error if rename not possible.
    """
    from ..server import ensure_file_open, ensure_pyright_indexed, resolve_file_for_tool

    try:
        resolved = resolve_file_for_tool(file_path)
        client = await ensure_pyright_indexed(resolved.path)
    except (PathValidationError, ValueError) as e:
        return exception_to_tool_error(e)
    except PyrightNotInitializedError as e:
        return tool_error("pyright_not_initialized", str(e), retryable=True)

    if ctx:
        await ctx.info(
            f"Renaming symbol at {resolved.display_path}:{line}:{character} to '{new_name}'"
        )

    # Ensure file is open
    await ensure_file_open(client, resolved.path, resolved.uri)

    # First check if rename is possible at this position
    try:
        prepare_result = await client.request(
            LSPMethods.PREPARE_RENAME,
            {
                "textDocument": {"uri": resolved.uri},
                "position": {"line": line, "character": character},
            },
        )
    except LSPRequestError as e:
        return exception_to_tool_error(e)

    if not prepare_result:
        return tool_error("rename_not_available", "Cannot rename at this position")

    # Perform rename
    try:
        result = await client.request(
            LSPMethods.RENAME,
            {
                "textDocument": {"uri": resolved.uri},
                "position": {"line": line, "character": character},
                "newName": new_name,
            },
        )
    except LSPRequestError as e:
        return exception_to_tool_error(e)

    return {"workspaceEdit": result or {}}
