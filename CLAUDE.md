# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A FastMCP server that exposes Pyright LSP features through the Model Context Protocol (MCP). It manages Pyright as a subprocess and translates between MCP and LSP protocols, enabling AI assistants to interact with Python code using Pyright's language intelligence.

## Build and Development Commands

```bash
# Install dependencies
uv pip install -e .

# Install with dev dependencies
uv pip install -e ".[dev]"

# Run the server
uv run jons-mcp-pyright

# Run all tests
uv run pytest

# Run a single test file
uv run pytest tests/test_lsp_client.py

# Run a single test
uv run pytest tests/test_lsp_client.py::test_name

# Run integration tests (requires pyright installed)
uv run pytest tests/test_integration.py -m integration

# Type check
uv run mypy src/jons_mcp_pyright

# Format code
uv run black src tests

# Lint code
uv run ruff check src tests
```

## Architecture

### Package Structure

```
src/jons_mcp_pyright/
├── __init__.py          # Package exports
├── constants.py         # Magic numbers, timeouts, LSP method constants
├── exceptions.py        # Custom exception classes
├── utils.py             # Pagination, file URI helpers, sort keys
├── lsp_client.py        # PyrightClient - LSP subprocess management
├── server.py            # FastMCP server setup, lifespan, main()
└── tools/
    ├── __init__.py      # Re-exports all tools
    ├── language.py      # symbol_info, type_info, definition, references, etc.
    ├── intelligence.py  # diagnostics, rename
    └── extensions.py    # restart_server
```

### Core Components

- **`lsp_client.py`**: `PyrightClient` - Thread-based LSP client that manages Pyright subprocess, handles LSP message framing (Content-Length headers), and maintains pending request futures

- **`server.py`**: FastMCP server with lifespan context manager for Pyright lifecycle. Contains global `pyright` client and `current_diagnostics` dict storing published diagnostics per file URI

- **`tools/`**: MCP tool functions organized by domain. Each tool validates context, translates MCP requests to LSP, and formats responses

### Key Patterns

- **File URI handling**: `ensure_file_uri()` in `utils.py` converts paths to `file://` URIs, supporting both absolute and relative paths (relative to cwd)

- **Pagination**: `apply_pagination()` provides consistent limit/offset handling across list-returning tools (`references`, `document_symbols`, `workspace_symbols`, `diagnostics`, `type_info`) with stable sorting

- **LSP message protocol**: Messages use JSON-RPC 2.0 with `Content-Length` headers over stdio

- **Context validation**: All tools accept `ctx: Context | None = None` and validate Pyright is initialized before proceeding

- **Custom exceptions**: `LSPRequestError`, `PyrightNotInitializedError`, `PyrightNotFoundError` provide specific error handling

- **Thread-based I/O**: Unlike rust-analyzer client, Pyright client uses threads for reading stdout/stderr to handle blocking I/O

### Test Structure

- `tests/test_lsp_client.py`: Unit tests for the LSP client message parsing/sending
- `tests/test_mcp_tools.py`: Unit tests for MCP tool wrappers with mocked pyright
- `tests/test_integration.py`: Integration tests requiring real pyright process (marked with `@pytest.mark.integration`)

## Configuration

- Server reads `pyrightconfig.json` from project root if present
- `LOG_LEVEL`: Set logging level (default: INFO)
- Server can be launched with project path argument or uses current directory

## MCP Tools (11 total)

### Navigation & Discovery
- **workspace_symbols**: Search for types/functions across the project by name
- **document_symbols**: List all symbols defined in a file
- **definition**: Jump to where a symbol is defined
- **type_definition**: Jump to the type definition of a symbol
- **implementation**: Find implementations of protocols/abstract classes
- **references**: Find all usages of a symbol

### Understanding Code
- **type_info**: Get type name, fields, and methods for a value (primary tool)
- **symbol_info**: Get type signature and docs for any symbol (via hover)

### Code Intelligence
- **diagnostics**: Get type errors and warnings

### Refactoring
- **rename**: Safely rename a symbol across the project

### Server Management
- **restart_server**: Restart Pyright after config changes
