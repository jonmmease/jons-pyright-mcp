"""Utility functions for the Pyright MCP server."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import unquote, urlparse

from .constants import DEFAULT_PAGINATION_LIMIT, DEFAULT_PAGINATION_OFFSET
from .exceptions import DocumentSyncError, LSPRequestError, PathValidationError
from .schemas import (
    ErrorDetail,
    NavigationLocation,
    NavigationResult,
    PublicPosition,
    PublicRange,
    ToolErrorResult,
    dump_model,
)

T = TypeVar("T")


@dataclass(frozen=True)
class ResolvedFilePath:
    """A user file path resolved inside the configured project root."""

    path: Path
    uri: str
    project_root: Path

    @property
    def display_path(self) -> str:
        """Return a project-relative display path when possible."""
        try:
            return str(self.path.relative_to(self.project_root))
        except ValueError:
            return str(self.path)


def path_to_file_uri(path: Path) -> str:
    """Convert a local path to a normalized file URI."""
    return path.resolve().as_uri()


def file_uri_to_path(uri: str) -> Path:
    """Convert a local file URI to a path.

    Raises:
        PathValidationError: If the URI is not a local file URI.
    """
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise PathValidationError(f"Expected a file:// URI, got: {uri}")
    if parsed.netloc not in ("", "localhost"):
        raise PathValidationError(f"Only local file:// URIs are supported: {uri}")
    if not parsed.path:
        raise PathValidationError(f"File URI is missing a path: {uri}")
    return Path(unquote(parsed.path))


def is_path_within_root(path: Path, project_root: Path) -> bool:
    """Return True when path resolves within project_root."""
    try:
        path.resolve().relative_to(project_root.resolve())
        return True
    except (OSError, ValueError):
        return False


def resolve_project_file(
    file_path: str,
    project_root: Path,
    *,
    must_exist: bool = True,
    require_file: bool = True,
) -> ResolvedFilePath:
    """Resolve a user supplied path inside the configured project root.

    Accepts relative paths, absolute paths, and local file:// URIs. Relative paths
    are resolved from project_root, not the MCP process cwd.
    """
    raw_path = file_path.strip()
    if not raw_path:
        raise PathValidationError("file_path is required")

    root = project_root.resolve()
    path = (
        file_uri_to_path(raw_path) if raw_path.startswith("file://") else Path(raw_path)
    )
    if not path.is_absolute():
        path = root / path

    try:
        resolved = path.resolve(strict=must_exist)
    except FileNotFoundError as exc:
        raise PathValidationError(f"File not found: {file_path}") from exc
    except OSError as exc:
        raise PathValidationError(f"Unable to resolve file path: {file_path}") from exc

    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise PathValidationError(
            f"Path is outside the configured project root: {file_path}"
        ) from exc

    if must_exist and require_file and not resolved.is_file():
        raise PathValidationError(f"Path is not a file: {file_path}")

    return ResolvedFilePath(
        path=resolved, uri=path_to_file_uri(resolved), project_root=root
    )


def ensure_file_uri(file_path: str, project_root: Path | None = None) -> str:
    """Convert file path to proper file URI.

    Args:
        file_path: Path to the file (absolute, relative, or already a URI)
        project_root: Root used for relative paths. Defaults to process cwd for
            backward compatibility. Runtime tools pass the configured project root.

    Returns:
        Properly formatted file:// URI
    """
    if file_path.startswith("file://"):
        return file_path

    path = Path(file_path)
    if not path.is_absolute():
        path = (project_root or Path.cwd()) / path

    return path_to_file_uri(path)


def tool_error(
    code: str,
    message: str,
    *,
    retryable: bool = False,
) -> dict[str, Any]:
    """Return a consistent MCP tool error payload."""
    return dump_model(
        ToolErrorResult(
            error=ErrorDetail(
                code=code,
                message=message,
                retryable=retryable,
            )
        )
    )


def exception_to_tool_error(exc: Exception) -> dict[str, Any]:
    """Convert a known exception to the public tool error shape."""
    if isinstance(exc, PathValidationError):
        return tool_error(exc.code, str(exc))
    if isinstance(exc, DocumentSyncError):
        return tool_error(exc.code, str(exc), retryable=True)
    if isinstance(exc, LSPRequestError):
        code = "lsp_timeout" if exc.is_retryable else "lsp_error"
        return tool_error(code, exc.message, retryable=exc.is_retryable)
    return tool_error(type(exc).__name__, str(exc))


def public_position_to_lsp(line: int, character: int) -> dict[str, int]:
    """Convert one-based public coordinates to zero-based LSP coordinates."""
    return {
        "line": max(0, line - 1),
        "character": max(0, character - 1),
    }


def _lsp_position_to_public(position: dict[str, Any]) -> dict[str, int]:
    """Convert a zero-based LSP position to a one-based public position."""
    return {
        "line": max(0, int(position.get("line", 0))) + 1,
        "character": max(0, int(position.get("character", 0))) + 1,
    }


def _is_lsp_position(value: dict[str, Any]) -> bool:
    """Return True when a dict looks like an LSP Position."""
    return "line" in value and "character" in value


def lsp_result_to_public(value: Any) -> Any:
    """Recursively convert LSP line/character positions to one-based output."""
    if isinstance(value, list):
        return [lsp_result_to_public(item) for item in value]
    if isinstance(value, dict):
        if _is_lsp_position(value):
            return _lsp_position_to_public(value)
        return {key: lsp_result_to_public(item) for key, item in value.items()}
    return value


def _public_range_from_lsp(range_value: dict[str, Any] | None) -> PublicRange:
    """Build a public range from an LSP range, filling missing ends defensively."""
    range_value = range_value or {}
    start = range_value.get("start") or {}
    end = range_value.get("end") or start
    return PublicRange(
        start=PublicPosition(**_lsp_position_to_public(start)),
        end=PublicPosition(**_lsp_position_to_public(end)),
    )


def locations_to_items(response: Any) -> list[dict[str, Any]]:
    """Normalize LSP Location/LocationLink responses to public location dicts."""
    if not response:
        return []
    if isinstance(response, list):
        locations = [item for item in response if isinstance(item, dict)]
    if isinstance(response, dict):
        locations = [response]
    elif not isinstance(response, list):
        return []

    normalized: list[NavigationLocation] = []
    seen: set[str] = set()
    for item in locations:
        if "targetUri" in item:
            uri = item.get("targetUri")
            range_value = item.get("targetSelectionRange") or item.get("targetRange")
            full_range_value = item.get("targetRange")
            origin_range_value = item.get("originSelectionRange")
            if not uri or not range_value:
                continue
            location = NavigationLocation(
                uri=uri,
                range=_public_range_from_lsp(range_value),
                fullRange=(
                    _public_range_from_lsp(full_range_value)
                    if full_range_value and full_range_value != range_value
                    else None
                ),
                originRange=(
                    _public_range_from_lsp(origin_range_value)
                    if origin_range_value
                    else None
                ),
            )
        else:
            uri = item.get("uri")
            range_value = item.get("range")
            if not uri or not range_value:
                continue
            location = NavigationLocation(
                uri=uri,
                range=_public_range_from_lsp(range_value),
            )

        key = json.dumps(dump_model(location), sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(location)

    normalized.sort(key=lambda item: location_sort_key(dump_model(item)))
    return [dump_model(item) for item in normalized]


def navigation_result(response: Any) -> dict[str, Any]:
    """Return a validated public navigation response."""
    items = [
        NavigationLocation.model_validate(item) for item in locations_to_items(response)
    ]
    return dump_model(NavigationResult(items=items, totalItems=len(items)))


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
    offset = max(DEFAULT_PAGINATION_OFFSET, offset)
    limit = max(1, limit)
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
