"""SimpleNotes — Distraction-free archival Markdown notebook for Jaiho Super Simple Office."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from apps.simple_notes.repository import (
    APP_ID,
    NoteRecord,
    NotesRepository,
    normalize_tags,
)
from core.file_manager.file_ops import FileOperationError, atomic_write_text, sanitize_filename
from core.notifications.notifier import NotificationLevel
from core.ui.base_window import BaseWindow, bootstrap_application
from core.ui.dialogs import ask_confirmation, show_error_dialog
from core.ui.widgets import (
    CardFrame,
    EmptyStateWidget,
    HorizontalDivider,
    SearchInput,
    SectionHeader,
    StatusBadge,
    make_button,
)

logger = logging.getLogger(__name__)

_VIEW_MODES: tuple[str, ...] = ("write", "split", "preview")


class SimpleNotesWindow(BaseWindow):
    """Three-pane Markdown notebook with FTS5 search, tag filtering, and live preview."""

    def __init__(self, repository: NotesRepository | None = None) -> None:
        super().__init__(app_id=APP_ID, min_width=980, min_height=640)
        self.repo = repository or NotesRepository()
        self.repo.seed_welcome_note_if_empty()

        self._active_collection: str = "all"  # 'all' | 'pinned' | 'archive'
        self._active_tag: str = ""
        self._current_note_id: int | None = None
        self._is_loading_note: bool = False
        self._has_unsaved_changes: bool = False

        saved_view = str(self.settings.get("view_mode", "split")).lower()
        self._view_mode: str = saved_view if saved_view in _VIEW_MODES else "split"

        # Debounced auto-save timer (350ms after last keystroke)
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(350)
        self._autosave_timer.timeout.connect(self._flush_save)

        self._build_workspace()
        self._bind_shortcuts()
        self._apply_preview_stylesheet()
        self._set_view_mode(self._view_mode, persist=False)

        self._reload_sidebar_counts_and_tags()
        self._reload_notes_list(select_first_if_missing=True)

    def _build_workspace(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        self._main_splitter = QSplitter(Qt.Orientation.Horizontal, central)
        self._main_splitter.setChildrenCollapsible(False)

        # ==============================================================
        # Pane 1: Collections & Tags Rail
        # ==============================================================
        rail_frame = QFrame(self._main_splitter)
        rail_frame.setProperty("surface", "card")
        rail_frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        rail_frame.setMinimumWidth(185)
        rail_frame.setMaximumWidth(250)

        rail_layout = QVBoxLayout(rail_frame)
        rail_layout.setContentsMargins(10, 12, 10, 12)
        rail_layout.setSpacing(8)

        new_note_btn = make_button(
            text="+ New Note",
            variant="primary",
            tooltip="Create a new note (Ctrl+N)",
            on_click=self._create_new_note,
            parent=rail_frame,
        )
        rail_layout.addWidget(new_note_btn)

        rail_layout.addSpacing(4)
        rail_layout.addWidget(SectionHeader("Library", parent=rail_frame))

        self._collections_list = QListWidget(rail_frame)
        self._collections_list.setMaximumHeight(124)
        self._collections_list.currentItemChanged.connect(self._on_collection_changed)
        rail_layout.addWidget(self._collections_list)

        rail_layout.addWidget(HorizontalDivider(rail_frame))

        clear_tag_btn = make_button(
            text="All",
            variant="ghost",
            tooltip="Clear active tag filter",
            on_click=self._clear_tag_filter,
            parent=rail_frame,
        )
        rail_layout.addWidget(SectionHeader("Tags", accessory=clear_tag_btn, parent=rail_frame))

        self._tags_list = QListWidget(rail_frame)
        self._tags_list.itemClicked.connect(self._on_tag_item_clicked)
        rail_layout.addWidget(self._tags_list, 1)

        self._no_tags_lbl = QLabel("Add comma-separated tags to organize notes.", rail_frame)
        self._no_tags_lbl.setProperty("role", "caption")
        self._no_tags_lbl.setWordWrap(True)
        rail_layout.addWidget(self._no_tags_lbl)

        self._main_splitter.addWidget(rail_frame)

        # ==============================================================
        # Pane 2: Note Index & Full-Text Search Column
        # ==============================================================
        index_frame = QFrame(self._main_splitter)
        index_frame.setProperty("surface", "card")
        index_frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        index_frame.setMinimumWidth(240)
        index_frame.setMaximumWidth(350)

        index_layout = QVBoxLayout(index_frame)
        index_layout.setContentsMargins(10, 12, 10, 12)
        index_layout.setSpacing(8)

        self._index_count_badge = StatusBadge("0", variant="neutral", parent=index_frame)
        self._index_header = SectionHeader(
            "All Notes",
            accessory=self._index_count_badge,
            parent=index_frame,
        )
        index_layout.addWidget(self._index_header)

        self._search_input = SearchInput(
            placeholder="Search titles, text, tags...",
            debounce_ms=140,
            parent=index_frame,
        )
        self._search_input.search_changed.connect(
            lambda _q: self._reload_notes_list(select_first_if_missing=True)
        )
        index_layout.addWidget(self._search_input)

        self._notes_list = QListWidget(index_frame)
        self._notes_list.setAlternatingRowColors(True)
        self._notes_list.setWordWrap(True)
        self._notes_list.currentItemChanged.connect(self._on_note_selection_changed)
        index_layout.addWidget(self._notes_list, 1)

        self._index_empty_lbl = QLabel(
            "No matching notes.\nClick '+ New Note' to start writing.",
            index_frame,
        )
        self._index_empty_lbl.setProperty("role", "muted")
        self._index_empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._index_empty_lbl.setWordWrap(True)
        index_layout.addWidget(self._index_empty_lbl, 1)

        self._main_splitter.addWidget(index_frame)

        # ==============================================================
        # Pane 3: Archival Paper Editor & Live Markdown Preview
        # ==============================================================
        editor_container = QWidget(self._main_splitter)
        editor_col = QVBoxLayout(editor_container)
        editor_col.setContentsMargins(6, 0, 0, 0)
        editor_col.setSpacing(8)

        # --- Top Action & View-Mode Strip ---
        action_bar = QHBoxLayout()
        action_bar.setSpacing(6)

        self._view_group = QButtonGroup(self)
        self._view_group.setExclusive(True)

        self._write_mode_btn = QPushButton("Write", editor_container)
        self._write_mode_btn.setProperty("variant", "segmented")
        self._write_mode_btn.setCheckable(True)
        self._write_mode_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._write_mode_btn.clicked.connect(lambda: self._set_view_mode("write"))

        self._split_mode_btn = QPushButton("Split", editor_container)
        self._split_mode_btn.setProperty("variant", "segmented")
        self._split_mode_btn.setCheckable(True)
        self._split_mode_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._split_mode_btn.clicked.connect(lambda: self._set_view_mode("split"))

        self._preview_mode_btn = QPushButton("Preview", editor_container)
        self._preview_mode_btn.setProperty("variant", "segmented")
        self._preview_mode_btn.setCheckable(True)
        self._preview_mode_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._preview_mode_btn.clicked.connect(lambda: self._set_view_mode("preview"))

        for btn in (self._write_mode_btn, self._split_mode_btn, self._preview_mode_btn):
            self._view_group.addButton(btn)
            action_bar.addWidget(btn)

        action_bar.addStretch(1)

        self._pin_btn = make_button(
            text="Pin",
            tooltip="Pin or unpin note (Ctrl+Shift+P)",
            on_click=self._toggle_pin_current,
            parent=editor_container,
        )
        action_bar.addWidget(self._pin_btn)

        self._archive_btn = make_button(
            text="Archive",
            tooltip="Move note to Archive or restore to All Notes",
            on_click=self._toggle_archive_current,
            parent=editor_container,
        )
        action_bar.addWidget(self._archive_btn)

        self._export_btn = make_button(
            text="Export",
            tooltip="Export note as Markdown (.md) or Plain Text (.txt)",
            on_click=self._export_current_note,
            parent=editor_container,
        )
        action_bar.addWidget(self._export_btn)

        self._delete_btn = make_button(
            text="Delete",
            variant="danger",
            tooltip="Permanently delete this note",
            on_click=self._delete_current_note,
            parent=editor_container,
        )
        action_bar.addWidget(self._delete_btn)

        editor_col.addLayout(action_bar)

        # --- Empty Editor Placeholder (shown only when no note is selected) ---
        self._no_selection_state = EmptyStateWidget(
            title="Select or Create a Note",
            description="Choose an existing note from the index or create a fresh page to start writing.",
            action_text="+ Create Note",
            parent=editor_container,
        )
        self._no_selection_state.action_triggered.connect(self._create_new_note)
        editor_col.addWidget(self._no_selection_state, 1)

        # --- Archival Paper Sheet Card ---
        self._sheet_card = CardFrame(padding=16, spacing=10, parent=editor_container)

        # Document Title Input
        self._title_input = QLineEdit(self._sheet_card)
        self._title_input.setPlaceholderText("Note title...")
        self._title_input.setStyleSheet(
            "QLineEdit { border: none; background: transparent; font-size: 21px; font-weight: 600; padding: 2px 0px; }"
        )
        self._title_input.textEdited.connect(self._on_note_edited)
        self._sheet_card.body_layout.addWidget(self._title_input)

        # Tag Input Row
        tag_row = QHBoxLayout()
        tag_row.setSpacing(6)
        tag_prefix = QLabel("Tags:", self._sheet_card)
        tag_prefix.setProperty("role", "caption")
        tag_row.addWidget(tag_prefix)

        self._tags_input = QLineEdit(self._sheet_card)
        self._tags_input.setPlaceholderText("Add tags separated by commas (e.g., work, ideas, meeting)...")
        self._tags_input.setStyleSheet(
            "QLineEdit { border: none; background: transparent; font-size: 12px; padding: 2px 0px; }"
        )
        self._tags_input.textEdited.connect(self._on_note_edited)
        tag_row.addWidget(self._tags_input, 1)
        self._sheet_card.body_layout.addLayout(tag_row)

        self._sheet_card.body_layout.addWidget(HorizontalDivider(self._sheet_card))

        # Compact Markdown Formatting Toolbar
        fmt_row = QHBoxLayout()
        fmt_row.setSpacing(4)
        fmt_specs: tuple[tuple[str, str, str], ...] = (
            ("H1", "Heading 1", "prefix:# "),
            ("H2", "Heading 2", "prefix:## "),
            ("Bold", "Bold text (Ctrl+B)", "wrap:**"),
            ("Italic", "Italic text (Ctrl+I)", "wrap:*"),
            ("Code", "Inline code", "wrap:`"),
            ("List", "Bullet list item", "prefix:- "),
            ("Task", "Checklist item", "prefix:- [ ] "),
            ("Quote", "Blockquote", "prefix:> "),
        )
        for label, tip, cmd in fmt_specs:
            fbtn = make_button(
                text=label,
                variant="ghost",
                tooltip=tip,
                on_click=lambda _=False, c=cmd: self._apply_markdown_format(c),
                parent=self._sheet_card,
            )
            fbtn.setStyleSheet("padding: 3px 8px; font-size: 12px;")
            fmt_row.addWidget(fbtn)
        fmt_row.addStretch(1)
        self._sheet_card.body_layout.addLayout(fmt_row)

        # Write + Live Preview Split Workspace
        self._editor_splitter = QSplitter(Qt.Orientation.Horizontal, self._sheet_card)
        self._editor_splitter.setChildrenCollapsible(False)

        self._editor = QPlainTextEdit(self._editor_splitter)
        self._editor.setPlaceholderText("Write your thoughts in Markdown or plain text...")
        self._editor.setStyleSheet(
            "QPlainTextEdit { border: none; background: transparent; font-size: 14px; line-height: 1.55; padding: 4px 2px; }"
        )
        self._editor.textChanged.connect(self._on_body_text_changed)
        self._editor_splitter.addWidget(self._editor)

        self._preview_browser = QTextBrowser(self._editor_splitter)
        self._preview_browser.setOpenExternalLinks(True)
        self._preview_browser.setStyleSheet(
            "QTextBrowser { border: none; background: transparent; padding: 4px 8px; }"
        )
        self._editor_splitter.addWidget(self._preview_browser)
        self._editor_splitter.setSizes([400, 400])

        self._sheet_card.body_layout.addWidget(self._editor_splitter, 1)

        self._sheet_card.body_layout.addWidget(HorizontalDivider(self._sheet_card))

        # Footer Telemetry Bar
        footer_row = QHBoxLayout()
        footer_row.setSpacing(10)

        self._save_badge = StatusBadge("Saved", variant="success", parent=self._sheet_card)
        footer_row.addWidget(self._save_badge)

        self._stats_lbl = QLabel("0 words  •  0 chars  •  0 min read", self._sheet_card)
        self._stats_lbl.setProperty("role", "caption")
        footer_row.addWidget(self._stats_lbl)

        footer_row.addStretch(1)

        self._updated_lbl = QLabel("", self._sheet_card)
        self._updated_lbl.setProperty("role", "caption")
        footer_row.addWidget(self._updated_lbl)

        self._sheet_card.body_layout.addLayout(footer_row)
        editor_col.addWidget(self._sheet_card, 1)

        self._main_splitter.addWidget(editor_container)
        self._main_splitter.setSizes([205, 285, 620])
        root.addWidget(self._main_splitter, 1)

    def _bind_shortcuts(self) -> None:
        QShortcut(QKeySequence.StandardKey.New, self).activated.connect(self._create_new_note)
        QShortcut(QKeySequence.StandardKey.Find, self).activated.connect(self._search_input.setFocus)
        QShortcut(QKeySequence.StandardKey.Save, self).activated.connect(self._manual_save)
        QShortcut(QKeySequence("Ctrl+E"), self).activated.connect(self._cycle_view_mode)
        QShortcut(QKeySequence("Ctrl+Shift+P"), self).activated.connect(self._toggle_pin_current)
        QShortcut(QKeySequence("Ctrl+B"), self).activated.connect(
            lambda: self._apply_markdown_format("wrap:**")
        )
        QShortcut(QKeySequence("Ctrl+I"), self).activated.connect(
            lambda: self._apply_markdown_format("wrap:*")
        )

    # ------------------------------------------------------------------
    # Collections, Tags & Index Filtering
    # ------------------------------------------------------------------

    def _reload_sidebar_counts_and_tags(self) -> None:
        counts = self.repo.get_counts()
        collections: tuple[tuple[str, str], ...] = (
            ("all", f"All Notes  ({counts['active']})"),
            ("pinned", f"Pinned  ({counts['pinned']})"),
            ("archive", f"Archive  ({counts['archived']})"),
        )

        self._collections_list.blockSignals(True)
        try:
            self._collections_list.clear()
            selected_row = 0
            for idx, (col_id, label) in enumerate(collections):
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, col_id)
                self._collections_list.addItem(item)
                if col_id == self._active_collection:
                    selected_row = idx
            self._collections_list.setCurrentRow(selected_row)
        finally:
            self._collections_list.blockSignals(False)

        # Refresh tag cloud
        tag_counts = self.repo.list_all_tags(include_archived=(self._active_collection == "archive"))
        self._tags_list.clear()
        for tag_name, count in tag_counts:
            item = QListWidgetItem(f"#{tag_name}  ({count})")
            item.setData(Qt.ItemDataRole.UserRole, tag_name)
            self._tags_list.addItem(item)
            if tag_name == self._active_tag:
                self._tags_list.setCurrentItem(item)

        has_tags = len(tag_counts) > 0
        self._tags_list.setVisible(has_tags)
        self._no_tags_lbl.setVisible(not has_tags)

    def _on_collection_changed(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        if current is None:
            return
        col_id = str(current.data(Qt.ItemDataRole.UserRole))
        if col_id == self._active_collection:
            return

        self._flush_save()
        self._active_collection = col_id
        self._reload_sidebar_counts_and_tags()
        self._reload_notes_list(select_first_if_missing=True)

    def _on_tag_item_clicked(self, item: QListWidgetItem) -> None:
        clicked_tag = str(item.data(Qt.ItemDataRole.UserRole) or "")
        self._flush_save()
        if clicked_tag == self._active_tag:
            self._active_tag = ""
            self._tags_list.clearSelection()
        else:
            self._active_tag = clicked_tag
        self._reload_notes_list(select_first_if_missing=True)

    def _clear_tag_filter(self) -> None:
        if not self._active_tag:
            return
        self._flush_save()
        self._active_tag = ""
        self._tags_list.clearSelection()
        self._reload_notes_list(select_first_if_missing=True)

    def _format_note_list_label(self, note: NoteRecord) -> str:
        pin_prefix = "★ " if note.is_pinned else ""
        date_short = note.updated_at[:10]
        return f"{pin_prefix}{note.title}\n{note.preview_excerpt}  •  {date_short}"

    def _reload_notes_list(self, select_first_if_missing: bool = False) -> None:
        notes = self.repo.list_notes(
            search_query=self._search_input.text(),
            tag_filter=self._active_tag,
            only_archived=(self._active_collection == "archive"),
            only_pinned=(self._active_collection == "pinned"),
        )

        # Update index header title & badge
        title_map = {
            "all": "All Notes",
            "pinned": "Pinned Notes",
            "archive": "Archived Notes",
        }
        base_heading = title_map.get(self._active_collection, "All Notes")
        if self._active_tag:
            base_heading = f"{base_heading}  •  #{self._active_tag}"
        self._index_header.set_title(base_heading)
        self._index_count_badge.set_status(str(len(notes)), variant="info" if notes else "neutral")

        self._notes_list.blockSignals(True)
        target_row = -1
        try:
            self._notes_list.clear()
            for idx, note in enumerate(notes):
                item = QListWidgetItem(self._format_note_list_label(note))
                item.setData(Qt.ItemDataRole.UserRole, note.note_id)
                self._notes_list.addItem(item)
                if note.note_id == self._current_note_id:
                    target_row = idx

            if target_row == -1 and select_first_if_missing and notes:
                target_row = 0

            if target_row >= 0:
                self._notes_list.setCurrentRow(target_row)
        finally:
            self._notes_list.blockSignals(False)

        has_notes = len(notes) > 0
        self._notes_list.setVisible(has_notes)
        self._index_empty_lbl.setVisible(not has_notes)

        if target_row >= 0:
            selected_item = self._notes_list.item(target_row)
            if selected_item is not None:
                note_id = int(selected_item.data(Qt.ItemDataRole.UserRole))
                if note_id != self._current_note_id:
                    self._load_note_into_editor(note_id)
        else:
            self._current_note_id = None
            self._sync_editor_visibility()

    def _on_note_selection_changed(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        if current is None:
            return
        next_id = int(current.data(Qt.ItemDataRole.UserRole))
        if next_id == self._current_note_id:
            return

        self._flush_save()
        self._load_note_into_editor(next_id)

    # ------------------------------------------------------------------
    # Editor Loading, Auto-Save & Note Actions
    # ------------------------------------------------------------------

    def _sync_editor_visibility(self) -> None:
        has_active = self._current_note_id is not None
        self._sheet_card.setVisible(has_active)
        self._no_selection_state.setVisible(not has_active)
        for btn in (self._pin_btn, self._archive_btn, self._export_btn, self._delete_btn):
            btn.setEnabled(has_active)

    def _load_note_into_editor(self, note_id: int) -> None:
        note = self.repo.get_note(note_id)
        if note is None:
            self._current_note_id = None
            self._sync_editor_visibility()
            return

        self._is_loading_note = True
        try:
            self._current_note_id = note.note_id
            self._has_unsaved_changes = False
            self._title_input.setText(note.title)
            self._tags_input.setText(", ".join(note.tags))
            self._editor.setPlainText(note.content)
            self._update_preview_content()
            self._update_note_telemetry(note)
            self._save_badge.set_status("Saved", variant="success")
        finally:
            self._is_loading_note = False

        self._sync_editor_visibility()

    def _update_note_telemetry(self, note: NoteRecord) -> None:
        self._pin_btn.setText("Unpin" if note.is_pinned else "Pin")
        self._archive_btn.setText("Restore" if note.is_archived else "Archive")
        self._stats_lbl.setText(
            f"{note.word_count} words  •  {note.char_count} chars  •  {note.reading_time_minutes} min read"
        )
        self._updated_lbl.setText(f"Updated {note.updated_at}")

    def _on_note_edited(self, _text: str) -> None:
        if self._is_loading_note or self._current_note_id is None:
            return
        self._has_unsaved_changes = True
        self._save_badge.set_status("Saving...", variant="warning")
        self._autosave_timer.start()

    def _on_body_text_changed(self) -> None:
        if self._is_loading_note or self._current_note_id is None:
            return
        self._update_preview_content()
        self._has_unsaved_changes = True
        self._save_badge.set_status("Saving...", variant="warning")
        self._autosave_timer.start()

    def _manual_save(self) -> None:
        self._flush_save()
        if self._current_note_id is not None:
            self.set_status_message("Note saved", timeout_ms=2000)

    def _flush_save(self) -> NoteRecord | None:
        """Persist any pending edits immediately without disrupting editor cursor position."""
        if self._autosave_timer.isActive():
            self._autosave_timer.stop()

        if self._current_note_id is None or not self._has_unsaved_changes:
            return (
                self.repo.get_note(self._current_note_id)
                if self._current_note_id is not None
                else None
            )

        updated = self.repo.update_note(
            self._current_note_id,
            title=self._title_input.text(),
            content=self._editor.toPlainText(),
            tags=normalize_tags(self._tags_input.text()),
        )
        self._has_unsaved_changes = False
        self._save_badge.set_status("Saved", variant="success")
        self._update_note_telemetry(updated)

        # Update the active list item label in place so typing cursor never jumps
        for row in range(self._notes_list.count()):
            item = self._notes_list.item(row)
            if item is not None and int(item.data(Qt.ItemDataRole.UserRole)) == updated.note_id:
                item.setText(self._format_note_list_label(updated))
                break

        self._reload_sidebar_counts_and_tags()
        return updated

    def _create_new_note(self) -> None:
        self._flush_save()
        if self._active_collection == "archive":
            self._active_collection = "all"

        initial_tags = (self._active_tag,) if self._active_tag else ()
        created = self.repo.create_note(
            title="Untitled Note",
            content="",
            tags=initial_tags,
            is_pinned=(self._active_collection == "pinned"),
        )
        self._current_note_id = created.note_id
        self._reload_sidebar_counts_and_tags()
        self._reload_notes_list(select_first_if_missing=False)
        self._load_note_into_editor(created.note_id)

        self._title_input.setFocus()
        self._title_input.selectAll()
        self.set_status_message("Created new note", timeout_ms=2000)

    def _toggle_pin_current(self) -> None:
        if self._current_note_id is None:
            return
        self._flush_save()
        updated = self.repo.toggle_pin(self._current_note_id)
        self._reload_sidebar_counts_and_tags()
        self._reload_notes_list(select_first_if_missing=True)
        if self._current_note_id == updated.note_id:
            self._update_note_telemetry(updated)
        self.set_status_message(
            "Pinned note to top" if updated.is_pinned else "Unpinned note",
            timeout_ms=2200,
        )

    def _toggle_archive_current(self) -> None:
        if self._current_note_id is None:
            return
        self._flush_save()
        updated = self.repo.toggle_archive(self._current_note_id)
        self._current_note_id = None
        self._reload_sidebar_counts_and_tags()
        self._reload_notes_list(select_first_if_missing=True)
        self.notify(
            "Note Archived" if updated.is_archived else "Note Restored",
            f"'{updated.title}' moved to {'Archive' if updated.is_archived else 'All Notes'}.",
            level=NotificationLevel.INFO,
        )

    def _delete_current_note(self) -> None:
        if self._current_note_id is None:
            return
        self._flush_save()
        note = self.repo.get_note(self._current_note_id)
        if note is None:
            return

        confirmed = ask_confirmation(
            parent=self,
            title="Delete Note Permanently?",
            message=f"Are you sure you want to permanently delete '{note.title}'? This cannot be undone.",
            confirm_text="Delete Note",
            destructive=True,
        )
        if not confirmed:
            return

        self.repo.delete_note(note.note_id)
        self._current_note_id = None
        self._reload_sidebar_counts_and_tags()
        self._reload_notes_list(select_first_if_missing=True)
        self.notify(
            "Note Deleted",
            f"Deleted '{note.title}'.",
            level=NotificationLevel.INFO,
        )

    def _export_current_note(self) -> None:
        if self._current_note_id is None:
            return
        note = self._flush_save()
        if note is None:
            return

        suggested_stem = sanitize_filename(note.title) or "note"
        default_path = str(Path.home() / "Documents" / f"{suggested_stem}.md")
        selected_path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export Note",
            default_path,
            "Markdown Document (*.md);;Plain Text File (*.txt)",
        )
        if not selected_path:
            return

        target = Path(selected_path)
        if not target.suffix:
            target = target.with_suffix(".txt" if "Plain Text" in selected_filter else ".md")

        if target.suffix.lower() == ".md":
            header_lines = [f"# {note.title}"]
            if note.tags:
                header_lines.append("Tags: " + ", ".join(f"#{t}" for t in note.tags))
            export_text = "\n\n".join([*header_lines, note.content]).strip() + "\n"
        else:
            export_text = f"{note.title}\n{'=' * len(note.title)}\n\n{note.content}".strip() + "\n"

        try:
            atomic_write_text(target, export_text, backup_app_id=APP_ID)
        except FileOperationError as exc:
            show_error_dialog(self, "Export Failed", str(exc))
            return

        self.notify(
            "Note Exported",
            f"Saved '{target.name}' successfully.",
            level=NotificationLevel.SUCCESS,
        )

    # ------------------------------------------------------------------
    # Markdown Formatting & View Modes
    # ------------------------------------------------------------------

    def _apply_markdown_format(self, command: str) -> None:
        if self._current_note_id is None:
            return
        if self._view_mode == "preview":
            self._set_view_mode("split")

        cursor = self._editor.textCursor()
        if command.startswith("wrap:"):
            marker = command.split(":", 1)[1]
            selected = cursor.selectedText()
            if selected:
                cursor.insertText(f"{marker}{selected}{marker}")
            else:
                cursor.insertText(f"{marker}{marker}")
                cursor.movePosition(
                    QTextCursor.MoveOperation.Left,
                    QTextCursor.MoveMode.MoveAnchor,
                    len(marker),
                )
                self._editor.setTextCursor(cursor)
        elif command.startswith("prefix:"):
            prefix = command.split(":", 1)[1]
            cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
            cursor.insertText(prefix)
            self._editor.setTextCursor(cursor)

        self._editor.setFocus()

    def _set_view_mode(self, mode: str, persist: bool = True) -> None:
        resolved = mode if mode in _VIEW_MODES else "split"
        self._view_mode = resolved

        self._write_mode_btn.setChecked(resolved == "write")
        self._split_mode_btn.setChecked(resolved == "split")
        self._preview_mode_btn.setChecked(resolved == "preview")

        self._editor.setVisible(resolved in {"write", "split"})
        self._preview_browser.setVisible(resolved in {"split", "preview"})

        if resolved in {"split", "preview"}:
            self._update_preview_content()

        if persist:
            self.settings.set("view_mode", resolved)

    def _cycle_view_mode(self) -> None:
        idx = _VIEW_MODES.index(self._view_mode)
        next_mode = _VIEW_MODES[(idx + 1) % len(_VIEW_MODES)]
        self._set_view_mode(next_mode, persist=True)
        self.set_status_message(f"View mode: {next_mode.title()}", timeout_ms=1800)

    def _update_preview_content(self) -> None:
        self._preview_browser.setMarkdown(self._editor.toPlainText())

    def _apply_preview_stylesheet(self) -> None:
        tokens = self.theme_manager.tokens
        doc = self._preview_browser.document()
        if doc is not None:
            doc.setDefaultStyleSheet(
                f"""
                body {{
                    color: {tokens.text_primary};
                    font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                    font-size: 14px;
                    line-height: 1.6;
                }}
                h1, h2, h3 {{
                    color: {tokens.text_primary};
                    font-weight: 600;
                }}
                a {{
                    color: {tokens.accent_primary};
                }}
                code, pre {{
                    font-family: 'Cascadia Code', 'Consolas', monospace;
                    background-color: {tokens.bg_recessed};
                    color: {tokens.text_primary};
                }}
                blockquote {{
                    color: {tokens.text_secondary};
                    border-left: 3px solid {tokens.accent_primary};
                    padding-left: 10px;
                }}
                """
            )
            self._update_preview_content()

    def can_close(self) -> bool:
        """Ensure any pending debounced edits are committed before closing."""
        self._flush_save()
        return True

    def on_theme_updated(self, theme_name: str) -> None:
        _ = theme_name
        if hasattr(self, "_preview_browser"):
            self._apply_preview_stylesheet()


def main() -> int:
    """Application entry point for SimpleNotes.exe."""
    return bootstrap_application(
        app_id=APP_ID,
        window_factory=SimpleNotesWindow,
    )


if __name__ == "__main__":
    raise SystemExit(main())