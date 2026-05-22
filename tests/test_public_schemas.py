"""Focused tests for public response schemas and position conversion."""

from pydantic import ValidationError

from jons_mcp_pyright.schemas import (
    NavigationResult,
    SymbolInfoResult,
    ToolErrorResult,
)
from jons_mcp_pyright.utils import (
    lsp_result_to_public,
    navigation_result,
    public_position_to_lsp,
    tool_error,
)


def test_public_position_to_lsp_clamps_one_based_inputs():
    """Public one-based positions are converted to zero-based LSP positions."""
    assert public_position_to_lsp(1, 1) == {"line": 0, "character": 0}
    assert public_position_to_lsp(0, -4) == {"line": 0, "character": 0}
    assert public_position_to_lsp(12, 8) == {"line": 11, "character": 7}


def test_lsp_result_to_public_recursively_converts_positions():
    """Returned LSP ranges are recursively converted to one-based positions."""
    assert lsp_result_to_public(
        {
            "range": {
                "start": {"line": 0, "character": 0},
                "end": {"line": 2, "character": 4},
            }
        }
    ) == {
        "range": {
            "start": {"line": 1, "character": 1},
            "end": {"line": 3, "character": 5},
        }
    }


def test_navigation_result_supports_location_links_and_deduplicates():
    """Location and LocationLink responses normalize to the same public shape."""
    response = [
        {
            "targetUri": "file:///project/a.py",
            "targetRange": {
                "start": {"line": 0, "character": 0},
                "end": {"line": 4, "character": 0},
            },
            "targetSelectionRange": {
                "start": {"line": 1, "character": 2},
                "end": {"line": 1, "character": 5},
            },
            "originSelectionRange": {
                "start": {"line": 9, "character": 1},
                "end": {"line": 9, "character": 4},
            },
        },
        {
            "targetUri": "file:///project/a.py",
            "targetRange": {
                "start": {"line": 0, "character": 0},
                "end": {"line": 4, "character": 0},
            },
            "targetSelectionRange": {
                "start": {"line": 1, "character": 2},
                "end": {"line": 1, "character": 5},
            },
            "originSelectionRange": {
                "start": {"line": 9, "character": 1},
                "end": {"line": 9, "character": 4},
            },
        },
    ]

    result = NavigationResult.model_validate(navigation_result(response))

    assert result.totalItems == 1
    item = result.items[0]
    assert item.uri == "file:///project/a.py"
    assert item.range.start.line == 2
    assert item.range.start.character == 3
    assert item.fullRange is not None
    assert item.fullRange.end.line == 5
    assert item.originRange is not None
    assert item.originRange.start.line == 10


def test_public_schema_extra_fields_are_rejected():
    """Strict response schemas reject undocumented fields."""
    try:
        SymbolInfoResult.model_validate({"content": "x", "surprise": True})
    except ValidationError as exc:
        assert "Extra inputs are not permitted" in str(exc)
    else:
        raise AssertionError("expected strict schema validation to fail")


def test_tool_error_matches_public_error_schema():
    """tool_error returns the normalized public error shape."""
    result = ToolErrorResult.model_validate(tool_error("boom", "Broken"))
    assert result.error.code == "boom"
    assert result.error.message == "Broken"
    assert result.error.retryable is False
