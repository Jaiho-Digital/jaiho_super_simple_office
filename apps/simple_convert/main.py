"""SimpleConvert — Soft Modern Pastel offline batch document, spreadsheet & image converter."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import (
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from apps.simple_convert.converter_engine import (
    ALL_SUPPORTED_INPUT_EXTENSIONS,
    APP_ID,
    ConversionJob,
    convert_single_file,
    format_file_size,
    get_valid_target_formats,
)
from core.file_manager.file_ops import FileOperationError
from core.notifications.notifier import NotificationLevel
from core.ui.base_window import BaseWindow, bootstrap_application
from core.ui.dialogs import show_error_dialog
from core.ui.widgets import (
    CardFrame,
    HorizontalDivider,
    SectionHeader,
    StatusBadge,
    make_button,
)

logger = logging.getLogger(__name__)

_CATEGORY_BADGE_META: dict[str, tuple[str, str]] = {
    "document": ("Document", "lavender"),
    "spreadsheet": ("Sheet", "sage"),
    "image": ("Image", "peach"),
    "pdf": ("PDF", "blush"),
}


class SimpleConvertWindow(BaseWindow):
    """Main window for SimpleConvert — clean drag-and-drop batch format converter."""

    def __init__(self) -> None:
        super().__init__(app_id=APP_ID, min_width=940, min_height=620)
        if self.settings.get("window_width") is None:
            self.resize(1140, 740)

        self.setAcceptDrops(True)
        self.jobs: list[ConversionJob] = []
        self._custom_output_dir: Path | None = None
        self._last_output_dir: Path | None = None

        saved_out = self.settings.get("custom_output_dir")
        if isinstance(saved_out, str) and saved_out.strip():
            candidate = Path(saved_out)
            if candidate.exists() and candidate.is_dir():
                self._custom_output_dir = candidate

        self._build_header_actions()
        self._build_workspace()
        self._bind_shortcuts()

        self._sync_output_dir_label()
        self._sync_queue_state_ui()

    def _build_header_actions(self) -> None:
        """Mount Add Files, Clear Queue, and Convert All actions in the integrated header."""
        add_btn = make_button(
            text="Add Files",
            variant="ghost",
            tooltip="Select files to add to the conversion queue (Ctrl+O)",
            on_click=self.add_files_prompt,
            icon_name="plus",
            icon_size=14,
            parent=self,
        )
        add_btn.setStyleSheet("padding: 4px 10px; font-size: 12px;")
        self.header_actions_layout.addWidget(add_btn)

        self._clear_btn = make_button(
            text="Clear List",
            variant="ghost",
            tooltip="Remove all items from the conversion queue",
            on_click=self.clear_jobs,
            parent=self,
        )
        self._clear_btn.setStyleSheet("padding: 4px 10px; font-size: 12px;")
        self.header_actions_layout.addWidget(self._clear_btn)

        self._convert_btn = make_button(
            text="Convert All",
            variant="primary",
            tooltip="Convert all queued files locally (Ctrl+Return)",
            on_click=self.convert_all_jobs,
            icon_name="check",
            icon_size=14,
            parent=self,
        )
        self._convert_btn.setStyleSheet("padding: 4px 13px; font-size: 12px;")
        self.header_actions_layout.addWidget(self._convert_btn)

    def _build_workspace(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)

        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ==============================================================
        # Main 2-Pane Layout (Left Drop Zone & Queue | Right Settings)
        # ==============================================================
        body_row = QHBoxLayout()
        body_row.setContentsMargins(20, 18, 20, 16)
        body_row.setSpacing(18)

        # Left Column: Drop-Zone Empty State OR Batch Queue Table
        left_col = QVBoxLayout()
        left_col.setSpacing(12)

        # Empty-State Drop Card
        self._empty_card = CardFrame(padding=32, spacing=14, parent=central)
        self._empty_card.setMinimumHeight(420)

        empty_inner = QVBoxLayout()
        empty_inner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_inner.setSpacing(12)

        hero_title = QLabel("Drop Files Here to Convert Offline", self._empty_card)
        hero_title.setProperty("role", "title")
        hero_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_inner.addWidget(hero_title)

        hero_sub = QLabel(
            "Convert Documents (.docx, .md, .html, .txt, .pdf), Spreadsheets (.xlsx, .csv, .tsv, .json), "
            "and Images (.png, .jpg, .webp, .bmp, .ico) locally in seconds.",
            self._empty_card,
        )
        hero_sub.setProperty("role", "muted")
        hero_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hero_sub.setWordWrap(True)
        hero_sub.setMaximumWidth(520)
        empty_inner.addWidget(hero_sub)

        cta_row = QHBoxLayout()
        cta_row.setSpacing(10)
        cta_row.addStretch(1)
        pick_btn = make_button(
            text="Choose Files...",
            variant="primary",
            on_click=self.add_files_prompt,
            icon_name="folder",
            icon_size=15,
            parent=self._empty_card,
        )
        cta_row.addWidget(pick_btn)
        cta_row.addStretch(1)
        empty_inner.addLayout(cta_row)

        self._empty_card.body_layout.addStretch(1)
        self._empty_card.body_layout.addLayout(empty_inner)
        self._empty_card.body_layout.addStretch(1)
        left_col.addWidget(self._empty_card, 1)

        # Active Batch Queue Card
        self._queue_card = CardFrame(padding=14, spacing=10, parent=central)
        self._queue_card.setVisible(False)

        add_more_btn = make_button(
            text="+ Add More",
            variant="pastel",
            on_click=self.add_files_prompt,
            parent=self._queue_card,
        )
        add_more_btn.setStyleSheet("padding: 4px 10px; font-size: 12px;")
        self._queue_card.body_layout.addWidget(
            SectionHeader("Conversion Queue", accessory=add_more_btn, parent=self._queue_card)
        )

        self._queue_table = QTableWidget(0, 6, self._queue_card)
        self._queue_table.setHorizontalHeaderLabels(
            ["File Name", "Type", "Size", "Convert To", "Status", ""]
        )
        self._queue_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._queue_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._queue_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._queue_table.verticalHeader().setVisible(False)
        self._queue_table.verticalHeader().setDefaultSectionSize(38)

        hdr = self._queue_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self._queue_table.setColumnWidth(3, 185)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self._queue_table.setColumnWidth(5, 42)

        self._queue_card.body_layout.addWidget(self._queue_table, 1)

        self._progress_bar = QProgressBar(self._queue_card)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(False)
        self._queue_card.body_layout.addWidget(self._progress_bar)

        left_col.addWidget(self._queue_card, 1)
        body_row.addLayout(left_col, 1)

        # Right Column: Output Destination & Quality Inspector Card
        settings_card = CardFrame(padding=18, spacing=12, parent=central)
        settings_card.setFixedWidth(300)

        settings_card.body_layout.addWidget(SectionHeader("Output Settings", parent=settings_card))
        settings_card.body_layout.addWidget(HorizontalDivider(settings_card))

        dest_lbl = QLabel("Destination Folder", settings_card)
        dest_lbl.setProperty("role", "caption")
        settings_card.body_layout.addWidget(dest_lbl)

        self._output_dir_edit = QLineEdit(settings_card)
        self._output_dir_edit.setReadOnly(True)
        self._output_dir_edit.setToolTip("Converted files are saved here")
        settings_card.body_layout.addWidget(self._output_dir_edit)

        dir_btns_row = QHBoxLayout()
        dir_btns_row.setSpacing(6)
        choose_dir_btn = make_button(
            text="Choose Folder...",
            variant="ghost",
            on_click=self.choose_output_directory,
            parent=settings_card,
        )
        choose_dir_btn.setStyleSheet("padding: 4px 8px; font-size: 12px;")
        dir_btns_row.addWidget(choose_dir_btn, 1)

        same_dir_btn = make_button(
            text="Same as Source",
            variant="ghost",
            on_click=self.reset_output_directory,
            parent=settings_card,
        )
        same_dir_btn.setStyleSheet("padding: 4px 8px; font-size: 12px;")
        dir_btns_row.addWidget(same_dir_btn, 1)
        settings_card.body_layout.addLayout(dir_btns_row)

        settings_card.body_layout.addWidget(HorizontalDivider(settings_card))

        # Image Compression Quality
        qual_lbl = QLabel("JPEG / WebP Image Quality", settings_card)
        qual_lbl.setProperty("role", "caption")
        settings_card.body_layout.addWidget(qual_lbl)

        self._quality_combo = QComboBox(settings_card)
        for q_label, q_val in (
            ("Maximum (100%)", 100),
            ("High Quality (92%)", 92),
            ("Balanced (82%)", 82),
            ("Compact Web (72%)", 72),
        ):
            self._quality_combo.addItem(q_label, q_val)
        self._quality_combo.setCurrentIndex(1)
        settings_card.body_layout.addWidget(self._quality_combo)

        # PDF-to-Image Resolution
        dpi_lbl = QLabel("PDF Page Render Resolution", settings_card)
        dpi_lbl.setProperty("role", "caption")
        settings_card.body_layout.addWidget(dpi_lbl)

        self._dpi_combo = QComboBox(settings_card)
        self._dpi_combo.addItem("Standard (150 DPI)", 150)
        self._dpi_combo.addItem("High Print (300 DPI)", 300)
        settings_card.body_layout.addWidget(self._dpi_combo)

        settings_card.body_layout.addStretch(1)
        settings_card.body_layout.addWidget(HorizontalDivider(settings_card))

        self._open_out_btn = make_button(
            text="Open Output Folder",
            variant="pastel",
            tooltip="Reveal converted files in File Explorer",
            on_click=self.open_output_folder,
            parent=settings_card,
        )
        settings_card.body_layout.addWidget(self._open_out_btn)

        self._run_panel_btn = make_button(
            text="Convert All Files",
            variant="primary",
            on_click=self.convert_all_jobs,
            parent=settings_card,
        )
        settings_card.body_layout.addWidget(self._run_panel_btn)

        body_row.addWidget(settings_card)
        outer.addLayout(body_row, 1)

        # ==============================================================
        # Bottom Status Bar
        # ==============================================================
        footer_bar = QFrame(central)
        footer_bar.setProperty("surface", "footer-bar")
        footer_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        footer_bar.setFixedHeight(34)

        f_row = QHBoxLayout(footer_bar)
        f_row.setContentsMargins(16, 4, 16, 4)
        f_row.setSpacing(10)

        self._status_badge = StatusBadge("Ready", variant="lavender", parent=footer_bar)
        f_row.addWidget(self._status_badge)

        self._summary_lbl = QLabel("0 files queued  •  100% local offline conversion", footer_bar)
        self._summary_lbl.setProperty("role", "muted")
        f_row.addWidget(self._summary_lbl)

        f_row.addStretch(1)
        outer.addWidget(footer_bar)

    def _bind_shortcuts(self) -> None:
        QShortcut(QKeySequence.StandardKey.Open, self).activated.connect(self.add_files_prompt)
        QShortcut(QKeySequence("Ctrl+Return"), self).activated.connect(self.convert_all_jobs)
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self).activated.connect(self.remove_selected_job)

    # ------------------------------------------------------------------
    # Drag-and-Drop Support
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        mime = event.mimeData()
        if mime is not None and mime.hasUrls():
            for url in mime.urls():
                if url.isLocalFile():
                    ext = Path(url.toLocalFile()).suffix.lower()
                    if ext in ALL_SUPPORTED_INPUT_EXTENSIONS:
                        event.acceptProposedAction()
                        return
        super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        mime = event.mimeData()
        if mime is not None and mime.hasUrls():
            paths = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]
            if paths:
                event.acceptProposedAction()
                self.add_files(paths)
                return
        super().dropEvent(event)

    # ------------------------------------------------------------------
    # Queue Management
    # ------------------------------------------------------------------

    def add_files_prompt(self) -> None:
        ext_list = " ".join(f"*{ext}" for ext in sorted(ALL_SUPPORTED_INPUT_EXTENSIONS))
        selected, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Files to Convert",
            str(Path.home() / "Documents"),
            f"All Supported Files ({ext_list});;Documents (*.docx *.md *.html *.txt *.pdf);;Spreadsheets (*.xlsx *.csv *.tsv *.json);;Images (*.png *.jpg *.jpeg *.webp *.bmp *.ico)",
        )
        if selected:
            self.add_files(selected)

    def add_files(self, file_paths: list[str | Path], target_ext: str | None = None) -> int:
        """Validate and append files to the conversion queue, returning count added."""
        added = 0
        existing_sources = {j.source_path.resolve() for j in self.jobs}

        for fp in file_paths:
            try:
                resolved = Path(fp).resolve()
                if resolved in existing_sources:
                    continue
                job = ConversionJob.from_path(resolved, target_ext=target_ext)
                self.jobs.append(job)
                existing_sources.add(resolved)
                added += 1
            except FileOperationError as exc:
                show_error_dialog(self, "Unsupported File", str(exc))

        if added > 0:
            self._rebuild_queue_table()
            self._sync_queue_state_ui()
            self.set_status_message(f"Added {added} file(s) to queue", timeout_ms=1800)
        return added

    def _rebuild_queue_table(self) -> None:
        self._queue_table.setRowCount(len(self.jobs))
        for row_idx, job in enumerate(self.jobs):
            # Col 0: File Name
            name_item = QTableWidgetItem(job.source_path.name)
            name_item.setToolTip(str(job.source_path))
            self._queue_table.setItem(row_idx, 0, name_item)

            # Col 1: Pastel Category Badge
            cat_label, badge_variant = _CATEGORY_BADGE_META.get(
                job.category, (job.category.title(), "lavender")
            )
            cat_wrap = QWidget(self._queue_table)
            cat_lay = QHBoxLayout(cat_wrap)
            cat_lay.setContentsMargins(6, 2, 6, 2)
            cat_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cat_lay.addWidget(StatusBadge(cat_label, variant=badge_variant, parent=cat_wrap))
            self._queue_table.setCellWidget(row_idx, 1, cat_wrap)

            # Col 2: File Size
            size_item = QTableWidgetItem(format_file_size(job.size_bytes))
            size_item.setTextAlignment(int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter))
            self._queue_table.setItem(row_idx, 2, size_item)

            # Col 3: Target Format Selector
            fmt_combo = QComboBox(self._queue_table)
            for ext_code, ext_label in get_valid_target_formats(job.category):
                fmt_combo.addItem(ext_label, ext_code)
            curr_idx = fmt_combo.findData(job.target_ext)
            fmt_combo.setCurrentIndex(curr_idx if curr_idx >= 0 else 0)
            fmt_combo.setStyleSheet("padding: 3px 6px; font-size: 11px;")
            fmt_combo.currentIndexChanged.connect(
                lambda idx, j=job, cb=fmt_combo: self._on_job_target_changed(j, cb, idx)
            )
            self._queue_table.setCellWidget(row_idx, 3, fmt_combo)

            # Col 4: Status Badge
            st_wrap = QWidget(self._queue_table)
            st_lay = QHBoxLayout(st_wrap)
            st_lay.setContentsMargins(6, 2, 6, 2)
            st_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if job.status == "done":
                out_label = (
                    job.output_paths[0].name
                    if len(job.output_paths) == 1
                    else f"{len(job.output_paths)} files"
                )
                badge = StatusBadge(f"✓ {out_label}", variant="sage", parent=st_wrap)
            elif job.status == "error":
                badge = StatusBadge("Failed", variant="blush", parent=st_wrap)
                badge.setToolTip(job.error_message)
            else:
                badge = StatusBadge("Queued", variant="butter", parent=st_wrap)
            st_lay.addWidget(badge)
            self._queue_table.setCellWidget(row_idx, 4, st_wrap)

            # Col 5: Remove Row Button
            rm_btn = make_button(
                text="✕",
                variant="ghost",
                tooltip="Remove from queue",
                on_click=lambda _=False, jid=job.job_id: self.remove_job_by_id(jid),
                parent=self._queue_table,
            )
            rm_btn.setStyleSheet("padding: 2px 6px; font-size: 11px;")
            self._queue_table.setCellWidget(row_idx, 5, rm_btn)

    def _on_job_target_changed(self, job: ConversionJob, combo: QComboBox, idx: int) -> None:
        ext_val = combo.itemData(idx)
        if isinstance(ext_val, str):
            job.target_ext = ext_val
            job.status = "queued"
            job.output_paths.clear()
            job.error_message = ""
            self._rebuild_queue_table()
            self._sync_queue_state_ui()

    def remove_job_by_id(self, job_id: str) -> None:
        before = len(self.jobs)
        self.jobs = [j for j in self.jobs if j.job_id != job_id]
        if len(self.jobs) < before:
            self._rebuild_queue_table()
            self._sync_queue_state_ui()

    def remove_selected_job(self) -> None:
        row = self._queue_table.currentRow()
        if 0 <= row < len(self.jobs):
            self.jobs.pop(row)
            self._rebuild_queue_table()
            self._sync_queue_state_ui()

    def clear_jobs(self) -> None:
        self.jobs.clear()
        self._progress_bar.setVisible(False)
        self._rebuild_queue_table()
        self._sync_queue_state_ui()

    def _sync_queue_state_ui(self) -> None:
        has_jobs = bool(self.jobs)
        self._empty_card.setVisible(not has_jobs)
        self._queue_card.setVisible(has_jobs)

        self._clear_btn.setEnabled(has_jobs)
        self._convert_btn.setEnabled(has_jobs)
        self._run_panel_btn.setEnabled(has_jobs)
        self._open_out_btn.setEnabled(
            self._last_output_dir is not None or self._custom_output_dir is not None
        )

        total = len(self.jobs)
        done_count = sum(1 for j in self.jobs if j.status == "done")
        err_count = sum(1 for j in self.jobs if j.status == "error")

        if total == 0:
            self._status_badge.set_status("Ready", variant="lavender")
            self._summary_lbl.setText("0 files queued  •  100% local offline conversion")
        elif done_count == total:
            self._status_badge.set_status("Completed", variant="sage")
            self._summary_lbl.setText(f"Converted {done_count} of {total} file(s) cleanly")
        elif err_count > 0:
            self._status_badge.set_status("Attention", variant="blush")
            self._summary_lbl.setText(
                f"{done_count} succeeded, {err_count} failed out of {total} file(s)"
            )
        else:
            self._status_badge.set_status(f"{total} Queued", variant="butter")
            self._summary_lbl.setText(f"{total} file(s) ready for conversion")

    # ------------------------------------------------------------------
    # Output Directory & Batch Conversion Execution
    # ------------------------------------------------------------------

    def choose_output_directory(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Select Output Folder for Converted Files",
            str(self._custom_output_dir or (Path.home() / "Documents")),
        )
        if not chosen:
            return
        self._custom_output_dir = Path(chosen).resolve()
        self.settings.set("custom_output_dir", str(self._custom_output_dir))
        self._sync_output_dir_label()
        self._sync_queue_state_ui()

    def reset_output_directory(self) -> None:
        self._custom_output_dir = None
        self.settings.set("custom_output_dir", "")
        self._sync_output_dir_label()
        self._sync_queue_state_ui()

    def _sync_output_dir_label(self) -> None:
        if self._custom_output_dir is not None:
            self._output_dir_edit.setText(str(self._custom_output_dir))
        else:
            self._output_dir_edit.setText("Same folder as each source file")

    def open_output_folder(self) -> None:
        target_dir = self._last_output_dir or self._custom_output_dir
        if target_dir is None and self.jobs:
            target_dir = self.jobs[0].source_path.parent
        if target_dir is not None and target_dir.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target_dir)))

    def convert_all_jobs(self) -> int:
        """Convert every queued item in `self.jobs`, returning count of succeeded jobs."""
        if not self.jobs:
            return 0

        quality = int(self._quality_combo.currentData() or 92)
        dpi = int(self._dpi_combo.currentData() or 150)

        self._progress_bar.setVisible(True)
        self._progress_bar.setRange(0, len(self.jobs))
        self._progress_bar.setValue(0)

        succeeded = 0
        for idx, job in enumerate(self.jobs):
            out_dir = self._custom_output_dir or job.source_path.parent
            try:
                written = convert_single_file(
                    source_path=job.source_path,
                    target_ext=job.target_ext,
                    output_dir=out_dir,
                    jpeg_quality=quality,
                    pdf_dpi=dpi,
                )
                job.status = "done"
                job.output_paths = written
                job.error_message = ""
                if written:
                    self._last_output_dir = written[0].parent
                succeeded += 1
            except FileOperationError as exc:
                job.status = "error"
                job.error_message = str(exc)

            self._progress_bar.setValue(idx + 1)
            QApplication.processEvents()

        self._rebuild_queue_table()
        self._sync_queue_state_ui()

        if succeeded == len(self.jobs):
            self.notify(
                "Conversion Complete",
                f"Successfully converted {succeeded} file(s).",
                level=NotificationLevel.SUCCESS,
            )
        elif succeeded > 0:
            self.notify(
                "Partial Conversion",
                f"Converted {succeeded} of {len(self.jobs)} file(s). Hover 'Failed' for details.",
                level=NotificationLevel.WARNING,
            )
        else:
            show_error_dialog(
                self,
                "Conversion Failed",
                self.jobs[0].error_message or "Could not convert the queued files.",
            )

        return succeeded


def main() -> int:
    """Application entry point for SimpleConvert.exe."""
    return bootstrap_application(
        app_id=APP_ID,
        window_factory=SimpleConvertWindow,
    )


if __name__ == "__main__":
    raise SystemExit(main())