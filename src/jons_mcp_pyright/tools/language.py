"""Core language feature tools."""

from pathlib import Path
from typing import Any

from fastmcp import Context

from ..constants import DEFAULT_PAGINATION_LIMIT, DEFAULT_PAGINATION_OFFSET, LSPMethods
from ..exceptions import LSPRequestError, PyrightNotInitializedError
from ..utils import (
    apply_pagination,
    ensure_file_uri,
    flatten_document_symbols,
    location_sort_key,
    symbol_sort_key,
)


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
    from ..server import ensure_file_open, ensure_pyright_indexed

    try:
        client = await ensure_pyright_indexed(file_path)
    except PyrightNotInitializedError as e:
        if "still initializing" in str(e):
            return {
                "error": "Pyright is still initializing. Please try again in a few seconds."
            }
        return {"error": f"Pyright error: {e}"}

    file_uri = ensure_file_uri(file_path)

    if ctx:
        await ctx.info(f"Getting symbol info at {file_path}:{line}:{character}")

    # Ensure file is open
    await ensure_file_open(client, file_path, file_uri)

    try:
        response = await client.request(
            LSPMethods.HOVER,
            {
                "textDocument": {"uri": file_uri},
                "position": {"line": line, "character": character},
            },
        )
    except LSPRequestError as e:
        if "timed out" in str(e):
            return {
                "error": "Request timed out. The file might be too large or pyright is still analyzing. Please try again."
            }
        return {"error": f"LSP error: {e}"}

    if not response:
        return {"contents": "No symbol information available"}

    return dict(response) if isinstance(response, dict) else {"contents": response}


async def definition(
    file_path: str,
    line: int,
    character: int,
    ctx: Context | None = None,
) -> dict[str, Any] | list[dict[str, Any]]:
    """Go to definition of symbol at position (0-indexed).

    Returns location(s) where the symbol is defined: {uri, range}.
    Use this to jump from a variable/function usage to its declaration.
    """
    from ..server import ensure_file_open, ensure_pyright_indexed

    client = await ensure_pyright_indexed(file_path)
    file_uri = ensure_file_uri(file_path)

    if ctx:
        await ctx.info(f"Finding definition at {file_path}:{line}:{character}")

    # Ensure file is open
    await ensure_file_open(client, file_path, file_uri)

    response = await client.request(
        LSPMethods.DEFINITION,
        {
            "textDocument": {"uri": file_uri},
            "position": {"line": line, "character": character},
        },
    )

    return response or {"message": "No definition found"}


async def type_definition(
    file_path: str,
    line: int,
    character: int,
    ctx: Context | None = None,
) -> dict[str, Any] | list[dict[str, Any]]:
    """Go to type definition of symbol at position (0-indexed).

    Returns location(s) of the type definition: {uri, range}.
    Use this to navigate from a variable to its type's declaration.
    """
    from ..server import ensure_file_open, ensure_pyright_indexed

    client = await ensure_pyright_indexed(file_path)
    file_uri = ensure_file_uri(file_path)

    if ctx:
        await ctx.info(f"Finding type definition at {file_path}:{line}:{character}")

    # Ensure file is open
    await ensure_file_open(client, file_path, file_uri)

    response = await client.request(
        LSPMethods.TYPE_DEFINITION,
        {
            "textDocument": {"uri": file_uri},
            "position": {"line": line, "character": character},
        },
    )

    return response or {"message": "No type definition found"}


async def implementation(
    file_path: str,
    line: int,
    character: int,
    ctx: Context | None = None,
) -> dict[str, Any] | list[dict[str, Any]]:
    """Find implementations of class/protocol at position (0-indexed).

    Returns location(s) of implementations: {uri, range}.
    Call on a Protocol to find all classes implementing it.
    """
    from ..server import ensure_file_open, ensure_pyright_indexed

    client = await ensure_pyright_indexed(file_path)
    file_uri = ensure_file_uri(file_path)

    if ctx:
        await ctx.info(f"Finding implementations at {file_path}:{line}:{character}")

    # Ensure file is open
    await ensure_file_open(client, file_path, file_uri)

    response = await client.request(
        LSPMethods.IMPLEMENTATION,
        {
            "textDocument": {"uri": file_uri},
            "position": {"line": line, "character": character},
        },
    )

    return response or {"message": "No implementations found"}


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
    from ..server import ensure_file_open, ensure_pyright_indexed

    client = await ensure_pyright_indexed(file_path)
    file_uri = ensure_file_uri(file_path)

    if ctx:
        await ctx.info(
            f"Finding references at {file_path}:{line}:{character} "
            f"(limit: {limit}, offset: {offset})"
        )

    # Ensure file is open
    await ensure_file_open(client, file_path, file_uri)

    response = await client.request(
        LSPMethods.REFERENCES,
        {
            "textDocument": {"uri": file_uri},
            "position": {"line": line, "character": character},
            "context": {"includeDeclaration": include_declaration},
        },
    )

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
    from ..server import ensure_file_open, ensure_pyright_indexed

    client = await ensure_pyright_indexed(file_path)
    file_uri = ensure_file_uri(file_path)

    if ctx:
        await ctx.info(
            f"Getting document symbols for {file_path} (limit: {limit}, offset: {offset})"
        )

    # Ensure file is open
    await ensure_file_open(client, file_path, file_uri)

    response = await client.request(
        LSPMethods.DOCUMENT_SYMBOL,
        {"textDocument": {"uri": file_uri}},
    )

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

    # Read file content to find token boundaries
    content = Path(file_path).read_text()
    lines_list = content.splitlines()
    current_line = lines_list[line] if line < len(lines_list) else ""

    # Find end of current token (variable name)
    # Token ends at first non-identifier character
    token_end = character
    while token_end < len(current_line) and (
        current_line[token_end].isalnum() or current_line[token_end] == "_"
    ):
        token_end += 1

    # Check if there's already a dot after the token
    has_dot = token_end < len(current_line) and current_line[token_end] == "."

    # Track document version - start at 2 (1 was didOpen)
    doc_version = 2

    if has_dot:
        # Dot already exists, complete at position after dot
        dot_position = token_end + 1
        logger.info(f"type_info: dot already exists, completing at {line}:{dot_position}")
        response = await client.request(
            LSPMethods.COMPLETION,
            {
                "textDocument": {"uri": file_uri},
                "position": {"line": line, "character": dot_position},
            },
        )
    else:
        # Need to insert a dot - use didChange to modify document temporarily
        # Insert "." after the token
        modified_line = current_line[:token_end] + "." + current_line[token_end:]
        modified_lines = lines_list.copy()
        modified_lines[line] = modified_line

        logger.info(
            f"type_info: inserting dot at {line}:{token_end}, completing at {line}:{token_end + 1}"
        )

        # Send didChange with the modified content
        await client.notify(
            LSPMethods.DID_CHANGE,
            {
                "textDocument": {"uri": file_uri, "version": doc_version},
                "contentChanges": [{"text": "\n".join(modified_lines)}],
            },
        )
        doc_version += 1

        # Complete at position after the inserted dot
        response = await client.request(
            LSPMethods.COMPLETION,
            {
                "textDocument": {"uri": file_uri},
                "position": {"line": line, "character": token_end + 1},
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

    # Restore original content if we modified it
    if not has_dot:
        await client.notify(
            LSPMethods.DID_CHANGE,
            {
                "textDocument": {"uri": file_uri, "version": doc_version},
                "contentChanges": [{"text": content}],
            },
        )

    return methods


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

    from ..server import ensure_file_open, ensure_pyright_indexed
    from ..utils import members_method_sort_key

    logger = logging.getLogger(__name__)

    client = await ensure_pyright_indexed(file_path)
    file_uri = ensure_file_uri(file_path)

    # Resolve the file path for reading
    resolved_path = file_path
    if not Path(file_path).is_absolute():
        resolved_path = str(Path.cwd() / file_path)

    if ctx:
        await ctx.info(
            f"Getting type info at {file_path}:{line}:{character} "
            f"(limit: {limit}, offset: {offset})"
        )

    # Ensure file is open
    await ensure_file_open(client, file_path, file_uri)

    # Step 1: Get type definition location
    type_def_response = await client.request(
        LSPMethods.TYPE_DEFINITION,
        {
            "textDocument": {"uri": file_uri},
            "position": {"line": line, "character": character},
        },
    )

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
    type_file_content = None
    type_name = "unknown"
    type_kind = "primitive"  # Default to primitive if no type definition found
    field_symbols: list[dict[str, Any]] = []
    is_primitive_fallback = False  # Track if we're using hover fallback

    if type_location:
        # Handle both Location (uri, range) and LocationLink formats
        type_uri = type_location.get("uri") or type_location.get("targetUri") or ""
        type_range = type_location.get("range") or type_location.get("targetRange") or {}
        type_start = type_range.get("start", {})
        type_line = type_start.get("line", 0)
        type_character = type_start.get("character", 0)

    if type_uri:
        # We have a type definition location - this is a user-defined type
        type_file_path = type_uri.replace("file://", "")
        type_location_info = {
            "file_path": type_file_path,
            "line": type_line,
            "character": type_character,
        }

        logger.info(f"type_info: type definition at {type_uri}:{type_line}")

        # Step 2: Open the type definition file if it's a local file
        # This is needed for hover to work on fields
        if Path(type_file_path).exists():
            try:
                type_file_content = Path(type_file_path).read_text()
                await client.notify(
                    LSPMethods.DID_OPEN,
                    {
                        "textDocument": {
                            "uri": type_uri,
                            "languageId": "python",
                            "version": 1,
                            "text": type_file_content,
                        }
                    },
                )
            except Exception as e:
                logger.warning(f"Failed to open type file {type_uri}: {e}")

        # Step 3: Get document symbols for the type definition file to find fields
        type_symbols_response = await client.request(
            LSPMethods.DOCUMENT_SYMBOL,
            {"textDocument": {"uri": type_uri}},
        )

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
        # No type definition found - likely a built-in type
        # Fall back to hover to get the type name
        logger.info("type_info: no type definition, falling back to hover for type name")

        hover_response = await client.request(
            LSPMethods.HOVER,
            {
                "textDocument": {"uri": file_uri},
                "position": {"line": line, "character": character},
            },
        )

        if hover_response:
            contents = hover_response.get("contents", {})
            # Contents may be string, {kind, value}, or array
            if isinstance(contents, list):
                contents = contents[0] if contents else {}
            if isinstance(contents, str):
                # Try to extract type from hover text
                type_name = contents.strip()
            elif isinstance(contents, dict):
                value = contents.get("value", "")
                # Parse type from markdown hover
                lines = value.split("\n")
                for hover_line in lines:
                    hover_line = hover_line.strip()
                    if hover_line.startswith("```"):
                        continue
                    # Look for type annotation pattern "name: Type" or just the type
                    if ": " in hover_line:
                        type_name = hover_line.split(": ", 1)[1].strip()
                        break
                    elif hover_line and not hover_line.startswith("//"):
                        type_name = hover_line
                        break

        if type_name == "unknown":
            return {"error": "Could not determine type at position"}

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

    # Close the type file if we opened it
    if type_file_content is not None:
        try:
            await client.notify(
                LSPMethods.DID_CLOSE,
                {"textDocument": {"uri": type_uri}},
            )
        except Exception as e:
            logger.warning(f"Failed to close type file {type_uri}: {e}")

    # Step 5: Get methods via completion (finds ALL methods including inherited)
    methods: list[dict[str, Any]] = []
    if not is_primitive_fallback:
        methods = await _get_methods_via_completion(
            client, file_uri, resolved_path, line, character, include_documentation
        )
        # Sort methods: inherent first, then by class, then by name
        methods.sort(key=members_method_sort_key)
        logger.info(f"type_info: found {len(methods)} methods via completion")
    else:
        # For primitives, we can still try to get methods
        methods = await _get_methods_via_completion(
            client, file_uri, resolved_path, line, character, include_documentation
        )
        methods.sort(key=members_method_sort_key)
        logger.info(f"type_info: found {len(methods)} methods for primitive type")

    # Calculate totals
    total_fields = len(fields)
    total_methods = len(methods)

    # Paginate methods only (fields are typically few and always returned in full)
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
