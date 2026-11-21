"""Centralized rotating file logging and global crash diagnostics."""

from __future__ import annotations

import logging
import sys
import threading
import traceback
from collections.abc import Callable
from logging.handlers import RotatingFileHandler
from pathlib import Path

from core.utilities.paths import get_logs_dir
from core.version import SUITE_VERSION

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_MAX_BYTES = 5 * 1024 * 1024  # 5 MB per log file
_BACKUP_COUNT = 5

_configured_app_id: str | None = None


def setup_logging(app_id: str, debug: bool = False) -> logging.Logger:
    """Initialize rotating file logging (and console logging when attached) for an app."""
    global _configured_app_id

    root_logger = logging.getLogger()
    level = logging.DEBUG if debug else logging.INFO
    root_logger.setLevel(level)

    # Prevent duplicate handlers if called repeatedly (e.g., during pytest runs)
    if _configured_app_id == app_id and root_logger.handlers:
        return logging.getLogger(app_id)

    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT)

    log_file: Path = get_logs_dir() / f"{app_id}.log"
    file_handler = RotatingFileHandler(
        filename=log_file,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # In Windows PyInstaller --windowed builds, sys.stderr is None
    if sys.stderr is not None:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    _configured_app_id = app_id
    app_logger = logging.getLogger(app_id)
    app_logger.info("Initialized logging for %s (suite v%s)", app_id, SUITE_VERSION)
    return app_logger


def get_logger(name: str) -> logging.Logger:
    """Return a named child logger."""
    return logging.getLogger(name)


def install_exception_hooks(
    app_id: str,
    on_uncaught_error: Callable[[str, str], None] | None = None,
) -> None:
    """Install global exception hooks to log unhandled main and worker thread crashes."""
    logger = logging.getLogger(app_id)

    def _handle_sys_exception(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_traceback: object,
    ) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)  # type: ignore[arg-type]
            return

        formatted = "".join(
            traceback.format_exception(exc_type, exc_value, exc_traceback)  # type: ignore[arg-type]
        )
        logger.critical("Unhandled application exception:\n%s", formatted)

        if on_uncaught_error is not None:
            try:
                on_uncaught_error(str(exc_value) or exc_type.__name__, formatted)
            except Exception:
                logger.exception("Failed while invoking uncaught error UI callback")

    def _handle_thread_exception(args: threading.ExceptHookArgs) -> None:
        if args.exc_type is SystemExit:
            return
        thread_name = args.thread.name if args.thread else "unknown-thread"
        formatted = "".join(
            traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)
        )
        logger.critical("Unhandled exception in worker thread '%s':\n%s", thread_name, formatted)

    sys.excepthook = _handle_sys_exception
    threading.excepthook = _handle_thread_exception