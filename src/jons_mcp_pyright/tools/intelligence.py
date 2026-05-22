"""Code intelligence tools."""

from __future__ import annotations

import logging
from typing import Any

from fastmcp import Context

from ..constants import DEFAULT_PAGINATION_LIMIT, DEFAULT_PAGINATION_OFFSET, LSPMethods
from ..exceptions import (
    DocumentSyncError,
    LSPRequestError,
    PathValidationError,
    PyrightNotInitializedError,
)
from ..schemas import (
    DiagnosticItem,
    PaginatedResult,
    RenamePreviewEdit,
    RenamePreviewResult,
    dump_model,
)
from ..utils import (
    apply_pagination,
    diagnostic_sort_key,
    exception_to_tool_error,
    file_uri_to_path,
    public_position_to_lsp,
    resolve_project_file,
    tool_error,
)

logger = logging.getLogger(__name__)


def _range_to_public(range_value: dict[str, Any] | None) -> dict[str, Any] | None:
    """Convert a possibly partial LSP range to a complete public range."""
    if not range_value:
        return None
    start = range_value.get("start") or {}
    end = range_value.get("end") or start
    return {
        "start": {
            "line": max(0, int(start.get("line", 0))) + 1,
            "character": max(0, int(start.get("character", 0))) + 1,
        },
        "end": {
            "line": max(0, int(end.get("line", start.get("line", 0)))) + 1,
            "character": max(0, int(end.get("character", start.get("character", 0))))
            + 1,
        },
    }


async def _sync_file(
    client: Any,
    file_path: str,
    file_uri: str,
    *,
    wait_for_diagnostics: bool = False,
) -> dict[str, Any] | None:
    """Synchronize a file and return a public error if sync fails."""
    from ..server import ensure_file_open_and_ready

    try:
        await ensure_file_open_and_ready(
            client,
            file_path,
            file_uri,
            wait_for_diagnostics=wait_for_diagnostics,
        )
    except DocumentSyncError as exc:
        return exception_to_tool_error(exc)
    return None


def _diagnostic_items(raw_items: list[dict[str, Any]]) -> list[DiagnosticItem]:
    """Validate and one-base public diagnostic items."""
    items: list[DiagnosticItem] = []
    for item in raw_items:
        public_item = dict(item)
        public_range = _range_to_public(public_item.get("range")) or _range_to_public(
            {"start": {"line": 0, "character": 0}}
        )
        public_item["range"] = public_range
        items.append(DiagnosticItem.model_validate(public_item))
    return items


def _text_edit_sort_key(edit: dict[str, Any]) -> tuple[str, int, int, int, int, str]:
    """Sort key for deterministic rename preview edits."""
    range_value = edit.get("range", {})
    start = range_value.get("start", {})
    end = range_value.get("end", {})
    return (
        edit.get("uri", ""),
        start.get("line", 0),
        start.get("character", 0),
        end.get("line", 0),
        end.get("character", 0),
        edit.get("newText", ""),
    )


def _normalize_rename_edits(workspace_edit: Any) -> dict[str, Any]:
    """Normalize WorkspaceEdit changes/documentChanges to preview edits."""
    if not workspace_edit:
        return dump_model(RenamePreviewResult(edits=[], totalEdits=0))
    if not isinstance(workspace_edit, dict):
        return tool_error(
            "rename_unsupported_edit_shape",
            "Rename returned an unsupported workspace edit shape",
        )

    edits: list[dict[str, Any]] = []

    changes = workspace_edit.get("changes")
    if changes is not None:
        if not isinstance(changes, dict):
            return tool_error(
                "rename_unsupported_edit_shape",
                "Rename changes must be a URI-to-edits mapping",
            )
        for uri, text_edits in changes.items():
            if not isinstance(text_edits, list):
                return tool_error(
                    "rename_unsupported_edit_shape",
                    "Rename changes must contain text edit arrays",
                )
            for text_edit in text_edits:
                if not isinstance(text_edit, dict):
                    return tool_error(
                        "rename_unsupported_edit_shape",
                        "Rename changes contain an unsupported text edit",
                    )
                public_range = _range_to_public(text_edit.get("range"))
                if not public_range:
                    return tool_error(
                        "rename_unsupported_edit_shape",
                        "Rename text edit is missing a range",
                    )
                edits.append(
                    {
                        "uri": str(uri),
                        "range": public_range,
                        "newText": str(text_edit.get("newText", "")),
                    }
                )

    document_changes = workspace_edit.get("documentChanges")
    if document_changes is not None:
        if not isinstance(document_changes, list):
            return tool_error(
                "rename_unsupported_edit_shape",
                "Rename documentChanges must be a list",
            )
        for document_change in document_changes:
            if not isinstance(document_change, dict) or "edits" not in document_change:
                return tool_error(
                    "rename_unsupported_edit_shape",
                    "Rename documentChanges may only contain text document edits",
                )
            text_document = document_change.get("textDocument")
            uri = (
                text_document.get("uri")
                if isinstance(text_document, dict)
                else document_change.get("uri")
            )
            if not uri:
                return tool_error(
                    "rename_unsupported_edit_shape",
                    "Rename text document edit is missing a URI",
                )
            text_edits = document_change.get("edits")
            if not isinstance(text_edits, list):
                return tool_error(
                    "rename_unsupported_edit_shape",
                    "Rename text document edit must contain an edits list",
                )
            for text_edit in text_edits:
                if not isinstance(text_edit, dict):
                    return tool_error(
                        "rename_unsupported_edit_shape",
                        "Rename documentChanges contain an unsupported edit",
                    )
                public_range = _range_to_public(text_edit.get("range"))
                if not public_range:
                    return tool_error(
                        "rename_unsupported_edit_shape",
                        "Rename text edit is missing a range",
                    )
                edits.append(
                    {
                        "uri": str(uri),
                        "range": public_range,
                        "newText": str(text_edit.get("newText", "")),
                    }
                )

    edits.sort(key=_text_edit_sort_key)
    validated = [RenamePreviewEdit.model_validate(edit) for edit in edits]
    return dump_model(RenamePreviewResult(edits=validated, totalEdits=len(validated)))


async def diagnostics(
    file_path: str | None = None,
    env_id: str | None = None,
    limit: int = DEFAULT_PAGINATION_LIMIT,
    offset: int = DEFAULT_PAGINATION_OFFSET,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Get current diagnostics for a file, environment, or all active environments."""
    from ..server import ensure_pyright_indexed, get_manager, get_project_root

    mgr = get_manager()
    project_root = get_project_root()

    all_diagnostics: list[dict[str, Any]] = []

    if file_path:
        try:
            resolved = resolve_project_file(file_path, project_root)
            client = await ensure_pyright_indexed(resolved.path)
        except (PathValidationError, ValueError) as exc:
            return exception_to_tool_error(exc)
        except PyrightNotInitializedError as exc:
            return tool_error("pyright_not_initialized", str(exc), retryable=True)

        sync_error = await _sync_file(
            client,
            str(resolved.path),
            resolved.uri,
            wait_for_diagnostics=True,
        )
        if sync_error:
            return sync_error

        for diag in mgr.get_diagnostics_for_file(str(resolved.path)):
            all_diagnostics.append({**diag, "uri": resolved.uri})

    elif env_id:
        env = mgr.get_environment(env_id)
        if not env:
            return tool_error(
                "environment_not_found", f"Environment not found: {env_id}"
            )

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
                sync_error = await _sync_file(
                    env.client,
                    str(resolved.path),
                    uri,
                    wait_for_diagnostics=True,
                )
                if sync_error:
                    return sync_error

        for uri, diags in mgr.get_diagnostics_for_environment(env_id).items():
            for diag in diags:
                all_diagnostics.append({**diag, "uri": uri, "environment": env_id})

    else:
        for env in mgr.get_all_environments():
            if not env.client:
                continue
            for uri in list(env.opened_files):
                try:
                    file_path_from_uri = file_uri_to_path(uri)
                    resolved = resolve_project_file(
                        str(file_path_from_uri), project_root
                    )
                except PathValidationError:
                    logger.warning("Skipping unsafe opened file URI: %s", uri)
                    continue
                sync_error = await _sync_file(
                    env.client,
                    str(resolved.path),
                    uri,
                    wait_for_diagnostics=True,
                )
                if sync_error:
                    return sync_error

        for uri, diags in mgr.get_all_diagnostics().items():
            for diag in diags:
                all_diagnostics.append({**diag, "uri": uri})

    all_diagnostics.sort(key=diagnostic_sort_key)
    public_diagnostics = _diagnostic_items(all_diagnostics)
    paginated_items, metadata = apply_pagination(
        [dump_model(item) for item in public_diagnostics],
        offset,
        limit,
        add_offset_field=False,
    )
    return dump_model(
        PaginatedResult[DiagnosticItem](
            items=[DiagnosticItem.model_validate(item) for item in paginated_items],
            **metadata,
        )
    )


async def preview_rename(
    file_path: str,
    line: int,
    character: int,
    new_name: str,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Preview all edits for a project-wide rename without writing files."""
    from ..server import ensure_pyright_indexed, resolve_file_for_tool

    try:
        resolved = resolve_file_for_tool(file_path)
        client = await ensure_pyright_indexed(resolved.path)
    except (PathValidationError, ValueError) as exc:
        return exception_to_tool_error(exc)
    except PyrightNotInitializedError as exc:
        return tool_error("pyright_not_initialized", str(exc), retryable=True)

    if ctx:
        await ctx.info(
            "Previewing rename at "
            f"{resolved.display_path}:{line}:{character} to '{new_name}'"
        )

    sync_error = await _sync_file(
        client,
        str(resolved.path),
        resolved.uri,
        wait_for_diagnostics=True,
    )
    if sync_error:
        return sync_error

    position = public_position_to_lsp(line, character)

    try:
        prepare_result = await client.request(
            LSPMethods.PREPARE_RENAME,
            {"textDocument": {"uri": resolved.uri}, "position": position},
        )
    except LSPRequestError as exc:
        return exception_to_tool_error(exc)

    if not prepare_result:
        return tool_error("rename_not_available", "Cannot rename at this position")

    try:
        result = await client.request(
            LSPMethods.RENAME,
            {
                "textDocument": {"uri": resolved.uri},
                "position": position,
                "newName": new_name,
            },
        )
    except LSPRequestError as exc:
        return exception_to_tool_error(exc)

    return _normalize_rename_edits(result)
