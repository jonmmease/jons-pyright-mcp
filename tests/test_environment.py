"""Tests for environment discovery and management."""

import os
from pathlib import Path
from datetime import datetime
import pytest

from jons_mcp_pyright.environment import (
    EnvironmentState,
    discover_project_roots,
    resolve_venv_for_root,
    resolve_pixi_env,
    discover_environments,
    get_environment_for_file,
    get_venv_patterns,
    read_pyright_config,
    DEFAULT_VENV_PATTERNS,
    PIXI_ENV_VAR,
    DEFAULT_PIXI_ENV,
)


class TestGetVenvPatterns:
    """Tests for get_venv_patterns function."""

    def test_default_patterns(self, monkeypatch):
        """Should return default patterns when env var not set."""
        monkeypatch.delenv("PYRIGHT_VENV_PATTERNS", raising=False)
        patterns = get_venv_patterns()
        assert patterns == DEFAULT_VENV_PATTERNS

    def test_custom_patterns_from_env(self, monkeypatch):
        """Should return patterns from environment variable."""
        monkeypatch.setenv("PYRIGHT_VENV_PATTERNS", ".venv, venv, .custom-env")
        patterns = get_venv_patterns()
        assert patterns == [".venv", "venv", ".custom-env"]

    def test_empty_patterns_ignored(self, monkeypatch):
        """Should ignore empty patterns."""
        monkeypatch.setenv("PYRIGHT_VENV_PATTERNS", ".venv,,venv, ,")
        patterns = get_venv_patterns()
        assert patterns == [".venv", "venv"]


class TestEnvironmentState:
    """Tests for EnvironmentState dataclass."""

    def test_creation(self, tmp_path):
        """Should create EnvironmentState with required fields."""
        env = EnvironmentState(
            env_id=str(tmp_path),
            project_root=tmp_path,
            venv_path=tmp_path / ".venv",
            config={"typeCheckingMode": "strict"},
        )
        assert env.env_id == str(tmp_path)
        assert env.project_root == tmp_path
        assert env.venv_path == tmp_path / ".venv"
        assert env.config == {"typeCheckingMode": "strict"}
        assert env.client is None
        assert env.opened_files == set()
        assert env.doc_versions == {}
        assert env.diagnostics == {}
        assert isinstance(env.last_accessed, datetime)

    def test_update_access_time(self, tmp_path):
        """Should update last_accessed timestamp."""
        env = EnvironmentState(
            env_id=str(tmp_path),
            project_root=tmp_path,
            venv_path=None,
            config={},
        )
        original_time = env.last_accessed
        env.update_access_time()
        assert env.last_accessed >= original_time

    def test_clear_state(self, tmp_path):
        """Should clear runtime state."""
        env = EnvironmentState(
            env_id=str(tmp_path),
            project_root=tmp_path,
            venv_path=None,
            config={},
        )
        env.opened_files.add("file:///test.py")
        env.doc_versions["file:///test.py"] = 1
        env.diagnostics["file:///test.py"] = [{"message": "error"}]

        env.clear_state()

        assert env.opened_files == set()
        assert env.doc_versions == {}
        assert env.diagnostics == {}


class TestReadPyrightConfig:
    """Tests for read_pyright_config function."""

    def test_no_config_file(self, tmp_path):
        """Should return empty dict when no config exists."""
        config = read_pyright_config(tmp_path)
        assert config == {}

    def test_valid_config(self, tmp_path):
        """Should read valid pyrightconfig.json."""
        config_path = tmp_path / "pyrightconfig.json"
        config_path.write_text('{"typeCheckingMode": "strict", "pythonVersion": "3.10"}')

        config = read_pyright_config(tmp_path)

        assert config == {"typeCheckingMode": "strict", "pythonVersion": "3.10"}

    def test_invalid_json(self, tmp_path):
        """Should return empty dict for invalid JSON."""
        config_path = tmp_path / "pyrightconfig.json"
        config_path.write_text("not valid json {")

        config = read_pyright_config(tmp_path)

        assert config == {}


class TestDiscoverProjectRoots:
    """Tests for discover_project_roots function."""

    def test_root_always_included(self, tmp_path):
        """Should always include the root directory."""
        roots = discover_project_roots(tmp_path)
        assert tmp_path in roots

    def test_finds_pyproject_toml(self, tmp_path):
        """Should find directories with pyproject.toml."""
        pkg_dir = tmp_path / "packages" / "pkg-a"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "pyproject.toml").write_text("[project]\nname = 'pkg-a'")

        roots = discover_project_roots(tmp_path)

        assert pkg_dir in roots
        assert tmp_path in roots

    def test_finds_pyrightconfig_json(self, tmp_path):
        """Should find directories with pyrightconfig.json."""
        pkg_dir = tmp_path / "packages" / "pkg-b"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "pyrightconfig.json").write_text("{}")

        roots = discover_project_roots(tmp_path)

        assert pkg_dir in roots

    def test_finds_nested_projects(self, tmp_path):
        """Should find multiple nested project roots."""
        # Create nested structure
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'root'")

        pkg_a = tmp_path / "packages" / "pkg-a"
        pkg_a.mkdir(parents=True)
        (pkg_a / "pyproject.toml").write_text("[project]\nname = 'pkg-a'")

        pkg_b = tmp_path / "packages" / "pkg-b"
        pkg_b.mkdir(parents=True)
        (pkg_b / "pyproject.toml").write_text("[project]\nname = 'pkg-b'")

        roots = discover_project_roots(tmp_path)

        assert len(roots) == 3
        assert tmp_path in roots
        assert pkg_a in roots
        assert pkg_b in roots

    def test_ignores_node_modules(self, tmp_path):
        """Should ignore node_modules directories."""
        node_pkg = tmp_path / "node_modules" / "some-pkg"
        node_pkg.mkdir(parents=True)
        (node_pkg / "pyproject.toml").write_text("[project]\nname = 'bad'")

        roots = discover_project_roots(tmp_path)

        assert node_pkg not in roots

    def test_ignores_pycache(self, tmp_path):
        """Should ignore __pycache__ directories."""
        cache_dir = tmp_path / "__pycache__"
        cache_dir.mkdir()
        (cache_dir / "pyproject.toml").write_text("[project]\nname = 'bad'")

        roots = discover_project_roots(tmp_path)

        assert cache_dir not in roots

    def test_ignores_git(self, tmp_path):
        """Should ignore .git directories."""
        git_dir = tmp_path / ".git" / "hooks"
        git_dir.mkdir(parents=True)
        (git_dir / "pyproject.toml").write_text("[project]\nname = 'bad'")

        roots = discover_project_roots(tmp_path)

        assert git_dir not in roots

    def test_sorted_by_depth(self, tmp_path):
        """Should return roots sorted by depth (shallowest first)."""
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        (deep / "pyproject.toml").write_text("")

        shallow = tmp_path / "x"
        shallow.mkdir()
        (shallow / "pyproject.toml").write_text("")

        (tmp_path / "pyproject.toml").write_text("")

        roots = discover_project_roots(tmp_path)

        assert roots[0] == tmp_path  # Root first
        assert roots.index(shallow) < roots.index(deep)  # Shallow before deep


class TestResolveVenvForRoot:
    """Tests for resolve_venv_for_root function."""

    def test_finds_venv_in_project_root(self, tmp_path):
        """Should find venv in the project root."""
        venv_dir = tmp_path / ".venv" / "bin"
        venv_dir.mkdir(parents=True)
        (venv_dir / "python").write_text("")

        result = resolve_venv_for_root(tmp_path)

        assert result == tmp_path / ".venv"

    def test_finds_venv_named_venv(self, tmp_path):
        """Should find venv named 'venv'."""
        venv_dir = tmp_path / "venv" / "bin"
        venv_dir.mkdir(parents=True)
        (venv_dir / "python").write_text("")

        result = resolve_venv_for_root(tmp_path)

        assert result == tmp_path / "venv"

    def test_finds_shared_venv_in_parent(self, tmp_path):
        """Should find shared venv in parent directory."""
        # Create venv in root
        venv_dir = tmp_path / ".venv" / "bin"
        venv_dir.mkdir(parents=True)
        (venv_dir / "python").write_text("")

        # Create project in subdirectory
        pkg_dir = tmp_path / "packages" / "pkg-a"
        pkg_dir.mkdir(parents=True)

        result = resolve_venv_for_root(pkg_dir)

        assert result == tmp_path / ".venv"

    def test_returns_none_when_no_venv(self, tmp_path):
        """Should return None when no venv found."""
        result = resolve_venv_for_root(tmp_path)
        assert result is None

    def test_ignores_empty_venv_dir(self, tmp_path):
        """Should ignore venv dir without python executable."""
        venv_dir = tmp_path / ".venv"
        venv_dir.mkdir()
        # No bin/python

        result = resolve_venv_for_root(tmp_path)

        assert result is None

    def test_custom_patterns(self, tmp_path):
        """Should use custom venv patterns."""
        venv_dir = tmp_path / ".custom-env" / "bin"
        venv_dir.mkdir(parents=True)
        (venv_dir / "python").write_text("")

        result = resolve_venv_for_root(tmp_path, patterns=[".custom-env"])

        assert result == tmp_path / ".custom-env"


class TestPixiEnvironmentDetection:
    """Tests for pixi environment detection."""

    def _create_pixi_env(self, root: Path, env_name: str = "default") -> Path:
        """Helper to create a mock pixi environment."""
        pixi_env = root / ".pixi" / "envs" / env_name / "bin"
        pixi_env.mkdir(parents=True)
        (pixi_env / "python").write_text("")
        return root / ".pixi" / "envs" / env_name

    def test_detects_pixi_env_with_pixi_lock(self, tmp_path):
        """Should detect pixi env when pixi.lock exists."""
        (tmp_path / "pixi.lock").write_text("")
        env_path = self._create_pixi_env(tmp_path)

        result = resolve_pixi_env(tmp_path)

        assert result == env_path

    def test_detects_pixi_env_with_pixi_toml(self, tmp_path):
        """Should detect pixi env when pixi.toml exists."""
        (tmp_path / "pixi.toml").write_text("[project]\nname = 'test'")
        env_path = self._create_pixi_env(tmp_path)

        result = resolve_pixi_env(tmp_path)

        assert result == env_path

    def test_returns_none_without_pixi_markers(self, tmp_path):
        """Should return None when no pixi.lock or pixi.toml exists."""
        self._create_pixi_env(tmp_path)  # env exists but no marker files

        result = resolve_pixi_env(tmp_path)

        assert result is None

    def test_returns_none_when_env_not_installed(self, tmp_path):
        """Should return None when pixi project exists but env not installed."""
        (tmp_path / "pixi.lock").write_text("")
        # Don't create the .pixi/envs/default directory

        result = resolve_pixi_env(tmp_path)

        assert result is None

    def test_pixi_env_override(self, tmp_path, monkeypatch):
        """Should use PYRIGHT_PIXI_ENV to override default env name."""
        (tmp_path / "pixi.lock").write_text("")
        self._create_pixi_env(tmp_path, "default")  # default exists
        custom_env = self._create_pixi_env(tmp_path, "dev")

        monkeypatch.setenv(PIXI_ENV_VAR, "dev")

        result = resolve_pixi_env(tmp_path)

        assert result == custom_env

    def test_pixi_takes_priority_over_venv(self, tmp_path):
        """Should prefer pixi env over .venv in the same directory."""
        # Create both pixi and venv in same directory
        (tmp_path / "pixi.lock").write_text("")
        pixi_env = self._create_pixi_env(tmp_path)

        venv_dir = tmp_path / ".venv" / "bin"
        venv_dir.mkdir(parents=True)
        (venv_dir / "python").write_text("")

        result = resolve_venv_for_root(tmp_path)

        # Should pick pixi, not venv
        assert result == pixi_env

    def test_pixi_in_parent_takes_priority_over_venv_higher_up(self, tmp_path):
        """Should prefer pixi env in parent over venv higher in tree."""
        # Create venv at root
        venv_dir = tmp_path / ".venv" / "bin"
        venv_dir.mkdir(parents=True)
        (venv_dir / "python").write_text("")

        # Create pixi env in python/ subdirectory
        python_dir = tmp_path / "python"
        python_dir.mkdir()
        (python_dir / "pixi.lock").write_text("")
        pixi_env = self._create_pixi_env(python_dir)

        # Create a project under python/
        pkg_dir = python_dir / "common"
        pkg_dir.mkdir()
        (pkg_dir / "pyproject.toml").write_text("")

        # Resolving for pkg_dir should find the pixi env, not the root venv
        result = resolve_venv_for_root(pkg_dir)

        assert result == pixi_env

    def test_falls_back_to_venv_when_no_pixi(self, tmp_path):
        """Should fall back to venv when pixi not present."""
        venv_dir = tmp_path / ".venv" / "bin"
        venv_dir.mkdir(parents=True)
        (venv_dir / "python").write_text("")

        result = resolve_venv_for_root(tmp_path)

        assert result == tmp_path / ".venv"

    def test_pixi_env_without_python(self, tmp_path):
        """Should return None if pixi env dir exists but has no python."""
        (tmp_path / "pixi.lock").write_text("")
        pixi_env = tmp_path / ".pixi" / "envs" / "default"
        pixi_env.mkdir(parents=True)
        # No bin/python created

        result = resolve_pixi_env(tmp_path)

        assert result is None


class TestDiscoverEnvironments:
    """Tests for discover_environments function."""

    def test_discovers_single_environment(self, tmp_path):
        """Should discover a single environment."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test'")

        envs = discover_environments(tmp_path)

        assert len(envs) == 1
        assert envs[0].project_root == tmp_path
        assert envs[0].env_id == str(tmp_path)

    def test_discovers_multiple_environments(self, tmp_path):
        """Should discover multiple environments."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'root'")

        pkg_a = tmp_path / "packages" / "pkg-a"
        pkg_a.mkdir(parents=True)
        (pkg_a / "pyproject.toml").write_text("[project]\nname = 'pkg-a'")

        pkg_b = tmp_path / "packages" / "pkg-b"
        pkg_b.mkdir(parents=True)
        (pkg_b / "pyproject.toml").write_text("[project]\nname = 'pkg-b'")

        envs = discover_environments(tmp_path)

        assert len(envs) == 3
        project_roots = {env.project_root for env in envs}
        assert tmp_path in project_roots
        assert pkg_a in project_roots
        assert pkg_b in project_roots

    def test_resolves_venvs_correctly(self, tmp_path):
        """Should resolve venvs for each environment."""
        # Root venv (shared)
        root_venv = tmp_path / ".venv" / "bin"
        root_venv.mkdir(parents=True)
        (root_venv / "python").write_text("")
        (tmp_path / "pyproject.toml").write_text("")

        # pkg-a with its own venv
        pkg_a = tmp_path / "packages" / "pkg-a"
        pkg_a.mkdir(parents=True)
        (pkg_a / "pyproject.toml").write_text("")
        pkg_a_venv = pkg_a / ".venv" / "bin"
        pkg_a_venv.mkdir(parents=True)
        (pkg_a_venv / "python").write_text("")

        # pkg-b without venv (should inherit root)
        pkg_b = tmp_path / "packages" / "pkg-b"
        pkg_b.mkdir(parents=True)
        (pkg_b / "pyproject.toml").write_text("")

        envs = discover_environments(tmp_path)

        env_by_root = {env.project_root: env for env in envs}

        assert env_by_root[tmp_path].venv_path == tmp_path / ".venv"
        assert env_by_root[pkg_a].venv_path == pkg_a / ".venv"
        assert env_by_root[pkg_b].venv_path == tmp_path / ".venv"  # Inherited

    def test_loads_pyrightconfig(self, tmp_path):
        """Should load pyrightconfig.json for each environment."""
        (tmp_path / "pyproject.toml").write_text("")
        (tmp_path / "pyrightconfig.json").write_text(
            '{"typeCheckingMode": "strict"}'
        )

        envs = discover_environments(tmp_path)

        assert envs[0].config == {"typeCheckingMode": "strict"}


class TestGetEnvironmentForFile:
    """Tests for get_environment_for_file function."""

    def test_finds_exact_match(self, tmp_path):
        """Should find environment for file in project root."""
        env = EnvironmentState(
            env_id=str(tmp_path),
            project_root=tmp_path,
            venv_path=None,
            config={},
        )

        result = get_environment_for_file(tmp_path / "src" / "main.py", [env])

        assert result == env

    def test_finds_most_specific_match(self, tmp_path):
        """Should find the most specific (longest prefix) environment."""
        pkg_a = tmp_path / "packages" / "pkg-a"
        pkg_a.mkdir(parents=True)

        root_env = EnvironmentState(
            env_id=str(tmp_path),
            project_root=tmp_path,
            venv_path=None,
            config={},
        )
        pkg_env = EnvironmentState(
            env_id=str(pkg_a),
            project_root=pkg_a,
            venv_path=None,
            config={},
        )

        # File in pkg-a should match pkg_env, not root_env
        result = get_environment_for_file(
            pkg_a / "src" / "main.py",
            [root_env, pkg_env],
        )

        assert result == pkg_env

    def test_returns_none_for_unmatched_file(self, tmp_path):
        """Should return None for file not in any environment."""
        other_dir = tmp_path.parent / "other_project"
        other_dir.mkdir(parents=True, exist_ok=True)

        env = EnvironmentState(
            env_id=str(tmp_path),
            project_root=tmp_path,
            venv_path=None,
            config={},
        )

        result = get_environment_for_file(other_dir / "main.py", [env])

        assert result is None

    def test_handles_symlinks(self, tmp_path):
        """Should resolve symlinks before matching."""
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        (real_dir / "main.py").write_text("")

        link_dir = tmp_path / "link"
        link_dir.symlink_to(real_dir)

        env = EnvironmentState(
            env_id=str(real_dir),
            project_root=real_dir,
            venv_path=None,
            config={},
        )

        # File accessed via symlink should still match
        result = get_environment_for_file(link_dir / "main.py", [env])

        assert result == env
