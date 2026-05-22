"""Code intelligence tools."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastmcp import Context

from ..constants import DEFAULT_PAGINATION_LIMIT, DEFAULT_PAGINATION_OFFSET, LSPMethods
from ..diagnostic_filter import filter_diagnostics_by_member_config
from ..environment import IGNORE_PATTERNS, get_venv_patterns
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
    path_to_file_uri,
    public_position_to_lsp,
    resolve_project_file,
    tool_error,
)

logger = logging.getLogger(__name__)

RENAME_PREWARM_FILE_SUFFIXES = {".py", ".pyi"}
RENAME_PREWARM_DEFAULT_LIMIT = 2000
RENAME_PREWARM_DEFAULT_TIMEOUT_SECONDS = 8.0


@dataclass(frozen=True)
class RenamePrewarmCandidates:
    """Ordered candidate files and skipped-path count for rename prewarm."""

    paths: list[Path]
    skipped: int = 0


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


def _rename_edit_identity(
    edit: dict[str, Any],
) -> tuple[str, int, int, int, int, str]:
    """Build a stable identity for a public rename preview edit."""
    range_value = edit.get("range", {})
    start = range_value.get("start", {})
    end = range_value.get("end", {})
    return (
        str(edit.get("uri", "")),
        int(start.get("line", 0)),
        int(start.get("character", 0)),
        int(end.get("line", 0)),
        int(end.get("character", 0)),
        str(edit.get("newText", "")),
    )


def _rename_preview_from_edit_items(
    edits: list[dict[str, Any]],
    *,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Validate, deduplicate, and sort public rename preview edits."""
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int, int, int, str]] = set()
    for edit in edits:
        identity = _rename_edit_identity(edit)
        if identity in seen:
            continue
        seen.add(identity)
        deduped.append(edit)

    deduped.sort(key=_text_edit_sort_key)
    validated = [RenamePreviewEdit.model_validate(edit) for edit in deduped]
    return dump_model(
        RenamePreviewResult(
            edits=validated,
            totalEdits=len(validated),
            warnings=warnings or None,
        )
    )


def _workspace_edit_to_public_edits(workspace_edit: Any) -> list[dict[str, Any]]:
    """Convert WorkspaceEdit changes/documentChanges to public preview edits."""
    if not workspace_edit:
        return []
    if not isinstance(workspace_edit, dict):
        raise ValueError(
            "Rename returned an unsupported workspace edit shape",
        )

    edits: list[dict[str, Any]] = []

    changes = workspace_edit.get("changes")
    if changes is not None:
        if not isinstance(changes, dict):
            raise ValueError("Rename changes must be a URI-to-edits mapping")
        for uri, text_edits in changes.items():
            if not isinstance(text_edits, list):
                raise ValueError("Rename changes must contain text edit arrays")
            for text_edit in text_edits:
                if not isinstance(text_edit, dict):
                    raise ValueError("Rename changes contain an unsupported text edit")
                public_range = _range_to_public(text_edit.get("range"))
                if not public_range:
                    raise ValueError("Rename text edit is missing a range")
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
            raise ValueError("Rename documentChanges must be a list")
        for document_change in document_changes:
            if not isinstance(document_change, dict) or "edits" not in document_change:
                raise ValueError(
                    "Rename documentChanges may only contain text document edits"
                )
            text_document = document_change.get("textDocument")
            uri = (
                text_document.get("uri")
                if isinstance(text_document, dict)
                else document_change.get("uri")
            )
            if not uri:
                raise ValueError("Rename text document edit is missing a URI")
            text_edits = document_change.get("edits")
            if not isinstance(text_edits, list):
                raise ValueError("Rename text document edit must contain an edits list")
            for text_edit in text_edits:
                if not isinstance(text_edit, dict):
                    raise ValueError(
                        "Rename documentChanges contain an unsupported edit"
                    )
                public_range = _range_to_public(text_edit.get("range"))
                if not public_range:
                    raise ValueError("Rename text edit is missing a range")
                edits.append(
                    {
                        "uri": str(uri),
                        "range": public_range,
                        "newText": str(text_edit.get("newText", "")),
                    }
                )

    return edits


async def _reference_tool_edits_to_rename_edits(
    file_path: str,
    line: int,
    character: int,
    new_name: str,
) -> list[dict[str, Any]] | dict[str, Any]:
    """Use the public references path to backfill rename preview edits."""
    from .language import references

    references_result = await references(
        file_path=file_path,
        line=line,
        character=character,
        include_declaration=True,
        limit=100_000,
        offset=0,
        ctx=None,
    )
    if "error" in references_result:
        return references_result

    return [
        {
            "uri": item["uri"],
            "range": item["range"],
            "newText": new_name,
        }
        for item in references_result.get("items", [])
    ]


def _prepare_rename_range(prepare_result: Any) -> dict[str, Any] | None:
    """Extract an LSP range from a prepareRename response when available."""
    if not isinstance(prepare_result, dict):
        return None
    if "start" in prepare_result and "end" in prepare_result:
        return prepare_result
    range_value = prepare_result.get("range")
    return range_value if isinstance(range_value, dict) else None


def _identifier_at_position(line_text: str, character: int) -> str | None:
    """Extract a Python-like identifier around a zero-based character."""
    if not line_text:
        return None
    index = min(max(character, 0), max(len(line_text) - 1, 0))
    if not (line_text[index].isalnum() or line_text[index] == "_"):
        index = max(index - 1, 0)
    if not (line_text[index].isalnum() or line_text[index] == "_"):
        return None

    start = index
    while start > 0 and (line_text[start - 1].isalnum() or line_text[start - 1] == "_"):
        start -= 1
    end = index + 1
    while end < len(line_text) and (line_text[end].isalnum() or line_text[end] == "_"):
        end += 1

    identifier = line_text[start:end]
    return identifier or None


def _old_symbol_text(
    file_path: Path,
    prepare_result: Any,
    position: dict[str, int],
) -> str | None:
    """Best-effort old symbol text for prewarm candidate prioritization."""
    try:
        lines = file_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None

    range_value = _prepare_rename_range(prepare_result)
    if range_value:
        start = range_value.get("start") or {}
        end = range_value.get("end") or {}
        start_line = int(start.get("line", -1))
        end_line = int(end.get("line", -1))
        if start_line == end_line and 0 <= start_line < len(lines):
            start_char = max(0, int(start.get("character", 0)))
            end_char = max(start_char, int(end.get("character", start_char)))
            symbol = lines[start_line][start_char:end_char].strip()
            if symbol:
                return symbol

    line_index = position.get("line", 0)
    if 0 <= line_index < len(lines):
        return _identifier_at_position(lines[line_index], position.get("character", 0))
    return None


def _is_ignored_prewarm_dir(path: Path, ignored_names: set[str]) -> bool:
    """Return True when a directory should be skipped during prewarm discovery."""
    return path.name in ignored_names or path.name.endswith(".egg-info")


def _discover_python_files(root: Path) -> tuple[list[Path], int]:
    """Discover root-bound Python files while pruning ignored directories."""
    root = root.resolve()
    ignored_names = IGNORE_PATTERNS | set(get_venv_patterns())
    discovered: list[Path] = []
    skipped = 0
    stack = [root]

    while stack:
        directory = stack.pop()
        try:
            entries = sorted(directory.iterdir(), key=lambda entry: entry.name)
        except OSError:
            skipped += 1
            continue

        for entry in entries:
            if entry.is_symlink():
                skipped += 1
                continue
            if entry.is_dir():
                if _is_ignored_prewarm_dir(entry, ignored_names):
                    continue
                stack.append(entry)
                continue
            if entry.suffix not in RENAME_PREWARM_FILE_SUFFIXES:
                continue

            try:
                resolved = entry.resolve(strict=True)
                resolved.relative_to(root)
            except (OSError, ValueError):
                skipped += 1
                continue
            if resolved.is_file():
                discovered.append(resolved)
            else:
                skipped += 1

    return sorted(discovered), skipped


def _rename_prewarm_candidates(
    root: Path, old_symbol: str | None
) -> RenamePrewarmCandidates:
    """Return Python files ordered by likelihood of containing rename references."""
    candidates, skipped = _discover_python_files(root)
    if not old_symbol:
        return RenamePrewarmCandidates(paths=candidates, skipped=skipped)

    matching: list[Path] = []
    remaining: list[Path] = []
    for candidate in candidates:
        try:
            content = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            skipped += 1
            continue
        if old_symbol in content:
            matching.append(candidate)
        else:
            remaining.append(candidate)

    return RenamePrewarmCandidates(
        paths=matching + remaining,
        skipped=skipped,
    )


async def _prewarm_rename_workspace(
    client: Any,
    target_path: Path,
    target_uri: str,
    old_symbol: str | None,
    *,
    prewarm: bool,
    prewarm_limit: int,
    prewarm_timeout_seconds: float,
) -> list[str]:
    """Open and lightly inspect candidate files so Pyright indexes callers."""
    if not prewarm:
        return ["Prewarm was disabled; unopened files may be missed."]
    if prewarm_limit <= 0:
        return ["Prewarm limit was 0; unopened files may be missed."]
    if prewarm_timeout_seconds <= 0:
        return ["Prewarm timeout was 0; unopened files may be missed."]

    from ..server import get_manager

    mgr = get_manager()
    env = mgr.get_environment_for_file(str(target_path))
    if not env:
        return [
            "Could not determine the active Pyright environment; "
            "unopened files may be missed."
        ]

    candidate_result = _rename_prewarm_candidates(env.project_root, old_symbol)
    candidates = [
        path
        for path in candidate_result.paths
        if path != target_path
        and not mgr.is_file_opened(str(path), path_to_file_uri(path))
    ]

    warnings: list[str] = []
    skipped = candidate_result.skipped
    if len(candidates) > prewarm_limit:
        warnings.append(
            f"Prewarm hit the file limit ({prewarm_limit}); unopened files may be missed."
        )
    deadline = time.monotonic() + prewarm_timeout_seconds
    warmed = 0
    timed_out = False

    for candidate in candidates[:prewarm_limit]:
        if time.monotonic() >= deadline:
            timed_out = True
            break

        candidate_uri = path_to_file_uri(candidate)
        try:
            sync_error = await _sync_file(
                client,
                str(candidate),
                candidate_uri,
                wait_for_diagnostics=False,
            )
        except Exception as exc:
            skipped += 1
            logger.debug("Prewarm sync failed for %s: %s", candidate_uri, exc)
            continue
        if sync_error:
            skipped += 1
            continue

        try:
            await client.request(
                LSPMethods.DOCUMENT_SYMBOL,
                {"textDocument": {"uri": candidate_uri}},
            )
            warmed += 1
        except Exception as exc:
            skipped += 1
            logger.debug("Prewarm documentSymbol failed for %s: %s", candidate_uri, exc)

    if timed_out:
        warnings.append(
            f"Prewarm timed out after {prewarm_timeout_seconds:g}s; results may be incomplete."
        )
    if skipped:
        warnings.append(f"Prewarm skipped {skipped} unsafe or unreadable path(s).")
    if not warmed and candidates:
        warnings.append(
            "Prewarm did not warm any candidate files; unopened files may be missed."
        )

    logger.debug(
        "Rename prewarm completed for %s: warmed=%s candidates=%s skipped=%s",
        target_uri,
        warmed,
        len(candidates),
        skipped,
    )
    return warnings


def _normalize_rename_edits(
    workspace_edit: Any,
    supplemental_edits: list[dict[str, Any]] | None = None,
    *,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Normalize WorkspaceEdit plus optional supplemental edits to preview edits."""
    try:
        edits = _workspace_edit_to_public_edits(workspace_edit)
    except ValueError as exc:
        message = exc.args[0] if exc.args else "Rename returned an unsupported shape"
        return tool_error("rename_unsupported_edit_shape", str(message))

    if supplemental_edits:
        edits.extend(supplemental_edits)

    return _rename_preview_from_edit_items(edits, warnings=warnings)


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

    all_diagnostics = filter_diagnostics_by_member_config(all_diagnostics, project_root)
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
    prewarm: bool = True,
    prewarm_limit: int = RENAME_PREWARM_DEFAULT_LIMIT,
    prewarm_timeout_seconds: float = RENAME_PREWARM_DEFAULT_TIMEOUT_SECONDS,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Preview workspace-aware rename edits without writing files."""
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

    prewarm_warnings = await _prewarm_rename_workspace(
        client,
        resolved.path,
        resolved.uri,
        _old_symbol_text(resolved.path, prepare_result, position),
        prewarm=prewarm,
        prewarm_limit=prewarm_limit,
        prewarm_timeout_seconds=prewarm_timeout_seconds,
    )

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

    supplemental_edits_result = await _reference_tool_edits_to_rename_edits(
        file_path,
        line,
        character,
        new_name,
    )
    if isinstance(supplemental_edits_result, dict):
        return supplemental_edits_result

    return _normalize_rename_edits(
        result,
        supplemental_edits=supplemental_edits_result,
        warnings=prewarm_warnings,
    )
