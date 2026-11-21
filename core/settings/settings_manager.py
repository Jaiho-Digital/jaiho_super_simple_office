"""Thread-safe JSON settings, window geometry, and recent-files manager."""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.file_manager.file_ops import atomic_write_json, read_json_safe
from core.utilities.paths import get_config_dir, get_default_documents_dir

logger = logging.getLogger(__name__)

_MAX_RECENT_FILES = 15
_MAX_RECENT_APPS = 8


class SettingsManager:
    """Manages persistent suite-wide and application-specific JSON preferences."""

    def __init__(self, app_id: str, config_dir: Path | None = None) -> None:
        self.app_id = app_id
        self._lock = threading.RLock()
        self._config_dir = config_dir or get_config_dir()
        self._config_dir.mkdir(parents=True, exist_ok=True)

        self._suite_path = self._config_dir / "suite.json"
        self._app_path = self._config_dir / f"{app_id}.json"

        self._suite_data: dict[str, Any] = self._load_suite_defaults()
        self._app_data: dict[str, Any] = self._load_app_defaults()
        self.reload()

    def _load_suite_defaults(self) -> dict[str, Any]:
        return {
            "theme": "light",
            "default_directory": str(get_default_documents_dir()),
            "recent_apps": [],
            "recent_files_global": [],
        }

    def _load_app_defaults(self) -> dict[str, Any]:
        return {
            "window": {
                "width": 1100,
                "height": 720,
                "x": None,
                "y": None,
                "maximized": False,
            },
            "recent_files": [],
            "preferences": {},
        }

    def reload(self) -> None:
        """Reload suite and application settings from disk, preserving defaults for missing keys."""
        with self._lock:
            loaded_suite = read_json_safe(self._suite_path, default={})
            if isinstance(loaded_suite, dict):
                defaults = self._load_suite_defaults()
                defaults.update(loaded_suite)
                self._suite_data = defaults

            loaded_app = read_json_safe(self._app_path, default={})
            if isinstance(loaded_app, dict):
                app_defaults = self._load_app_defaults()
                if isinstance(loaded_app.get("window"), dict):
                    app_defaults["window"].update(loaded_app["window"])
                if isinstance(loaded_app.get("recent_files"), list):
                    app_defaults["recent_files"] = loaded_app["recent_files"]
                if isinstance(loaded_app.get("preferences"), dict):
                    app_defaults["preferences"].update(loaded_app["preferences"])
                self._app_data = app_defaults

    def save_suite(self) -> None:
        """Atomically persist shared suite preferences."""
        with self._lock:
            atomic_write_json(self._suite_path, self._suite_data)

    def save_app(self) -> None:
        """Atomically persist application-specific preferences."""
        with self._lock:
            atomic_write_json(self._app_path, self._app_data)

    # ------------------------------------------------------------------
    # Shared Suite Preferences (Theme, Default Directory, Recent Apps)
    # ------------------------------------------------------------------

    def get_theme(self) -> str:
        """Return the active suite theme ('light' or 'dark')."""
        with self._lock:
            self.reload()
            theme = str(self._suite_data.get("theme", "light")).lower()
            return theme if theme in {"light", "dark"} else "light"

    def set_theme(self, theme: str) -> None:
        """Set and persist the active suite theme ('light' or 'dark')."""
        normalized = theme.strip().lower()
        if normalized not in {"light", "dark"}:
            raise ValueError(f"Unsupported theme '{theme}'. Expected 'light' or 'dark'.")
        with self._lock:
            self.reload()
            self._suite_data["theme"] = normalized
            self.save_suite()

    def get_default_directory(self) -> Path:
        """Return the user's preferred default directory for file dialogs."""
        with self._lock:
            raw = self._suite_data.get("default_directory")
            if raw:
                candidate = Path(str(raw))
                if candidate.exists() and candidate.is_dir():
                    return candidate
            return get_default_documents_dir()

    def set_default_directory(self, directory: str | Path) -> None:
        """Persist a preferred default directory for file dialogs."""
        path = Path(directory)
        with self._lock:
            self.reload()
            self._suite_data["default_directory"] = str(path)
            self.save_suite()

    def record_app_launch(self, launched_app_id: str | None = None) -> None:
        """Record an application launch in the suite's recently used apps list."""
        target_id = launched_app_id or self.app_id
        if target_id == "jaiho_office":
            return
        with self._lock:
            self.reload()
            recent: list[dict[str, str]] = [
                entry
                for entry in self._suite_data.get("recent_apps", [])
                if isinstance(entry, dict) and entry.get("app_id") != target_id
            ]
            recent.insert(
                0,
                {
                    "app_id": target_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            )
            self._suite_data["recent_apps"] = recent[:_MAX_RECENT_APPS]
            self.save_suite()

    def get_recent_apps(self) -> list[dict[str, str]]:
        """Return recently launched applications ordered most-recent first."""
        with self._lock:
            self.reload()
            raw = self._suite_data.get("recent_apps", [])
            return [entry for entry in raw if isinstance(entry, dict) and "app_id" in entry]

    # ------------------------------------------------------------------
    # Application Preferences, Window Geometry & Recent Files
    # ------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve an application-specific preference value."""
        with self._lock:
            return self._app_data["preferences"].get(key, default)

    def set(self, key: str, value: Any, auto_save: bool = True) -> None:
        """Store an application-specific preference value."""
        with self._lock:
            self._app_data["preferences"][key] = value
            if auto_save:
                self.save_app()

    def get_window_geometry(self) -> dict[str, Any]:
        """Return saved window dimensions, coordinates, and maximized flag."""
        with self._lock:
            return dict(self._app_data["window"])

    def set_window_geometry(
        self,
        width: int,
        height: int,
        x: int | None,
        y: int | None,
        maximized: bool,
    ) -> None:
        """Persist window geometry on close."""
        with self._lock:
            self._app_data["window"] = {
                "width": max(320, int(width)),
                "height": max(240, int(height)),
                "x": int(x) if x is not None else None,
                "y": int(y) if y is not None else None,
                "maximized": bool(maximized),
            }
            self.save_app()

    def add_recent_file(self, file_path: str | Path) -> None:
        """Add a file path to both per-app and global recent files lists."""
        resolved = str(Path(file_path).resolve())
        now_iso = datetime.now(UTC).isoformat()

        with self._lock:
            # Update per-app recent files
            app_recent: list[str] = [
                p for p in self._app_data.get("recent_files", []) if p != resolved
            ]
            app_recent.insert(0, resolved)
            self._app_data["recent_files"] = app_recent[:_MAX_RECENT_FILES]
            self.save_app()

            # Update suite-wide recent files for JaihoOffice hub
            self.reload()
            global_recent: list[dict[str, str]] = [
                entry
                for entry in self._suite_data.get("recent_files_global", [])
                if isinstance(entry, dict) and entry.get("path") != resolved
            ]
            global_recent.insert(
                0,
                {
                    "path": resolved,
                    "app_id": self.app_id,
                    "timestamp": now_iso,
                },
            )
            self._suite_data["recent_files_global"] = global_recent[:_MAX_RECENT_FILES]
            self.save_suite()

    def get_recent_files(self, existing_only: bool = True) -> list[Path]:
        """Return this application's recent file paths, optionally filtering missing files."""
        with self._lock:
            raw_paths = self._app_data.get("recent_files", [])
            results: list[Path] = []
            for raw in raw_paths:
                candidate = Path(str(raw))
                if not existing_only or candidate.exists():
                    results.append(candidate)
            return results

    def clear_recent_files(self) -> None:
        """Clear this application's recent files list."""
        with self._lock:
            self._app_data["recent_files"] = []
            self.save_app()