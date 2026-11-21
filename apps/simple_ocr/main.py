"""SimpleOCR — Soft Modern Pastel offline text recognition studio for images & PDFs."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QIcon,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from apps.simple_ocr.ocr_engine import (
    APP_ID,
    OCR_LANGUAGE_CHOICES,
    SUPPORTED_OCR_EXTENSIONS,
    OcrPageItem,
    discover_ocr_backends,
    export_ocr_text_document,
    generate_sample_ocr_image,
    load_ocr_items_from_file,
    recognize_image_text,
    render_ocr_thumbnail,
)
from core.file_manager.file_ops import FileOperationError, sanitize_filename
from core.notifications.notifier import NotificationLevel
from core.themes.tokens import LIGHT_THEME, ThemeTokens
from core.ui.base_window import BaseWindow, bootstrap_application
from core.ui.dialogs import show_error_dialog
from core.ui.widgets import (
    CardFrame,
    HorizontalDivider,
    SectionHeader,
    StatusBadge,
    VerticalDivider,
    make_button,
)

logger = logging.getLogger(__name__)


class OcrRegionCanvasWidget(QWidget):
    """Image stage allowing users to drag a pastel bounding box to OCR a specific region."""

    region_selected = Signal(tuple)  # Emits (x, y, w, h) normalized [0.0, 1.0]

    def __init__(self, tokens: ThemeTokens = LIGHT_THEME, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tokens = tokens
        self._pixmap: QPixmap | None = None
        self._crop_rect: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)
        self._is_dragging: bool = False
        self._drag_start: QPointF = QPointF()
        self._drag_curr: QPointF = QPointF()

        self.setFixedSize(560, 400)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def set_tokens(self, tokens: ThemeTokens) -> None:
        self._tokens = tokens
        self.update()

    def set_image_pixmap(
        self,
        pixmap: QPixmap,
        logical_w: int,
        logical_h: int,
        crop_rect: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0),
    ) -> None:
        self._pixmap = pixmap
        self._crop_rect = crop_rect
        self.setFixedSize(max(200, logical_w), max(140, logical_h))
        self.update()

    def set_crop_rect(self, crop_rect: tuple[float, float, float, float]) -> None:
        self._crop_rect = crop_rect
        self.update()

    def _to_norm(self, pos: QPointF) -> tuple[float, float]:
        w = max(1.0, float(self.width()))
        h = max(1.0, float(self.height()))
        return (
            max(0.0, min(1.0, pos.x() / w)),
            max(0.0, min(1.0, pos.y() / h)),
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._pixmap is not None:
            self._is_dragging = True
            self._drag_start = event.position()
            self._drag_curr = event.position()
            self.update()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._is_dragging:
            self._drag_curr = event.position()
            self.update()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._is_dragging and event.button() == Qt.MouseButton.LeftButton:
            self._is_dragging = False
            x1, y1 = self._to_norm(self._drag_start)
            x2, y2 = self._to_norm(event.position())
            left, right = min(x1, x2), max(x1, x2)
            top, bottom = min(y1, y2), max(y1, y2)
            if (right - left) >= 0.03 and (bottom - top) >= 0.03:
                new_rect = (left, top, right - left, bottom - top)
                self._crop_rect = new_rect
                self.region_selected.emit(new_rect)
            self.update()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        full = QRectF(0.0, 0.0, float(self.width()), float(self.height()))
        if self._pixmap is not None:
            painter.drawPixmap(0, 0, self._pixmap)
        else:
            painter.fillRect(full, QColor(self._tokens.bg_surface))

        painter.setPen(QPen(QColor(self._tokens.border_strong), 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(full.adjusted(0.0, 0.0, -1.0, -1.0))

        cx, cy, cw, ch = self._crop_rect
        is_custom_roi = cx > 0.002 or cy > 0.002 or cw < 0.998 or ch < 0.998
        if self._pixmap is not None and is_custom_roi:
            roi = QRectF(
                cx * full.width(),
                cy * full.height(),
                cw * full.width(),
                ch * full.height(),
            )
            shade = QColor(29, 29, 35, 95)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(shade)
            painter.drawRect(QRectF(0.0, 0.0, full.width(), roi.top()))
            painter.drawRect(
                QRectF(0.0, roi.bottom(), full.width(), full.height() - roi.bottom())
            )
            painter.drawRect(QRectF(0.0, roi.top(), roi.left(), roi.height()))
            painter.drawRect(
                QRectF(roi.right(), roi.top(), full.width() - roi.right(), roi.height())
            )

            accent = QColor(self._tokens.accent_primary)
            painter.setPen(QPen(accent, 1.8))
            painter.setBrush(QColor(232, 224, 247, 36))
            painter.drawRect(roi)

        if self._is_dragging:
            drag_r = QRectF(self._drag_start, self._drag_curr).normalized()
            painter.setPen(QPen(QColor(self._tokens.accent_primary), 1.6, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(232, 224, 247, 55))
            painter.drawRect(drag_r)

        painter.end()


class SimpleOCRWindow(BaseWindow):
    """Main window for SimpleOCR — offline OCR for images, scans & PDFs."""

    def __init__(self) -> None:
        super().__init__(app_id=APP_ID, min_width=980, min_height=650)
        if self.settings.get("window_width") is None:
            self.resize(1240, 790)

        self.setAcceptDrops(True)
        self.items: list[OcrPageItem] = []
        self._active_idx: int = -1
        self._backends = discover_ocr_backends()
        self._is_syncing_text: bool = False

        self._build_header_actions()
        self._build_workspace()
        self._bind_shortcuts()

        self._populate_engine_selectors()
        self._sync_workspace_state()

    def _build_header_actions(self) -> None:
        """Mount Open File, Sample, Extract Page, Extract All, and Export in header."""
        open_btn = make_button(
            text="Open Image / PDF",
            variant="ghost",
            tooltip="Open an image or multi-page PDF for OCR (Ctrl+O)",
            on_click=self.open_file_prompt,
            icon_name="folder",
            icon_size=14,
            parent=self,
        )
        open_btn.setStyleSheet("padding: 4px 10px; font-size: 12px;")
        self.header_actions_layout.addWidget(open_btn)

        sample_btn = make_button(
            text="Load Sample",
            variant="ghost",
            tooltip="Load a sample invoice image to test OCR immediately",
            on_click=self.load_sample_image,
            parent=self,
        )
        sample_btn.setStyleSheet("padding: 4px 9px; font-size: 12px;")
        self.header_actions_layout.addWidget(sample_btn)

        self._extract_page_btn = make_button(
            text="Extract Page",
            variant="pastel",
            tooltip="Run offline OCR on the active page or selected region (F2)",
            on_click=self.extract_active_page,
            icon_name="check",
            icon_size=14,
            parent=self,
        )
        self._extract_page_btn.setStyleSheet("padding: 4px 11px; font-size: 12px;")
        self.header_actions_layout.addWidget(self._extract_page_btn)

        self._extract_all_btn = make_button(
            text="Extract All",
            variant="primary",
            tooltip="Run offline OCR across every loaded page (Ctrl+R)",
            on_click=self.extract_all_pages,
            parent=self,
        )
        self._extract_all_btn.setStyleSheet("padding: 4px 12px; font-size: 12px;")
        self.header_actions_layout.addWidget(self._extract_all_btn)

        self._export_btn = make_button(
            text="Export Text",
            variant="ghost",
            tooltip="Save extracted text as .txt, .md, or .docx (Ctrl+S)",
            on_click=self.export_text_prompt,
            parent=self,
        )
        self._export_btn.setStyleSheet("padding: 4px 10px; font-size: 12px;")
        self.header_actions_layout.addWidget(self._export_btn)

    def _build_workspace(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)

        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ==============================================================
        # 1. OCR Engine, Language & Preprocessing Strip
        # ==============================================================
        ocr_bar = QFrame(central)
        ocr_bar.setProperty("surface", "app-header")
        ocr_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        ob_row = QHBoxLayout(ocr_bar)
        ob_row.setContentsMargins(14, 6, 14, 6)
        ob_row.setSpacing(8)

        eng_lbl = QLabel("Engine:", ocr_bar)
        eng_lbl.setProperty("role", "caption")
        ob_row.addWidget(eng_lbl)

        self._backend_combo = QComboBox(ocr_bar)
        self._backend_combo.setToolTip("Select offline OCR backend")
        self._backend_combo.setStyleSheet("padding: 4px 8px; font-size: 12px;")
        ob_row.addWidget(self._backend_combo)

        lang_lbl = QLabel("Language:", ocr_bar)
        lang_lbl.setProperty("role", "caption")
        ob_row.addWidget(lang_lbl)

        self._lang_combo = QComboBox(ocr_bar)
        for tag, disp in OCR_LANGUAGE_CHOICES:
            self._lang_combo.addItem(disp, tag)
        self._lang_combo.setToolTip("OCR language hint")
        self._lang_combo.setStyleSheet("padding: 4px 8px; font-size: 12px;")
        ob_row.addWidget(self._lang_combo)

        ob_row.addWidget(VerticalDivider(ocr_bar))

        self._enhance_chk = QCheckBox("Contrast Boost", ocr_bar)
        self._enhance_chk.setChecked(True)
        self._enhance_chk.setToolTip("Stretch contrast before recognition for cleaner text extraction")
        ob_row.addWidget(self._enhance_chk)

        self._clear_roi_btn = make_button(
            text="Full Image Region",
            variant="ghost",
            tooltip="Clear custom selection box and OCR the full image",
            on_click=self.reset_region_selection,
            parent=ocr_bar,
        )
        self._clear_roi_btn.setStyleSheet("padding: 4px 8px; font-size: 12px;")
        ob_row.addWidget(self._clear_roi_btn)

        ob_row.addStretch(1)
        outer.addWidget(ocr_bar)

        # ==============================================================
        # 2. Main 3-Pane Split Studio (Pages | Image ROI Stage | Text Editor)
        # ==============================================================
        body_row = QHBoxLayout()
        body_row.setContentsMargins(0, 0, 0, 0)
        body_row.setSpacing(0)

        # Left Pane: Pages / Queue Strip
        pages_pane = QFrame(central)
        pages_pane.setProperty("surface", "sidebar")
        pages_pane.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        pages_pane.setFixedWidth(215)

        p_layout = QVBoxLayout(pages_pane)
        p_layout.setContentsMargins(12, 14, 12, 12)
        p_layout.setSpacing(8)

        p_layout.addWidget(SectionHeader("Pages", parent=pages_pane))
        p_layout.addWidget(HorizontalDivider(pages_pane))

        self._page_list = QListWidget(pages_pane)
        self._page_list.setIconSize(QSize(124, 92))
        self._page_list.setSpacing(4)
        self._page_list.setStyleSheet(
            "QListWidget { border: none; background: transparent; padding: 0px; }"
        )
        self._page_list.currentRowChanged.connect(self._on_page_selected)
        p_layout.addWidget(self._page_list, 1)

        p_layout.addWidget(HorizontalDivider(pages_pane))

        rm_btn = make_button(
            text="Remove Page",
            variant="ghost",
            tooltip="Remove active page from list",
            on_click=self.remove_active_page,
            parent=pages_pane,
        )
        rm_btn.setStyleSheet("padding: 4px 8px; font-size: 11px;")
        p_layout.addWidget(rm_btn)

        body_row.addWidget(pages_pane)

        # Center Pane: Empty Card OR Interactive Image Stage
        stage_host = QWidget(central)
        stage_layout = QVBoxLayout(stage_host)
        stage_layout.setContentsMargins(0, 0, 0, 0)
        stage_layout.setSpacing(0)

        self._empty_host = QWidget(stage_host)
        empty_wrap = QVBoxLayout(self._empty_host)
        empty_wrap.setContentsMargins(24, 24, 24, 24)
        empty_wrap.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._empty_card = CardFrame(padding=32, spacing=14, parent=self._empty_host)
        self._empty_card.setMaximumWidth(460)

        e_title = QLabel("Extract Text Offline from Images & PDFs", self._empty_card)
        e_title.setProperty("role", "title")
        e_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        e_title.setWordWrap(True)
        self._empty_card.body_layout.addWidget(e_title)

        e_desc = QLabel(
            "Open screenshots, scanned documents, receipts, or PDFs—drag a box around any "
            "region to focus recognition, then copy or export clean text offline.",
            self._empty_card,
        )
        e_desc.setProperty("role", "muted")
        e_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        e_desc.setWordWrap(True)
        self._empty_card.body_layout.addWidget(e_desc)

        self._empty_card.body_layout.addWidget(HorizontalDivider(self._empty_card))

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch(1)
        btn_row.addWidget(
            make_button(
                text="Open Image / PDF...",
                variant="primary",
                on_click=self.open_file_prompt,
                icon_name="folder",
                icon_size=15,
                parent=self._empty_card,
            )
        )
        btn_row.addWidget(
            make_button(
                text="Try Sample Sheet",
                variant="pastel",
                on_click=self.load_sample_image,
                parent=self._empty_card,
            )
        )
        btn_row.addStretch(1)
        self._empty_card.body_layout.addLayout(btn_row)

        empty_wrap.addWidget(self._empty_card)
        stage_layout.addWidget(self._empty_host, 1)

        self._scroll_area = QScrollArea(stage_host)
        self._scroll_area.setWidgetResizable(False)
        self._scroll_area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)

        self._image_canvas = OcrRegionCanvasWidget(
            tokens=self.theme_manager.tokens,
            parent=self._scroll_area,
        )
        self._image_canvas.region_selected.connect(self._on_region_selected)
        self._scroll_area.setWidget(self._image_canvas)
        self._scroll_area.setVisible(False)
        stage_layout.addWidget(self._scroll_area, 1)

        body_row.addWidget(stage_host, 1)

        # Right Pane: Recognized Text Output & Export Inspector
        text_pane = QFrame(central)
        text_pane.setProperty("surface", "inspector")
        text_pane.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        text_pane.setFixedWidth(360)

        t_layout = QVBoxLayout(text_pane)
        t_layout.setContentsMargins(16, 16, 16, 14)
        t_layout.setSpacing(10)

        copy_btn = make_button(
            text="Copy",
            variant="ghost",
            tooltip="Copy recognized text to clipboard (Ctrl+Shift+C)",
            on_click=self.copy_text_to_clipboard,
            parent=text_pane,
        )
        copy_btn.setStyleSheet("padding: 3px 8px; font-size: 11px;")
        t_layout.addWidget(
            SectionHeader("Recognized Text", accessory=copy_btn, parent=text_pane)
        )
        t_layout.addWidget(HorizontalDivider(text_pane))

        self._text_editor = QPlainTextEdit(text_pane)
        self._text_editor.setPlaceholderText(
            "Recognized text will appear here after clicking 'Extract Page' or 'Extract All'..."
        )
        self._text_editor.textChanged.connect(self._on_text_edited)
        t_layout.addWidget(self._text_editor, 1)

        self._word_stats_lbl = QLabel("0 words  •  0 characters", text_pane)
        self._word_stats_lbl.setProperty("role", "caption")
        t_layout.addWidget(self._word_stats_lbl)

        t_layout.addWidget(HorizontalDivider(text_pane))

        export_row = QHBoxLayout()
        export_row.setSpacing(6)
        self._save_txt_btn = make_button(
            text="Save .TXT",
            variant="pastel",
            on_click=lambda: self.export_text_to(".txt"),
            parent=text_pane,
        )
        self._save_txt_btn.setStyleSheet("padding: 5px 8px; font-size: 11px;")
        export_row.addWidget(self._save_txt_btn, 1)

        self._save_md_btn = make_button(
            text="Save .MD",
            variant="ghost",
            on_click=lambda: self.export_text_to(".md"),
            parent=text_pane,
        )
        self._save_md_btn.setStyleSheet("padding: 5px 8px; font-size: 11px;")
        export_row.addWidget(self._save_md_btn, 1)

        self._save_docx_btn = make_button(
            text="Save .DOCX",
            variant="ghost",
            on_click=lambda: self.export_text_to(".docx"),
            parent=text_pane,
        )
        self._save_docx_btn.setStyleSheet("padding: 5px 8px; font-size: 11px;")
        export_row.addWidget(self._save_docx_btn, 1)
        t_layout.addLayout(export_row)

        body_row.addWidget(text_pane)
        outer.addLayout(body_row, 1)

        # ==============================================================
        # 3. Bottom Status Bar
        # ==============================================================
        footer_bar = QFrame(central)
        footer_bar.setProperty("surface", "footer-bar")
        footer_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        footer_bar.setFixedHeight(34)

        f_row = QHBoxLayout(footer_bar)
        f_row.setContentsMargins(16, 4, 16, 4)
        f_row.setSpacing(10)

        self._status_badge = StatusBadge("Offline Ready", variant="lavender", parent=footer_bar)
        f_row.addWidget(self._status_badge)

        self._ocr_meta_lbl = QLabel("Drag a box on the image to OCR a specific region", footer_bar)
        self._ocr_meta_lbl.setProperty("role", "muted")
        f_row.addWidget(self._ocr_meta_lbl)

        f_row.addStretch(1)
        outer.addWidget(footer_bar)

    def _bind_shortcuts(self) -> None:
        QShortcut(QKeySequence.StandardKey.Open, self).activated.connect(self.open_file_prompt)
        QShortcut(QKeySequence.StandardKey.Save, self).activated.connect(self.export_text_prompt)
        QShortcut(QKeySequence(Qt.Key.Key_F2), self).activated.connect(self.extract_active_page)
        QShortcut(QKeySequence("Ctrl+R"), self).activated.connect(self.extract_all_pages)
        QShortcut(QKeySequence("Ctrl+Shift+C"), self).activated.connect(self.copy_text_to_clipboard)

    # ------------------------------------------------------------------
    # Drag-and-Drop Support
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        mime = event.mimeData()
        if mime is not None and mime.hasUrls():
            for url in mime.urls():
                if url.isLocalFile():
                    ext = Path(url.toLocalFile()).suffix.lower()
                    if ext in SUPPORTED_OCR_EXTENSIONS:
                        event.acceptProposedAction()
                        return
        super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        mime = event.mimeData()
        if mime is not None and mime.hasUrls():
            paths = [
                url.toLocalFile()
                for url in mime.urls()
                if url.isLocalFile()
                and Path(url.toLocalFile()).suffix.lower() in SUPPORTED_OCR_EXTENSIONS
            ]
            if paths:
                event.acceptProposedAction()
                self.load_files(paths)
                return
        super().dropEvent(event)

    # ------------------------------------------------------------------
    # File Loading, Selection & Preview Rendering
    # ------------------------------------------------------------------

    def _populate_engine_selectors(self) -> None:
        self._backend_combo.clear()
        self._backend_combo.addItem("Auto (Best Offline Engine)", "auto")
        for eng in self._backends:
            self._backend_combo.addItem(eng.display_name, eng.backend_id)

    def open_file_prompt(self) -> None:
        selected, _ = QFileDialog.getOpenFileNames(
            self,
            "Open Images or PDFs for OCR",
            str(Path.home() / "Pictures"),
            "Images & PDFs (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff *.pdf)",
        )
        if selected:
            self.load_files(selected)

    def load_sample_image(self) -> bool:
        sample_img = generate_sample_ocr_image()
        self.items = [OcrPageItem(source_image=sample_img, label="Sample_Invoice_2026.png")]
        self._active_idx = 0
        self._rebuild_page_list()
        self._sync_workspace_state()
        self._render_active_item()
        self.set_status_message("Loaded sample document for OCR", timeout_ms=1800)
        return True

    def load_files(self, file_paths: list[str | Path]) -> int:
        loaded_any = 0
        for fp in file_paths:
            try:
                new_items = load_ocr_items_from_file(fp)
            except FileOperationError as exc:
                show_error_dialog(self, "Could Not Load File", str(exc))
                continue

            if not self.items:
                self.items = new_items
                self._active_idx = 0
            else:
                self.items.extend(new_items)
            loaded_any += len(new_items)

        if loaded_any > 0:
            if self._active_idx < 0:
                self._active_idx = 0
            self._rebuild_page_list()
            self._sync_workspace_state()
            self._render_active_item()
            self.set_status_message(f"Loaded {loaded_any} page(s) for OCR", timeout_ms=2000)
        return loaded_any

    def _rebuild_page_list(self) -> None:
        self._page_list.blockSignals(True)
        try:
            self._page_list.clear()
            for idx, item in enumerate(self.items):
                tag = " ✓" if item.extracted_text.strip() else ""
                lw_item = QListWidgetItem(f"{idx + 1}. {item.label}{tag}")
                lw_item.setIcon(QIcon(render_ocr_thumbnail(item, width_px=124)))
                lw_item.setToolTip(item.label)
                self._page_list.addItem(lw_item)
            if 0 <= self._active_idx < len(self.items):
                self._page_list.setCurrentRow(self._active_idx)
        finally:
            self._page_list.blockSignals(False)

    def _on_page_selected(self, row: int) -> None:
        if not (0 <= row < len(self.items)):
            return
        self._active_idx = row
        self._sync_workspace_state()
        self._render_active_item()

    def remove_active_page(self) -> None:
        if not (0 <= self._active_idx < len(self.items)):
            return
        self.items.pop(self._active_idx)
        if not self.items:
            self._active_idx = -1
        else:
            self._active_idx = max(0, min(len(self.items) - 1, self._active_idx))
        self._rebuild_page_list()
        self._sync_workspace_state()
        self._render_active_item()

    def _render_active_item(self) -> None:
        if not (0 <= self._active_idx < len(self.items)):
            self._is_syncing_text = True
            try:
                self._text_editor.clear()
            finally:
                self._is_syncing_text = False
            self._update_word_stats()
            return

        item = self.items[self._active_idx]
        src = item.source_image
        src_w = max(120, src.width())
        src_h = max(100, src.height())

        logical_w = 560
        logical_h = max(160, int(round(logical_w * (src_h / float(src_w)))))
        dpr = max(1.0, float(self.devicePixelRatioF()))

        scaled = src.scaled(
            QSize(int(round(logical_w * dpr)), int(round(logical_h * dpr))),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        px = QPixmap.fromImage(scaled)
        px.setDevicePixelRatio(dpr)

        self._image_canvas.set_image_pixmap(px, logical_w, logical_h, item.crop_rect)

        self._is_syncing_text = True
        try:
            self._text_editor.setPlainText(item.extracted_text)
        finally:
            self._is_syncing_text = False
        self._update_word_stats()

    def _on_region_selected(self, rect_tuple: tuple[float, float, float, float]) -> None:
        if 0 <= self._active_idx < len(self.items):
            self.items[self._active_idx].crop_rect = rect_tuple
            self._ocr_meta_lbl.setText(
                f"Selected ROI: ({rect_tuple[0]:.2f}, {rect_tuple[1]:.2f}) "
                f"{rect_tuple[2]:.2f}×{rect_tuple[3]:.2f}"
            )

    def reset_region_selection(self) -> None:
        if 0 <= self._active_idx < len(self.items):
            self.items[self._active_idx].crop_rect = (0.0, 0.0, 1.0, 1.0)
            self._image_canvas.set_crop_rect((0.0, 0.0, 1.0, 1.0))
            self._ocr_meta_lbl.setText("Full image region selected")

    def _on_text_edited(self) -> None:
        if self._is_syncing_text:
            return
        if 0 <= self._active_idx < len(self.items):
            self.items[self._active_idx].extracted_text = self._text_editor.toPlainText()
        self._update_word_stats()

    def _update_word_stats(self) -> None:
        txt = self._text_editor.toPlainText()
        words = len(txt.split())
        chars = len(txt)
        self._word_stats_lbl.setText(f"{words} word{'s' if words != 1 else ''}  •  {chars} characters")

    def _sync_workspace_state(self) -> None:
        has_items = 0 <= self._active_idx < len(self.items)
        self._empty_host.setVisible(not has_items)
        self._scroll_area.setVisible(has_items)

        for btn in (
            self._extract_page_btn,
            self._extract_all_btn,
            self._export_btn,
            self._clear_roi_btn,
            self._save_txt_btn,
            self._save_md_btn,
            self._save_docx_btn,
        ):
            btn.setEnabled(has_items)

        if not has_items:
            self._status_badge.set_status("Offline Ready", variant="lavender")
            self._ocr_meta_lbl.setText("Open an image or PDF to start extracting text")
        else:
            item = self.items[self._active_idx]
            if item.backend_used:
                self._status_badge.set_status(f"{item.backend_used}", variant="sage")
                self._ocr_meta_lbl.setText(
                    f"Page {self._active_idx + 1} of {len(self.items)}  •  {item.elapsed_ms} ms"
                )
            else:
                self._status_badge.set_status("Pending OCR", variant="butter")
                self._ocr_meta_lbl.setText(
                    f"Page {self._active_idx + 1} of {len(self.items)}  •  Ready to extract"
                )

    # ------------------------------------------------------------------
    # OCR Execution, Clipboard & Export
    # ------------------------------------------------------------------

    def extract_active_page(self) -> str:
        """Run OCR on the active page (or selected ROI) and update the text editor."""
        if not (0 <= self._active_idx < len(self.items)):
            return ""
        item = self.items[self._active_idx]
        backend_id = str(self._backend_combo.currentData() or "auto")
        lang_tag = str(self._lang_combo.currentData() or "en-US")
        enhance = self._enhance_chk.isChecked()

        text_out, backend_used, elapsed_ms = recognize_image_text(
            image=item.source_image,
            backend_id=backend_id,
            language_tag=lang_tag,
            crop_rect=item.crop_rect,
            enhance_contrast=enhance,
        )
        item.extracted_text = text_out
        item.backend_used = backend_used
        item.elapsed_ms = elapsed_ms

        self._is_syncing_text = True
        try:
            self._text_editor.setPlainText(text_out)
        finally:
            self._is_syncing_text = False

        self._update_word_stats()
        self._rebuild_page_list()
        self._sync_workspace_state()
        return text_out

    def extract_all_pages(self) -> str:
        """Run OCR across every loaded page and combine results into the editor."""
        if not self.items:
            return ""

        backend_id = str(self._backend_combo.currentData() or "auto")
        lang_tag = str(self._lang_combo.currentData() or "en-US")
        enhance = self._enhance_chk.isChecked()

        combined_sections: list[str] = []
        for idx, item in enumerate(self.items):
            text_out, backend_used, elapsed_ms = recognize_image_text(
                image=item.source_image,
                backend_id=backend_id,
                language_tag=lang_tag,
                crop_rect=item.crop_rect,
                enhance_contrast=enhance,
            )
            item.extracted_text = text_out
            item.backend_used = backend_used
            item.elapsed_ms = elapsed_ms

            header = f"--- {item.label} ---"
            combined_sections.append(f"{header}\n{text_out}" if text_out.strip() else header)
            if idx % 2 == 0:
                QApplication.processEvents()

        merged_text = "\n\n".join(combined_sections)
        self._is_syncing_text = True
        try:
            self._text_editor.setPlainText(merged_text)
        finally:
            self._is_syncing_text = False

        self._update_word_stats()
        self._rebuild_page_list()
        self._sync_workspace_state()
        self.notify(
            "OCR Complete",
            f"Extracted text across {len(self.items)} page(s) locally.",
            level=NotificationLevel.SUCCESS,
        )
        return merged_text

    def copy_text_to_clipboard(self) -> None:
        txt = self._text_editor.toPlainText()
        if not txt:
            return
        QApplication.clipboard().setText(txt)
        self.set_status_message("Copied recognized text to clipboard", timeout_ms=1600)

    def export_text_prompt(self) -> bool:
        if not (0 <= self._active_idx < len(self.items)):
            return False
        stem = sanitize_filename(Path(self.items[self._active_idx].label).stem) or "OCR_Output"
        selected, _ = QFileDialog.getSaveFileName(
            self,
            "Export Recognized Text",
            str(Path.home() / "Documents" / f"{stem}.txt"),
            "Plain Text (*.txt);;Markdown (*.md);;Word Document (*.docx)",
        )
        if not selected:
            return False
        ext = Path(selected).suffix.lower() or ".txt"
        return self.export_text_to(selected, ext=ext)

    def export_text_to(self, destination: str | Path, ext: str | None = None) -> bool:
        if not (0 <= self._active_idx < len(self.items)):
            return False
        target = Path(destination)
        if ext and target.suffix.lower() != ext.lower():
            target = target.with_suffix(ext if ext.startswith(".") else f".{ext}")
        elif not target.suffix:
            target = target.with_suffix(".txt")

        content = self._text_editor.toPlainText()
        title = Path(self.items[self._active_idx].label).stem or "Recognized OCR Text"

        try:
            saved_path = export_ocr_text_document(target, content, title=title)
        except FileOperationError as exc:
            show_error_dialog(self, "Export Failed", str(exc))
            return False

        self.notify(
            "Text Exported",
            f"Saved '{saved_path.name}' cleanly.",
            level=NotificationLevel.SUCCESS,
        )
        return True

    def on_theme_updated(self, theme_name: str) -> None:
        _ = theme_name
        if hasattr(self, "_image_canvas"):
            self._image_canvas.set_tokens(self.theme_manager.tokens)


def main() -> int:
    """Application entry point for SimpleOCR.exe."""
    return bootstrap_application(
        app_id=APP_ID,
        window_factory=SimpleOCRWindow,
    )


if __name__ == "__main__":
    raise SystemExit(main())