"""Utility functions for the Pyright MCP server."""

from pathlib import Path
from typing import Any, TypeVar

from .constants import DEFAULT_PAGINATION_LIMIT, DEFAULT_PAGINATION_OFFSET

T = TypeVar("T")


def ensure_file_uri(file_path: str) -> str:
    """Convert file path to proper file URI.

    Args:
        file_path: Path to the file (absolute, relative, or already a URI)

    Returns:
        Properly formatted file:// URI
    """
    if file_path.startswith("file://"):
        return file_path

    path = Path(file_path)
    if not path.is_absolute():
        path = Path.cwd() / path

    return f"file://{path.absolute()}"


def apply_pagination(
    items: list[T],
    offset: int = DEFAULT_PAGINATION_OFFSET,
    limit: int = DEFAULT_PAGINATION_LIMIT,
    add_offset_field: bool = True,
) -> tuple[list[T | dict[str, Any]], dict[str, Any]]:
    """Apply pagination to a list of items.

    Args:
        items: The full list of items to paginate
        offset: Number of items to skip
        limit: Maximum number of items to return
        add_offset_field: Whether to add an 'offset' field to each item

    Returns:
        Tuple of (paginated_items, metadata_dict)
    """
    total_items = len(items)
    start_idx = min(offset, total_items)
    end_idx = min(start_idx + limit, total_items)
    paginated = items[start_idx:end_idx]

    # Add offset field to each item if requested
    result_items: list[T | dict[str, Any]]
    if add_offset_field:
        processed_items: list[dict[str, Any]] = []
        for i, item in enumerate(paginated):
            if isinstance(item, dict):
                processed_item = item.copy()
            else:
                processed_item = {"item": item}
            processed_item["offset"] = start_idx + i
            processed_items.append(processed_item)
        result_items = processed_items  # type: ignore[assignment]
    else:
        result_items = list(paginated)

    has_more = end_idx < total_items

    metadata = {
        "totalItems": total_items,
        "offset": offset,
        "limit": limit,
        "hasMore": has_more,
        "nextOffset": end_idx if has_more else None,
    }

    return result_items, metadata


# Sort key functions for consistent pagination ordering


def location_sort_key(item: dict[str, Any]) -> tuple[str, int, int]:
    """Sort key for items with location info (references, etc.).

    Sorts by URI, then by line, then by character.
    """
    uri = item.get("uri", "")
    start = item.get("range", {}).get("start", {})
    line = start.get("line", 0)
    char = start.get("character", 0)
    return (uri, line, char)


def symbol_sort_key(item: dict[str, Any]) -> tuple[int, int, str]:
    """Sort key for document symbols.

    Sorts by line number, then character, then by name.
    """
    # For DocumentSymbol format
    if "range" in item:
        start = item.get("range", {}).get("start", {})
        line = start.get("line", 0)
        char = start.get("character", 0)
    # For SymbolInformation format
    else:
        location = item.get("location", {})
        start = location.get("range", {}).get("start", {})
        line = start.get("line", 0)
        char = start.get("character", 0)

    name = item.get("fullName", item.get("name", ""))
    return (line, char, name)


def diagnostic_sort_key(item: dict[str, Any]) -> tuple[int, str, int, int]:
    """Sort key for diagnostics.

    Sorts by severity (errors first), then by URI, then by position.
    """
    severity = item.get("severity", 999)  # Lower is more severe
    uri = item.get("uri", "")
    start = item.get("range", {}).get("start", {})
    line = start.get("line", 0)
    char = start.get("character", 0)
    return (severity, uri, line, char)


def members_method_sort_key(method: dict[str, Any]) -> tuple[int, str, str]:
    """Sort key for type_info methods: inherent first, then by class, then by name."""
    class_name = method.get("class")
    is_inherent = 0 if class_name is None else 1
    return (is_inherent, class_name or "", method.get("name", ""))


def parse_method_label(label: str) -> tuple[str, str | None, bool]:
    """Parse completion label to extract method name and class info.

    Pyright completion labels use these formats:
    - "method_name"                    -> method with no special annotation
    - "method_name (ClassName)"        -> method from a specific class

    Args:
        label: The completion item label from pyright

    Returns:
        Tuple of (method_name, class_name or None, needs_import)
    """
    import re

    # Match "method (ClassName)" pattern
    class_match = re.match(r"^(\w+)\s*\((.+)\)$", label)
    if class_match:
        return (class_match.group(1), class_match.group(2), False)

    # Plain method name
    return (label, None, False)


def flatten_document_symbols(
    symbols: list[dict[str, Any]],
    parent_name: str = "",
) -> list[dict[str, Any]]:
    """Flatten hierarchical document symbols for pagination.

    Args:
        symbols: List of potentially nested symbols
        parent_name: Name of parent symbol for context

    Returns:
        Flattened list of symbols with fullName field added
    """
    flat: list[dict[str, Any]] = []
    for symbol in symbols:
        # Create a copy to avoid modifying the original
        symbol_copy = symbol.copy()

        # Add parent context to name for clarity
        if parent_name:
            symbol_copy["fullName"] = f"{parent_name}.{symbol_copy['name']}"
        else:
            symbol_copy["fullName"] = symbol_copy["name"]

        # Add containerName for compatibility
        if parent_name:
            symbol_copy["containerName"] = parent_name

        flat.append(symbol_copy)

        # Recursively flatten children
        if "children" in symbol:
            flat.extend(
                flatten_document_symbols(symbol["children"], symbol_copy["fullName"])
            )

    return flat
