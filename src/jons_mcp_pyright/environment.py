"""Environment discovery and management for multi-environment support.

This module provides functionality to discover Python project environments
in a monorepo structure, where each subdirectory may have its own virtual
environment and pyproject.toml.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .lsp_client import PyrightClient

logger = logging.getLogger(__name__)

# Default patterns for virtual environment directories
DEFAULT_VENV_PATTERNS = [".venv", "venv", ".env"]

# Directories to ignore during discovery
IGNORE_PATTERNS = {
    ".git",
    "node_modules",
    "dist",
    "__pycache__",
    ".tox",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "build",
    "egg-info",
    ".eggs",
}


def get_venv_patterns() -> list[str]:
    """Get virtual environment directory patterns from environment or defaults.

    Returns:
        List of venv directory names to search for
    """
    env_patterns = os.environ.get("PYRIGHT_VENV_PATTERNS")
    if env_patterns:
        return [p.strip() for p in env_patterns.split(",") if p.strip()]
    return DEFAULT_VENV_PATTERNS


@dataclass
class EnvironmentState:
    """State for a single Python environment.

    Each environment corresponds to a project root that may have its own
    virtual environment and pyrightconfig.json.
    """

    env_id: str
    """Unique identifier (canonical path of project root)"""

    project_root: Path
    """Project root directory"""

    venv_path: Path | None
    """Path to the virtual environment, if found"""

    config: dict[str, Any]
    """Configuration from pyrightconfig.json"""

    client: PyrightClient | None = None
    """Lazily initialized Pyright client"""

    opened_files: set[str] = field(default_factory=set)
    """URIs of files opened in this environment"""

    doc_versions: dict[str, int] = field(default_factory=dict)
    """Document version counters per URI"""

    diagnostics: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    """Per-file diagnostics for this environment"""

    last_accessed: datetime = field(default_factory=datetime.now)
    """Last time this environment was accessed (for LRU eviction)"""

    def update_access_time(self) -> None:
        """Update the last accessed time to now."""
        self.last_accessed = datetime.now()

    def clear_state(self) -> None:
        """Clear runtime state (diagnostics, opened files, versions)."""
        self.opened_files.clear()
        self.doc_versions.clear()
        self.diagnostics.clear()


def read_pyright_config(project_root: Path) -> dict[str, Any]:
    """Read pyrightconfig.json if it exists.

    Args:
        project_root: Path to the project root directory

    Returns:
        Configuration dictionary, or empty dict if no config found
    """
    config_path = project_root / "pyrightconfig.json"
    if config_path.exists():
        try:
            with open(config_path, "r") as f:
                config = json.load(f)
                logger.debug(f"Loaded pyrightconfig.json from {project_root}: {config}")
                return config
        except Exception as e:
            logger.warning(f"Failed to read pyrightconfig.json at {config_path}: {e}")
    return {}


def discover_project_roots(root: Path) -> list[Path]:
    """Discover all project roots under the given root directory.

    A project root is a directory containing pyproject.toml or pyrightconfig.json.
    The root directory itself is always included.

    Args:
        root: Root directory to search from

    Returns:
        List of project root paths, sorted by depth (shallowest first)
    """
    root = root.resolve()
    project_roots: set[Path] = {root}  # Always include the root

    def should_ignore(path: Path) -> bool:
        """Check if a path should be ignored during traversal."""
        return path.name in IGNORE_PATTERNS or path.name.endswith(".egg-info")

    def scan_directory(directory: Path, depth: int = 0) -> None:
        """Recursively scan for project roots."""
        # Limit recursion depth to avoid scanning too deep
        if depth > 10:
            return

        try:
            for entry in directory.iterdir():
                if not entry.is_dir():
                    continue
                if should_ignore(entry):
                    continue

                # Check if this is a project root
                has_pyproject = (entry / "pyproject.toml").exists()
                has_pyrightconfig = (entry / "pyrightconfig.json").exists()

                if has_pyproject or has_pyrightconfig:
                    project_roots.add(entry.resolve())
                    logger.debug(
                        f"Found project root: {entry} "
                        f"(pyproject.toml={has_pyproject}, pyrightconfig.json={has_pyrightconfig})"
                    )

                # Continue scanning subdirectories
                scan_directory(entry, depth + 1)
        except PermissionError:
            logger.debug(f"Permission denied accessing {directory}")
        except Exception as e:
            logger.warning(f"Error scanning {directory}: {e}")

    scan_directory(root)

    # Sort by path depth (shallowest first) then by name for deterministic order
    sorted_roots = sorted(project_roots, key=lambda p: (len(p.parts), str(p)))
    logger.info(f"Discovered {len(sorted_roots)} project root(s)")
    return sorted_roots


def resolve_venv_for_root(
    project_root: Path,
    patterns: list[str] | None = None,
) -> Path | None:
    """Resolve the virtual environment for a project root.

    Searches for a venv in the project root first, then walks up to parent
    directories to find a shared venv.

    Args:
        project_root: The project root directory
        patterns: Venv directory patterns to search for (default from env/config)

    Returns:
        Path to the venv directory, or None if not found
    """
    if patterns is None:
        patterns = get_venv_patterns()

    project_root = project_root.resolve()

    def find_venv_in_dir(directory: Path) -> Path | None:
        """Look for a venv in the given directory."""
        for pattern in patterns:
            venv_path = directory / pattern
            if venv_path.is_dir():
                # Verify it looks like a venv (has bin/python or Scripts/python.exe)
                python_paths = [
                    venv_path / "bin" / "python",
                    venv_path / "bin" / "python3",
                    venv_path / "Scripts" / "python.exe",
                    venv_path / "Scripts" / "python3.exe",
                ]
                for python_path in python_paths:
                    if python_path.exists():
                        logger.debug(f"Found venv at {venv_path}")
                        return venv_path
        return None

    # First check the project root itself
    venv = find_venv_in_dir(project_root)
    if venv:
        return venv

    # Walk up to parent directories
    current = project_root.parent
    while current != current.parent:  # Stop at filesystem root
        venv = find_venv_in_dir(current)
        if venv:
            logger.debug(f"Found shared venv at {venv} for project {project_root}")
            return venv
        current = current.parent

    logger.debug(f"No venv found for project {project_root}")
    return None


def discover_environments(root: Path) -> list[EnvironmentState]:
    """Discover all Python environments under the given root.

    This performs a two-phase discovery:
    1. Find all project roots (directories with pyproject.toml or pyrightconfig.json)
    2. Resolve the virtual environment for each project root

    Args:
        root: Root directory to search from

    Returns:
        List of EnvironmentState objects for each discovered environment
    """
    root = root.resolve()
    project_roots = discover_project_roots(root)
    venv_patterns = get_venv_patterns()

    environments: list[EnvironmentState] = []

    for project_root in project_roots:
        # Resolve venv for this project
        venv_path = resolve_venv_for_root(project_root, venv_patterns)

        # Read project-specific pyrightconfig.json
        config = read_pyright_config(project_root)

        # Create environment ID from canonical path
        env_id = str(project_root)

        env = EnvironmentState(
            env_id=env_id,
            project_root=project_root,
            venv_path=venv_path,
            config=config,
        )

        environments.append(env)
        logger.info(
            f"Discovered environment: {env_id} "
            f"(venv={venv_path}, config_keys={list(config.keys())})"
        )

    return environments


def get_environment_for_file(
    file_path: str | Path,
    environments: list[EnvironmentState],
) -> EnvironmentState | None:
    """Find the environment that should handle a given file.

    Uses longest-prefix matching to find the most specific environment
    for the file.

    Args:
        file_path: Path to the file (will be resolved to canonical form)
        environments: List of available environments

    Returns:
        The matching EnvironmentState, or None if no match found
    """
    # Resolve to canonical path to handle symlinks
    try:
        file_path = Path(file_path).resolve()
    except Exception:
        # If resolution fails, try with the raw path
        file_path = Path(file_path)

    best_match: EnvironmentState | None = None
    best_match_len = 0

    for env in environments:
        try:
            # Check if file is under this environment's project root
            file_path.relative_to(env.project_root)
            # If we get here, the file is under this project root
            # Check if this is a longer (more specific) match
            match_len = len(env.project_root.parts)
            if match_len > best_match_len:
                best_match = env
                best_match_len = match_len
        except ValueError:
            # File is not under this project root
            continue

    return best_match
