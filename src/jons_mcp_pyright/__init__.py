"""MCP server for Pyright LSP features."""

from .constants import (
    DEFAULT_PAGINATION_LIMIT,
    DEFAULT_PAGINATION_OFFSET,
    LSPMethods,
    READ_BUFFER_SIZE,
    REQUEST_TIMEOUT,
    SHUTDOWN_TIMEOUT,
)
from .environment import (
    EnvironmentState,
    discover_environments,
    get_environment_for_file,
)
from .exceptions import (
    LSPRequestError,
    Position,
    PyrightNotFoundError,
    PyrightNotInitializedError,
    Range,
)
from .lsp_client import PyrightClient
from .manager import PyrightClientManager
from .server import (
    ensure_pyright,
    ensure_pyright_indexed,
    get_manager,
    main,
    manager,
    mcp,
)
from .tools import (
    definition,
    diagnostics,
    document_symbols,
    implementation,
    references,
    rename,
    restart_server,
    symbol_info,
    type_definition,
    type_info,
    workspace_symbols,
)
from .utils import apply_pagination, ensure_file_uri

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "DEFAULT_PAGINATION_LIMIT",
    "DEFAULT_PAGINATION_OFFSET",
    "LSPMethods",
    "READ_BUFFER_SIZE",
    "REQUEST_TIMEOUT",
    "SHUTDOWN_TIMEOUT",
    "LSPRequestError",
    "Position",
    "PyrightNotFoundError",
    "PyrightNotInitializedError",
    "Range",
    "PyrightClient",
    "PyrightClientManager",
    "EnvironmentState",
    "discover_environments",
    "get_environment_for_file",
    "ensure_pyright",
    "ensure_pyright_indexed",
    "get_manager",
    "main",
    "manager",
    "mcp",
    "apply_pagination",
    "ensure_file_uri",
    "symbol_info",
    "type_info",
    "definition",
    "type_definition",
    "implementation",
    "references",
    "document_symbols",
    "workspace_symbols",
    "diagnostics",
    "rename",
    "restart_server",
]
