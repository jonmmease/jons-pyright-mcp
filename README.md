# pyright-mcp

A FastMCP server that exposes [pyright](https://github.com/microsoft/pyright) LSP features through the Model Context Protocol.

## Installation

```bash
# Install with pip
pip install fastmcp pyright

# Or use uv to run directly
uv run pyright_mcp.py
```

## Usage

### Running the server

```bash
# Run with Python
python pyright_mcp.py

# Or make executable
chmod +x pyright_mcp.py
./pyright_mcp.py
```

### Claude Code Integration

Add as a project-scoped MCP server:

```bash
claude mcp add --scope project pyright uv run /path/to/pyright_mcp.py
```

### Claude Desktop Integration

Add to your Claude Desktop configuration:

```json
{
  "mcpServers": {
    "pyright": {
      "command": "uv",
      "args": ["run", "/path/to/pyright_mcp.py"]
    }
  }
}
```

## Features

### Core Language Features
- `hover` - Get type information and documentation at a position
- `completion` - Code completions with type information
- `definition` - Go to definition
- `type_definition` - Go to type definition
- `implementation` - Find implementations
- `references` - Find all references
- `document_symbols` - List symbols in a document
- `workspace_symbols` - Search symbols across workspace

### Code Intelligence
- `diagnostics` - Get type checking errors and warnings
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

## Configuration

The server uses the current working directory as the project root. It will look for:
- `pyrightconfig.json`
- `pyproject.toml` with `[tool.pyright]` section
- Common Python project files (`setup.py`, `requirements.txt`, etc.)

You can also set the `PYRIGHT_PATH` environment variable to use a specific pyright installation.

## Development

### Running tests

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run specific test files
pytest tests/test_lsp_client.py
pytest tests/test_mcp_tools.py
```

### Project Structure

- `pyright_mcp.py` - Main server implementation
- `requirements.md` - Detailed requirements document
- `tests/` - Test suite
  - `conftest.py` - Test fixtures
  - `test_lsp_client.py` - LSP client tests
  - `test_mcp_tools.py` - MCP tool tests
  - `test_integration.py` - Integration tests

## License

This project follows the same license as the rust-analyzer MCP server it was based on.