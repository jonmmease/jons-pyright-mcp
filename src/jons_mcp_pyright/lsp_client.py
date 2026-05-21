"""LSP client for Pyright language server."""

import asyncio
import contextlib
import json
import logging
import os
import queue
import shlex
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from .constants import REQUEST_TIMEOUT
from .exceptions import LSPRequestError, PyrightNotFoundError

logger = logging.getLogger(__name__)


def read_pyright_config(project_root: Path) -> dict[str, Any]:
    """Read pyrightconfig.json if it exists."""
    config_path = project_root / "pyrightconfig.json"
    if config_path.exists():
        try:
            with open(config_path) as f:
                config = json.load(f)
                logger.info(f"Loaded pyrightconfig.json: {config}")
                return cast(dict[str, Any], config)
        except Exception as e:
            logger.warning(f"Failed to read pyrightconfig.json: {e}")
    return {}


def get_python_interpreter(project_root: Path, config: dict[str, Any]) -> str | None:
    """Determine the Python interpreter path from config or environment."""
    # First check if pythonPath is explicitly set in config
    if "pythonPath" in config:
        python_path = str(config["pythonPath"])
        if not Path(python_path).is_absolute():
            python_path = str(project_root / python_path)
        if Path(python_path).exists():
            logger.info(f"Using Python interpreter from config: {python_path}")
            return python_path
        else:
            logger.warning(f"Python interpreter not found at: {python_path}")

    # Check for venv configuration
    if "venv" in config:
        venv_path = str(config["venv"])
        if not Path(venv_path).is_absolute():
            # Handle venvPath + venv combination
            if "venvPath" in config:
                venv_base = Path(config["venvPath"])
                if not venv_base.is_absolute():
                    venv_base = project_root / venv_base
                venv_path = str(venv_base / venv_path)
            else:
                venv_path = str(project_root / venv_path)

        # Try common Python locations in the venv
        for python_exe in [
            "bin/python",
            "bin/python3",
            "Scripts/python.exe",
            "Scripts/python3.exe",
        ]:
            python_path_candidate = Path(venv_path) / python_exe
            if python_path_candidate.exists():
                logger.info(
                    f"Using Python interpreter from venv: {python_path_candidate}"
                )
                return str(python_path_candidate)

        logger.warning(f"Could not find Python interpreter in venv: {venv_path}")

    # Check for common virtual environment locations
    for venv_dir in [".venv", "venv", ".pixi/envs/default", ".pixi/envs/dev"]:
        common_venv_path = project_root / venv_dir
        if common_venv_path.exists():
            for python_exe in [
                "bin/python",
                "bin/python3",
                "Scripts/python.exe",
                "Scripts/python3.exe",
            ]:
                python_path_candidate = common_venv_path / python_exe
                if python_path_candidate.exists():
                    logger.info(
                        f"Found Python interpreter in {venv_dir}: {python_path_candidate}"
                    )
                    return str(python_path_candidate)

    return None


class PyrightClient:
    """Thread-based LSP client for pyright."""

    def __init__(
        self,
        project_root: Path,
        config: dict[str, Any] | None = None,
        pyright_path: str | None = None,
    ):
        self.project_root = project_root
        self.config = config or {}
        self.pyright_path = pyright_path or self._find_pyright()
        self.process: subprocess.Popen | None = None
        self.request_id = 0
        self.pending_requests: dict[int, asyncio.Future] = {}
        self.notification_handlers: dict[str, Callable[..., Any]] = {}
        self._initialized = False
        self._shutting_down = False
        self.request_timeout = REQUEST_TIMEOUT

        # Thread-based I/O
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._writer_lock = threading.Lock()
        self._message_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._message_task: asyncio.Task[None] | None = None

    def _find_pyright(self) -> str:
        """Find pyright executable."""
        # Check environment variable first
        if env_path := os.environ.get("PYRIGHT_PATH"):
            return env_path

        # Try to find pyright-langserver from the pyright package
        try:
            import pyright  # noqa: F401

            # The pyright package includes node and the langserver
            # We can run it directly via the pyright.langserver module
            return sys.executable + " -m pyright.langserver --stdio"
        except ImportError:
            pass

        # Check if pyright-langserver is on PATH
        if path := shutil.which("pyright-langserver"):
            return path

        # Check if pyright is on PATH (CLI version)
        if path := shutil.which("pyright"):
            # Try to use it with --langserver flag
            return f"{path} --langserver"

        # Last resort: try node-based installation
        if not shutil.which("npm"):
            raise PyrightNotFoundError(
                "pyright not found. Install it with: pip install pyright"
            )

        npm_prefix = subprocess.run(
            ["npm", "prefix", "-g"], capture_output=True, text=True, check=False
        ).stdout.strip()

        if npm_prefix:
            node_path = (
                Path(npm_prefix)
                / "lib"
                / "node_modules"
                / "pyright"
                / "langserver.index.js"
            )
            if node_path.exists():
                return f"node {node_path} --stdio"

        raise PyrightNotFoundError(
            "pyright not found. Install it with: pip install pyright"
        )

    def is_initialized(self) -> bool:
        """Check if the client is initialized."""
        return self._initialized

    async def start(self) -> None:
        """Start the pyright process and initialize communication."""
        if self.process:
            raise RuntimeError("Already started")

        # Store event loop for thread-to-async communication
        self._loop = asyncio.get_running_loop()

        logger.info(f"Starting pyright for project: {self.project_root}")
        logger.info(f"Using pyright command: {self.pyright_path}")

        # Start process with unbuffered output
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        try:
            self.process = subprocess.Popen(
                shlex.split(self.pyright_path),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(self.project_root),
                env=env,
                bufsize=0,  # Unbuffered
            )
        except Exception as e:
            raise RuntimeError(f"Failed to start pyright: {e}") from e

        try:
            # Start reader threads
            self._reader_thread = threading.Thread(
                target=self._reader_loop, daemon=True
            )
            self._stderr_thread = threading.Thread(
                target=self._stderr_loop, daemon=True
            )
            self._reader_thread.start()
            self._stderr_thread.start()

            # Process messages from queue
            self._message_task = asyncio.create_task(self._process_messages())

            # Initialize LSP connection
            await self._initialize()
        except Exception:
            await self._cleanup_started_process()
            raise

    def _reader_loop(self) -> None:
        """Read messages from stdout in a thread."""
        buffer = b""
        logger.debug("Reader thread started")

        while self.process and not self._shutting_down:
            try:
                stdout = self.process.stdout
                if not stdout:
                    break
                # Read one byte at a time to avoid blocking
                byte = stdout.read(1)
                if not byte:
                    logger.debug("Reader thread: EOF")
                    break

                buffer += byte

                # Check for complete message
                header_end = buffer.find(b"\r\n\r\n")
                if header_end == -1:
                    continue

                # Parse header
                header = buffer[:header_end].decode("utf-8")
                content_length = None
                for line in header.split("\r\n"):
                    if line.startswith("Content-Length: "):
                        content_length = int(line[16:])
                        break

                if content_length is None:
                    buffer = buffer[header_end + 4 :]
                    continue

                # Read content
                content_start = header_end + 4
                while len(buffer) < content_start + content_length:
                    chunk = stdout.read(
                        min(4096, content_start + content_length - len(buffer))
                    )
                    if not chunk:
                        return
                    buffer += chunk

                # Extract message
                content = buffer[content_start : content_start + content_length]
                buffer = buffer[content_start + content_length :]

                try:
                    message = json.loads(content.decode("utf-8"))
                    # Put message in queue for async processing
                    method_or_id = message.get(
                        "method", f"response id={message.get('id')}"
                    )
                    logger.debug(f"Reader thread: queuing message {method_or_id}")
                    self._message_queue.put(message)
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse JSON: {e}")

            except Exception as e:
                if not self._shutting_down:
                    logger.error(f"Error in reader thread: {e}")
                break

    def _stderr_loop(self) -> None:
        """Read stderr in a thread."""
        while self.process and self.process.stderr and not self._shutting_down:
            try:
                line = self.process.stderr.readline()
                if line:
                    decoded = line.decode().strip()
                    if "error" in decoded.lower() or "panic" in decoded.lower():
                        logger.error(f"pyright stderr: {decoded}")
                    else:
                        logger.info(f"pyright stderr: {decoded}")
                else:
                    break
            except Exception:
                break

    async def _process_messages(self) -> None:
        """Process messages from the queue."""
        logger.debug("Message processor started")
        while not self._shutting_down:
            try:
                # Use a simple approach - check if queue has items
                if not self._message_queue.empty():
                    message = self._message_queue.get_nowait()
                    logger.debug(
                        f"Processing message from queue: {message.get('method', 'response')}"
                    )
                    await self._handle_message(message)
                else:
                    # Small sleep to avoid busy waiting
                    await asyncio.sleep(0.01)
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Error processing message: {e}")

    async def _handle_message(self, message: dict[str, Any]) -> None:
        """Handle incoming LSP message."""
        logger.debug(f"Received: {message}")

        if "id" in message and "method" in message:
            # Request from server to client
            request_id = message["id"]
            method = message["method"]
            params = message.get("params", {})

            logger.debug(f"Server request: {method} (id={request_id})")

            # Handle workspace/configuration request
            if method == "workspace/configuration":
                # Build response based on requested configuration sections
                result = []
                items = params.get("items", [])

                for item in items:
                    section = item.get("section", "")
                    config_response: dict[str, Any] = {}

                    if section == "python":
                        # Provide Python interpreter path if we have it
                        python_path = get_python_interpreter(
                            self.project_root, self.config
                        )
                        if python_path:
                            config_response["defaultInterpreterPath"] = python_path
                            config_response["pythonPath"] = python_path
                    elif section == "python.analysis":
                        # Provide analysis settings
                        if "extraPaths" in self.config:
                            extra_paths = cast(list[str], self.config["extraPaths"])
                            # Convert relative paths to absolute
                            abs_paths = []
                            for path in extra_paths:
                                if not Path(path).is_absolute():
                                    abs_paths.append(str(self.project_root / path))
                                else:
                                    abs_paths.append(path)
                            config_response["extraPaths"] = abs_paths
                        if "typeCheckingMode" in self.config:
                            config_response["typeCheckingMode"] = self.config[
                                "typeCheckingMode"
                            ]
                    elif section == "pyright":
                        # Return the full pyright config if requested
                        config_response = self.config.copy()

                    result.append(config_response)

                await self._send_message(
                    {"jsonrpc": "2.0", "id": request_id, "result": result}
                )
            else:
                # Send error response for unsupported methods
                await self._send_message(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": -32601,
                            "message": f"Method not supported: {method}",
                        },
                    }
                )
        elif "id" in message:
            # Response to our request
            request_id = message["id"]
            future = self.pending_requests.pop(request_id, None)

            if future and not future.done():
                if "error" in message:
                    error = message["error"]
                    future.set_exception(
                        LSPRequestError(
                            f"{error.get('message', 'Unknown error')}",
                            code=error.get("code"),
                        )
                    )
                else:
                    future.set_result(message.get("result"))
        else:
            # Server notification
            method = message.get("method", "")
            params = message.get("params", {})

            handler = self.notification_handlers.get(method)
            if handler:
                try:
                    # Check if handler is async
                    if asyncio.iscoroutinefunction(handler):
                        await handler(params)
                    else:
                        handler(params)
                except Exception as e:
                    logger.error(f"Error in notification handler for {method}: {e}")
            else:
                logger.debug(f"Unhandled notification: {method}")

    async def _initialize(self) -> None:
        """Send LSP initialize request."""
        logger.debug("Sending initialize request...")

        # Build initialization options from config
        init_options: dict[str, Any] = {"python": {"analysis": {}}}

        # Add analysis settings from config
        if "typeCheckingMode" in self.config:
            init_options["python"]["analysis"]["typeCheckingMode"] = self.config[
                "typeCheckingMode"
            ]
        else:
            init_options["python"]["analysis"]["typeCheckingMode"] = "basic"

        init_options["python"]["analysis"]["autoSearchPaths"] = True
        init_options["python"]["analysis"]["useLibraryCodeForTypes"] = True
        init_options["python"]["analysis"]["diagnosticMode"] = "workspace"

        # Add extraPaths if specified
        if "extraPaths" in self.config:
            extra_paths = cast(list[str], self.config["extraPaths"])
            # Convert relative paths to absolute
            abs_paths = []
            for path in extra_paths:
                if not Path(path).is_absolute():
                    abs_paths.append(str(self.project_root / path))
                else:
                    abs_paths.append(path)
            init_options["python"]["analysis"]["extraPaths"] = abs_paths

        # Try to determine Python interpreter
        python_path = get_python_interpreter(self.project_root, self.config)
        if python_path:
            init_options["python"]["pythonPath"] = python_path
            logger.info(f"Using Python interpreter: {python_path}")

        # Add other config options
        if "pythonVersion" in self.config:
            init_options["python"]["pythonVersion"] = self.config["pythonVersion"]
        if "pythonPlatform" in self.config:
            init_options["python"]["pythonPlatform"] = self.config["pythonPlatform"]

        await self.request(
            "initialize",
            {
                "processId": os.getpid(),
                "clientInfo": {"name": "pyright-mcp", "version": "0.1.0"},
                "rootUri": f"file://{self.project_root.absolute()}",
                "capabilities": {
                    "textDocument": {
                        "hover": {"contentFormat": ["plaintext", "markdown"]},
                        "completion": {
                            "completionItem": {
                                "snippetSupport": True,
                                "resolveSupport": {
                                    "properties": [
                                        "documentation",
                                        "detail",
                                        "additionalTextEdits",
                                    ]
                                },
                            }
                        },
                        "signatureHelp": {
                            "signatureInformation": {
                                "documentationFormat": ["plaintext", "markdown"],
                                "parameterInformation": {"labelOffsetSupport": True},
                            }
                        },
                        "definition": {"linkSupport": True},
                        "typeDefinition": {"linkSupport": True},
                        "implementation": {"linkSupport": True},
                        "references": {},
                        "documentHighlight": {},
                        "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
                        "formatting": {},
                        "rangeFormatting": {},
                        "rename": {"prepareSupport": True},
                        "codeAction": {
                            "codeActionLiteralSupport": {
                                "codeActionKind": {
                                    "valueSet": [
                                        "quickfix",
                                        "refactor",
                                        "refactor.extract",
                                        "refactor.inline",
                                        "refactor.rewrite",
                                        "source",
                                        "source.organizeImports",
                                    ]
                                }
                            },
                            "resolveSupport": {"properties": ["edit"]},
                        },
                        "publishDiagnostics": {"relatedInformation": True},
                        "callHierarchy": {},
                        "semanticTokens": {
                            "requests": {"full": True, "range": True},
                            "tokenTypes": [],
                            "tokenModifiers": [],
                            "formats": ["relative"],
                        },
                    },
                    "workspace": {
                        "applyEdit": True,
                        "symbol": {},
                        "executeCommand": {},
                        "workspaceFolders": True,
                        "configuration": True,
                    },
                },
                "initializationOptions": init_options,
            },
        )

        logger.info("pyright initialized successfully")

        # Send initialized notification
        await self.notify("initialized", {})

        self._initialized = True

    async def request(self, method: str, params: Any = None) -> Any:
        """Send request and wait for response."""
        if not self.process:
            raise RuntimeError("Not started")

        request_id = self.request_id
        self.request_id += 1

        logger.debug(f"Creating request {method} with id {request_id}")

        # Create future for response
        future: asyncio.Future = asyncio.Future()
        self.pending_requests[request_id] = future

        # Send request
        await self._send_message(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            }
        )

        # Wait for response with timeout
        try:
            logger.debug(f"Waiting for response to {method} (id={request_id})")
            result = await asyncio.wait_for(future, timeout=self.request_timeout)
            logger.debug(f"Got response for {method} (id={request_id}): {result}")
            return result
        except asyncio.TimeoutError:
            self.pending_requests.pop(request_id, None)
            logger.error(
                f"Request {method} (id={request_id}) timed out. "
                f"Pending requests: {list(self.pending_requests.keys())}"
            )
            raise LSPRequestError(
                f"Request {method} timed out after {self.request_timeout}s",
                is_retryable=True,
            ) from None

    async def notify(self, method: str, params: Any = None) -> None:
        """Send notification (no response expected)."""
        await self._send_message(
            {"jsonrpc": "2.0", "method": method, "params": params or {}}
        )

    async def _send_message(self, message: dict[str, Any]) -> None:
        """Send message to pyright."""
        if not self.process or not self.process.stdin:
            raise RuntimeError("Process not running")

        content = json.dumps(message).encode("utf-8")
        header = f"Content-Length: {len(content)}\r\n\r\n".encode()

        # Thread-safe write
        with self._writer_lock:
            self.process.stdin.write(header + content)
            self.process.stdin.flush()

        logger.debug(f"Sent: {message}")

    def on_notification(self, method: str, handler: Callable[..., Any]) -> None:
        """Register notification handler."""
        self.notification_handlers[method] = handler

    async def _cleanup_started_process(self) -> None:
        """Clean up a process that failed during startup."""
        self._shutting_down = True
        self._fail_pending_requests("pyright startup failed")
        await self._cancel_message_task()
        self._terminate_process()
        self._join_threads()
        self.process = None
        self._initialized = False
        self._shutting_down = False

    def _fail_pending_requests(self, message: str) -> None:
        """Fail all pending request futures."""
        for future in self.pending_requests.values():
            if not future.done():
                future.set_exception(LSPRequestError(message, is_retryable=True))
        self.pending_requests.clear()

    async def _cancel_message_task(self) -> None:
        """Cancel the async message processing task if it exists."""
        if self._message_task and not self._message_task.done():
            self._message_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._message_task
        self._message_task = None

    def _terminate_process(self) -> None:
        """Terminate or kill the subprocess if still running."""
        if not self.process:
            return
        if self.process.poll() is None:
            logger.debug("Process still running, terminating...")
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.debug("Process didn't terminate, killing...")
                self.process.kill()
                self.process.wait()

    def _join_threads(self) -> None:
        """Join background reader threads briefly during shutdown."""
        for thread in (self._reader_thread, self._stderr_thread):
            if thread and thread.is_alive():
                thread.join(timeout=1)
        self._reader_thread = None
        self._stderr_thread = None

    async def shutdown(self) -> None:
        """Shutdown the language server."""
        if not self.process:
            return

        logger.debug("Starting shutdown...")

        try:
            # Send shutdown request first
            shutdown_result = await self.request("shutdown", {})
            logger.debug(f"Shutdown response: {shutdown_result}")

            # Then mark as shutting down to stop message processing
            self._shutting_down = True

            # Send exit notification
            await self.notify("exit", {})

            # Give it a moment to exit cleanly
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
            self._shutting_down = True

        self._fail_pending_requests("pyright server shut down")
        await self._cancel_message_task()
        self._terminate_process()
        self._join_threads()

        self.process = None
        self._initialized = False
        logger.debug("Shutdown complete")
