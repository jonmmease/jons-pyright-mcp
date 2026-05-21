"""Core language feature tools."""

from pathlib import Path
from typing import Any

from fastmcp import Context

from ..constants import DEFAULT_PAGINATION_LIMIT, DEFAULT_PAGINATION_OFFSET, LSPMethods
from ..exceptions import (
    LSPRequestError,
    PathValidationError,
    PyrightNotInitializedError,
)
from ..utils import (
    ResolvedFilePath,
    apply_pagination,
    exception_to_tool_error,
    file_uri_to_path,
    flatten_document_symbols,
    is_path_within_root,
    location_sort_key,
    locations_to_items,
    symbol_sort_key,
    tool_error,
)


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


def _navigation_result(response: Any) -> dict[str, Any]:
    """Normalize navigation responses to the public tool shape."""
    items = locations_to_items(response)
    return {"items": items, "totalItems": len(items)}


async def symbol_info(
    file_path: str,
    line: int,
    character: int,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Get type signature and documentation for symbol at position (0-indexed).

    Returns {contents} with type info and docs.
    Use this to get full details for any symbol, field, or method.
    """
    from ..server import ensure_file_open

    try:
        client, resolved = await _resolve_client_and_file(file_path)
    except PyrightNotInitializedError as e:
        return _not_initialized_error(e)
    except (PathValidationError, ValueError) as e:
        return exception_to_tool_error(e)

    if ctx:
        await ctx.info(
            f"Getting symbol info at {resolved.display_path}:{line}:{character}"
        )

    # Ensure file is open
    await ensure_file_open(client, resolved.path, resolved.uri)

    try:
        response = await client.request(
            LSPMethods.HOVER,
            {
                "textDocument": {"uri": resolved.uri},
                "position": {"line": line, "character": character},
            },
        )
    except LSPRequestError as e:
        return exception_to_tool_error(e)

    if not response:
        return {"contents": "No symbol information available"}

    return dict(response) if isinstance(response, dict) else {"contents": response}


async def definition(
    file_path: str,
    line: int,
    character: int,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Go to definition of symbol at position (0-indexed).

    Returns location(s) where the symbol is defined: {uri, range}.
    Use this to jump from a variable/function usage to its declaration.
    """
    from ..server import ensure_file_open

    try:
        client, resolved = await _resolve_client_and_file(file_path)
    except PyrightNotInitializedError as e:
        return _not_initialized_error(e)
    except (PathValidationError, ValueError) as e:
        return exception_to_tool_error(e)

    if ctx:
        await ctx.info(
            f"Finding definition at {resolved.display_path}:{line}:{character}"
        )

    # Ensure file is open
    await ensure_file_open(client, resolved.path, resolved.uri)

    try:
        response = await client.request(
            LSPMethods.DEFINITION,
            {
                "textDocument": {"uri": resolved.uri},
                "position": {"line": line, "character": character},
            },
        )
    except LSPRequestError as e:
        return exception_to_tool_error(e)

    return _navigation_result(response)


async def type_definition(
    file_path: str,
    line: int,
    character: int,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Go to type definition of symbol at position (0-indexed).

    Returns location(s) of the type definition: {uri, range}.
    Use this to navigate from a variable to its type's declaration.
    """
    from ..server import ensure_file_open

    try:
        client, resolved = await _resolve_client_and_file(file_path)
    except PyrightNotInitializedError as e:
        return _not_initialized_error(e)
    except (PathValidationError, ValueError) as e:
        return exception_to_tool_error(e)

    if ctx:
        await ctx.info(
            f"Finding type definition at {resolved.display_path}:{line}:{character}"
        )

    # Ensure file is open
    await ensure_file_open(client, resolved.path, resolved.uri)

    try:
        response = await client.request(
            LSPMethods.TYPE_DEFINITION,
            {
                "textDocument": {"uri": resolved.uri},
                "position": {"line": line, "character": character},
            },
        )
    except LSPRequestError as e:
        return exception_to_tool_error(e)

    return _navigation_result(response)


async def implementation(
    file_path: str,
    line: int,
    character: int,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Find implementations of class/protocol at position (0-indexed).

    Returns location(s) of implementations: {uri, range}.
    Call on a Protocol to find all classes implementing it.
    """
    from ..server import ensure_file_open

    try:
        client, resolved = await _resolve_client_and_file(file_path)
    except PyrightNotInitializedError as e:
        return _not_initialized_error(e)
    except (PathValidationError, ValueError) as e:
        return exception_to_tool_error(e)

    if ctx:
        await ctx.info(
            f"Finding implementations at {resolved.display_path}:{line}:{character}"
        )

    # Ensure file is open
    await ensure_file_open(client, resolved.path, resolved.uri)

    try:
        response = await client.request(
            LSPMethods.IMPLEMENTATION,
            {
                "textDocument": {"uri": resolved.uri},
                "position": {"line": line, "character": character},
            },
        )
    except LSPRequestError as e:
        return exception_to_tool_error(e)

    return _navigation_result(response)


async def references(
    file_path: str,
    line: int,
    character: int,
    include_declaration: bool = True,
    limit: int = DEFAULT_PAGINATION_LIMIT,
    offset: int = DEFAULT_PAGINATION_OFFSET,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Find all references to symbol at position (0-indexed).

    Returns {items, totalItems, hasMore, nextOffset} where each item has {uri, range}.
    Set include_declaration=false to exclude the definition itself.
    Paginated: use limit/offset, check hasMore for more results.
    """
    from ..server import ensure_file_open

    try:
        client, resolved = await _resolve_client_and_file(file_path)
    except PyrightNotInitializedError as e:
        return _not_initialized_error(e)
    except (PathValidationError, ValueError) as e:
        return exception_to_tool_error(e)

    if ctx:
        await ctx.info(
            f"Finding references at {resolved.display_path}:{line}:{character} "
            f"(limit: {limit}, offset: {offset})"
        )

    # Ensure file is open
    await ensure_file_open(client, resolved.path, resolved.uri)

    try:
        response = await client.request(
            LSPMethods.REFERENCES,
            {
                "textDocument": {"uri": resolved.uri},
                "position": {"line": line, "character": character},
                "context": {"includeDeclaration": include_declaration},
            },
        )
    except LSPRequestError as e:
        return exception_to_tool_error(e)

    items = response or []
    items.sort(key=location_sort_key)
    paginated_items, metadata = apply_pagination(items, offset, limit)

    return {"items": paginated_items, **metadata}


async def document_symbols(
    file_path: str,
    limit: int = DEFAULT_PAGINATION_LIMIT,
    offset: int = DEFAULT_PAGINATION_OFFSET,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Get all symbols defined in a file (functions, classes, methods, etc.).

    Returns {items, totalItems, hasMore, nextOffset} where each item has:
    - name, fullName: Symbol name (fullName includes parent context like "MyClass.method")
    - kind: Symbol type (5=Class, 6=Method, 8=Field, 12=Function, etc.)
    - range: Location in file

    Paginated: use limit/offset, check hasMore for more results.
    """
    from ..server import ensure_file_open

    try:
        client, resolved = await _resolve_client_and_file(file_path)
    except PyrightNotInitializedError as e:
        return _not_initialized_error(e)
    except (PathValidationError, ValueError) as e:
        return exception_to_tool_error(e)

    if ctx:
        await ctx.info(
            f"Getting document symbols for {resolved.display_path} "
            f"(limit: {limit}, offset: {offset})"
        )

    # Ensure file is open
    await ensure_file_open(client, resolved.path, resolved.uri)

    try:
        response = await client.request(
            LSPMethods.DOCUMENT_SYMBOL,
            {"textDocument": {"uri": resolved.uri}},
        )
    except LSPRequestError as e:
        return exception_to_tool_error(e)

    symbols = response or []

    # Check if symbols are hierarchical (DocumentSymbol) or flat (SymbolInformation)
    if symbols and "children" in symbols[0]:
        # Hierarchical - flatten for consistent pagination
        symbols = flatten_document_symbols(symbols)

    symbols.sort(key=symbol_sort_key)
    paginated_items, metadata = apply_pagination(symbols, offset, limit)

    # Remove children from paginated items to reduce response size
    for item in paginated_items:
        if isinstance(item, dict):
            item.pop("children", None)

    return {"items": paginated_items, **metadata}


async def _get_methods_via_completion(
    client: Any,
    file_uri: str,
    file_path: str,
    line: int,
    character: int,
    include_documentation: bool = False,
) -> list[dict[str, Any]]:
    """Get available methods using completion after a dot.

    The cursor can be anywhere in a variable name. This function:
    1. Finds the end of the current token (variable name)
    2. Checks if there's already a dot after it
    3. If no dot, inserts one temporarily using didChange
    4. Calls completion at the position after the dot
    5. Uses completionItem/resolve to get method signatures (detail field)
    6. Restores the original content if modified

    Args:
        include_documentation: If True, include doc comments for each method
    """
    import logging

    logger = logging.getLogger(__name__)

    def identifier_bounds(line_text: str, cursor: int) -> tuple[int, int] | None:
        """Find identifier bounds at or immediately before cursor."""
        if not line_text:
            return None
        index = min(cursor, len(line_text) - 1)
        if not (line_text[index].isalnum() or line_text[index] == "_"):
            if cursor > 0 and (
                line_text[cursor - 1].isalnum() or line_text[cursor - 1] == "_"
            ):
                index = cursor - 1
            else:
                return None

        start = index
        while start > 0 and (
            line_text[start - 1].isalnum() or line_text[start - 1] == "_"
        ):
            start -= 1

        end = index + 1
        while end < len(line_text) and (
            line_text[end].isalnum() or line_text[end] == "_"
        ):
            end += 1

        return start, end

    # Read file content to find token boundaries
    content = Path(file_path).read_text(encoding="utf-8")
    lines_list = content.splitlines()
    current_line = lines_list[line] if line < len(lines_list) else ""

    bounds = identifier_bounds(current_line, character)
    if bounds is None:
        logger.info("type_info: no safe identifier for completion enrichment")
        return []
    _, token_end = bounds

    # Check if there's already a dot after the token
    has_dot = token_end < len(current_line) and current_line[token_end] == "."
    modified_document = False
    mgr = None

    try:
        if has_dot:
            # Dot already exists, complete at position after dot
            dot_position = token_end + 1
            logger.info(
                f"type_info: dot already exists, completing at {line}:{dot_position}"
            )
            response = await client.request(
                LSPMethods.COMPLETION,
                {
                    "textDocument": {"uri": file_uri},
                    "position": {"line": line, "character": dot_position},
                },
            )
        else:
            # Need to insert a dot - use didChange to modify document temporarily
            # Get manager for proper version tracking (only needed when modifying)
            from ..server import get_manager

            mgr = get_manager()

            # Insert "." after the token
            modified_line = current_line[:token_end] + "." + current_line[token_end:]
            modified_lines = lines_list.copy()
            modified_lines[line] = modified_line

            logger.info(
                f"type_info: inserting dot at {line}:{token_end}, "
                f"completing at {line}:{token_end + 1}"
            )

            # Use manager to properly increment version
            doc_version = mgr.increment_doc_version(file_path, file_uri)

            # Send didChange with the modified content
            await client.notify(
                LSPMethods.DID_CHANGE,
                {
                    "textDocument": {"uri": file_uri, "version": doc_version},
                    "contentChanges": [{"text": "\n".join(modified_lines)}],
                },
            )
            modified_document = True

            # Complete at position after the inserted dot
            response = await client.request(
                LSPMethods.COMPLETION,
                {
                    "textDocument": {"uri": file_uri},
                    "position": {"line": line, "character": token_end + 1},
                },
            )
    finally:
        # Restore original content if we modified it, even when completion fails.
        if modified_document and mgr is not None:
            restore_version = mgr.increment_doc_version(file_path, file_uri)
            await client.notify(
                LSPMethods.DID_CHANGE,
                {
                    "textDocument": {"uri": file_uri, "version": restore_version},
                    "contentChanges": [{"text": content}],
                },
            )

    # Filter to method-like items
    # CompletionItemKind: 2=Method, 3=Function
    items = (
        response
        if isinstance(response, list)
        else (response.get("items", []) if response else [])
    )

    # Filter to methods (kind 2) and functions (kind 3)
    method_items = [item for item in items if item.get("kind") in (2, 3)]

    methods: list[dict[str, Any]] = []

    # Build method list from completion items
    for item in method_items:
        label = item.get("label", "")
        name = label
        class_info = None

        # Parse label to extract method name and class info
        # Pyright labels may include "(ClassName)" suffix
        if " (" in label and label.endswith(")"):
            parts = label.rsplit(" (", 1)
            name = parts[0]
            class_info = parts[1][:-1]  # Remove trailing )

        # Get full detail via completionItem/resolve
        detail = item.get("detail")
        documentation = None
        try:
            resolved = await client.request(LSPMethods.COMPLETION_RESOLVE, item)
            if resolved:
                if detail is None:
                    detail = resolved.get("detail")
                # Only extract documentation if requested (can be large)
                if include_documentation:
                    doc = resolved.get("documentation")
                    if doc:
                        if isinstance(doc, str):
                            documentation = doc
                        elif isinstance(doc, dict):
                            documentation = doc.get("value")
        except Exception as e:
            logger.warning(f"Failed to resolve completion item {name}: {e}")

        method_entry: dict[str, Any] = {
            "name": name,
            "kind": item.get("kind"),
            "detail": detail,
            "class": class_info,
        }
        if include_documentation:
            method_entry["documentation"] = documentation
        methods.append(method_entry)

    return methods


def _type_name_from_hover(hover_response: Any) -> str:
    """Best-effort extraction of a type name from Pyright hover contents."""
    if not hover_response:
        return "unknown"
    contents = hover_response.get("contents", {})
    if isinstance(contents, list):
        contents = contents[0] if contents else {}
    if isinstance(contents, str):
        return contents.strip() or "unknown"
    if isinstance(contents, dict):
        value = str(contents.get("value", ""))
        lines = value.split("\n")
        for hover_line in lines:
            hover_line = hover_line.strip()
            if not hover_line or hover_line.startswith("```"):
                continue
            if ": " in hover_line:
                return hover_line.split(": ", 1)[1].strip() or "unknown"
            if not hover_line.startswith("//"):
                return hover_line
    return "unknown"


async def type_info(
    file_path: str,
    line: int,
    character: int,
    limit: int = DEFAULT_PAGINATION_LIMIT,
    offset: int = DEFAULT_PAGINATION_OFFSET,
    include_documentation: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Get complete type information including fields and methods for the type at position.

    Call this on a variable or expression to discover its type and all accessible members.
    This is the primary tool for understanding what you can do with a value.
    Works with both user-defined types (classes) and built-in types (str, list, dict).

    Response includes:
    - typeName: The type's name (e.g., "list", "MyClass", "str")
    - typeKind: Kind of type (class, protocol, primitive)
    - typeLocation: Where the type is defined {file_path, line, character}, or null for built-ins
    - fields: Array of field members with {name, kind, detail} (empty for built-ins)
      - detail: Field type (e.g., "int", "list[str]")
    - methods: Array of method members with {name, kind, detail, class}
      - detail: Method signature (e.g., "(self, count: int) -> bool")
      - class: Which class this method comes from, or null for inherent methods
      - documentation: (only if include_documentation=True) Doc comments

    Args:
        include_documentation: If True, include doc comments for methods (default False)

    Paginated: use limit/offset for methods, check hasMore for more results.
    """
    import logging

    from ..server import ensure_file_open, get_project_root
    from ..utils import members_method_sort_key

    logger = logging.getLogger(__name__)

    try:
        client, resolved = await _resolve_client_and_file(file_path)
    except PyrightNotInitializedError as e:
        return _not_initialized_error(e)
    except (PathValidationError, ValueError) as e:
        return exception_to_tool_error(e)

    if ctx:
        await ctx.info(
            f"Getting type info at {resolved.display_path}:{line}:{character} "
            f"(limit: {limit}, offset: {offset})"
        )

    # Ensure file is open
    await ensure_file_open(client, resolved.path, resolved.uri)

    try:
        hover_response = await client.request(
            LSPMethods.HOVER,
            {
                "textDocument": {"uri": resolved.uri},
                "position": {"line": line, "character": character},
            },
        )
    except LSPRequestError as e:
        return exception_to_tool_error(e)

    hover_type_name = _type_name_from_hover(hover_response)

    # Step 1: Get type definition location
    try:
        type_def_response = await client.request(
            LSPMethods.TYPE_DEFINITION,
            {
                "textDocument": {"uri": resolved.uri},
                "position": {"line": line, "character": character},
            },
        )
    except LSPRequestError as e:
        return exception_to_tool_error(e)

    # Handle single location or array of locations
    type_location = None
    if type_def_response:
        if isinstance(type_def_response, list):
            type_location = type_def_response[0] if type_def_response else None
        else:
            type_location = type_def_response

    # Initialize variables for type info
    type_uri = ""
    type_line = 0
    type_character = 0
    type_location_info: dict[str, Any] | None = None
    type_name = hover_type_name
    type_kind = "primitive"  # Default to primitive if no type definition found
    field_symbols: list[dict[str, Any]] = []
    is_primitive_fallback = False  # Track if we're using hover fallback

    if type_location:
        # Handle both Location (uri, range) and LocationLink formats
        type_uri = type_location.get("uri") or type_location.get("targetUri") or ""
        type_range = (
            type_location.get("range") or type_location.get("targetRange") or {}
        )
        type_start = type_range.get("start", {})
        type_line = type_start.get("line", 0)
        type_character = type_start.get("character", 0)

    if type_uri:
        # We have a type definition location - this is a user-defined type
        project_root = get_project_root()
        try:
            type_file_path = file_uri_to_path(type_uri)
            in_project = is_path_within_root(type_file_path, project_root)
        except PathValidationError:
            type_file_path = None
            in_project = False
        type_location_info = {
            "uri": type_uri,
            "line": type_line,
            "character": type_character,
            "inProject": in_project,
        }

        logger.info(f"type_info: type definition at {type_uri}:{type_line}")

        # Step 2: Open the type definition file if it's a local file
        # This is needed for hover to work on fields
        if in_project and type_file_path and type_file_path.exists():
            try:
                await ensure_file_open(client, type_file_path, type_uri)
            except Exception as e:
                logger.warning(f"Failed to open type file {type_uri}: {e}")
        else:
            logger.info("type_info: skipping external type definition inspection")

        # Step 3: Get document symbols for in-root type definition files to find fields
        if in_project:
            try:
                type_symbols_response = await client.request(
                    LSPMethods.DOCUMENT_SYMBOL,
                    {"textDocument": {"uri": type_uri}},
                )
            except LSPRequestError as e:
                return exception_to_tool_error(e)
        else:
            type_symbols_response = []

        type_symbols = type_symbols_response or []

        # Find the type symbol at the definition location
        for symbol in type_symbols:
            symbol_range = symbol.get("range", {})
            symbol_start = symbol_range.get("start", {})
            if symbol_start.get("line") == type_line:
                type_name = symbol.get("name", "unknown")
                # LSP SymbolKind: 5=Class, 10=Enum, 11=Interface
                kind_num = symbol.get("kind", 0)
                kind_map = {5: "class", 10: "enum", 11: "protocol"}
                type_kind = kind_map.get(kind_num, "unknown")

                # Extract fields from children
                for child in symbol.get("children", []):
                    child_kind = child.get("kind", 0)
                    # LSP SymbolKind: 8=Field, 7=Variable (class variables)
                    if child_kind in (8, 7):
                        field_symbols.append(child)
                break
    else:
        # No type definition found - likely a built-in type. Fall back to hover.
        logger.info(
            "type_info: no type definition, falling back to hover for type name"
        )

        if type_name == "unknown":
            return tool_error("type_not_found", "Could not determine type at position")

        is_primitive_fallback = True
        logger.info(f"type_info: detected primitive/external type: {type_name}")

    # Step 4: Get field types via hover
    fields: list[dict[str, Any]] = []
    for field_symbol in field_symbols:
        field_name = field_symbol.get("name", "")
        field_kind = field_symbol.get("kind", 0)

        # Get field type via hover at the field's selection range
        field_detail = None
        selection_range = field_symbol.get("selectionRange", {})
        field_start = selection_range.get("start", {})
        field_line_num = field_start.get("line", 0)
        field_char = field_start.get("character", 0)

        try:
            hover_response = await client.request(
                LSPMethods.HOVER,
                {
                    "textDocument": {"uri": type_uri},
                    "position": {"line": field_line_num, "character": field_char},
                },
            )
            if hover_response:
                contents = hover_response.get("contents", {})
                logger.debug(f"Hover for field {field_name}: {contents}")
                # Contents may be string, {kind, value}, or array
                if isinstance(contents, list):
                    contents = contents[0] if contents else {}
                if isinstance(contents, str):
                    if ": " in contents:
                        field_detail = contents.split(": ", 1)[1].strip()
                    else:
                        field_detail = contents
                elif isinstance(contents, dict):
                    value = contents.get("value", "")
                    lines = value.split("\n")
                    for hover_line in lines:
                        hover_line = hover_line.strip()
                        if hover_line.startswith("```"):
                            continue
                        if hover_line.startswith(f"{field_name}:"):
                            field_detail = hover_line.split(":", 1)[1].strip()
                            break
                        elif ": " in hover_line and not hover_line.startswith("//"):
                            parts = hover_line.split(": ", 1)
                            if len(parts) == 2:
                                field_detail = parts[1].strip()
                                break
        except Exception as e:
            logger.warning(f"Failed to get hover for field {field_name}: {e}")

        fields.append(
            {
                "name": field_name,
                "kind": field_kind,
                "detail": field_detail,
            }
        )

    logger.info(
        f"type_info: found type {type_name} ({type_kind}) with {len(fields)} fields"
    )

    # Step 5: Get methods via completion (finds ALL methods including inherited)
    methods: list[dict[str, Any]] = []
    try:
        methods = await _get_methods_via_completion(
            client,
            resolved.uri,
            str(resolved.path),
            line,
            character,
            include_documentation,
        )
    except LSPRequestError as e:
        return exception_to_tool_error(e)
    except OSError as e:
        return tool_error("file_read_error", str(e))

    # Sort methods: inherent first, then by class, then by name
    methods.sort(key=members_method_sort_key)
    if not is_primitive_fallback:
        logger.info(f"type_info: found {len(methods)} methods via completion")
    else:
        logger.info(f"type_info: found {len(methods)} methods for primitive type")

    # Calculate totals
    total_fields = len(fields)
    total_methods = len(methods)

    # Paginate methods only (fields are typically few and always returned in full)
    offset = max(DEFAULT_PAGINATION_OFFSET, offset)
    limit = max(1, limit)
    start_idx = min(offset, total_methods)
    end_idx = min(start_idx + limit, total_methods)
    paginated_methods = methods[start_idx:end_idx]

    has_more = end_idx < total_methods

    return {
        "typeName": type_name,
        "typeKind": type_kind,
        "typeLocation": type_location_info,
        "fields": fields,
        "methods": paginated_methods,
        "totalFields": total_fields,
        "totalMethods": total_methods,
        "offset": offset,
        "limit": limit,
        "hasMore": has_more,
        "nextOffset": end_idx if has_more else None,
    }
