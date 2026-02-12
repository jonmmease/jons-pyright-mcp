"""Constants for the Pyright MCP server."""

import os

# Timeouts
REQUEST_TIMEOUT: float = float(os.environ.get("PYRIGHT_TIMEOUT", "60.0"))
SHUTDOWN_TIMEOUT: float = 5.0
DIAGNOSTIC_WAIT_TIMEOUT: float = float(os.environ.get("PYRIGHT_DIAG_TIMEOUT", "5.0"))

# Buffer sizes
READ_BUFFER_SIZE: int = 4096

# LSP Protocol
CONTENT_LENGTH_HEADER: str = "Content-Length: "
HEADER_SEPARATOR: bytes = b"\r\n\r\n"

# Pagination defaults
DEFAULT_PAGINATION_LIMIT: int = 20
DEFAULT_PAGINATION_OFFSET: int = 0


class LSPMethods:
    """LSP method name constants."""

    # Lifecycle
    INITIALIZE = "initialize"
    INITIALIZED = "initialized"
    SHUTDOWN = "shutdown"
    EXIT = "exit"

    # Text document synchronization
    DID_OPEN = "textDocument/didOpen"
    DID_CLOSE = "textDocument/didClose"
    DID_CHANGE = "textDocument/didChange"

    # Language features
    HOVER = "textDocument/hover"
    COMPLETION = "textDocument/completion"
    COMPLETION_RESOLVE = "completionItem/resolve"
    DEFINITION = "textDocument/definition"
    TYPE_DEFINITION = "textDocument/typeDefinition"
    IMPLEMENTATION = "textDocument/implementation"
    REFERENCES = "textDocument/references"
    DOCUMENT_SYMBOL = "textDocument/documentSymbol"

    # Code intelligence
    CODE_ACTION = "textDocument/codeAction"
    RENAME = "textDocument/rename"
    PREPARE_RENAME = "textDocument/prepareRename"

    # Diagnostics
    PUBLISH_DIAGNOSTICS = "textDocument/publishDiagnostics"

    # Workspace
    WORKSPACE_CONFIGURATION = "workspace/configuration"
    EXECUTE_COMMAND = "workspace/executeCommand"
