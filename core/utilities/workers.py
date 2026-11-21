"""Reusable background worker and cancellation utilities built on Qt's QThreadPool."""

from __future__ import annotations

import logging
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

logger = logging.getLogger(__name__)


class TaskCancelledError(Exception):
    """Raised cooperatively inside a worker task when cancellation is requested."""


class CancellationToken:
    """Thread-safe cooperative cancellation token passed to background tasks."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        """Signal that the associated operation should abort as soon as possible."""
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        """Return True if cancellation has been requested."""
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        """Raise TaskCancelledError if cancellation was requested."""
        if self._event.is_set():
            raise TaskCancelledError("Operation was cancelled by user.")


@dataclass(frozen=True, slots=True)
class WorkerContext:
    """Execution context injected into tasks that accept a `context` keyword argument."""

    token: CancellationToken
    report_progress: Callable[[int, str], None]

    def check_cancelled(self) -> None:
        """Abort execution if the user requested cancellation."""
        self.token.raise_if_cancelled()


class WorkerSignals(QObject):
    """Qt signals emitted across thread boundaries by BackgroundWorker."""

    started = Signal()
    progress = Signal(int, str)  # (percent 0..100, status message)
    result = Signal(object)
    error = Signal(str, str)  # (short error message, full traceback)
    cancelled = Signal()
    finished = Signal()


class BackgroundWorker(QRunnable):
    """Generic QRunnable wrapper for executing any callable on QThreadPool."""

    def __init__(
        self,
        fn: Callable[..., Any],
        *args: Any,
        inject_context: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.inject_context = inject_context
        self.token = CancellationToken()
        self.signals = WorkerSignals()

    def cancel(self) -> None:
        """Request cooperative cancellation of this worker."""
        self.token.cancel()

    def _emit_progress(self, percent: int, message: str = "") -> None:
        if not self.token.is_cancelled:
            clamped = max(0, min(100, int(percent)))
            self.signals.progress.emit(clamped, message)

    def run(self) -> None:
        """Execute the target callable inside the thread pool."""
        self.signals.started.emit()
        try:
            if self.token.is_cancelled:
                self.signals.cancelled.emit()
                return

            call_kwargs = dict(self.kwargs)
            if self.inject_context:
                call_kwargs["context"] = WorkerContext(
                    token=self.token,
                    report_progress=self._emit_progress,
                )

            output = self.fn(*self.args, **call_kwargs)

            if self.token.is_cancelled:
                self.signals.cancelled.emit()
            else:
                self.signals.result.emit(output)

        except TaskCancelledError:
            logger.info("Background task '%s' cancelled.", getattr(self.fn, "__name__", "task"))
            self.signals.cancelled.emit()
        except Exception as exc:
            tb = traceback.format_exc()
            logger.error(
                "Background task '%s' failed: %s\n%s",
                getattr(self.fn, "__name__", "task"),
                exc,
                tb,
            )
            self.signals.error.emit(str(exc) or exc.__class__.__name__, tb)
        finally:
            self.signals.finished.emit()


def run_in_background(
    fn: Callable[..., Any],
    *args: Any,
    inject_context: bool = False,
    on_result: Callable[[Any], None] | None = None,
    on_error: Callable[[str, str], None] | None = None,
    on_progress: Callable[[int, str], None] | None = None,
    on_cancelled: Callable[[], None] | None = None,
    on_finished: Callable[[], None] | None = None,
    **kwargs: Any,
) -> BackgroundWorker:
    """Create, wire, and schedule a BackgroundWorker on the global QThreadPool."""
    worker = BackgroundWorker(fn, *args, inject_context=inject_context, **kwargs)
    if on_result is not None:
        worker.signals.result.connect(on_result)
    if on_error is not None:
        worker.signals.error.connect(on_error)
    if on_progress is not None:
        worker.signals.progress.connect(on_progress)
    if on_cancelled is not None:
        worker.signals.cancelled.connect(on_cancelled)
    if on_finished is not None:
        worker.signals.finished.connect(on_finished)

    QThreadPool.globalInstance().start(worker)
    return worker