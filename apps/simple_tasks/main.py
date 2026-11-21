"""SimpleTasks — Clean, tactile paper checklist for Jaiho Super Simple Office."""

from __future__ import annotations

import logging
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from apps.simple_tasks.repository import (
    APP_ID,
    TaskPriority,
    TaskRecord,
    TasksRepository,
)
from core.ui.base_window import BaseWindow, bootstrap_application
from core.ui.widgets import CardFrame, HorizontalDivider, make_button

logger = logging.getLogger(__name__)


class SimpleTasksWindow(BaseWindow):
    """Streamlined single-card task checklist with Today, Important, and Done filters."""

    def __init__(self, repository: TasksRepository | None = None) -> None:
        super().__init__(app_id=APP_ID, min_width=440, min_height=540)
        self.repo = repository or TasksRepository()

        if self.settings.get("window_width") is None:
            self.resize(480, 620)

        self._active_view: str = "all"  # 'all' | 'today' | 'high' | 'completed'
        self._is_rendering: bool = False

        self._build_workspace()
        self._bind_shortcuts()
        self._refresh_all()

    def _build_workspace(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(20, 16, 20, 14)
        root.setSpacing(12)

        # --- Top Filter Tabs ---
        tab_row = QHBoxLayout()
        tab_row.setSpacing(6)

        self._tab_group = QButtonGroup(self)
        self._tab_group.setExclusive(True)

        self._tab_buttons: dict[str, QPushButton] = {}
        tabs: tuple[tuple[str, str], ...] = (
            ("all", "All"),
            ("today", "Today"),
            ("high", "Important"),
            ("completed", "Done"),
        )
        for view_id, label in tabs:
            btn = QPushButton(label, central)
            btn.setProperty("variant", "segmented")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, v=view_id: self._select_view(v))
            self._tab_group.addButton(btn)
            tab_row.addWidget(btn, 1)
            self._tab_buttons[view_id] = btn

        self._tab_buttons["all"].setChecked(True)
        root.addLayout(tab_row)

        # --- Single Archival Paper Checklist Card ---
        card = CardFrame(padding=16, spacing=12, parent=central)

        # Quick-Add Bar
        add_row = QHBoxLayout()
        add_row.setSpacing(6)

        self._task_input = QLineEdit(card)
        self._task_input.setPlaceholderText("Add a task and press Enter...")
        self._task_input.returnPressed.connect(self._add_task)
        add_row.addWidget(self._task_input, 1)

        self._today_pill = QPushButton("Today", card)
        self._today_pill.setProperty("variant", "segmented")
        self._today_pill.setCheckable(True)
        self._today_pill.setToolTip("Set due date to today")
        self._today_pill.setCursor(Qt.CursorShape.PointingHandCursor)
        self._today_pill.setStyleSheet("padding: 5px 9px; font-size: 12px;")
        add_row.addWidget(self._today_pill)

        self._star_pill = QPushButton("★", card)
        self._star_pill.setProperty("variant", "segmented")
        self._star_pill.setCheckable(True)
        self._star_pill.setToolTip("Mark as important")
        self._star_pill.setCursor(Qt.CursorShape.PointingHandCursor)
        self._star_pill.setStyleSheet("padding: 5px 9px; font-size: 13px;")
        add_row.addWidget(self._star_pill)

        add_btn = make_button(
            text="Add",
            variant="primary",
            tooltip="Add task (Enter)",
            on_click=self._add_task,
            parent=card,
        )
        add_row.addWidget(add_btn)
        card.body_layout.addLayout(add_row)

        card.body_layout.addWidget(HorizontalDivider(card))

        # Checkable Task List
        self._tasks_list = QListWidget(card)
        self._tasks_list.setAlternatingRowColors(True)
        self._tasks_list.setWordWrap(True)
        self._tasks_list.itemChanged.connect(self._on_item_checked)
        self._tasks_list.itemDoubleClicked.connect(self._rename_task_item)
        self._tasks_list.currentItemChanged.connect(lambda *_: self._sync_footer_actions())
        card.body_layout.addWidget(self._tasks_list, 1)

        self._empty_lbl = QLabel(
            "Nothing here yet.\nType a task above and press Enter.",
            card,
        )
        self._empty_lbl.setProperty("role", "muted")
        self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_lbl.setWordWrap(True)
        card.body_layout.addWidget(self._empty_lbl, 1)

        card.body_layout.addWidget(HorizontalDivider(card))

        # Quiet Footer Actions
        footer_row = QHBoxLayout()
        footer_row.setSpacing(6)

        self._remaining_lbl = QLabel("0 remaining", card)
        self._remaining_lbl.setProperty("role", "caption")
        footer_row.addWidget(self._remaining_lbl)

        footer_row.addStretch(1)

        self._toggle_star_btn = make_button(
            text="★ Star",
            variant="ghost",
            tooltip="Toggle important star on selected task",
            on_click=self._toggle_selected_star,
            parent=card,
        )
        self._toggle_star_btn.setStyleSheet("padding: 3px 8px; font-size: 12px;")
        footer_row.addWidget(self._toggle_star_btn)

        self._delete_btn = make_button(
            text="Remove",
            variant="ghost",
            tooltip="Remove selected task (Delete)",
            on_click=self._delete_selected_task,
            parent=card,
        )
        self._delete_btn.setStyleSheet("padding: 3px 8px; font-size: 12px;")
        footer_row.addWidget(self._delete_btn)

        self._clear_done_btn = make_button(
            text="Clear Done",
            variant="ghost",
            tooltip="Remove all completed tasks",
            on_click=self._clear_done_tasks,
            parent=card,
        )
        self._clear_done_btn.setStyleSheet("padding: 3px 8px; font-size: 12px;")
        footer_row.addWidget(self._clear_done_btn)

        card.body_layout.addLayout(footer_row)
        root.addWidget(card, 1)

    def _bind_shortcuts(self) -> None:
        QShortcut(QKeySequence.StandardKey.New, self).activated.connect(self._task_input.setFocus)
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self).activated.connect(
            self._delete_selected_if_unfocused
        )

    def _delete_selected_if_unfocused(self) -> None:
        if not self._task_input.hasFocus():
            self._delete_selected_task()

    # ------------------------------------------------------------------
    # View Switching & Task Actions
    # ------------------------------------------------------------------

    def _select_view(self, view_id: str) -> None:
        self._active_view = view_id
        self._tab_buttons[view_id].setChecked(True)
        self._refresh_all()

    def _add_task(self) -> None:
        title = self._task_input.text().strip()
        if not title:
            return

        is_high = self._star_pill.isChecked() or self._active_view == "high"
        priority = TaskPriority.HIGH if is_high else TaskPriority.NORMAL
        due = date.today() if (self._today_pill.isChecked() or self._active_view == "today") else None

        self.repo.create_task(title=title, priority=priority, due_date=due)

        self._task_input.clear()
        self._today_pill.setChecked(False)
        self._star_pill.setChecked(False)

        if self._active_view == "completed":
            self._active_view = "all"
            self._tab_buttons["all"].setChecked(True)

        self._refresh_all()
        self.set_status_message("Task added", timeout_ms=1800)

    def _format_row(self, task: TaskRecord) -> str:
        tags: list[str] = []
        if task.priority == TaskPriority.HIGH:
            tags.append("★")
        if task.is_overdue():
            tags.append("Overdue")
        elif task.is_due_today():
            tags.append("Today")
        elif task.due_date:
            tags.append(task.due_date)

        suffix = f"   ({' • '.join(tags)})" if tags else ""
        return f"{task.title}{suffix}"

    def _refresh_all(self) -> None:
        counts = self.repo.get_summary_counts()
        self._tab_buttons["all"].setText(f"All ({counts['all']})")
        self._tab_buttons["today"].setText(f"Today ({counts['today']})")
        self._tab_buttons["high"].setText(f"Important ({counts['high']})")
        self._tab_buttons["completed"].setText(f"Done ({counts['completed']})")

        rem_word = "task" if counts["all"] == 1 else "tasks"
        self._remaining_lbl.setText(f"{counts['all']} {rem_word} remaining")
        self._clear_done_btn.setVisible(counts["completed"] > 0)

        tasks = self.repo.list_tasks(smart_view=self._active_view)

        self._is_rendering = True
        self._tasks_list.blockSignals(True)
        try:
            self._tasks_list.clear()
            for task in tasks:
                item = QListWidgetItem(self._format_row(task))
                item.setData(Qt.ItemDataRole.UserRole, task.task_id)
                item.setFlags(
                    item.flags()
                    | Qt.ItemFlag.ItemIsUserCheckable
                    | Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsEnabled
                )
                item.setCheckState(
                    Qt.CheckState.Checked if task.is_completed else Qt.CheckState.Unchecked
                )
                font = item.font()
                font.setStrikeOut(task.is_completed)
                item.setFont(font)
                self._tasks_list.addItem(item)
        finally:
            self._tasks_list.blockSignals(False)
            self._is_rendering = False

        has_items = len(tasks) > 0
        self._tasks_list.setVisible(has_items)
        self._empty_lbl.setVisible(not has_items)
        self._sync_footer_actions()

    def _sync_footer_actions(self) -> None:
        has_selection = self._tasks_list.currentItem() is not None
        self._toggle_star_btn.setEnabled(has_selection)
        self._delete_btn.setEnabled(has_selection)

    def _on_item_checked(self, item: QListWidgetItem) -> None:
        if self._is_rendering:
            return
        task_id = int(item.data(Qt.ItemDataRole.UserRole))
        checked = item.checkState() == Qt.CheckState.Checked
        self.repo.update_task(task_id, is_completed=checked)
        self._refresh_all()

    def _rename_task_item(self, item: QListWidgetItem) -> None:
        task_id = int(item.data(Qt.ItemDataRole.UserRole))
        task = self.repo.get_task(task_id)
        if task is None:
            return

        new_title, ok = QInputDialog.getText(
            self,
            "Edit Task",
            "Task:",
            text=task.title,
        )
        if ok and new_title.strip():
            self.repo.update_task(task_id, title=new_title.strip())
            self._refresh_all()

    def _toggle_selected_star(self) -> None:
        item = self._tasks_list.currentItem()
        if item is None:
            return
        task_id = int(item.data(Qt.ItemDataRole.UserRole))
        task = self.repo.get_task(task_id)
        if task is None:
            return
        next_prio = (
            TaskPriority.NORMAL if task.priority == TaskPriority.HIGH else TaskPriority.HIGH
        )
        self.repo.update_task(task_id, priority=next_prio)
        self._refresh_all()

    def _delete_selected_task(self) -> None:
        item = self._tasks_list.currentItem()
        if item is None:
            return
        task_id = int(item.data(Qt.ItemDataRole.UserRole))
        self.repo.delete_task(task_id)
        self._refresh_all()
        self.set_status_message("Task removed", timeout_ms=1800)

    def _clear_done_tasks(self) -> None:
        removed = self.repo.clear_completed_tasks()
        self._refresh_all()
        self.set_status_message(f"Cleared {removed} completed", timeout_ms=1800)


def main() -> int:
    """Application entry point for SimpleTasks.exe."""
    return bootstrap_application(
        app_id=APP_ID,
        window_factory=SimpleTasksWindow,
    )


if __name__ == "__main__":
    raise SystemExit(main())