"""Public diagnostic filtering based on member Pyright configuration."""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from .exceptions import PathValidationError
from .utils import file_uri_to_path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[import-not-found]

logger = logging.getLogger(__name__)

_SEVERITY_BY_NAME = {
    "error": 1,
    "warning": 2,
    "information": 3,
    "hint": 4,
}


@dataclass(frozen=True)
class _ConfigCacheEntry:
    """Cached report-rule overrides for one config file signature."""

    signature: tuple[int, int]
    rules: dict[str, Any]


_CONFIG_CACHE: dict[Path, _ConfigCacheEntry] = {}


def clear_diagnostic_filter_cache() -> None:
    """Clear parsed diagnostic config cache.

    Primarily useful for tests that rewrite temporary config files quickly.
    """

    _CONFIG_CACHE.clear()


def _config_signature(path: Path) -> tuple[int, int] | None:
    """Return the mtime/size signature for a regular config file."""

    try:
        stat = path.stat()
    except OSError:
        return None
    if not path.is_file():
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _extract_report_overrides(config: dict[str, Any]) -> dict[str, Any]:
    """Keep only explicit top-level Pyright report* diagnostic overrides."""

    return {key: value for key, value in config.items() if key.startswith("report")}


def _load_pyrightconfig_json(path: Path) -> dict[str, Any]:
    """Load a pyrightconfig.json dictionary."""

    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def _load_pyproject_pyright(path: Path) -> dict[str, Any]:
    """Load the [tool.pyright] table from pyproject.toml."""

    with open(path, "rb") as f:
        data = tomllib.load(f)
    tool_config = data.get("tool")
    if not isinstance(tool_config, dict):
        return {}
    pyright_config = tool_config.get("pyright")
    return (
        cast(dict[str, Any], pyright_config) if isinstance(pyright_config, dict) else {}
    )


def _cached_report_overrides(
    config_path: Path,
    loader: Callable[[Path], dict[str, Any]],
) -> dict[str, Any]:
    """Load report-rule overrides for config_path with mtime/size caching."""

    signature = _config_signature(config_path)
    if signature is None:
        _CONFIG_CACHE.pop(config_path, None)
        return {}

    cached = _CONFIG_CACHE.get(config_path)
    if cached and cached.signature == signature:
        return dict(cached.rules)

    try:
        rules = _extract_report_overrides(loader(config_path))
    except (OSError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        logger.warning("Failed to read diagnostic config at %s: %s", config_path, exc)
        rules = {}

    _CONFIG_CACHE[config_path] = _ConfigCacheEntry(signature=signature, rules=rules)
    return dict(rules)


def _directory_report_overrides(directory: Path) -> dict[str, Any]:
    """Load report-rule overrides for one directory using Pyright precedence."""

    pyrightconfig_path = directory / "pyrightconfig.json"
    if pyrightconfig_path.exists():
        return _cached_report_overrides(
            pyrightconfig_path,
            _load_pyrightconfig_json,
        )

    pyproject_path = directory / "pyproject.toml"
    if pyproject_path.exists():
        return _cached_report_overrides(pyproject_path, _load_pyproject_pyright)

    return {}


def _diagnostic_path_from_uri(uri: str, project_root: Path) -> Path | None:
    """Resolve a diagnostic URI to an in-root path without requiring existence."""

    try:
        path = file_uri_to_path(uri)
        root = project_root.resolve()
        resolved = path.resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, PathValidationError, ValueError):
        return None
    return resolved


def _ancestor_directories(project_root: Path, file_path: Path) -> list[Path]:
    """Return root-to-file-parent directories for an already root-bound path."""

    root = project_root.resolve()
    relative_path = file_path.relative_to(root)
    directories = [root]
    current = root
    for part in relative_path.parts[:-1]:
        current = current / part
        directories.append(current)
    return directories


def _report_overrides_for_uri(uri: str, project_root: Path) -> dict[str, Any]:
    """Resolve merged report overrides for a diagnostic URI."""

    file_path = _diagnostic_path_from_uri(uri, project_root)
    if file_path is None:
        return {}

    overrides: dict[str, Any] = {}
    for directory in _ancestor_directories(project_root, file_path):
        overrides.update(_directory_report_overrides(directory))
    return overrides


def diagnostic_rule_name(diagnostic: dict[str, Any]) -> str | None:
    """Extract an exact Pyright report* rule identity from a diagnostic."""

    code = diagnostic.get("code")
    if isinstance(code, str) and code.startswith("report"):
        return code

    rule = diagnostic.get("rule")
    if isinstance(rule, str):
        return rule

    data = diagnostic.get("data")
    if isinstance(data, dict):
        data_rule = data.get("rule")
        if isinstance(data_rule, str):
            return data_rule

    return None


def _apply_rule_override(
    diagnostic: dict[str, Any],
    rule: str,
    override: Any,
) -> dict[str, Any] | None:
    """Apply one diagnostic-rule override to a diagnostic."""

    if override is False:
        return None
    if override is True:
        return diagnostic

    if isinstance(override, str):
        normalized = override.lower()
        if normalized == "none":
            return None
        severity = _SEVERITY_BY_NAME.get(normalized)
        if severity is not None:
            filtered = dict(diagnostic)
            filtered["severity"] = severity
            return filtered

    logger.debug("Ignoring unsupported diagnostic override for %s: %r", rule, override)
    return diagnostic


def filter_diagnostics_by_member_config(
    diagnostics: list[dict[str, Any]],
    project_root: Path,
) -> list[dict[str, Any]]:
    """Apply in-root member pyright diagnostic overrides to public diagnostics."""

    overrides_by_uri: dict[str, dict[str, Any]] = {}
    filtered_diagnostics: list[dict[str, Any]] = []

    for diagnostic in diagnostics:
        rule = diagnostic_rule_name(diagnostic)
        uri = diagnostic.get("uri")
        if not rule or not isinstance(uri, str):
            filtered_diagnostics.append(diagnostic)
            continue

        if uri not in overrides_by_uri:
            overrides_by_uri[uri] = _report_overrides_for_uri(uri, project_root)

        overrides = overrides_by_uri[uri]
        if rule not in overrides:
            filtered_diagnostics.append(diagnostic)
            continue

        filtered = _apply_rule_override(diagnostic, rule, overrides[rule])
        if filtered is not None:
            filtered_diagnostics.append(filtered)

    return filtered_diagnostics
