"""Exceptions and data classes for the Pyright MCP server."""

from dataclasses import dataclass
from typing import Any


class LSPRequestError(Exception):
    """Raised when an LSP request fails.

    Attributes:
        message: Human-readable error description
        code: LSP error code (if available)
        is_retryable: Whether the error might succeed on retry
    """

    def __init__(
        self,
        message: str,
        code: int | None = None,
        is_retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.is_retryable = is_retryable

    def __str__(self) -> str:
        if self.code is not None:
            return f"{self.message} (code: {self.code})"
        return self.message


class PyrightNotInitializedError(Exception):
    """Raised when pyright client is not initialized."""

    pass


class PyrightNotFoundError(Exception):
    """Raised when pyright executable cannot be found."""

    pass


class PathValidationError(ValueError):
    """Raised when a user-supplied path is outside the project boundary."""

    code = "path_validation_error"


@dataclass
class Position:
    """LSP position in a text document."""

    line: int
    character: int

    def to_dict(self) -> dict[str, int]:
        return {"line": self.line, "character": self.character}


@dataclass
class Range:
    """LSP range in a text document."""

    start: Position
    end: Position

    def to_dict(self) -> dict[str, Any]:
        return {"start": self.start.to_dict(), "end": self.end.to_dict()}
