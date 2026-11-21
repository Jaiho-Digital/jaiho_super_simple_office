"""Standardized modal dialogs for errors, confirmations, progress, and suite info."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.utilities.paths import get_logs_dir, get_user_data_root
from core.utilities.workers import BackgroundWorker, CancellationToken
from core.version import (
    BUILD_YEAR,
    ORGANIZATION_NAME,
    ORGANIZATION_TAGLINE,
    SUITE_NAME,
    SUITE_TAGLINE,
    SUITE_VERSION,
    get_app_metadata,
)


class ErrorDialog(QDialog):
    """User-friendly error dialog with collapsible technical traceback and copy support."""

    def __init__(
        self,
        title: str,
        message: str,
        details: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(440)
        self.setMaximumWidth(680)

        self._title = title
        self._message = message
        self._details = (details or "").strip()

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)

        # Header row
        header_row = QHBoxLayout()
        header_row.setSpacing(10)

        badge = QLabel("!", self)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedSize(26, 26)
        badge.setStyleSheet(
            "background-color: #FDECEC; color: #C42B2B; font-weight: 700; "
            "border: 1px solid #C42B2B; border-radius: 13px;"
        )
        header_row.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)

        title_lbl = QLabel(self._title, self)
        title_lbl.setProperty("role", "section-header")
        title_lbl.setWordWrap(True)
        header_row.addWidget(title_lbl, 1)
        layout.addLayout(header_row)

        # Plain-language explanation
        msg_lbl = QLabel(self._message, self)
        msg_lbl.setWordWrap(True)
        msg_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(msg_lbl)

        # Expandable technical details
        self._details_box: QPlainTextEdit | None = None
        self._toggle_btn: QPushButton | None = None

        if self._details:
            self._details_box = QPlainTextEdit(self)
            self._details_box.setReadOnly(True)
            self._details_box.setPlainText(self._details)
            self._details_box.setMinimumHeight(150)
            self._details_box.setMaximumHeight(240)
            self._details_box.setVisible(False)
            layout.addWidget(self._details_box)

        # Action bar
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        if self._details:
            self._toggle_btn = QPushButton("Show Details", self)
            self._toggle_btn.setProperty("variant", "ghost")
            self._toggle_btn.clicked.connect(self._toggle_details)
            btn_row.addWidget(self._toggle_btn)

            self._copy_btn = QPushButton("Copy Details", self)
            self._copy_btn.clicked.connect(self._copy_to_clipboard)
            btn_row.addWidget(self._copy_btn)

        btn_row.addStretch(1)

        ok_btn = QPushButton("Close", self)
        ok_btn.setProperty("variant", "primary")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(ok_btn)

        layout.addLayout(btn_row)

    def _toggle_details(self) -> None:
        if self._details_box is None or self._toggle_btn is None:
            return
        expanding = not self._details_box.isVisible()
        self._details_box.setVisible(expanding)
        self._toggle_btn.setText("Hide Details" if expanding else "Show Details")
        self.adjustSize()

    def _copy_to_clipboard(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return
        report = [
            f"{SUITE_NAME} v{SUITE_VERSION}",
            f"Error: {self._title}",
            f"Message: {self._message}",
        ]
        if self._details:
            report.append("\nTechnical Details:\n" + self._details)
        clipboard.setText("\n".join(report))

        if hasattr(self, "_copy_btn"):
            self._copy_btn.setText("Copied")
            QTimer.singleShot(1800, lambda: self._copy_btn.setText("Copy Details"))


class ConfirmDialog(QDialog):
    """Confirmation prompt supporting standard and destructive action variants."""

    def __init__(
        self,
        title: str,
        message: str,
        confirm_text: str = "Confirm",
        cancel_text: str = "Cancel",
        destructive: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(400)
        self.setMaximumWidth(540)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)

        title_lbl = QLabel(title, self)
        title_lbl.setProperty("role", "section-header")
        title_lbl.setWordWrap(True)
        layout.addWidget(title_lbl)

        msg_lbl = QLabel(message, self)
        msg_lbl.setWordWrap(True)
        layout.addWidget(msg_lbl)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch(1)

        cancel_btn = QPushButton(cancel_text, self)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        confirm_btn = QPushButton(confirm_text, self)
        confirm_btn.setProperty("variant", "danger" if destructive else "primary")
        confirm_btn.setDefault(not destructive)
        confirm_btn.clicked.connect(self.accept)
        btn_row.addWidget(confirm_btn)

        layout.addLayout(btn_row)


class ProgressDialog(QDialog):
    """Progress dialog for long-running background operations with cancellation support."""

    cancelled = Signal()

    def __init__(
        self,
        title: str,
        status_text: str = "Processing...",
        cancellable: bool = True,
        indeterminate: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setMaximumWidth(540)

        self._cancellable = cancellable
        self._cancel_requested = False
        self._completed = False
        self._token: CancellationToken | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)

        title_lbl = QLabel(title, self)
        title_lbl.setProperty("role", "section-header")
        layout.addWidget(title_lbl)

        self._status_lbl = QLabel(status_text, self)
        self._status_lbl.setProperty("role", "muted")
        self._status_lbl.setWordWrap(True)
        layout.addWidget(self._status_lbl)

        self._progress_bar = QProgressBar(self)
        if indeterminate:
            self._progress_bar.setRange(0, 0)
        else:
            self._progress_bar.setRange(0, 100)
            self._progress_bar.setValue(0)
        layout.addWidget(self._progress_bar)

        self._cancel_btn: QPushButton | None = None
        if cancellable:
            btn_row = QHBoxLayout()
            btn_row.addStretch(1)
            self._cancel_btn = QPushButton("Cancel", self)
            self._cancel_btn.clicked.connect(self.request_cancel)
            btn_row.addWidget(self._cancel_btn)
            layout.addLayout(btn_row)

    def update_progress(self, percent: int, status_text: str = "") -> None:
        """Update progress bar percentage (0-100) and optional status message."""
        if self._progress_bar.maximum() == 0:
            self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(max(0, min(100, int(percent))))
        if status_text:
            self._status_lbl.setText(status_text)

    def set_indeterminate(self, status_text: str = "") -> None:
        """Switch the progress bar into busy/indeterminate animation mode."""
        self._progress_bar.setRange(0, 0)
        if status_text:
            self._status_lbl.setText(status_text)

    def bind_worker(self, worker: BackgroundWorker) -> None:
        """Connect a BackgroundWorker's progress, cancellation, and completion signals."""
        self._token = worker.token
        worker.signals.progress.connect(self.update_progress)
        worker.signals.finished.connect(self.mark_finished)

    def request_cancel(self) -> None:
        """Trigger cooperative cancellation on bound tokens and emit `cancelled`."""
        if self._cancel_requested or not self._cancellable:
            return
        self._cancel_requested = True
        self._status_lbl.setText("Cancelling operation...")
        if self._cancel_btn is not None:
            self._cancel_btn.setEnabled(False)
        if self._token is not None:
            self._token.cancel()
        self.cancelled.emit()

    def mark_finished(self) -> None:
        """Mark the background task finished and close the dialog cleanly."""
        self._completed = True
        self.accept()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Prevent closing mid-task unless completed or cancellable."""
        if self._completed:
            event.accept()
            return
        if self._cancellable:
            self.request_cancel()
        event.ignore()


class AboutDialog(QDialog):
    """Displays Jaiho Digital branding, application metadata, and local diagnostic paths."""

    def __init__(self, app_id: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._meta = get_app_metadata(app_id)
        self.setWindowTitle(f"About {self._meta.display_name}")
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setMaximumWidth(560)

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(14)

        # App title & suite badge
        app_title = QLabel(self._meta.display_name, self)
        app_title.setProperty("role", "title")
        layout.addWidget(app_title)

        version_lbl = QLabel(
            f"{SUITE_NAME}  •  Version {self._meta.version}  ({self._meta.executable})",
            self,
        )
        version_lbl.setProperty("role", "muted")
        layout.addWidget(version_lbl)

        desc_lbl = QLabel(self._meta.description, self)
        desc_lbl.setWordWrap(True)
        layout.addWidget(desc_lbl)

        # Brand identity card
        brand_card = QFrame(self)
        brand_card.setProperty("surface", "card")
        card_layout = QVBoxLayout(brand_card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(4)

        tagline_lbl = QLabel(f'"{SUITE_TAGLINE}"', brand_card)
        tagline_lbl.setStyleSheet("font-weight: 600;")
        card_layout.addWidget(tagline_lbl)

        org_lbl = QLabel(ORGANIZATION_TAGLINE, brand_card)
        org_lbl.setProperty("role", "muted")
        card_layout.addWidget(org_lbl)

        copy_lbl = QLabel(f"© {BUILD_YEAR} {ORGANIZATION_NAME}. All rights reserved.", brand_card)
        copy_lbl.setProperty("role", "caption")
        card_layout.addWidget(copy_lbl)

        layout.addWidget(brand_card)

        # Footer buttons: Open Logs, Open Data Dir, Close
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        logs_btn = QPushButton("Open Logs Folder", self)
        logs_btn.setProperty("variant", "ghost")
        logs_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(get_logs_dir())))
        )
        btn_row.addWidget(logs_btn)

        data_btn = QPushButton("Open Data Folder", self)
        data_btn.setProperty("variant", "ghost")
        data_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(get_user_data_root())))
        )
        btn_row.addWidget(data_btn)

        btn_row.addStretch(1)

        close_btn = QPushButton("Close", self)
        close_btn.setProperty("variant", "primary")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)


# ----------------------------------------------------------------------
# Convenience Helper Functions
# ----------------------------------------------------------------------


def show_error_dialog(
    parent: QWidget | None,
    title: str,
    message: str,
    details: str | None = None,
) -> None:
    """Display a modal ErrorDialog."""
    dialog = ErrorDialog(title=title, message=message, details=details, parent=parent)
    dialog.exec()


def ask_confirmation(
    parent: QWidget | None,
    title: str,
    message: str,
    confirm_text: str = "Confirm",
    cancel_text: str = "Cancel",
    destructive: bool = False,
) -> bool:
    """Display a modal ConfirmDialog and return True if the user confirmed."""
    dialog = ConfirmDialog(
        title=title,
        message=message,
        confirm_text=confirm_text,
        cancel_text=cancel_text,
        destructive=destructive,
        parent=parent,
    )
    return dialog.exec() == QDialog.DialogCode.Accepted


def show_about_dialog(parent: QWidget | None, app_id: str) -> None:
    """Display the modal AboutDialog for the specified application ID."""
    dialog = AboutDialog(app_id=app_id, parent=parent)
    dialog.exec()