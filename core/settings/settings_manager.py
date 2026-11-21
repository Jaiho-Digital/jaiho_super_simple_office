"""Persistent JSON user preferences and window geometry storage."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from core.file_manager.file_ops import atomic_write_json
from core.themes.tokens import BUILT_IN_THEMES, THEME_ALIASES, resolve_theme_id
from core.utilities.paths import get_user_data_root

logger = logging.getLogger(__name__)

_SUPPORTED_THEME_INPUTS: frozenset[str] = frozenset(
    {*BUILT_IN_THEMES.keys(), *THEME_ALIASES.keys()}
)


class SettingsManager:
    """Per-application JSON settings store with atomic persistence."""

    def __init__(
        self,
        app_id: str,
        settings_path: Path | None = None,
    ) -> None:
        self.app_id = app_id
        self.settings_path: Path = settings_path or (
            get_user_data_root() / "settings" / f"{app_id}.json"
        )
        self._data: dict[str, Any] = {}
        self.reload()

    def reload(self) -> None:
        """Load settings from disk if the file exists, otherwise initialize empty state."""
        if not self.settings_path.exists():
            self._data = {}
            return

        try:
            raw = self.settings_path.read_text(encoding="utf-8")
            parsed = json.loads(raw)
            self._data = parsed if isinstance(parsed, dict) else {}
        except Exception as exc:
            logger.warning(
                "Could not read settings for '%s' at '%s': %s",
                self.app_id,
                self.settings_path,
                exc,
            )
            self._data = {}

    def save(self) -> None:
        """Persist current settings dictionary atomically to disk."""
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.settings_path, self._data)

    def get(self, key: str, default: Any = None) -> Any:
        """Return the value stored at `key`, or `default` if absent."""
        return self._data.get(key, default)

    def set(self, key: str, value: Any, auto_save: bool = True) -> None:
        """Store `value` under `key` and optionally flush immediately to disk."""
        self._data[key] = value
        if auto_save:
            self.save()

    def delete(self, key: str, auto_save: bool = True) -> None:
        """Remove `key` from settings if present."""
        if key in self._data:
            del self._data[key]
            if auto_save:
                self.save()

    def all(self) -> dict[str, Any]:
        """Return a shallow copy of all stored settings."""
        return dict(self._data)

    # ------------------------------------------------------------------
    # Theme Preferences (4 Pastel Themes + Legacy Light/Dark Aliases)
    # ------------------------------------------------------------------

    def get_theme(self) -> str:
        """Return the active canonical theme ID (defaults to 'lavender_dream')."""
        raw = str(self._data.get("theme", "lavender_dream")).strip()
        return resolve_theme_id(raw)

    def set_theme(self, theme: str) -> None:
        """Validate and persist any supported pastel theme ID or 'light'/'dark' alias."""
        cleaned = theme.strip().lower().replace(" ", "_")
        if cleaned not in _SUPPORTED_THEME_INPUTS:
            allowed = ", ".join(sorted(BUILT_IN_THEMES.keys()))
            raise ValueError(
                f"Unsupported theme '{theme}'. Expected one of: {allowed} (or 'light'/'dark')."
            )
        canonical = resolve_theme_id(cleaned)
        self.set("theme", canonical, auto_save=True)

    # ------------------------------------------------------------------
    # Window Geometry & Launch Telemetry
    # ------------------------------------------------------------------

    def get_window_geometry(self) -> dict[str, Any]:
        """Return persisted window dimensions and position metadata."""
        geom = self._data.get("window_geometry")
        if isinstance(geom, dict):
            return dict(geom)

        # Backwards-compatible flat keys support
        return {
            "width": self._data.get("window_width", 1040),
            "height": self._data.get("window_height", 680),
            "x": self._data.get("window_x"),
            "y": self._data.get("window_y"),
            "maximized": bool(self._data.get("window_maximized", False)),
        }

    def set_window_geometry(
        self,
        *,
        width: int,
        height: int,
        x: int | None = None,
        y: int | None = None,
        maximized: bool = False,
    ) -> None:
        """Persist window size, screen coordinates, and maximized state."""
        self._data["window_geometry"] = {
            "width": int(width),
            "height": int(height),
            "x": int(x) if x is not None else None,
            "y": int(y) if y is not None else None,
            "maximized": bool(maximized),
        }
        self._data["window_width"] = int(width)
        self._data["window_height"] = int(height)
        self.save()

    def record_app_launch(self, app_id: str | None = None) -> None:
        """Increment launch counter and store last-opened ISO timestamp."""
        target = app_id or self.app_id
        launches = self._data.get("launch_count", 0)
        self._data["launch_count"] = int(launches) + 1
        self._data["last_launched_app"] = target
        self._data["last_launched_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.save()