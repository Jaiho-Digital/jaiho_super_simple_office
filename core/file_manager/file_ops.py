"""Safe file operations, atomic writes, input validation, and backup management."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import datetime
from pathlib import Path
from typing import Any

from core.utilities.paths import get_backups_dir, get_temp_dir

logger = logging.getLogger(__name__)

_INVALID_WIN_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_WIN_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)


class FileOperationError(Exception):
    """Raised when a file validation, read, write, or backup operation fails safely."""


def sanitize_filename(name: str, fallback: str = "untitled") -> str:
    """Return a Windows-safe filename by stripping illegal characters and reserved names."""
    cleaned = _INVALID_WIN_CHARS_RE.sub("_", name).strip().rstrip(". ")
    if not cleaned:
        return fallback

    stem = Path(cleaned).stem.upper()
    if stem in _RESERVED_WIN_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned


def validate_input_file(
    file_path: str | Path,
    allowed_extensions: set[str] | tuple[str, ...] | None = None,
    max_size_mb: float | None = None,
    allow_empty: bool = False,
) -> Path:
    """Validate an untrusted input file path before opening or parsing it."""
    path = Path(file_path)

    if not path.exists():
        raise FileOperationError(f"File does not exist: {path}")
    if not path.is_file():
        raise FileOperationError(f"Path is not a regular file: {path}")

    if allowed_extensions is not None:
        normalized_exts = {
            ext.lower() if ext.startswith(".") else f".{ext.lower()}"
            for ext in allowed_extensions
        }
        if path.suffix.lower() not in normalized_exts:
            allowed_str = ", ".join(sorted(normalized_exts))
            raise FileOperationError(
                f"Unsupported file format '{path.suffix or '(none)'}'. Expected: {allowed_str}"
            )

    try:
        size_bytes = path.stat().st_size
    except OSError as exc:
        raise FileOperationError(f"Unable to read file metadata for '{path}': {exc}") from exc

    if not allow_empty and size_bytes == 0:
        raise FileOperationError(f"File is empty (0 bytes): {path.name}")

    if max_size_mb is not None:
        max_bytes = int(max_size_mb * 1024 * 1024)
        if size_bytes > max_bytes:
            actual_mb = size_bytes / (1024 * 1024)
            raise FileOperationError(
                f"File '{path.name}' ({actual_mb:.1f} MB) exceeds the {max_size_mb:.0f} MB limit."
            )

    return path


def create_backup(
    file_path: str | Path,
    app_id: str,
    max_backups: int = 10,
) -> Path | None:
    """Create a timestamped backup of an existing file and prune older backups."""
    source = Path(file_path)
    if not source.exists() or not source.is_file():
        return None

    backup_dir = get_backups_dir(app_id)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safe_stem = sanitize_filename(source.stem, fallback="backup")
    backup_name = f"{safe_stem}_{timestamp}{source.suffix}"
    backup_path = backup_dir / backup_name

    try:
        shutil.copy2(source, backup_path)
        _prune_old_backups(backup_dir, safe_stem, source.suffix, max_backups=max_backups)
        logger.debug("Created backup: %s", backup_path)
        return backup_path
    except OSError as exc:
        logger.warning("Failed to create backup for '%s': %s", source, exc)
        return None


def _prune_old_backups(
    backup_dir: Path,
    stem_prefix: str,
    suffix: str,
    max_backups: int,
) -> None:
    """Keep only the newest `max_backups` files matching a given stem and suffix."""
    if max_backups < 1:
        return
    pattern = f"{stem_prefix}_*{suffix}"
    matches = sorted(
        (p for p in backup_dir.glob(pattern) if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for stale in matches[max_backups:]:
        with suppress(OSError):
            stale.unlink()


def atomic_write_bytes(
    target_path: str | Path,
    data: bytes,
    backup_app_id: str | None = None,
    max_backups: int = 10,
) -> Path:
    """Safely write binary data to `target_path` using a same-directory temp file and os.replace."""
    destination = Path(target_path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    if backup_app_id and destination.exists():
        create_backup(destination, app_id=backup_app_id, max_backups=max_backups)

    temp_fd, temp_name = tempfile.mkstemp(
        prefix=f".{destination.stem}_",
        suffix=".tmp",
        dir=str(destination.parent),
    )
    temp_path = Path(temp_name)

    try:
        with os.fdopen(temp_fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, destination)
        return destination
    except Exception as exc:
        if temp_path.exists():
            with suppress(OSError):
                temp_path.unlink()
        raise FileOperationError(f"Failed to save file '{destination}': {exc}") from exc


def atomic_write_text(
    target_path: str | Path,
    text: str,
    encoding: str = "utf-8",
    backup_app_id: str | None = None,
    max_backups: int = 10,
) -> Path:
    """Safely write UTF-8 text to `target_path` atomically."""
    return atomic_write_bytes(
        target_path=target_path,
        data=text.encode(encoding),
        backup_app_id=backup_app_id,
        max_backups=max_backups,
    )


def atomic_write_json(
    target_path: str | Path,
    payload: Any,
    indent: int = 2,
    backup_app_id: str | None = None,
) -> Path:
    """Serialize `payload` as formatted JSON and save it atomically."""
    try:
        serialized = json.dumps(payload, indent=indent, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise FileOperationError(f"Failed to serialize JSON payload: {exc}") from exc
    return atomic_write_text(
        target_path=target_path,
        text=serialized,
        encoding="utf-8",
        backup_app_id=backup_app_id,
    )


def read_json_safe(file_path: str | Path, default: Any = None) -> Any:
    """Read and parse a JSON file, returning `default` if missing or malformed."""
    path = Path(file_path)
    if not path.exists() or not path.is_file():
        return default
    try:
        raw = path.read_text(encoding="utf-8")
        return json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not parse JSON file '%s': %s", path, exc)
        return default


@contextmanager
def managed_temp_file(app_id: str, suffix: str = ".tmp") -> Iterator[Path]:
    """Yield a temporary file inside the suite's temp directory and delete it afterward."""
    temp_dir = get_temp_dir(app_id)
    fd, raw_path = tempfile.mkstemp(prefix="jaiho_", suffix=suffix, dir=str(temp_dir))
    os.close(fd)
    temp_path = Path(raw_path)
    try:
        yield temp_path
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError as exc:
                logger.debug("Could not delete temp file '%s': %s", temp_path, exc)