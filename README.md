# jons-pyright-mcp

A FastMCP server that exposes [pyright](https://github.com/microsoft/pyright) LSP features through the Model Context Protocol.

## Installation

```bash
# Install the package
uv add jons-pyright-mcp

# Or install from source
uv sync
```

## Usage

### Running the server

```bash
# Run the installed script
uv run jons-pyright-mcp

# Or run from source
uv run src/jons_mcp_pyright.py
```

### Claude Code Integration

Add as a project-scoped MCP server:

```bash
claude mcp add --scope project jons-pyright-mcp uv run jons-pyright-mcp
```

### Claude Desktop Integration

Add to your Claude Desktop configuration:

```json
{
  "mcpServers": {
    "jons-pyright-mcp": {
      "command": "uv",
      "args": ["run", "jons-pyright-mcp"]
    }
  }
}
```

## Features

### Core Language Features
- `hover` - Get type information and documentation at a position
- `completion` - Code completions with type information
  - `limit`: Maximum items to return (default: 50)
  - `offset`: Number of items to skip for pagination (default: 0)
  - Returns: Items with absolute `offset` for direct retrieval
- `definition` - Go to definition
- `type_definition` - Go to type definition
- `implementation` - Find implementations
- `references` - Find all references
  - `limit`: Maximum items to return (default: 50)
  - `offset`: Number of items to skip for pagination (default: 0)
  - Returns: Items with absolute `offset` for direct retrieval
- `document_symbols` - List symbols in a document
  - `limit`: Maximum items to return (default: 50)
  - `offset`: Number of items to skip for pagination (default: 0)
  - Returns: Items with absolute `offset` for direct retrieval
- `workspace_symbols` - Search symbols across workspace
  - `limit`: Maximum items to return (default: 50)
  - `offset`: Number of items to skip for pagination (default: 0)
  - Returns: Items with absolute `offset` for direct retrieval

### Code Intelligence
- `diagnostics` - Get type checking errors and warnings
  - `limit`: Maximum items to return (default: 50)
  - `offset`: Number of items to skip for pagination (default: 0)
  - Returns: Items with absolute `offset` for direct retrieval
- `code_actions` - Get available quick fixes
- `rename` - Rename symbols across the project
- `semantic_tokens` - Semantic syntax highlighting
- `signature_help` - Function signature help

### Formatting
- `format_document` - Format entire document
- `format_range` - Format selected range
- `organize_imports` - Organize imports according to PEP 8

### pyright Extensions
- `add_import` - Add missing import statements
- `create_config` - Create pyrightconfig.json
- `restart_server` - Restart the pyright server

### Pagination Support

Tools that return lists now support pagination to handle large result sets efficiently:

#### Example Usage

```python
# Get first page of references
result = await references(file_path="example.py", line=10, character=5, limit=20, offset=0)
# Returns: {"items": [...], "totalItems": 150, "hasMore": true, "nextOffset": 20}

# Get next page
result = await references(file_path="example.py", line=10, character=5, limit=20, offset=20)

# Get specific item by its offset
result = await references(file_path="example.py", line=10, character=5, limit=1, offset=42)
```

Each paginated response includes:
- `items`: Array of results with absolute `offset` for each item
- `totalItems`: Total number of items available
- `offset`: Current offset used
- `limit`: Current limit used
- `hasMore`: Boolean indicating if more items are available
- `nextOffset`: Offset to use for the next page (if `hasMore` is true)

## Configuration

The server uses the current working directory as the project root. It will look for:
- `pyrightconfig.json`
- `pyproject.toml` with `[tool.pyright]` section
- Common Python project files (`setup.py`, `requirements.txt`, etc.)

You can also set the `PYRIGHT_PATH` environment variable to use a specific pyright installation.

## Development

### Setup

```bash
# Clone the repository and sync dependencies
uv sync --extra dev
```

### Running tests

```bash
# Run all tests
uv run pytest

# Run specific test files
uv run pytest tests/test_lsp_client.py
uv run pytest tests/test_mcp_tools.py

# Run tests with coverage
uv run pytest --cov=src
```

### Development workflow

```bash
# Run the server during development
uv run src/jons_mcp_pyright.py

# Run linting/type checking (if configured)
uv run pyright src/

# Install in development mode
uv sync
```

### Project Structure

- `src/jons_mcp_pyright.py` - Main server implementation
- `requirements.md` - Detailed requirements document
- `tests/` - Test suite
  - `conftest.py` - Test fixtures
  - `test_lsp_client.py` - LSP client tests
  - `test_mcp_tools.py` - MCP tool tests
  - `test_integration.py` - Integration tests
- `pyproject.toml` - Project configuration and dependencies
- `uv.lock` - Dependency lock file

## License

This project follows the same license as the rust-analyzer MCP server it was based on.