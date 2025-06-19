# Requirements Document: FastMCP pyright Server

## Project Overview
Build a standalone Python script that creates a FastMCP server exposing all pyright LSP features through MCP tools. The server will manage pyright as a subprocess and translate between the MCP and LSP protocols.

## Technical Requirements

### 1. Standalone Script Architecture
- Single Python file with inline dependencies (using uv comment syntax)
- Dependencies specified as: `# /// script` block for uv
- Python version requirement: `>= 3.10` (required by FastMCP)
- Main dependencies: `fastmcp>=0.3.0`, `pyright>=1.1.0`, `asyncio` (built-in)
- Use pyright Python package for simplified installation

### 2. pyright Discovery
- Primary: Use pyright installed via pip (`pip install pyright`)
- Fallback: Check if `pyright-langserver` is on PATH
- Configurable path through environment variable: `PYRIGHT_PATH`
- The pyright Python package provides automatic Node.js management

### 3. Server Initialization
- Start pyright subprocess on MCP server startup using `pyright-langserver --stdio`
- Initialize LSP connection with proper capabilities
- Use current working directory as the Python project root
- pyright will be tied to this single project/workspace
- Assumes MCP server is launched from the Python project directory (Claude Code use case)

### 4. LSP Communication Layer
- Custom asyncio-based implementation (~300 lines total)
- Handle stdio communication with proper LSP headers
- Message parsing with Content-Length header processing
- JSON-RPC 2.0 message format with proper typing
- Request/response correlation with monotonic ID counter
- Notification handling for server-initiated messages (diagnostics)
- 30-second timeout for requests with proper cleanup
- Concurrent request support via asyncio.Future tracking

#### Why Custom Implementation Over Off-the-Shelf Libraries
After evaluating options like pygls, pylspclient, and python-lsp-jsonrpc, a custom implementation is recommended because:
- **Simplicity**: LSP client needs are straightforward (~200 lines) vs heavy dependencies
- **FastMCP Integration**: Native asyncio integration with FastMCP's event loop
- **Minimal Dependencies**: Only requires `fastmcp` and `pyright` for a cleaner standalone script
- **Full Control**: Easy debugging, custom logging, and pyright specific optimizations
- **Maintenance**: LSP protocol is stable; no risk of upstream breaking changes

The implementation is essentially JSON-RPC over stdio with header parsing - straightforward for our focused use case.

### 5. MCP Tool Mapping

#### Core Language Features
- `hover` - Get hover information at position
- `completion` - Get code completions
- `definition` - Go to definition
- `type_definition` - Go to type definition
- `implementation` - Find implementations
- `references` - Find all references
- `document_symbols` - List symbols in document
- `workspace_symbols` - Search symbols in workspace

#### Code Intelligence
- `diagnostics` - Get current diagnostics
- `code_actions` - Get available code actions
- `rename` - Rename symbol
- `semantic_tokens` - Get semantic highlighting
- `signature_help` - Get function signature help

#### Formatting and Organization
- `format_document` - Format entire document
- `format_range` - Format selected range
- `organize_imports` - Organize Python imports (pyright.organizeimports)

#### pyright Extensions
- `add_import` - Add missing import statement
- `create_config` - Create pyrightconfig.json
- `restart_server` - Restart pyright server

### 6. File Management
- Tools must handle file URIs correctly (`file://` protocol)
- Support both absolute and relative paths
- Automatic text document synchronization
- Support for .py, .pyi, and .pyw files

### 7. Python Environment Support
- Automatic virtual environment detection
- Support for multiple Python interpreters
- Respect VIRTUAL_ENV environment variable
- Handle installed packages and type stubs
- Extra paths configuration support

### 8. Error Handling
- Graceful handling of pyright crashes
- Automatic restart capability
- Clear error messages for MCP clients
- Timeout handling for LSP requests

### 9. Lifecycle Management
- Proper shutdown sequence (LSP shutdown → exit)
- Resource cleanup on MCP server termination
- Handle signal interrupts gracefully

## Implementation Structure

```python
#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "fastmcp>=0.3.0",
#   "pyright>=1.1.0",
# ]
# ///

import asyncio
import json
import os
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional
from contextlib import asynccontextmanager
from fastmcp import FastMCP, Context

# Global client instance
pyright: Optional[PyrightClient] = None

# Lifecycle management using lifespan context manager
@asynccontextmanager
async def lifespan(mcp: FastMCP):
    """Manage pyright lifecycle"""
    global pyright
    # Startup: Initialize pyright
    pyright = PyrightClient(Path.cwd())
    await pyright.start()
    yield
    # Shutdown: Clean up pyright
    await pyright.shutdown()

# FastMCP server instance with lifespan
mcp = FastMCP(
    name="pyright-mcp",
    lifespan=lifespan
)

# PyrightClient class implementation
class PyrightClient:
    # ... full asyncio-based implementation ...

# MCP Tools
@mcp.tool
async def hover(file_path: str, line: int, character: int, ctx: Context) -> Dict[str, Any]:
    """Get hover information at specified position"""
    # Implementation using pyright client
    pass

# ... additional tools ...

if __name__ == "__main__":
    mcp.run()
```

## Usage Example

```bash
# Install and run with uv
uv run pyright-mcp.py

# Or make executable and run directly
chmod +x pyright-mcp.py
./pyright-mcp.py
```

## MCP Client Configuration

### Claude Code (CLI)
```bash
# Add as project-scoped MCP server
claude mcp add --scope project pyright uv run /path/to/pyright_mcp.py

# Or with wrapper script if ENOENT errors occur
claude mcp add --scope project pyright /path/to/run_pyright_mcp.sh
```

### Claude Desktop
```json
{
  "mcpServers": {
    "pyright": {
      "command": "uv",
      "args": ["run", "/path/to/pyright-mcp.py"],
      "env": {
        "PYRIGHT_PATH": "/custom/path/to/pyright-langserver"
      }
    }
  }
}
```

## Deliverables
1. Single Python script file with all functionality
2. Comprehensive error handling and logging
3. Support for all pyright LSP features
4. Documentation in script comments
5. Example usage patterns

## Research Summary

### FastMCP Framework
- Standard Python framework for MCP servers
- Built-in support for stdio transport (ideal for LSP integration)
- Decorator-based tool definition
- Native asyncio support for concurrent operations
- Automatic schema generation from type hints

### pyright LSP Features
- Supports all standard LSP features
- Extensive Python type checking capabilities
- Communicates via stdio with JSON-RPC 2.0 protocol
- Requires proper initialization sequence
- Supports workspace and single-file modes
- Automatic virtual environment detection

### Python LSP Client Implementation
- Custom asyncio implementation recommended for full control
- Must handle LSP message framing (Content-Length headers)
- Asynchronous request/response correlation required
- Notification handling for diagnostics and other server-initiated messages
- Proper subprocess lifecycle management critical

## Implementation Lessons Learned

### Version Requirements
- **Python >= 3.10**: Required by FastMCP dependency chain
- **FastMCP >= 0.3.0**: Minimum version for stable API
- **pyright**: Use Python package for automatic Node.js management
- **uv script metadata**: Must include `requires-python = ">=3.10"`

### API Compatibility Issues Resolved
1. **FastMCP Initialization**: 
   - ❌ `FastMCP(version="0.1.0")` - version parameter not supported
   - ✅ `FastMCP(name="pyright-mcp")` - correct initialization
   
2. **Lifecycle Management**:
   - ❌ `@mcp.server.on_initialize` - deprecated pattern
   - ✅ `lifespan` parameter with asynccontextmanager - proper pattern

3. **Tool Function Access**:
   - ❌ Direct function calls in tests fail
   - ✅ Access via `.fn` attribute on decorated functions

4. **Semantic Tokens Capability**:
   - ❌ Missing `formats` field causes pyright errors
   - ✅ Must include `"formats": ["relative"]` in capability

### pyright Integration Insights
1. **Asynchronous Indexing**: pyright may return `None` while indexing
2. **File Synchronization**: Not required for read-only operations
3. **Diagnostics**: Published via notifications, not request/response
4. **Error Handling**: "content modified" errors are normal during rapid operations
5. **Subprocess Management**: Clean shutdown prevents zombie processes
6. **Virtual Environment**: Automatic detection simplifies Python version management

### Claude Code Integration
1. **Command Parsing**: Claude Code may treat full command strings as single executables
2. **Wrapper Scripts**: Shell scripts can work around command parsing issues
3. **MCP Tool Naming**: Tools are prefixed as `mcp__<serverName>__<toolName>`
4. **Security**: Use `--allowedTools` flag to explicitly permit MCP tools

This requirements document outlines a comprehensive MCP server that will make pyright's full feature set available through the Model Context Protocol. The implementation will prioritize reliability, completeness, and ease of use.