"""Frozen-aware path resolution and user data directory management."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from core.version import ORG_DATA_DIR_NAME, SUITE_DATA_DIR_NAME


def is_frozen() -> bool:
    """Return True when running inside a packaged PyInstaller executable."""
    return bool(getattr(sys, "frozen", False))


def get_bundle_root() -> Path:
    """Return the root directory where bundled read-only resources reside."""
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass).resolve()
        return Path(sys.executable).resolve().parent
    # Source mode: core/utilities/paths.py -> parents[2] is repository root
    return Path(__file__).resolve().parents[2]


def get_executable_dir() -> Path:
    """Return the directory containing the active executable (or repo root in dev)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def get_resource_path(relative_path: str | Path) -> Path:
    """Resolve a path inside the bundled resources directory."""
    clean_rel = Path(relative_path)
    bundle_root = get_bundle_root()
    candidate = bundle_root / "resources" / clean_rel
    if candidate.exists():
        return candidate
    # Fallback in case caller already prefixed 'resources/'
    direct_candidate = bundle_root / clean_rel
    return direct_candidate


def get_user_data_root() -> Path:
    """Return and create the persistent suite data directory in %LOCALAPPDATA%."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    base_dir = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"

    suite_dir = base_dir / ORG_DATA_DIR_NAME / SUITE_DATA_DIR_NAME
    suite_dir.mkdir(parents=True, exist_ok=True)
    return suite_dir


def get_config_dir() -> Path:
    """Return and create the directory for application JSON configuration files."""
    path = get_user_data_root() / "config"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_database_dir() -> Path:
    """Return and create the directory for isolated per-app SQLite databases."""
    path = get_user_data_root() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_logs_dir() -> Path:
    """Return and create the centralized rotating log directory."""
    path = get_user_data_root() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_backups_dir(app_id: str | None = None) -> Path:
    """Return and create the directory for automatic document and database backups."""
    path = get_user_data_root() / "backups"
    if app_id:
        path = path / app_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_temp_dir(app_id: str | None = None) -> Path:
    """Return and create a managed temporary workspace directory."""
    path = get_user_data_root() / "temp"
    if app_id:
        path = path / app_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_default_documents_dir() -> Path:
    """Return the user's standard Documents directory, falling back to home."""
    docs = Path.home() / "Documents"
    return docs if docs.exists() else Path.home()