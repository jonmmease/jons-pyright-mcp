"""Public response schemas for the Pyright MCP tools."""

from __future__ import annotations

from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class StrictModel(BaseModel):
    """Base model for strict public API responses."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ErrorDetail(StrictModel):
    """Normalized MCP tool error details."""

    code: str
    message: str
    retryable: bool = False


class ToolErrorResult(StrictModel):
    """Normalized MCP tool error response."""

    error: ErrorDetail


class PublicPosition(StrictModel):
    """One-based public position."""

    line: int
    character: int


class PublicRange(StrictModel):
    """One-based public range."""

    start: PublicPosition
    end: PublicPosition


class NavigationLocation(StrictModel):
    """Normalized Location or LocationLink response item."""

    uri: str
    range: PublicRange
    fullRange: PublicRange | None = None
    originRange: PublicRange | None = None


class NavigationResult(StrictModel):
    """Navigation result for definition-like tools."""

    items: list[NavigationLocation]
    totalItems: int


class PaginatedResult(StrictModel, Generic[T]):
    """Paginated public result."""

    items: list[T]
    totalItems: int
    offset: int
    limit: int
    hasMore: bool
    nextOffset: int | None = None


class SymbolInfoResult(StrictModel):
    """Hover-backed symbol information."""

    content: str
    range: PublicRange | None = None


class DocumentSymbolItem(StrictModel):
    """Flattened public document symbol."""

    name: str
    kind: int | None = None
    fullName: str | None = None
    containerName: str | None = None
    detail: str | None = None
    range: PublicRange | None = None
    selectionRange: PublicRange | None = None
    uri: str | None = None


class DiagnosticItem(BaseModel):
    """Diagnostic item with raw LSP extension fields allowed."""

    model_config = ConfigDict(extra="allow")

    uri: str
    range: PublicRange
    message: str
    severity: int | str | None = None
    code: str | int | None = None
    source: str | None = None
    environment: str | None = None


class TypeSourceLocation(NavigationLocation):
    """Type definition source location."""

    inProject: bool | None = None


class TypeMember(StrictModel):
    """Accessible field or method member."""

    name: str
    kind: int | None = None
    detail: str | None = None
    class_: str | None = Field(default=None, alias="class")
    documentation: str | None = None


class TypeInfoResult(StrictModel):
    """Reference-based type information."""

    displayString: str
    typeName: str
    kind: str | None = None
    sourceLocation: TypeSourceLocation | None = None
    fields: list[TypeMember]
    methods: PaginatedResult[TypeMember]


class RenamePreviewEdit(StrictModel):
    """Single text edit in a rename preview."""

    uri: str
    range: PublicRange
    newText: str


class RenamePreviewResult(StrictModel):
    """Preview-only rename result."""

    edits: list[RenamePreviewEdit]
    totalEdits: int


class EnvironmentItem(StrictModel):
    """Discovered Pyright environment."""

    env_id: str
    project_root: str
    venv_path: str | None = None
    is_active: bool
    last_accessed: str | None = None
    opened_files_count: int


class ListEnvironmentsResult(StrictModel):
    """Environment listing response."""

    total: int
    active_count: int
    project_root: str
    environments: list[EnvironmentItem]


class RestartServerResult(StrictModel):
    """Restart status response."""

    status: Literal["restarted"]
    scope: Literal["all", "environment"]
    env_id: str | None = None
    file: str | None = None


def dump_model(model: BaseModel) -> dict[str, Any]:
    """Dump a public schema model to a JSON-serializable dict."""

    return model.model_dump(exclude_none=True, by_alias=True)
