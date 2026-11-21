"""Phase 1 Foundation Verification Application for Jaiho Super Simple Office."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.database.db_manager import DatabaseManager, Migration
from core.file_manager.file_ops import atomic_write_text
from core.notifications.notifier import NotificationLevel
from core.ui.base_window import BaseWindow, bootstrap_application
from core.ui.dialogs import ProgressDialog, ask_confirmation, show_error_dialog
from core.ui.widgets import (
    CardFrame,
    EmptyStateWidget,
    HorizontalDivider,
    PageHeader,
    SearchInput,
    SectionHeader,
    StatusBadge,
    make_button,
)
from core.utilities.paths import get_user_data_root
from core.utilities.workers import WorkerContext, run_in_background
from core.version import SUITE_NAME, SUITE_VERSION

logger = logging.getLogger(__name__)

APP_ID = "foundation_demo"

DEMO_MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        description="Create foundation_items table for SQLite persistence verification",
        up="""
        CREATE TABLE IF NOT EXISTS foundation_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_foundation_items_created
            ON foundation_items (created_at DESC);
        """,
    ),
)


def _simulated_batch_job(steps: int, context: WorkerContext) -> str:
    """Background worker function demonstrating progress updates and cooperative cancellation."""
    for step in range(1, steps + 1):
        context.check_cancelled()
        percent = int((step / steps) * 100)
        context.report_progress(percent, f"Verifying subsystem {step} of {steps}...")
        time.sleep(0.25)
    return f"Successfully verified {steps} background pipeline stages."


class FoundationDemoWindow(BaseWindow):
    """Interactive verification window exercising all core infrastructure modules."""

    def __init__(self) -> None:
        super().__init__(app_id=APP_ID, min_width=860, min_height=580)
        self.setWindowTitle(f"Foundation Verification — {SUITE_NAME} v{SUITE_VERSION}")

        self.db = DatabaseManager(app_id=APP_ID)
        self.db.apply_migrations(DEMO_MIGRATIONS)

        self._build_workspace()
        self._reload_records()

    def _build_workspace(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        # Top workspace header
        header = PageHeader(
            title="Phase 1: Core Infrastructure Verification",
            subtitle=(
                "Validates themes, SQLite WAL persistence, atomic file writes, "
                "non-blocking workers, and notification pipelines."
            ),
            parent=central,
        )
        theme_btn = make_button(
            text="Switch Theme",
            tooltip="Toggle between Light Mode and Dark Mode (Ctrl+Shift+T)",
            on_click=self.toggle_theme,
            parent=central,
        )
        header.add_action(theme_btn)
        root.addWidget(header)

        root.addWidget(HorizontalDivider(central))

        # Two-column operational workspace
        columns = QHBoxLayout()
        columns.setSpacing(14)

        # Left column: SQLite Persistence & Search Verification
        db_card = CardFrame(padding=16, spacing=10, parent=central)
        self._db_badge = StatusBadge("SQLite WAL Ready", variant="success", parent=db_card)
        db_card.body_layout.addWidget(
            SectionHeader("SQLite Persistence & Migrations", accessory=self._db_badge, parent=db_card)
        )

        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        self._record_input = QLineEdit(db_card)
        self._record_input.setPlaceholderText("Enter a test record title and press Enter...")
        self._record_input.returnPressed.connect(self._add_record)
        input_row.addWidget(self._record_input, 1)

        add_btn = make_button("Add Record", variant="primary", on_click=self._add_record, parent=db_card)
        input_row.addWidget(add_btn)
        db_card.body_layout.addLayout(input_row)

        self._search_input = SearchInput(
            placeholder="Filter stored SQLite records...",
            debounce_ms=120,
            parent=db_card,
        )
        self._search_input.search_changed.connect(self._reload_records)
        db_card.body_layout.addWidget(self._search_input)

        self._records_list = QListWidget(db_card)
        self._records_list.setAlternatingRowColors(True)
        db_card.body_layout.addWidget(self._records_list, 1)

        self._empty_state = EmptyStateWidget(
            title="No Records Stored Yet",
            description="Add a test item above to verify SQLite WAL transactions and restart persistence.",
            parent=db_card,
        )
        db_card.body_layout.addWidget(self._empty_state, 1)

        db_actions = QHBoxLayout()
        db_actions.setSpacing(8)
        backup_btn = make_button("Backup Database", on_click=self._backup_database, parent=db_card)
        clear_btn = make_button(
            "Clear All",
            variant="danger",
            on_click=self._clear_records,
            parent=db_card,
        )
        db_actions.addWidget(backup_btn)
        db_actions.addStretch(1)
        db_actions.addWidget(clear_btn)
        db_card.body_layout.addLayout(db_actions)

        columns.addWidget(db_card, 3)

        # Right column: Runtime Subsystems Verification
        tools_card = CardFrame(padding=16, spacing=10, parent=central)
        tools_card.body_layout.addWidget(SectionHeader("Subsystem Diagnostics", parent=tools_card))

        data_path_lbl = QLabel(f"Data Root:\n{get_user_data_root()}", tools_card)
        data_path_lbl.setProperty("role", "caption")
        data_path_lbl.setWordWrap(True)
        data_path_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        tools_card.body_layout.addWidget(data_path_lbl)

        tools_card.body_layout.addWidget(HorizontalDivider(tools_card))

        atomic_btn = make_button(
            text="Test Atomic File Write & Backup",
            tooltip="Writes a diagnostic report atomically via temp file + os.replace",
            on_click=self._test_atomic_save,
            parent=tools_card,
        )
        tools_card.body_layout.addWidget(atomic_btn)

        worker_btn = make_button(
            text="Run Background Worker (Cancellable)",
            tooltip="Runs a multi-step QRunnable task on QThreadPool with a ProgressDialog",
            on_click=self._test_background_worker,
            parent=tools_card,
        )
        tools_card.body_layout.addWidget(worker_btn)

        toast_btn = make_button(
            text="Trigger Toast & Tray Notification",
            tooltip="Tests in-app toast stack and Windows system tray notification",
            on_click=self._test_notification,
            parent=tools_card,
        )
        tools_card.body_layout.addWidget(toast_btn)

        error_btn = make_button(
            text="Preview Structured Error Dialog",
            tooltip="Opens the standardized ErrorDialog with collapsible stack details",
            on_click=self._test_error_dialog,
            parent=tools_card,
        )
        tools_card.body_layout.addWidget(error_btn)

        tools_card.body_layout.addStretch(1)

        schema_lbl = QLabel(
            f"SQLite Schema Version: v{self.db.get_schema_version()}",
            tools_card,
        )
        schema_lbl.setProperty("role", "muted")
        tools_card.body_layout.addWidget(schema_lbl)

        columns.addWidget(tools_card, 2)
        root.addLayout(columns, 1)

    # ------------------------------------------------------------------
    # SQLite Actions
    # ------------------------------------------------------------------

    def _add_record(self) -> None:
        title = self._record_input.text().strip()
        if not title:
            self.notify(
                "Validation Notice",
                "Enter a non-empty record title before adding.",
                level=NotificationLevel.WARNING,
            )
            return

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO foundation_items (title, created_at) VALUES (?, ?);",
                (title, timestamp),
            )

        self._record_input.clear()
        self._reload_records(self._search_input.text())
        self.set_status_message(f"Saved '{title}' to SQLite database", timeout_ms=3000)

    def _reload_records(self, filter_text: str = "") -> None:
        query = filter_text.strip()
        with self.db.read_only() as conn:
            if query:
                rows = conn.execute(
                    """
                    SELECT id, title, created_at
                    FROM foundation_items
                    WHERE title LIKE ?
                    ORDER BY id DESC;
                    """,
                    (f"%{query}%",),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, title, created_at FROM foundation_items ORDER BY id DESC;"
                ).fetchall()

        self._records_list.clear()
        for row in rows:
            item = QListWidgetItem(f"#{row['id']}  {row['title']}   ({row['created_at']})")
            self._records_list.addItem(item)

        has_rows = len(rows) > 0
        self._records_list.setVisible(has_rows)
        self._empty_state.setVisible(not has_rows)
        self._db_badge.set_status(f"{len(rows)} Record(s)", variant="info" if has_rows else "neutral")

    def _backup_database(self) -> None:
        backup_path = self.db.create_backup(tag="user")
        self.notify(
            "Database Backup Created",
            f"Saved hot backup to:\n{backup_path.name}",
            level=NotificationLevel.SUCCESS,
        )

    def _clear_records(self) -> None:
        confirmed = ask_confirmation(
            parent=self,
            title="Clear All Test Records?",
            message="This will permanently delete all rows from foundation_demo.db.",
            confirm_text="Delete All",
            destructive=True,
        )
        if not confirmed:
            return

        with self.db.transaction() as conn:
            conn.execute("DELETE FROM foundation_items;")

        self._reload_records()
        self.notify(
            "Database Cleared",
            "All test records were removed.",
            level=NotificationLevel.INFO,
        )

    # ------------------------------------------------------------------
    # Subsystem Verification Handlers
    # ------------------------------------------------------------------

    def _test_atomic_save(self) -> None:
        target: Path = get_user_data_root() / "foundation_diagnostic.txt"
        content = (
            f"Jaiho Super Simple Office v{SUITE_VERSION}\n"
            f"Diagnostic Timestamp: {datetime.now().isoformat()}\n"
            f"Active Theme: {self.theme_manager.current_theme}\n"
        )
        atomic_write_text(target_path=target, text=content, backup_app_id=APP_ID)
        self.notify(
            "Atomic Save Verified",
            f"Atomically wrote '{target.name}' with automatic backup rotation.",
            level=NotificationLevel.SUCCESS,
        )

    def _test_background_worker(self) -> None:
        progress = ProgressDialog(
            title="Running Background Verification",
            status_text="Starting worker thread...",
            cancellable=True,
            parent=self,
        )

        worker = run_in_background(
            _simulated_batch_job,
            8,
            inject_context=True,
            on_result=lambda msg: self.notify(
                "Background Task Complete",
                str(msg),
                level=NotificationLevel.SUCCESS,
            ),
            on_cancelled=lambda: self.notify(
                "Task Cancelled",
                "The background worker stopped cooperatively.",
                level=NotificationLevel.WARNING,
            ),
            on_error=lambda err, tb: show_error_dialog(
                self, "Worker Error", err, details=tb
            ),
        )
        progress.bind_worker(worker)
        progress.exec()

    def _test_notification(self) -> None:
        self.notify(
            title="Jaiho Notification System",
            message="In-app toast stack and system tray integration are operational.",
            level=NotificationLevel.INFO,
            desktop_banner=True,
            play_sound=True,
        )

    def _test_error_dialog(self) -> None:
        sample_trace = (
            "Traceback (most recent call last):\n"
            '  File "apps/foundation_demo/main.py", line 245, in _test_error_dialog\n'
            '    raise ValueError("Sample diagnostic validation failure")\n'
            "ValueError: Sample diagnostic validation failure"
        )
        show_error_dialog(
            parent=self,
            title="Sample Validation Error",
            message=(
                "This demonstrates how unhandled or validation errors are presented "
                "with plain-language guidance and collapsible diagnostics."
            ),
            details=sample_trace,
        )


def main() -> int:
    """Application entry point."""
    return bootstrap_application(
        app_id=APP_ID,
        window_factory=FoundationDemoWindow,
    )


if __name__ == "__main__":
    raise SystemExit(main())