"""Desktop tray notifications, audio alerts, and wall-clock reminder scheduling."""

from __future__ import annotations

import contextlib
import logging
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QStyle, QSystemTrayIcon, QWidget

from core.version import get_app_metadata

logger = logging.getLogger(__name__)


class NotificationLevel(StrEnum):
    """Severity levels for desktop and in-app notifications."""

    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


@dataclass(slots=True)
class ScheduledReminder:
    """Represents a pending wall-clock reminder."""

    reminder_id: str
    title: str
    message: str
    trigger_at: datetime
    level: NotificationLevel = NotificationLevel.INFO
    play_sound: bool = True
    callback: Callable[[ScheduledReminder], None] | None = None


class NotificationManager(QObject):
    """Dispatches Windows tray notifications, in-app signals, and scheduled reminders."""

    notification_dispatched = Signal(str, str, str)  # (title, message, level.value)
    reminder_triggered = Signal(str, str, str)  # (reminder_id, title, message)

    def __init__(
        self,
        app_id: str,
        icon: QIcon | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.app_id = app_id
        self._meta = get_app_metadata(app_id)
        self._tray_icon: QSystemTrayIcon | None = None
        self._reminders: dict[str, ScheduledReminder] = {}

        self._init_tray_icon(icon)

        # Check scheduled reminders against wall-clock time every second
        self._scheduler_timer = QTimer(self)
        self._scheduler_timer.setInterval(1000)
        self._scheduler_timer.timeout.connect(self._poll_reminders)
        self._scheduler_timer.start()

    def _init_tray_icon(self, custom_icon: QIcon | None) -> None:
        """Initialize QSystemTrayIcon if the host desktop environment supports it."""
        try:
            if not QSystemTrayIcon.isSystemTrayAvailable():
                logger.info("System tray unavailable; falling back to in-app notifications.")
                return

            resolved_icon = custom_icon
            if resolved_icon is None or resolved_icon.isNull():
                app = QApplication.instance()
                if isinstance(app, QApplication):
                    resolved_icon = app.style().standardIcon(
                        QStyle.StandardPixmap.SP_MessageBoxInformation
                    )
                else:
                    resolved_icon = QIcon()

            tray = QSystemTrayIcon(resolved_icon, self)
            tray.setToolTip(self._meta.display_name)
            tray.show()
            self._tray_icon = tray
        except Exception as exc:
            logger.warning("Could not initialize system tray icon: %s", exc)
            self._tray_icon = None

    @property
    def tray_supported(self) -> bool:
        """Return True if native system tray balloon/toast messages are available."""
        return self._tray_icon is not None and self._tray_icon.isVisible()

    def notify(
        self,
        title: str,
        message: str,
        level: NotificationLevel = NotificationLevel.INFO,
        duration_ms: int = 5000,
        play_sound: bool = False,
        desktop_banner: bool = True,
    ) -> None:
        """Emit an in-app notification signal and optionally show a Windows tray notification."""
        clean_title = title.strip() or self._meta.display_name
        clean_message = message.strip()

        logger.debug("Notification [%s] %s: %s", level.value, clean_title, clean_message)
        self.notification_dispatched.emit(clean_title, clean_message, level.value)

        if play_sound:
            self.play_alert_sound(level)

        if desktop_banner and self.tray_supported and self._tray_icon is not None:
            tray_icon_type = self._map_tray_icon(level)
            self._tray_icon.showMessage(
                clean_title,
                clean_message,
                tray_icon_type,
                max(1000, int(duration_ms)),
            )

    def _map_tray_icon(self, level: NotificationLevel) -> QSystemTrayIcon.MessageIcon:
        """Map internal NotificationLevel to QSystemTrayIcon.MessageIcon."""
        if level == NotificationLevel.WARNING:
            return QSystemTrayIcon.MessageIcon.Warning
        if level == NotificationLevel.ERROR:
            return QSystemTrayIcon.MessageIcon.Critical
        return QSystemTrayIcon.MessageIcon.Information

    def play_alert_sound(self, level: NotificationLevel = NotificationLevel.INFO) -> None:
        """Play a non-blocking Windows system sound or standard Qt beep."""
        try:
            if sys.platform == "win32":
                import winsound

                sound_flag = (
                    winsound.MB_ICONHAND
                    if level == NotificationLevel.ERROR
                    else winsound.MB_ICONEXCLAMATION
                    if level == NotificationLevel.WARNING
                    else winsound.MB_OK
                )
                winsound.MessageBeep(sound_flag)
                return
        except Exception:
            pass

        with contextlib.suppress(Exception):
            QApplication.beep()

    # ------------------------------------------------------------------
    # Wall-Clock Reminder Management
    # ------------------------------------------------------------------

    def schedule_reminder(
        self,
        title: str,
        message: str,
        trigger_at: datetime,
        reminder_id: str | None = None,
        level: NotificationLevel = NotificationLevel.INFO,
        play_sound: bool = True,
        callback: Callable[[ScheduledReminder], None] | None = None,
    ) -> str:
        """Schedule a one-shot reminder checked against system wall-clock time."""
        rid = reminder_id or uuid.uuid4().hex
        self._reminders[rid] = ScheduledReminder(
            reminder_id=rid,
            title=title,
            message=message,
            trigger_at=trigger_at,
            level=level,
            play_sound=play_sound,
            callback=callback,
        )
        return rid

    def cancel_reminder(self, reminder_id: str) -> bool:
        """Cancel and remove a scheduled reminder by ID. Returns True if found."""
        return self._reminders.pop(reminder_id, None) is not None

    def clear_reminders(self) -> None:
        """Remove all pending scheduled reminders."""
        self._reminders.clear()

    def pending_reminders(self) -> list[ScheduledReminder]:
        """Return all pending reminders sorted chronologically."""
        return sorted(self._reminders.values(), key=lambda r: r.trigger_at)

    def _poll_reminders(self) -> None:
        """Check for due reminders using timezone-aware or naive matching."""
        if not self._reminders:
            return

        due_ids: list[str] = []
        for rid, reminder in self._reminders.items():
            now = (
                datetime.now(tz=reminder.trigger_at.tzinfo)
                if reminder.trigger_at.tzinfo is not None
                else datetime.now()
            )
            if now >= reminder.trigger_at:
                due_ids.append(rid)

        for rid in due_ids:
            reminder = self._reminders.pop(rid, None)
            if reminder is None:
                continue

            self.notify(
                title=reminder.title,
                message=reminder.message,
                level=reminder.level,
                play_sound=reminder.play_sound,
                desktop_banner=True,
            )
            self.reminder_triggered.emit(reminder.reminder_id, reminder.title, reminder.message)
            if reminder.callback is not None:
                try:
                    reminder.callback(reminder)
                except Exception:
                    logger.exception("Reminder callback failed for '%s'", reminder.reminder_id)

    def shutdown(self) -> None:
        """Stop timers and hide the system tray icon cleanly on application exit."""
        self._scheduler_timer.stop()
        self._reminders.clear()
        if self._tray_icon is not None:
            self._tray_icon.hide()
            self._tray_icon.deleteLater()
            self._tray_icon = None