"""Core language feature tools."""

from __future__ import annotations

import logging
from pathlib import Path
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
    DocumentSymbolItem,
    PaginatedResult,
    SymbolInfoResult,
    TypeInfoResult,
    TypeMember,
    TypeSourceLocation,
    dump_model,
)
from ..utils import (
    ResolvedFilePath,
    apply_pagination,
    exception_to_tool_error,
    file_uri_to_path,
    flatten_document_symbols,
    is_path_within_root,
    location_sort_key,
    members_method_sort_key,
    navigation_result,
    public_position_to_lsp,
    symbol_sort_key,
    tool_error,
)

logger = logging.getLogger(__name__)

METHOD_KINDS = {2, 3}
FIELD_KINDS = {5, 6, 10, 20}


async def _resolve_client_and_file(file_path: str) -> tuple[Any, ResolvedFilePath]:
    """Validate a tool path and return its routed Pyright client."""
    from ..server import ensure_pyright_indexed, resolve_file_for_tool

    resolved = resolve_file_for_tool(file_path)
    client = await ensure_pyright_indexed(resolved.path)
    return client, resolved


def _not_initialized_error(exc: PyrightNotInitializedError) -> dict[str, Any]:
    """Return a consistent initialization error response."""
    if "still initializing" in str(exc):
        return tool_error(
            "pyright_initializing",
            "Pyright is still initializing. Please try again in a few seconds.",
            retryable=True,
        )
    return tool_error("pyright_not_initialized", str(exc), retryable=True)


async def _sync_file(
    client: Any,
    resolved: ResolvedFilePath,
    *,
    wait_for_diagnostics: bool = False,
) -> dict[str, Any] | None:
    """Synchronize a file and return a public error if sync fails."""
    from ..server import ensure_file_open_and_ready

    try:
        await ensure_file_open_and_ready(
            client,
            resolved.path,
            resolved.uri,
            wait_for_diagnostics=wait_for_diagnostics,
        )
    except DocumentSyncError as exc:
        return exception_to_tool_error(exc)
    return None


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


def _extract_hover_text(hover_response: Any) -> str:
    """Extract display text from an LSP hover response."""
    if not hover_response:
        return ""
    contents = hover_response.get("contents", hover_response)
    if isinstance(contents, list):
        return "\n\n".join(_extract_hover_text({"contents": item}) for item in contents)
    if isinstance(contents, str):
        return contents.strip()
    if isinstance(contents, dict):
        value = contents.get("value")
        if value is not None:
            return str(value).strip()
        return str(contents).strip()
    return str(contents).strip()


def _type_name_from_display(display: str) -> str:
    """Best-effort extraction of a type name from Pyright hover text."""
    for raw_line in display.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("```") or line.startswith("//"):
            continue
        if ": " in line:
            return line.split(": ", 1)[1].strip() or "unknown"
        if " -> " in line:
            return line.rsplit(" -> ", 1)[1].strip() or "unknown"
        return line
    return "unknown"


def _kind_from_type_location(location: dict[str, Any] | None) -> str | None:
    """Infer a coarse type kind from source metadata when possible."""
    if location:
        return "class"
    return None


def _first_navigation_item(response: Any) -> dict[str, Any] | None:
    """Return the first normalized navigation item from an LSP response."""
    result = navigation_result(response)
    items = result["items"]
    return items[0] if items else None


def _identifier_bounds(line_text: str, cursor: int) -> tuple[int, int] | None:
    """Find identifier bounds at or immediately before cursor."""
    if not line_text:
        return None
    cursor = max(0, min(cursor, len(line_text)))
    index = min(cursor, len(line_text) - 1)
    if not (line_text[index].isalnum() or line_text[index] == "_"):
        if cursor > 0 and (
            line_text[cursor - 1].isalnum() or line_text[cursor - 1] == "_"
        ):
            index = cursor - 1
        else:
            return None

    start = index
    while start > 0 and (line_text[start - 1].isalnum() or line_text[start - 1] == "_"):
        start -= 1

    end = index + 1
    while end < len(line_text) and (line_text[end].isalnum() or line_text[end] == "_"):
        end += 1

    return start, end


def _member_name_and_class(label: str) -> tuple[str, str | None]:
    """Parse Pyright completion labels like ``method (BaseClass)``."""
    if " (" in label and label.endswith(")"):
        name, class_part = label.rsplit(" (", 1)
        return name, class_part[:-1]
    return label, None


async def _resolve_completion_item(
    client: Any,
    item: dict[str, Any],
    *,
    include_documentation: bool,
) -> tuple[str | None, str | None]:
    """Resolve a completion item for detail and optional documentation."""
    detail = item.get("detail")
    documentation = None
    try:
        resolved = await client.request(LSPMethods.COMPLETION_RESOLVE, item)
    except Exception as exc:
        logger.warning("Failed to resolve completion item %s: %s", item, exc)
        return detail, documentation

    if not resolved:
        return detail, documentation
    detail = detail if detail is not None else resolved.get("detail")
    if include_documentation:
        doc = resolved.get("documentation")
        if isinstance(doc, str):
            documentation = doc
        elif isinstance(doc, dict):
            documentation = doc.get("value")
    return detail, documentation


async def _get_members_via_completion(
    client: Any,
    file_uri: str,
    file_path: str,
    line: int,
    character: int,
    include_documentation: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Discover accessible fields and methods from a value reference."""
    content = Path(file_path).read_text(encoding="utf-8")
    lines = content.splitlines()
    current_line = lines[line] if 0 <= line < len(lines) else ""

    bounds = _identifier_bounds(current_line, character)
    if bounds is None:
        logger.info("type_info: no safe identifier for completion enrichment")
        return [], []

    _, token_end = bounds
    has_dot = token_end < len(current_line) and current_line[token_end] == "."
    modified_document = False
    mgr = None

    try:
        if has_dot:
            completion_character = token_end + 1
        else:
            from ..server import get_manager

            mgr = get_manager()
            modified_line = current_line[:token_end] + "." + current_line[token_end:]
            modified_lines = lines.copy()
            modified_lines[line] = modified_line
            doc_version = mgr.increment_doc_version(file_path, file_uri)
            await client.notify(
                LSPMethods.DID_CHANGE,
                {
                    "textDocument": {"uri": file_uri, "version": doc_version},
                    "contentChanges": [{"text": "\n".join(modified_lines)}],
                },
            )
            modified_document = True
            completion_character = token_end + 1

        response = await client.request(
            LSPMethods.COMPLETION,
            {
                "textDocument": {"uri": file_uri},
                "position": {"line": line, "character": completion_character},
            },
        )
    finally:
        if modified_document and mgr is not None:
            restore_version = mgr.increment_doc_version(file_path, file_uri)
            await client.notify(
                LSPMethods.DID_CHANGE,
                {
                    "textDocument": {"uri": file_uri, "version": restore_version},
                    "contentChanges": [{"text": content}],
                },
            )

    items = (
        response
        if isinstance(response, list)
        else (response.get("items", []) if response else [])
    )

    fields: list[dict[str, Any]] = []
    methods: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str | None]] = set()

    for item in items:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        if kind not in METHOD_KINDS and kind not in FIELD_KINDS:
            continue

        label = str(item.get("label") or "")
        if not label:
            continue
        name, class_info = _member_name_and_class(label)
        bucket = "method" if kind in METHOD_KINDS else "field"
        key = (bucket, name, class_info)
        if key in seen:
            continue
        seen.add(key)

        detail, documentation = await _resolve_completion_item(
            client,
            item,
            include_documentation=include_documentation,
        )
        entry: dict[str, Any] = {
            "name": name,
            "kind": kind,
            "detail": detail,
        }
        if bucket == "method":
            entry["class"] = class_info
        if include_documentation and documentation is not None:
            entry["documentation"] = documentation

        if bucket == "method":
            methods.append(entry)
        else:
            fields.append(entry)

    fields.sort(key=lambda item: item.get("name", ""))
    methods.sort(key=members_method_sort_key)
    return fields, methods


async def _get_methods_via_completion(
    client: Any,
    file_uri: str,
    file_path: str,
    line: int,
    character: int,
    include_documentation: bool = False,
) -> list[dict[str, Any]]:
    """Backward-compatible helper returning only method-like completion items."""
    _, methods = await _get_members_via_completion(
        client,
        file_uri,
        file_path,
        line,
        character,
        include_documentation,
    )
    return methods


async def symbol_info(
    file_path: str,
    line: int,
    character: int,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Get hover information for a symbol at a one-based public position."""
    try:
        client, resolved = await _resolve_client_and_file(file_path)
    except PyrightNotInitializedError as exc:
        return _not_initialized_error(exc)
    except (PathValidationError, ValueError) as exc:
        return exception_to_tool_error(exc)

    if ctx:
        await ctx.info(
            f"Getting symbol info at {resolved.display_path}:{line}:{character}"
        )

    sync_error = await _sync_file(client, resolved)
    if sync_error:
        return sync_error

    position = public_position_to_lsp(line, character)
    try:
        response = await client.request(
            LSPMethods.HOVER,
            {"textDocument": {"uri": resolved.uri}, "position": position},
        )
    except LSPRequestError as exc:
        return exception_to_tool_error(exc)

    content = _extract_hover_text(response) or "No symbol information available"
    public_range = None
    if isinstance(response, dict):
        public_range = _range_to_public(response.get("range"))

    return dump_model(
        SymbolInfoResult.model_validate({"content": content, "range": public_range})
    )


async def definition(
    file_path: str,
    line: int,
    character: int,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Go to the definition of a symbol at a one-based public position."""
    try:
        client, resolved = await _resolve_client_and_file(file_path)
    except PyrightNotInitializedError as exc:
        return _not_initialized_error(exc)
    except (PathValidationError, ValueError) as exc:
        return exception_to_tool_error(exc)

    if ctx:
        await ctx.info(
            f"Finding definition at {resolved.display_path}:{line}:{character}"
        )

    sync_error = await _sync_file(client, resolved)
    if sync_error:
        return sync_error

    try:
        response = await client.request(
            LSPMethods.DEFINITION,
            {
                "textDocument": {"uri": resolved.uri},
                "position": public_position_to_lsp(line, character),
            },
        )
    except LSPRequestError as exc:
        return exception_to_tool_error(exc)

    return navigation_result(response)


async def type_definition(
    file_path: str,
    line: int,
    character: int,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Go to the type definition at a one-based public position."""
    try:
        client, resolved = await _resolve_client_and_file(file_path)
    except PyrightNotInitializedError as exc:
        return _not_initialized_error(exc)
    except (PathValidationError, ValueError) as exc:
        return exception_to_tool_error(exc)

    if ctx:
        await ctx.info(
            f"Finding type definition at {resolved.display_path}:{line}:{character}"
        )

    sync_error = await _sync_file(client, resolved)
    if sync_error:
        return sync_error

    try:
        response = await client.request(
            LSPMethods.TYPE_DEFINITION,
            {
                "textDocument": {"uri": resolved.uri},
                "position": public_position_to_lsp(line, character),
            },
        )
    except LSPRequestError as exc:
        return exception_to_tool_error(exc)

    return navigation_result(response)


async def references(
    file_path: str,
    line: int,
    character: int,
    include_declaration: bool = True,
    limit: int = DEFAULT_PAGINATION_LIMIT,
    offset: int = DEFAULT_PAGINATION_OFFSET,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Find references at a one-based public position."""
    try:
        client, resolved = await _resolve_client_and_file(file_path)
    except PyrightNotInitializedError as exc:
        return _not_initialized_error(exc)
    except (PathValidationError, ValueError) as exc:
        return exception_to_tool_error(exc)

    if ctx:
        await ctx.info(
            f"Finding references at {resolved.display_path}:{line}:{character} "
            f"(limit: {limit}, offset: {offset})"
        )

    sync_error = await _sync_file(client, resolved, wait_for_diagnostics=True)
    if sync_error:
        return sync_error

    try:
        response = await client.request(
            LSPMethods.REFERENCES,
            {
                "textDocument": {"uri": resolved.uri},
                "position": public_position_to_lsp(line, character),
                "context": {"includeDeclaration": include_declaration},
            },
        )
    except LSPRequestError as exc:
        return exception_to_tool_error(exc)

    items = navigation_result(response)["items"]
    items.sort(key=location_sort_key)
    paginated_items, metadata = apply_pagination(
        items,
        offset,
        limit,
        add_offset_field=False,
    )
    return dump_model(
        PaginatedResult[dict[str, Any]](items=paginated_items, **metadata)
    )


async def document_symbols(
    file_path: str,
    limit: int = DEFAULT_PAGINATION_LIMIT,
    offset: int = DEFAULT_PAGINATION_OFFSET,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Get all symbols defined in a file."""
    try:
        client, resolved = await _resolve_client_and_file(file_path)
    except PyrightNotInitializedError as exc:
        return _not_initialized_error(exc)
    except (PathValidationError, ValueError) as exc:
        return exception_to_tool_error(exc)

    if ctx:
        await ctx.info(
            f"Getting document symbols for {resolved.display_path} "
            f"(limit: {limit}, offset: {offset})"
        )

    sync_error = await _sync_file(client, resolved)
    if sync_error:
        return sync_error

    try:
        response = await client.request(
            LSPMethods.DOCUMENT_SYMBOL,
            {"textDocument": {"uri": resolved.uri}},
        )
    except LSPRequestError as exc:
        return exception_to_tool_error(exc)

    symbols = response or []
    if symbols and isinstance(symbols[0], dict) and "children" in symbols[0]:
        symbols = flatten_document_symbols(symbols)

    symbols.sort(key=symbol_sort_key)
    public_symbols: list[dict[str, Any]] = []
    for symbol in symbols:
        if not isinstance(symbol, dict):
            continue
        item = {key: value for key, value in symbol.items() if key != "children"}
        location = item.pop("location", None)
        if isinstance(location, dict):
            item["uri"] = location.get("uri")
            item["range"] = _range_to_public(location.get("range"))
        elif "range" in item:
            item["range"] = _range_to_public(item.get("range"))
        if "selectionRange" in item:
            item["selectionRange"] = _range_to_public(item.get("selectionRange"))
        public_symbols.append(dump_model(DocumentSymbolItem.model_validate(item)))

    paginated_items, metadata = apply_pagination(
        public_symbols,
        offset,
        limit,
        add_offset_field=False,
    )
    return dump_model(
        PaginatedResult[DocumentSymbolItem](
            items=[DocumentSymbolItem.model_validate(item) for item in paginated_items],
            **metadata,
        )
    )


async def type_info(
    file_path: str,
    line: int,
    character: int,
    limit: int = DEFAULT_PAGINATION_LIMIT,
    offset: int = DEFAULT_PAGINATION_OFFSET,
    include_documentation: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Get reference-based type information at a one-based public position."""
    try:
        client, resolved = await _resolve_client_and_file(file_path)
    except PyrightNotInitializedError as exc:
        return _not_initialized_error(exc)
    except (PathValidationError, ValueError) as exc:
        return exception_to_tool_error(exc)

    if ctx:
        await ctx.info(
            f"Getting type info at {resolved.display_path}:{line}:{character} "
            f"(limit: {limit}, offset: {offset})"
        )

    sync_error = await _sync_file(client, resolved, wait_for_diagnostics=True)
    if sync_error:
        return sync_error

    position = public_position_to_lsp(line, character)

    try:
        hover_response = await client.request(
            LSPMethods.HOVER,
            {"textDocument": {"uri": resolved.uri}, "position": position},
        )
    except LSPRequestError as exc:
        return exception_to_tool_error(exc)

    display_string = _extract_hover_text(hover_response)
    type_name = _type_name_from_display(display_string)
    if not display_string or type_name == "unknown":
        return tool_error("type_not_found", "Could not determine type at position")

    try:
        type_def_response = await client.request(
            LSPMethods.TYPE_DEFINITION,
            {"textDocument": {"uri": resolved.uri}, "position": position},
        )
    except LSPRequestError as exc:
        return exception_to_tool_error(exc)

    type_location = _first_navigation_item(type_def_response)
    source_location = None
    if type_location:
        from ..server import get_project_root

        in_project = False
        try:
            type_path = file_uri_to_path(type_location["uri"])
            in_project = is_path_within_root(type_path, get_project_root())
        except PathValidationError:
            in_project = False
        source_location = TypeSourceLocation.model_validate(
            {**type_location, "inProject": in_project}
        )

    try:
        fields, methods = await _get_members_via_completion(
            client,
            resolved.uri,
            str(resolved.path),
            position["line"],
            position["character"],
            include_documentation,
        )
    except LSPRequestError as exc:
        return exception_to_tool_error(exc)
    except OSError as exc:
        return tool_error("file_read_error", str(exc))

    paginated_methods, method_metadata = apply_pagination(
        methods,
        offset,
        limit,
        add_offset_field=False,
    )
    methods_result = PaginatedResult[TypeMember](
        items=[TypeMember.model_validate(item) for item in paginated_methods],
        **method_metadata,
    )

    return dump_model(
        TypeInfoResult(
            displayString=display_string,
            typeName=type_name,
            kind=_kind_from_type_location(type_location),
            sourceLocation=source_location,
            fields=[TypeMember.model_validate(item) for item in fields],
            methods=methods_result,
        )
    )
