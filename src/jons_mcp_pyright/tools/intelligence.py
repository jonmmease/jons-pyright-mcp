"""Code intelligence tools."""

from typing import Any

from fastmcp import Context

from ..constants import DEFAULT_PAGINATION_LIMIT, DEFAULT_PAGINATION_OFFSET, LSPMethods
from ..utils import apply_pagination, diagnostic_sort_key, ensure_file_uri


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
    from ..server import ensure_file_open, ensure_pyright_indexed, get_manager

    mgr = get_manager()

    # Collect all diagnostics
    all_diagnostics: list[dict[str, Any]] = []

    if file_path:
        # Mode 1: Get diagnostics for specific file
        file_uri = ensure_file_uri(file_path)
        # Ensure file is open
        client = await ensure_pyright_indexed(file_path)
        await ensure_file_open(client, file_path, file_uri)

        # Get diagnostics for specific file from the manager
        file_diags = mgr.get_diagnostics_for_file(file_path)
        for diag in file_diags:
            all_diagnostics.append({**diag, "uri": file_uri})

    elif env_id:
        # Mode 2: Get all diagnostics for specific environment
        try:
            env_diagnostics = mgr.get_diagnostics_for_environment(env_id)
            for uri, diags in env_diagnostics.items():
                for diag in diags:
                    all_diagnostics.append({**diag, "uri": uri, "environment": env_id})
        except ValueError as e:
            return {
                "error": str(e),
                "items": [],
                "totalItems": 0,
                "hasMore": False,
            }

    else:
        # Mode 3: Aggregate diagnostics from all active environments
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
    from ..server import ensure_file_open, ensure_pyright_indexed

    client = await ensure_pyright_indexed(file_path)
    file_uri = ensure_file_uri(file_path)

    if ctx:
        await ctx.info(
            f"Renaming symbol at {file_path}:{line}:{character} to '{new_name}'"
        )

    # Ensure file is open
    await ensure_file_open(client, file_path, file_uri)

    # First check if rename is possible at this position
    prepare_result = await client.request(
        LSPMethods.PREPARE_RENAME,
        {
            "textDocument": {"uri": file_uri},
            "position": {"line": line, "character": character},
        },
    )

    if not prepare_result:
        return {"error": "Cannot rename at this position"}

    # Perform rename
    result = await client.request(
        LSPMethods.RENAME,
        {
            "textDocument": {"uri": file_uri},
            "position": {"line": line, "character": character},
            "newName": new_name,
        },
    )

    return result or {"error": "Rename returned no changes"}
