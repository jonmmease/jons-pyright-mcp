"""PyrightClientManager for multi-environment support.

This module provides the PyrightClientManager class which manages multiple
PyrightClient instances, one per discovered Python environment. It handles:
- Lazy initialization of clients
- LRU eviction when MAX_ACTIVE_CLIENTS is exceeded
- File-to-environment routing using longest-prefix matching
- Lifecycle management for all clients
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .environment import (
    EnvironmentState,
    discover_environments,
    get_environment_for_file,
    read_pyright_config,
)
from .lsp_client import PyrightClient, get_python_interpreter

logger = logging.getLogger(__name__)

# Default maximum number of active Pyright clients
DEFAULT_MAX_ACTIVE_CLIENTS = 5


def get_max_active_clients() -> int:
    """Get the maximum number of active clients from environment or default.

    Returns:
        Maximum number of concurrent Pyright clients
    """
    env_val = os.environ.get("PYRIGHT_MAX_CLIENTS")
    if env_val:
        try:
            return int(env_val)
        except ValueError:
            logger.warning(
                f"Invalid PYRIGHT_MAX_CLIENTS value: {env_val}, using default"
            )
    return DEFAULT_MAX_ACTIVE_CLIENTS


class PyrightClientManager:
    """Manages multiple PyrightClient instances for multi-environment support.

    This class provides:
    - Automatic environment discovery
    - Lazy initialization of clients on first access
    - LRU eviction when client limit is reached
    - File-to-environment routing
    """

    def __init__(
        self,
        root: Path,
        max_active_clients: int | None = None,
        notification_handler: Callable[[str, dict[str, Any]], Any] | None = None,
    ):
        """Initialize the client manager.

        Args:
            root: Root directory of the project/workspace
            max_active_clients: Maximum concurrent clients (default from env/config)
            notification_handler: Handler for diagnostics notifications
        """
        self.root = root.resolve()
        self.max_active_clients = max_active_clients or get_max_active_clients()
        self.notification_handler = notification_handler

        # Discover environments
        self.environments: dict[str, EnvironmentState] = {}
        self._discover_environments()

        # Track active clients count
        self._active_count = 0

        logger.info(
            f"PyrightClientManager initialized with {len(self.environments)} environment(s), "
            f"max_active={self.max_active_clients}"
        )

    def _discover_environments(self) -> None:
        """Discover and register all environments."""
        envs = discover_environments(self.root)
        self.environments = {env.env_id: env for env in envs}

    def rediscover_environments(self) -> None:
        """Re-discover environments (call after config changes)."""
        # Keep track of existing clients
        existing_clients: dict[str, PyrightClient] = {}
        for env_id, env in self.environments.items():
            if env.client:
                existing_clients[env_id] = env.client

        # Rediscover
        self._discover_environments()

        # Restore existing clients
        for env_id, client in existing_clients.items():
            if env_id in self.environments:
                self.environments[env_id].client = client

    @property
    def root_environment(self) -> EnvironmentState | None:
        """Get the root environment (if it exists)."""
        root_id = str(self.root)
        return self.environments.get(root_id)

    def get_environment_for_file(self, file_path: str) -> EnvironmentState | None:
        """Find the environment that should handle a given file.

        Args:
            file_path: Path to the file

        Returns:
            The matching EnvironmentState, or None if no match
        """
        return get_environment_for_file(file_path, list(self.environments.values()))

    async def get_client_for_file(self, file_path: str) -> PyrightClient:
        """Get the Pyright client for a given file, starting it if needed.

        Args:
            file_path: Path to the file

        Returns:
            The PyrightClient for the file's environment

        Raises:
            ValueError: If no environment found for the file
        """
        # Resolve to canonical path
        try:
            resolved_path = str(Path(file_path).resolve())
        except Exception:
            resolved_path = file_path

        # Find environment
        env = self.get_environment_for_file(resolved_path)

        if not env:
            # Fall back to root environment
            env = self.root_environment
            if not env:
                raise ValueError(
                    f"No environment found for file: {file_path} "
                    f"and no root environment available"
                )
            logger.debug(f"Using root environment for file: {file_path}")

        # Update access time
        env.update_access_time()

        # Start client if needed
        if not env.client:
            await self._start_client(env)

        return env.client  # type: ignore

    async def get_root_client(self) -> PyrightClient:
        """Get the root environment's client, starting it if needed.

        Returns:
            The PyrightClient for the root environment

        Raises:
            ValueError: If no root environment exists
        """
        env = self.root_environment
        if not env:
            raise ValueError("No root environment found")

        env.update_access_time()

        if not env.client:
            await self._start_client(env)

        return env.client  # type: ignore

    async def _start_client(self, env: EnvironmentState) -> None:
        """Start a client for the given environment, evicting if necessary.

        Args:
            env: The environment to start a client for
        """
        # Check if we need to evict
        if self._active_count >= self.max_active_clients:
            await self._evict_lru_client()

        # Create and start client
        logger.info(f"Starting Pyright client for environment: {env.env_id}")

        # Read config (may have changed since discovery)
        env.config = read_pyright_config(env.project_root)

        client = PyrightClient(env.project_root, env.config)

        # Set up diagnostics handler that routes to this environment
        if self.notification_handler:
            client.on_notification(
                "textDocument/publishDiagnostics",
                lambda params: self._handle_diagnostics(env, params),
            )

        await client.start()
        env.client = client
        self._active_count += 1

        logger.info(
            f"Started client for {env.env_id} "
            f"(active clients: {self._active_count}/{self.max_active_clients})"
        )

    def _handle_diagnostics(
        self, env: EnvironmentState, params: dict[str, Any]
    ) -> None:
        """Handle diagnostics notification for an environment.

        Args:
            env: The environment that received the diagnostics
            params: The notification params
        """
        uri = params.get("uri", "")
        diagnostics = params.get("diagnostics", [])
        env.diagnostics[uri] = diagnostics
        logger.debug(f"Received {len(diagnostics)} diagnostics for {uri} in {env.env_id}")

        # Forward to external handler if set
        if self.notification_handler:
            # Add environment info to params
            augmented_params = {**params, "_env_id": env.env_id}
            self.notification_handler("textDocument/publishDiagnostics", augmented_params)

    async def _evict_lru_client(self) -> None:
        """Evict the least recently used client."""
        # Find LRU environment with an active client
        lru_env: EnvironmentState | None = None
        oldest_time: datetime | None = None

        for env in self.environments.values():
            if env.client:
                if oldest_time is None or env.last_accessed < oldest_time:
                    oldest_time = env.last_accessed
                    lru_env = env

        if lru_env and lru_env.client:
            logger.info(
                f"Evicting LRU client: {lru_env.env_id} "
                f"(last accessed: {lru_env.last_accessed})"
            )
            await self._shutdown_client(lru_env)

    async def _shutdown_client(self, env: EnvironmentState) -> None:
        """Shutdown a client and clear its state.

        Args:
            env: The environment whose client to shutdown
        """
        if env.client:
            try:
                await env.client.shutdown()
            except Exception as e:
                logger.error(f"Error shutting down client for {env.env_id}: {e}")

            env.client = None
            env.clear_state()
            self._active_count -= 1

            logger.info(
                f"Shutdown client for {env.env_id} "
                f"(active clients: {self._active_count}/{self.max_active_clients})"
            )

    def get_all_active_clients(self) -> list[tuple[str, PyrightClient]]:
        """Get all currently active clients.

        Returns:
            List of (env_id, client) tuples for active clients
        """
        return [
            (env.env_id, env.client)
            for env in self.environments.values()
            if env.client
        ]

    def get_environment(self, env_id: str) -> EnvironmentState | None:
        """Get an environment by ID.

        Args:
            env_id: The environment ID (canonical path)

        Returns:
            The EnvironmentState, or None if not found
        """
        return self.environments.get(env_id)

    def get_all_environments(self) -> list[EnvironmentState]:
        """Get all discovered environments.

        Returns:
            List of all EnvironmentState objects
        """
        return list(self.environments.values())

    async def start_root_client(self) -> None:
        """Start the root environment's client (for backward compatibility)."""
        root_env = self.root_environment
        if root_env and not root_env.client:
            await self._start_client(root_env)

    async def shutdown_all(self) -> None:
        """Shutdown all active clients."""
        logger.info("Shutting down all clients...")

        shutdown_tasks = []
        for env in self.environments.values():
            if env.client:
                shutdown_tasks.append(self._shutdown_client(env))

        if shutdown_tasks:
            await asyncio.gather(*shutdown_tasks, return_exceptions=True)

        logger.info("All clients shutdown complete")

    async def restart_environment(self, env_id: str) -> None:
        """Restart a specific environment's client.

        Args:
            env_id: The environment ID to restart

        Raises:
            ValueError: If environment not found
        """
        env = self.environments.get(env_id)
        if not env:
            raise ValueError(f"Environment not found: {env_id}")

        # Store files that were open (as URIs)
        previously_opened_uris = set(env.opened_files)

        # Shutdown existing client
        if env.client:
            await self._shutdown_client(env)

        # Re-read config
        env.config = read_pyright_config(env.project_root)

        # Start new client
        await self._start_client(env)

        # Re-open previously opened files with the new client
        if env.client and previously_opened_uris:
            await self._reopen_files(env, previously_opened_uris)

        logger.info(f"Restarted environment: {env_id}")

    async def _reopen_files(
        self, env: EnvironmentState, uris: set[str]
    ) -> None:
        """Re-open files in an environment after restart.

        Args:
            env: The environment
            uris: Set of file URIs to re-open
        """
        if not env.client:
            return

        for uri in uris:
            try:
                # Convert URI to path
                file_path = uri.replace("file://", "")

                # Get mtime before reading
                mtime = os.stat(file_path).st_mtime_ns

                # Read file content
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()

                # Normalize URI for consistent tracking
                normalized_uri = self._normalize_uri(uri)

                # Reset version for this file
                version = env.doc_versions.get(normalized_uri, 0) + 1
                env.doc_versions[normalized_uri] = version

                # Send didOpen notification
                await env.client.notify(
                    "textDocument/didOpen",
                    {
                        "textDocument": {
                            "uri": uri,
                            "languageId": "python",
                            "version": version,
                            "text": content,
                        }
                    },
                )

                # Track as opened and store mtime
                env.opened_files.add(normalized_uri)
                env.file_mtimes[normalized_uri] = mtime
                logger.debug(f"Re-opened file: {uri} (mtime={mtime})")

            except Exception as e:
                logger.warning(f"Failed to re-open file {uri}: {e}")

    async def restart_all(self) -> None:
        """Restart all environments (re-discover and restart active clients)."""
        # Get list of active env IDs
        active_env_ids = [
            env.env_id for env in self.environments.values() if env.client
        ]

        # Shutdown all
        await self.shutdown_all()

        # Re-discover environments
        self.rediscover_environments()

        # Restart previously active clients
        for env_id in active_env_ids:
            env = self.environments.get(env_id)
            if env:
                await self._start_client(env)

        logger.info("Restarted all environments")

    def get_diagnostics_for_file(self, file_path: str) -> list[dict[str, Any]]:
        """Get diagnostics for a specific file.

        Args:
            file_path: Path to the file

        Returns:
            List of diagnostic entries
        """
        env = self.get_environment_for_file(file_path)
        if not env:
            return []

        # Convert to file URI
        try:
            uri = f"file://{Path(file_path).resolve()}"
        except Exception:
            uri = f"file://{file_path}"

        return env.diagnostics.get(uri, [])

    def get_all_diagnostics(self) -> dict[str, list[dict[str, Any]]]:
        """Get all diagnostics from all environments.

        Returns:
            Dict mapping URIs to diagnostic lists
        """
        all_diagnostics: dict[str, list[dict[str, Any]]] = {}

        for env in self.environments.values():
            if env.client:  # Only include diagnostics from active environments
                for uri, diags in env.diagnostics.items():
                    if uri in all_diagnostics:
                        all_diagnostics[uri].extend(diags)
                    else:
                        all_diagnostics[uri] = list(diags)

        return all_diagnostics

    def get_diagnostics_for_environment(
        self, env_id: str
    ) -> dict[str, list[dict[str, Any]]]:
        """Get all diagnostics for a specific environment.

        Args:
            env_id: The environment ID

        Returns:
            Dict mapping URIs to diagnostic lists for that environment

        Raises:
            ValueError: If environment not found
        """
        env = self.environments.get(env_id)
        if not env:
            raise ValueError(f"Environment not found: {env_id}")

        return dict(env.diagnostics)

    def get_opened_files(self, env_id: str) -> set[str]:
        """Get the set of opened files for an environment.

        Args:
            env_id: The environment ID

        Returns:
            Set of file URIs opened in that environment
        """
        env = self.environments.get(env_id)
        return env.opened_files if env else set()

    def mark_file_opened(self, file_path: str, uri: str, version: int = 1) -> None:
        """Mark a file as opened in its environment.

        Args:
            file_path: Path to the file
            uri: File URI (should be normalized via ensure_file_uri)
            version: Document version
        """
        env = self.get_environment_for_file(file_path)
        if env:
            # Normalize URI for consistent tracking
            normalized_uri = self._normalize_uri(uri)
            env.opened_files.add(normalized_uri)
            env.doc_versions[normalized_uri] = version

    def is_file_opened(self, file_path: str, uri: str) -> bool:
        """Check if a file is opened in its environment.

        Args:
            file_path: Path to the file
            uri: File URI

        Returns:
            True if the file is opened
        """
        env = self.get_environment_for_file(file_path)
        if env:
            # Normalize URI for consistent lookup
            normalized_uri = self._normalize_uri(uri)
            return normalized_uri in env.opened_files
        return False

    def _normalize_uri(self, uri: str) -> str:
        """Normalize a file URI to ensure consistent tracking.

        Args:
            uri: File URI to normalize

        Returns:
            Normalized URI with resolved path
        """
        if uri.startswith("file://"):
            file_path = uri[7:]  # Remove "file://"
            try:
                resolved = Path(file_path).resolve()
                return f"file://{resolved}"
            except Exception:
                return uri
        return uri

    def get_doc_version(self, file_path: str, uri: str) -> int:
        """Get the document version for a file.

        Args:
            file_path: Path to the file
            uri: File URI

        Returns:
            Document version, or 1 if not tracked
        """
        env = self.get_environment_for_file(file_path)
        if env:
            normalized_uri = self._normalize_uri(uri)
            return env.doc_versions.get(normalized_uri, 1)
        return 1

    def increment_doc_version(self, file_path: str, uri: str) -> int:
        """Increment and return the document version for a file.

        Args:
            file_path: Path to the file
            uri: File URI

        Returns:
            New document version
        """
        env = self.get_environment_for_file(file_path)
        if env:
            normalized_uri = self._normalize_uri(uri)
            current = env.doc_versions.get(normalized_uri, 0)
            env.doc_versions[normalized_uri] = current + 1
            return current + 1
        return 1

    def get_file_mtime(self, file_path: str, uri: str) -> int | None:
        """Get the cached modification time for a file.

        Args:
            file_path: Path to the file
            uri: File URI

        Returns:
            Cached mtime in nanoseconds, or None if not tracked
        """
        env = self.get_environment_for_file(file_path)
        if env:
            normalized_uri = self._normalize_uri(uri)
            return env.file_mtimes.get(normalized_uri)
        return None

    def set_file_mtime(self, file_path: str, uri: str, mtime: int) -> None:
        """Set the cached modification time for a file.

        Args:
            file_path: Path to the file
            uri: File URI
            mtime: Modification time in nanoseconds (st_mtime_ns)
        """
        env = self.get_environment_for_file(file_path)
        if env:
            normalized_uri = self._normalize_uri(uri)
            env.file_mtimes[normalized_uri] = mtime

    def clear_file_mtime(self, file_path: str, uri: str) -> None:
        """Clear the cached modification time for a file.

        Args:
            file_path: Path to the file
            uri: File URI
        """
        env = self.get_environment_for_file(file_path)
        if env:
            normalized_uri = self._normalize_uri(uri)
            env.file_mtimes.pop(normalized_uri, None)

    def is_file_stale(self, file_path: str, uri: str) -> bool:
        """Check if a file's cached content is stale.

        Compares the current file modification time to the cached value.

        Args:
            file_path: Path to the file
            uri: File URI

        Returns:
            True if the file has been modified since it was cached,
            False if unchanged or if the file doesn't exist
        """
        cached_mtime = self.get_file_mtime(file_path, uri)
        if cached_mtime is None:
            # Not tracked yet, not considered stale (will be opened fresh)
            return False

        try:
            current_mtime = os.stat(file_path).st_mtime_ns
            return current_mtime != cached_mtime
        except FileNotFoundError:
            # File was deleted, handle gracefully
            return False
        except OSError as e:
            logger.warning(f"Error checking mtime for {file_path}: {e}")
            return False

    def update_file_state(
        self, file_path: str, uri: str, mtime: int | None = None
    ) -> tuple[int, int | None]:
        """Atomically update version and mtime for a file.

        Args:
            file_path: Path to the file
            uri: File URI
            mtime: Modification time in nanoseconds, or None to fetch current

        Returns:
            Tuple of (new_version, mtime) where mtime may be None on error
        """
        env = self.get_environment_for_file(file_path)
        if not env:
            return (1, None)

        normalized_uri = self._normalize_uri(uri)

        # Increment version
        current_version = env.doc_versions.get(normalized_uri, 0)
        new_version = current_version + 1
        env.doc_versions[normalized_uri] = new_version

        # Update mtime
        if mtime is None:
            try:
                mtime = os.stat(file_path).st_mtime_ns
            except OSError as e:
                logger.warning(f"Error getting mtime for {file_path}: {e}")
                mtime = None

        if mtime is not None:
            env.file_mtimes[normalized_uri] = mtime

        return (new_version, mtime)
