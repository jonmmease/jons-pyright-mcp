"""Tests for PyrightClientManager."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jons_mcp_pyright.manager import (
    DEFAULT_MAX_ACTIVE_CLIENTS,
    PyrightClientManager,
    get_max_active_clients,
)


class TestGetMaxActiveClients:
    """Tests for get_max_active_clients function."""

    def test_default_value(self, monkeypatch):
        """Should return default when env var not set."""
        monkeypatch.delenv("PYRIGHT_MAX_CLIENTS", raising=False)
        assert get_max_active_clients() == DEFAULT_MAX_ACTIVE_CLIENTS

    def test_custom_value_from_env(self, monkeypatch):
        """Should return value from environment variable."""
        monkeypatch.setenv("PYRIGHT_MAX_CLIENTS", "10")
        assert get_max_active_clients() == 10

    def test_invalid_value_returns_default(self, monkeypatch):
        """Should return default for invalid env var value."""
        monkeypatch.setenv("PYRIGHT_MAX_CLIENTS", "not-a-number")
        assert get_max_active_clients() == DEFAULT_MAX_ACTIVE_CLIENTS


class TestPyrightClientManagerInit:
    """Tests for PyrightClientManager initialization."""

    def test_discovers_environments(self, tmp_path):
        """Should discover environments on init."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test'")

        manager = PyrightClientManager(tmp_path)

        assert len(manager.environments) >= 1
        assert str(tmp_path) in manager.environments

    def test_custom_max_active_clients(self, tmp_path):
        """Should use custom max_active_clients."""
        (tmp_path / "pyproject.toml").write_text("")

        manager = PyrightClientManager(tmp_path, max_active_clients=3)

        assert manager.max_active_clients == 3

    def test_root_environment_property(self, tmp_path):
        """Should provide access to root environment."""
        (tmp_path / "pyproject.toml").write_text("")

        manager = PyrightClientManager(tmp_path)

        assert manager.root_environment is not None
        assert manager.root_environment.project_root == tmp_path


class TestGetEnvironmentForFile:
    """Tests for get_environment_for_file method."""

    def test_finds_matching_environment(self, tmp_path):
        """Should find environment for file in project."""
        (tmp_path / "pyproject.toml").write_text("")

        manager = PyrightClientManager(tmp_path)

        test_file = tmp_path / "src" / "main.py"
        env = manager.get_environment_for_file(str(test_file))

        assert env is not None
        assert env.project_root == tmp_path

    def test_finds_most_specific_environment(self, tmp_path):
        """Should find most specific environment for nested projects."""
        (tmp_path / "pyproject.toml").write_text("")

        pkg_a = tmp_path / "packages" / "pkg-a"
        pkg_a.mkdir(parents=True)
        (pkg_a / "pyproject.toml").write_text("")

        manager = PyrightClientManager(tmp_path)

        # File in pkg-a
        file_in_pkg = pkg_a / "src" / "main.py"
        env = manager.get_environment_for_file(str(file_in_pkg))

        assert env is not None
        assert env.project_root == pkg_a

    def test_returns_none_for_external_file(self, tmp_path):
        """Should return None for file outside project."""
        (tmp_path / "pyproject.toml").write_text("")

        manager = PyrightClientManager(tmp_path)

        external_file = tmp_path.parent / "other_project" / "main.py"
        env = manager.get_environment_for_file(str(external_file))

        assert env is None


class TestGetClientForFile:
    """Tests for get_client_for_file method."""

    @pytest.mark.asyncio
    async def test_creates_client_on_first_access(self, tmp_path):
        """Should create and start client on first access."""
        (tmp_path / "pyproject.toml").write_text("")

        manager = PyrightClientManager(tmp_path)

        # Mock the client creation
        with patch.object(
            manager, "_start_client", new_callable=AsyncMock
        ) as mock_start:
            # Manually set a mock client after _start_client is called
            async def set_mock_client(env):
                env.client = MagicMock()

            mock_start.side_effect = set_mock_client

            test_file = tmp_path / "main.py"
            client = await manager.get_client_for_file(str(test_file))

            assert mock_start.called
            assert client is not None

    @pytest.mark.asyncio
    async def test_reuses_existing_client(self, tmp_path):
        """Should reuse existing client for same environment."""
        (tmp_path / "pyproject.toml").write_text("")

        manager = PyrightClientManager(tmp_path)

        # Pre-set a mock client
        root_env = manager.root_environment
        mock_client = MagicMock()
        root_env.client = mock_client

        test_file = tmp_path / "main.py"
        client = await manager.get_client_for_file(str(test_file))

        assert client is mock_client

    @pytest.mark.asyncio
    async def test_rejects_external_files(self, tmp_path):
        """Should reject files outside the configured project root."""
        (tmp_path / "pyproject.toml").write_text("")

        manager = PyrightClientManager(tmp_path)
        root_env = manager.root_environment
        mock_client = MagicMock()
        root_env.client = mock_client

        # External file that doesn't match any env
        external_file = "/tmp/random_file.py"

        with pytest.raises(ValueError, match="outside the configured project root"):
            await manager.get_client_for_file(external_file)


class TestLRUEviction:
    """Tests for LRU eviction behavior."""

    @pytest.mark.asyncio
    async def test_evicts_lru_when_at_limit(self, tmp_path):
        """Should evict least recently used client when at limit."""
        (tmp_path / "pyproject.toml").write_text("")

        pkg_a = tmp_path / "packages" / "pkg-a"
        pkg_a.mkdir(parents=True)
        (pkg_a / "pyproject.toml").write_text("")

        pkg_b = tmp_path / "packages" / "pkg-b"
        pkg_b.mkdir(parents=True)
        (pkg_b / "pyproject.toml").write_text("")

        # Create manager with limit of 2
        manager = PyrightClientManager(tmp_path, max_active_clients=2)

        # Set up mock clients
        root_env = manager.environments[str(tmp_path)]
        env_a = manager.environments[str(pkg_a)]
        assert str(pkg_b) in manager.environments

        # Simulate clients with different access times
        root_mock_client = AsyncMock()
        root_env.client = root_mock_client
        root_env.last_accessed = datetime.now() - timedelta(hours=2)

        env_a_mock_client = AsyncMock()
        env_a.client = env_a_mock_client
        env_a.last_accessed = datetime.now() - timedelta(hours=1)

        manager._active_count = 2

        # Starting client for env_b should evict root (oldest)
        with patch.object(
            PyrightClientManager, "_start_client", new_callable=AsyncMock
        ):
            await manager._evict_lru_client()

        # Root should have been shutdown (it was oldest)
        # Note: root_env.client is now None after shutdown, but we captured the mock
        root_mock_client.shutdown.assert_called_once()


class TestGetAllActiveClients:
    """Tests for get_all_active_clients method."""

    def test_returns_active_clients(self, tmp_path):
        """Should return list of active clients."""
        (tmp_path / "pyproject.toml").write_text("")

        manager = PyrightClientManager(tmp_path)
        root_env = manager.root_environment
        mock_client = MagicMock()
        root_env.client = mock_client

        active = manager.get_all_active_clients()

        assert len(active) == 1
        assert active[0][0] == str(tmp_path)
        assert active[0][1] is mock_client

    def test_excludes_inactive_environments(self, tmp_path):
        """Should not include environments without clients."""
        (tmp_path / "pyproject.toml").write_text("")

        pkg_a = tmp_path / "packages" / "pkg-a"
        pkg_a.mkdir(parents=True)
        (pkg_a / "pyproject.toml").write_text("")

        manager = PyrightClientManager(tmp_path)

        # Only root has a client
        manager.root_environment.client = MagicMock()

        active = manager.get_all_active_clients()

        assert len(active) == 1


class TestShutdownAll:
    """Tests for shutdown_all method."""

    @pytest.mark.asyncio
    async def test_shuts_down_all_clients(self, tmp_path):
        """Should shutdown all active clients."""
        (tmp_path / "pyproject.toml").write_text("")

        manager = PyrightClientManager(tmp_path)

        # Set up mock client
        mock_client = AsyncMock()
        manager.root_environment.client = mock_client
        manager._active_count = 1

        await manager.shutdown_all()

        mock_client.shutdown.assert_called_once()
        assert manager._active_count == 0
        assert manager.root_environment.client is None


class TestDiagnosticsTracking:
    """Tests for diagnostics tracking methods."""

    def test_get_diagnostics_for_file(self, tmp_path):
        """Should get diagnostics for a specific file."""
        (tmp_path / "pyproject.toml").write_text("")

        manager = PyrightClientManager(tmp_path)
        env = manager.root_environment

        # Add mock diagnostics
        file_uri = f"file://{tmp_path}/main.py"
        env.diagnostics[file_uri] = [{"message": "error 1"}]

        diags = manager.get_diagnostics_for_file(str(tmp_path / "main.py"))

        assert len(diags) == 1
        assert diags[0]["message"] == "error 1"

    def test_get_all_diagnostics(self, tmp_path):
        """Should aggregate diagnostics from all environments."""
        (tmp_path / "pyproject.toml").write_text("")

        pkg_a = tmp_path / "packages" / "pkg-a"
        pkg_a.mkdir(parents=True)
        (pkg_a / "pyproject.toml").write_text("")

        manager = PyrightClientManager(tmp_path)

        # Set up mock clients and diagnostics
        root_env = manager.root_environment
        root_env.client = MagicMock()  # Mark as active
        root_env.diagnostics["file:///root.py"] = [{"message": "root error"}]

        env_a = manager.environments[str(pkg_a)]
        env_a.client = MagicMock()  # Mark as active
        env_a.diagnostics["file:///pkg.py"] = [{"message": "pkg error"}]

        all_diags = manager.get_all_diagnostics()

        assert "file:///root.py" in all_diags
        assert "file:///pkg.py" in all_diags


class TestFileTracking:
    """Tests for file tracking methods."""

    def test_mark_file_opened(self, tmp_path):
        """Should track opened files."""
        (tmp_path / "pyproject.toml").write_text("")

        manager = PyrightClientManager(tmp_path)

        file_path = str(tmp_path / "main.py")
        uri = f"file://{tmp_path}/main.py"

        manager.mark_file_opened(file_path, uri, version=1)

        assert manager.is_file_opened(file_path, uri)
        assert manager.get_doc_version(file_path, uri) == 1

    def test_increment_doc_version(self, tmp_path):
        """Should increment document version."""
        (tmp_path / "pyproject.toml").write_text("")

        manager = PyrightClientManager(tmp_path)

        file_path = str(tmp_path / "main.py")
        uri = f"file://{tmp_path}/main.py"

        manager.mark_file_opened(file_path, uri, version=1)

        new_version = manager.increment_doc_version(file_path, uri)

        assert new_version == 2
        assert manager.get_doc_version(file_path, uri) == 2


class TestRestartEnvironment:
    """Tests for restart_environment method."""

    @pytest.mark.asyncio
    async def test_restarts_specific_environment(self, tmp_path):
        """Should restart a specific environment and re-open files."""
        (tmp_path / "pyproject.toml").write_text("")

        # Create a real test file that can be re-opened
        test_file = tmp_path / "test.py"
        test_file.write_text("# test file content")
        test_file_uri = f"file://{test_file.resolve()}"

        manager = PyrightClientManager(tmp_path)
        env = manager.root_environment

        # Set up mock client
        old_client = AsyncMock()
        env.client = old_client
        env.opened_files = {test_file_uri}
        manager._active_count = 1

        # Mock _start_client to set a new mock client
        new_client = MagicMock()
        new_client.notify = AsyncMock()

        async def mock_start(e):
            e.client = new_client
            manager._active_count += 1

        with patch.object(manager, "_start_client", side_effect=mock_start):
            await manager.restart_environment(str(tmp_path))

        # Old client should be shutdown
        old_client.shutdown.assert_called_once()

        # File should be re-opened with the new client
        assert test_file_uri in env.opened_files

        # New client should have received didOpen notification
        new_client.notify.assert_called_once()
        call_args = new_client.notify.call_args
        assert call_args[0][0] == "textDocument/didOpen"
        assert call_args[0][1]["textDocument"]["uri"] == test_file_uri

    @pytest.mark.asyncio
    async def test_raises_for_unknown_environment(self, tmp_path):
        """Should raise ValueError for unknown environment."""
        (tmp_path / "pyproject.toml").write_text("")

        manager = PyrightClientManager(tmp_path)

        with pytest.raises(ValueError, match="Environment not found"):
            await manager.restart_environment("/nonexistent/path")
