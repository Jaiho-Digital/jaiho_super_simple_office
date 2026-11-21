"""SimplePDF — Soft Modern Pastel PDF studio with continuous pages, drag-markup, and page tools."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QIcon,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPixmap,
    QShortcut,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from apps.simple_pdf.pdf_engine import (
    APP_ID,
    STAMP_PRESETS,
    AnnotationKind,
    PdfAnnotation,
    PdfDocumentService,
    SearchHit,
    build_sample_pdf_bytes,
    paint_annotations_on_page,
    parse_page_range_spec,
)
from core.file_manager.file_ops import (
    FileOperationError,
    atomic_write_text,
    sanitize_filename,
)
from core.notifications.notifier import NotificationLevel
from core.themes.tokens import LIGHT_THEME, ThemeTokens
from core.ui.base_window import BaseWindow, bootstrap_application
from core.ui.dialogs import ask_confirmation, show_error_dialog
from core.ui.widgets import (
    CardFrame,
    HorizontalDivider,
    SectionHeader,
    StatusBadge,
    VerticalDivider,
    make_button,
)

logger = logging.getLogger(__name__)

_ZOOM_PRESETS: tuple[int, ...] = (60, 75, 90, 100, 125, 150, 175, 200)

_ANNOTATION_SWATCHES: tuple[tuple[str, str], ...] = (
    ("Soft Lavender", "#E8E0F7"),
    ("Sage Mint", "#DDEFE8"),
    ("Blush Rose", "#F7DFE7"),
    ("Butter Cream", "#F8EBCF"),
    ("Powder Sky", "#DFEAF8"),
    ("Accent Violet", "#6F59C9"),
)


class DraggablePageThumbList(QListWidget):
    """Page thumbnail list supporting drag-and-drop page reordering."""

    page_reordered = Signal(int, int)  # (from_index, to_index)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setIconSize(QSize(132, 170))
        self.setSpacing(6)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setStyleSheet(
            "QListWidget { border: none; background: transparent; padding: 0px; }"
        )

    def dropEvent(self, event: QDropEvent) -> None:
        from_row = self.currentRow()
        super().dropEvent(event)
        to_row = self.currentRow()
        if from_row >= 0 and to_row >= 0 and from_row != to_row:
            self.page_reordered.emit(from_row, to_row)


class PdfDocumentCanvas(QWidget):
    """Continuous or single-page PDF surface with drag-to-pan and drag-move/resize annotations."""

    annotation_added = Signal(int, object)  # (display_page_index, PdfAnnotation)
    annotation_selected = Signal(int, str)  # (display_page_index, annotation_id)
    annotation_modified = Signal(int)  # (display_page_index)
    active_page_changed = Signal(int)  # (display_page_index)
    pan_requested = Signal(int, int)  # (dx_px, dy_px)

    def __init__(
        self,
        pdf_service: PdfDocumentService,
        tokens: ThemeTokens = LIGHT_THEME,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = pdf_service
        self._tokens = tokens
        self._zoom_percent: int = 100
        self._continuous_mode: bool = True
        self._current_page_idx: int = 0
        self._viewport_width: int = 760

        self._active_tool: str = "select"  # 'select' | 'highlight' | 'underline' | 'note' | 'text' | 'rect' | 'arrow' | 'stamp' | 'ink'
        self._active_color: str = "#E8E0F7"
        self._active_stamp: str = "APPROVED"
        self._selected_ann_id: str | None = None

        # Cached base page pixmaps keyed by (slot_id, rotation, render_width_px)
        self._pixmap_cache: dict[tuple[str, int, int], QPixmap] = {}
        self._page_rects: list[QRectF] = []

        # Mouse interaction state
        self._interaction_mode: str = "none"  # 'none' | 'pan' | 'move_ann' | 'resize_ann' | 'create_ann'
        self._target_page_idx: int = 0
        self._drag_start_px: QPointF = QPointF()
        self._drag_curr_px: QPointF = QPointF()
        self._last_pan_global: QPoint = QPoint()
        self._last_norm_pt: tuple[float, float] = (0.0, 0.0)
        self._ink_points: list[tuple[float, float]] = []

        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def set_tokens(self, tokens: ThemeTokens) -> None:
        self._tokens = tokens
        self.update()

    def clear_pixmap_cache(self) -> None:
        self._pixmap_cache.clear()

    def set_viewport_width(self, width_px: int) -> None:
        self._viewport_width = max(320, int(width_px))
        self.rebuild_layout()

    def set_zoom_percent(self, zoom_percent: int) -> None:
        self._zoom_percent = max(40, min(250, int(zoom_percent)))
        self.rebuild_layout()

    def set_continuous_mode(self, continuous: bool) -> None:
        self._continuous_mode = bool(continuous)
        self.rebuild_layout()

    def set_current_page(self, display_index: int) -> None:
        if self._service.page_count == 0:
            self._current_page_idx = 0
            return
        self._current_page_idx = max(0, min(self._service.page_count - 1, display_index))
        if not self._continuous_mode:
            self.rebuild_layout()
        else:
            self.update()

    def set_tool(self, tool: str) -> None:
        self._active_tool = tool
        if tool == "select":
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setCursor(Qt.CursorShape.CrossCursor)
        self.update()

    def set_color(self, hex_color: str) -> None:
        self._active_color = hex_color

    def set_stamp_label(self, stamp_text: str) -> None:
        self._active_stamp = stamp_text.strip().upper() or "APPROVED"

    def set_selected_annotation(self, annotation_id: str | None) -> None:
        self._selected_ann_id = annotation_id
        self.update()

    @property
    def selected_annotation_id(self) -> str | None:
        return self._selected_ann_id

    def page_rect(self, display_index: int) -> QRectF:
        if 0 <= display_index < len(self._page_rects):
            return self._page_rects[display_index]
        return QRectF(24.0, 24.0, 612.0, 792.0)

    def rebuild_layout(self) -> None:
        """Compute exact bounding rectangles for all rendered pages in the scrollable canvas."""
        total = self._service.page_count
        self._page_rects = []
        if total == 0:
            self.setFixedSize(self._viewport_width, 400)
            self.update()
            return

        scale = (self._zoom_percent / 100.0) * (96.0 / 72.0)
        top_pad = 24.0
        bottom_pad = 36.0
        page_gap = 22.0

        page_sizes: list[tuple[float, float]] = []
        max_w = 0.0
        for idx in range(total):
            pt = self._service.get_page_size_points(idx)
            pw = max(220.0, round(pt.width() * scale))
            ph = max(220.0, round(pt.height() * scale))
            page_sizes.append((pw, ph))
            if self._continuous_mode or idx == self._current_page_idx:
                max_w = max(max_w, pw)

        canvas_w = max(self._viewport_width, int(round(max_w + 56.0)))
        y_cursor = top_pad

        for idx, (pw, ph) in enumerate(page_sizes):
            if not self._continuous_mode and idx != self._current_page_idx:
                self._page_rects.append(QRectF())
                continue
            left = max(24.0, (float(canvas_w) - pw) / 2.0)
            self._page_rects.append(QRectF(left, y_cursor, pw, ph))
            y_cursor += ph + page_gap

        total_h = max(420, int(round(y_cursor - page_gap + bottom_pad)))
        self.setFixedSize(canvas_w, total_h)
        self.update()

    def _hit_test_page(self, pos: QPointF) -> int | None:
        for idx, r in enumerate(self._page_rects):
            if not r.isNull() and r.contains(pos):
                return idx
        return None

    @staticmethod
    def _to_norm(pos: QPointF, r: QRectF) -> tuple[float, float]:
        nx = max(0.0, min(1.0, (pos.x() - r.left()) / max(1.0, r.width())))
        ny = max(0.0, min(1.0, (pos.y() - r.top()) / max(1.0, r.height())))
        return (nx, ny)

    def _find_selected_annotation(self, page_idx: int) -> PdfAnnotation | None:
        if not self._selected_ann_id or not (0 <= page_idx < self._service.page_count):
            return None
        for ann in self._service.get_slot(page_idx).annotations:
            if ann.annotation_id == self._selected_ann_id:
                return ann
        return None

    def _get_base_page_pixmap(self, display_index: int, logical_w: int) -> QPixmap:
        slot = self._service.get_slot(display_index)
        dpr = max(1.0, float(self.devicePixelRatioF()))
        render_w = max(120, int(round(logical_w * dpr)))
        cache_key = (slot.slot_id, slot.rotation, render_w)

        cached = self._pixmap_cache.get(cache_key)
        if cached is not None:
            return cached

        # Keep cache bounded to ~24 high-DPI pages
        if len(self._pixmap_cache) >= 24:
            oldest_key = next(iter(self._pixmap_cache))
            self._pixmap_cache.pop(oldest_key, None)

        img = self._service.render_page_image(
            display_index=display_index,
            target_width_px=render_w,
            include_annotations=False,
        )
        px = QPixmap.fromImage(img)
        px.setDevicePixelRatio(dpr)
        self._pixmap_cache[cache_key] = px
        return px

    # ------------------------------------------------------------------
    # Mouse Drag-to-Pan, Annotation Drag-Move/Resize, and Markup Creation
    # ------------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._service.page_count == 0:
            super().mousePressEvent(event)
            return

        pos = event.position()
        hit_page = self._hit_test_page(pos)

        # Clicking outside page sheets in select mode -> pan the canvas
        if hit_page is None:
            if self._active_tool == "select":
                self._interaction_mode = "pan"
                self._last_pan_global = event.globalPosition().toPoint()
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return

        if hit_page != self._current_page_idx:
            self._current_page_idx = hit_page
            self.active_page_changed.emit(hit_page)

        p_rect = self._page_rects[hit_page]
        nx, ny = self._to_norm(pos, p_rect)
        slot = self._service.get_slot(hit_page)

        if self._active_tool == "select":
            # 1. Check bottom-right resize handle of currently selected annotation
            curr_sel = self._find_selected_annotation(hit_page)
            if curr_sel is not None and curr_sel.hits_bottom_right_handle(nx, ny):
                self._interaction_mode = "resize_ann"
                self._target_page_idx = hit_page
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
                return

            # 2. Hit-test existing annotations (topmost first) for drag-move
            for ann in reversed(slot.annotations):
                if ann.contains_norm_point(nx, ny):
                    self._selected_ann_id = ann.annotation_id
                    self._interaction_mode = "move_ann"
                    self._target_page_idx = hit_page
                    self._last_norm_pt = (nx, ny)
                    self.setCursor(Qt.CursorShape.SizeAllCursor)
                    self.annotation_selected.emit(hit_page, ann.annotation_id)
                    self.update()
                    return

            # 3. Clicked empty page area -> deselect annotation and start Hand Grab-to-Pan
            if self._selected_ann_id is not None:
                self._selected_ann_id = None
                self.annotation_selected.emit(hit_page, "")
                self.update()

            self._interaction_mode = "pan"
            self._last_pan_global = event.globalPosition().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return

        # Point-click annotation tools: Sticky Note, Text Callout, Stamp
        if self._active_tool == "note":
            note_txt, ok = QInputDialog.getText(
                self.window(),
                "Add Sticky Note",
                "Sticky note text:",
            )
            if ok and note_txt.strip():
                note_color = (
                    self._active_color
                    if self._active_color not in {"#6F59C9"}
                    else "#F8EBCF"
                )
                ann = PdfAnnotation(
                    kind="note",
                    x=min(0.72, nx),
                    y=min(0.90, ny),
                    w=0.25,
                    h=0.055,
                    color_hex=note_color,
                    text=note_txt.strip(),
                )
                self._commit_new_annotation(hit_page, ann)
            return

        if self._active_tool == "text":
            callout_txt, ok = QInputDialog.getText(
                self.window(),
                "Add Text Callout",
                "Text to place on page:",
            )
            if ok and callout_txt.strip():
                txt_col = (
                    "#35343B"
                    if self._active_color in {"#E8E0F7", "#DDEFE8", "#F7DFE7", "#F8EBCF", "#DFEAF8"}
                    else self._active_color
                )
                ann = PdfAnnotation(
                    kind="text",
                    x=min(0.68, nx),
                    y=min(0.92, ny),
                    w=0.30,
                    h=0.05,
                    color_hex=txt_col,
                    text=callout_txt.strip(),
                )
                self._commit_new_annotation(hit_page, ann)
            return

        if self._active_tool == "stamp":
            ann = PdfAnnotation(
                kind="stamp",
                x=min(0.74, nx),
                y=min(0.92, ny),
                w=0.22,
                h=0.048,
                color_hex=self._active_color,
                text=self._active_stamp,
            )
            self._commit_new_annotation(hit_page, ann)
            return

        # Drag-creation tools: highlight, underline, rect, arrow, ink
        self._interaction_mode = "create_ann"
        self._target_page_idx = hit_page
        self._drag_start_px = pos
        self._drag_curr_px = pos
        if self._active_tool == "ink":
            self._ink_points = [(nx, ny)]
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position()

        if self._interaction_mode == "pan":
            curr_global = event.globalPosition().toPoint()
            delta = curr_global - self._last_pan_global
            self._last_pan_global = curr_global
            self.pan_requested.emit(delta.x(), delta.y())
            return

        if self._interaction_mode == "move_ann":
            p_rect = self.page_rect(self._target_page_idx)
            nx, ny = self._to_norm(pos, p_rect)
            dx = nx - self._last_norm_pt[0]
            dy = ny - self._last_norm_pt[1]
            self._last_norm_pt = (nx, ny)
            ann = self._find_selected_annotation(self._target_page_idx)
            if ann is not None:
                ann.move_by(dx, dy)
                self.update()
            return

        if self._interaction_mode == "resize_ann":
            p_rect = self.page_rect(self._target_page_idx)
            nx, ny = self._to_norm(pos, p_rect)
            ann = self._find_selected_annotation(self._target_page_idx)
            if ann is not None:
                ann.resize_to(nx - ann.x, ny - ann.y)
                self.update()
            return

        if self._interaction_mode == "create_ann":
            self._drag_curr_px = pos
            if self._active_tool == "ink":
                p_rect = self.page_rect(self._target_page_idx)
                self._ink_points.append(self._to_norm(pos, p_rect))
            self.update()
            return

        # Hover cursor feedback in Select/Pan mode
        if self._active_tool == "select":
            hit_page = self._hit_test_page(pos)
            if hit_page is not None:
                p_rect = self._page_rects[hit_page]
                nx, ny = self._to_norm(pos, p_rect)
                curr_sel = self._find_selected_annotation(hit_page)
                if curr_sel is not None and curr_sel.hits_bottom_right_handle(nx, ny):
                    self.setCursor(Qt.CursorShape.SizeFDiagCursor)
                    return
                for ann in reversed(self._service.get_slot(hit_page).annotations):
                    if ann.contains_norm_point(nx, ny):
                        self.setCursor(Qt.CursorShape.SizeAllCursor)
                        return
            self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return

        mode = self._interaction_mode
        self._interaction_mode = "none"

        if mode == "pan":
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            return

        if mode in {"move_ann", "resize_ann"}:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.annotation_modified.emit(self._target_page_idx)
            return

        if mode == "create_ann":
            p_rect = self.page_rect(self._target_page_idx)
            x1, y1 = self._to_norm(self._drag_start_px, p_rect)
            x2, y2 = self._to_norm(event.position(), p_rect)
            left, right = min(x1, x2), max(x1, x2)
            top, bottom = min(y1, y2), max(y1, y2)

            if self._active_tool in {"highlight", "underline", "rect", "arrow"}:
                if (right - left) >= 0.012 or (bottom - top) >= 0.012:
                    stroke_col = (
                        "#6F59C9"
                        if self._active_tool in {"underline", "rect", "arrow"}
                        and self._active_color in {"#E8E0F7", "#F8EBCF", "#DFEAF8"}
                        else self._active_color
                    )
                    ann = PdfAnnotation(
                        kind=self._active_tool,  # type: ignore[arg-type]
                        x=left,
                        y=top,
                        w=max(0.02, right - left),
                        h=max(0.015, bottom - top),
                        color_hex=stroke_col,
                    )
                    self._commit_new_annotation(self._target_page_idx, ann)
            elif self._active_tool == "ink":
                self._ink_points.append((x2, y2))
                if len(self._ink_points) >= 2:
                    xs = [pt[0] for pt in self._ink_points]
                    ys = [pt[1] for pt in self._ink_points]
                    min_x, max_x = min(xs), max(xs)
                    min_y, max_y = min(ys), max(ys)
                    stroke_col = (
                        "#6F59C9"
                        if self._active_color in {"#E8E0F7", "#F8EBCF", "#DFEAF8"}
                        else self._active_color
                    )
                    ann = PdfAnnotation(
                        kind="ink",
                        x=min_x,
                        y=min_y,
                        w=max(0.02, max_x - min_x),
                        h=max(0.02, max_y - min_y),
                        color_hex=stroke_col,
                        points=list(self._ink_points),
                    )
                    self._commit_new_annotation(self._target_page_idx, ann)
                self._ink_points.clear()
            self.update()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseDoubleClickEvent(event)
            return
        pos = event.position()
        hit_page = self._hit_test_page(pos)
        if hit_page is None:
            return
        p_rect = self._page_rects[hit_page]
        nx, ny = self._to_norm(pos, p_rect)
        for ann in reversed(self._service.get_slot(hit_page).annotations):
            if ann.contains_norm_point(nx, ny) and ann.kind in {"note", "text", "stamp"}:
                updated_txt, ok = QInputDialog.getText(
                    self.window(),
                    f"Edit {ann.summary_label().split(':')[0]}",
                    "Text:",
                    text=ann.text,
                )
                if ok and updated_txt.strip():
                    ann.text = (
                        updated_txt.strip().upper()
                        if ann.kind == "stamp"
                        else updated_txt.strip()
                    )
                    self.annotation_modified.emit(hit_page)
                    self.update()
                return

    def _commit_new_annotation(self, page_idx: int, ann: PdfAnnotation) -> None:
        self._selected_ann_id = ann.annotation_id
        self.annotation_added.emit(page_idx, ann)
        self.update()

    # ------------------------------------------------------------------
    # Painting Visible Pages & Live Overlays
    # ------------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        dirty_rect = QRectF(event.rect())
        painter.fillRect(dirty_rect, QColor(self._tokens.bg_canvas))

        for idx, p_rect in enumerate(self._page_rects):
            if p_rect.isNull() or not p_rect.intersects(dirty_rect):
                continue

            # Subtle grounded shadow + Snow paper sheet
            shadow_r = p_rect.translated(0.0, 3.0)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(30, 30, 38, 16))
            painter.drawRoundedRect(shadow_r, 4.0, 4.0)

            px = self._get_base_page_pixmap(idx, int(round(p_rect.width())))
            painter.drawPixmap(p_rect.topLeft(), px)

            # Active page outline highlight in continuous scroll
            border_col = (
                QColor(self._tokens.accent_primary)
                if (self._continuous_mode and idx == self._current_page_idx)
                else QColor(self._tokens.border_strong)
            )
            painter.setPen(QPen(border_col, 1.3 if idx == self._current_page_idx else 1.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(p_rect.adjusted(0.0, 0.0, -1.0, -1.0))

            # Render annotations & selection handles on this page
            slot = self._service.get_slot(idx)
            sel_id = self._selected_ann_id if idx == self._current_page_idx else None
            paint_annotations_on_page(
                painter=painter,
                page_rect=p_rect,
                annotations=slot.annotations,
                selected_id=sel_id,
            )

        # Render live creation rubber-band preview
        if self._interaction_mode == "create_ann":
            preview = QRectF(self._drag_start_px, self._drag_curr_px).normalized()
            if self._active_tool in {"highlight", "rect"}:
                fill = QColor(self._active_color)
                fill.setAlpha(120)
                painter.setPen(QPen(QColor(self._tokens.accent_primary), 1.3, Qt.PenStyle.DashLine))
                painter.setBrush(fill)
                painter.drawRoundedRect(preview, 6.0, 6.0)
            elif self._active_tool in {"underline", "arrow"}:
                painter.setPen(QPen(QColor(self._tokens.accent_primary), 2.2))
                painter.drawLine(self._drag_start_px, self._drag_curr_px)
            elif self._active_tool == "ink" and len(self._ink_points) >= 2:
                p_rect = self.page_rect(self._target_page_idx)
                path = QPainterPath()
                path.moveTo(
                    p_rect.left() + self._ink_points[0][0] * p_rect.width(),
                    p_rect.top() + self._ink_points[0][1] * p_rect.height(),
                )
                for nx, ny in self._ink_points[1:]:
                    path.lineTo(
                        p_rect.left() + nx * p_rect.width(),
                        p_rect.top() + ny * p_rect.height(),
                    )
                pen = QPen(QColor(self._tokens.accent_primary), 2.5)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                painter.drawPath(path)

        painter.end()


class SimplePDFWindow(BaseWindow):
    """Main window for SimplePDF — continuous reader, drag-annotator, and page organizer."""

    def __init__(self) -> None:
        super().__init__(app_id=APP_ID, min_width=980, min_height=650)
        if self.settings.get("window_width") is None:
            self.resize(1240, 790)

        self.setAcceptDrops(True)
        self.pdf_service = PdfDocumentService()
        self._current_display_idx: int = 0
        self._zoom_percent: int = 100
        self._continuous_mode: bool = True
        self._sidebar_visible: bool = bool(self.settings.get("sidebar_visible", True))
        self._inspector_visible: bool = bool(self.settings.get("inspector_visible", True))
        self._has_unsaved_edits: bool = False
        self._is_syncing_scroll: bool = False

        self._build_header_actions()
        self._build_workspace()
        self._bind_shortcuts()

        self._sync_panel_visibility()
        self._sync_document_state_ui()

    def _build_header_actions(self) -> None:
        """Mount panel toggles, Open, Append/Merge, Save PDF, and Tools in header."""
        self._sidebar_btn = QPushButton("Pages", self)
        self._sidebar_btn.setProperty("variant", "segmented")
        self._sidebar_btn.setCheckable(True)
        self._sidebar_btn.setChecked(self._sidebar_visible)
        self._sidebar_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sidebar_btn.setToolTip("Toggle Pages, Search & Extracted Text sidebar")
        self._sidebar_btn.setStyleSheet("padding: 4px 10px; font-size: 12px;")
        self._sidebar_btn.clicked.connect(self._toggle_sidebar)
        self.header_actions_layout.addWidget(self._sidebar_btn)

        open_btn = make_button(
            text="Open PDF",
            variant="ghost",
            tooltip="Open a PDF file (Ctrl+O)",
            on_click=self.open_pdf_prompt,
            icon_name="folder",
            icon_size=14,
            parent=self,
        )
        open_btn.setStyleSheet("padding: 4px 10px; font-size: 12px;")
        self.header_actions_layout.addWidget(open_btn)

        self._append_btn = make_button(
            text="+ Append PDF",
            variant="ghost",
            tooltip="Append another PDF onto the end of this document",
            on_click=self.append_pdf_prompt,
            parent=self,
        )
        self._append_btn.setStyleSheet("padding: 4px 10px; font-size: 12px;")
        self.header_actions_layout.addWidget(self._append_btn)

        self._save_btn = make_button(
            text="Save PDF",
            variant="pastel",
            tooltip="Save annotated & reorganized PDF (Ctrl+S)",
            on_click=self.save_pdf_prompt,
            icon_name="check",
            icon_size=14,
            parent=self,
        )
        self._save_btn.setStyleSheet("padding: 4px 11px; font-size: 12px;")
        self.header_actions_layout.addWidget(self._save_btn)

        self._tools_btn = QPushButton("Tools", self)
        self._tools_btn.setProperty("variant", "ghost")
        self._tools_btn.setProperty("has_menu", True)
        self._tools_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._tools_btn.setToolTip("Extract pages, merge PDFs, rotate, or export text")
        self._tools_btn.setStyleSheet("padding: 4px 22px 4px 10px; font-size: 12px;")

        tools_menu = QMenu(self._tools_btn)
        tools_menu.addAction("Rotate Current Page 90° CW (Ctrl+R)", self.rotate_current_page)
        tools_menu.addAction("Duplicate Current Page", self.duplicate_current_page)
        tools_menu.addAction("Remove Current Page", self.delete_current_page)
        tools_menu.addSeparator()
        tools_menu.addAction("Extract / Split Page Range to PDF...", self.extract_pages_prompt)
        tools_menu.addAction("Merge Multiple PDF Files...", self.merge_pdfs_prompt)
        tools_menu.addSeparator()
        tools_menu.addAction("Export All Extracted Text (.txt)...", self.extract_text_prompt)
        tools_menu.addAction("Clear Annotations on Current Page", self.clear_current_page_annotations)
        self._tools_btn.setMenu(tools_menu)
        self.header_actions_layout.addWidget(self._tools_btn)

        self._inspector_btn = QPushButton("Inspector", self)
        self._inspector_btn.setProperty("variant", "segmented")
        self._inspector_btn.setCheckable(True)
        self._inspector_btn.setChecked(self._inspector_visible)
        self._inspector_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._inspector_btn.setToolTip("Show or hide Page & Annotations inspector")
        self._inspector_btn.setStyleSheet("padding: 4px 10px; font-size: 12px;")
        self._inspector_btn.clicked.connect(self._toggle_inspector)
        self.header_actions_layout.addWidget(self._inspector_btn)

    def _build_workspace(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)

        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ==============================================================
        # 1. Pastel Reader & Markup Ribbon Bar
        # ==============================================================
        ribbon_bar = QFrame(central)
        ribbon_bar.setProperty("surface", "app-header")
        ribbon_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        tb_row = QHBoxLayout(ribbon_bar)
        tb_row.setContentsMargins(14, 6, 14, 6)
        tb_row.setSpacing(4)

        # Page Navigation
        self._prev_btn = make_button(
            text="‹",
            variant="ghost",
            tooltip="Previous page (PageUp)",
            on_click=self.previous_page,
            parent=ribbon_bar,
        )
        self._prev_btn.setStyleSheet("padding: 4px 8px; font-size: 14px;")
        tb_row.addWidget(self._prev_btn)

        self._page_spin = QSpinBox(ribbon_bar)
        self._page_spin.setRange(1, 1)
        self._page_spin.setFixedWidth(54)
        self._page_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._page_spin.setToolTip("Jump to page number")
        self._page_spin.setStyleSheet("padding: 3px 4px; font-size: 12px;")
        self._page_spin.valueChanged.connect(self._on_page_spin_changed)
        tb_row.addWidget(self._page_spin)

        self._page_total_lbl = QLabel("of 0", ribbon_bar)
        self._page_total_lbl.setProperty("role", "muted")
        tb_row.addWidget(self._page_total_lbl)

        self._next_btn = make_button(
            text="›",
            variant="ghost",
            tooltip="Next page (PageDown)",
            on_click=self.next_page,
            parent=ribbon_bar,
        )
        self._next_btn.setStyleSheet("padding: 4px 8px; font-size: 14px;")
        tb_row.addWidget(self._next_btn)

        tb_row.addWidget(VerticalDivider(ribbon_bar))

        # Continuous Scroll vs Single-Page View Toggle
        self._continuous_btn = QPushButton("Continuous", ribbon_bar)
        self._continuous_btn.setProperty("variant", "segmented")
        self._continuous_btn.setCheckable(True)
        self._continuous_btn.setChecked(True)
        self._continuous_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._continuous_btn.setToolTip("Toggle continuous multi-page scroll vs single-page view")
        self._continuous_btn.setStyleSheet("padding: 4px 9px; font-size: 12px;")
        self._continuous_btn.clicked.connect(self._toggle_continuous_mode)
        tb_row.addWidget(self._continuous_btn)

        # Zoom Controls
        zoom_out_btn = make_button(
            text="−",
            variant="ghost",
            tooltip="Zoom out",
            on_click=lambda: self._step_zoom(-1),
            parent=ribbon_bar,
        )
        zoom_out_btn.setStyleSheet("padding: 4px 7px; font-size: 13px;")
        tb_row.addWidget(zoom_out_btn)

        self._zoom_combo = QComboBox(ribbon_bar)
        for z in _ZOOM_PRESETS:
            self._zoom_combo.addItem(f"{z}%", z)
        self._zoom_combo.setCurrentIndex(3)
        self._zoom_combo.setToolTip("Zoom percentage")
        self._zoom_combo.setStyleSheet("padding: 4px 7px; font-size: 12px;")
        self._zoom_combo.currentIndexChanged.connect(self._on_zoom_combo_changed)
        tb_row.addWidget(self._zoom_combo)

        zoom_in_btn = make_button(
            text="+",
            variant="ghost",
            tooltip="Zoom in",
            on_click=lambda: self._step_zoom(1),
            parent=ribbon_bar,
        )
        zoom_in_btn.setStyleSheet("padding: 4px 7px; font-size: 13px;")
        tb_row.addWidget(zoom_in_btn)

        fit_w_btn = make_button(
            text="Fit Width",
            variant="ghost",
            tooltip="Fit PDF page width to viewport",
            on_click=self.fit_page_width,
            parent=ribbon_bar,
        )
        fit_w_btn.setStyleSheet("padding: 4px 8px; font-size: 12px;")
        tb_row.addWidget(fit_w_btn)

        tb_row.addWidget(VerticalDivider(ribbon_bar))

        # Interactive Markup & Drag Tools
        self._tool_group = QButtonGroup(self)
        self._tool_group.setExclusive(True)
        self._tool_buttons: dict[str, QPushButton] = {}

        tool_specs: tuple[tuple[str, str, str], ...] = (
            ("select", "Hand / Select", "Drag page to pan, or click & drag annotations to move/resize"),
            ("highlight", "Highlight", "Drag pastel highlight across text"),
            ("underline", "Underline", "Drag crisp underline beneath text"),
            ("note", "Note", "Click on page to drop a draggable sticky note"),
            ("text", "Text", "Click on page to insert a text callout"),
            ("rect", "Box", "Drag a rounded pastel callout box"),
            ("arrow", "Arrow", "Drag a directional pastel arrow"),
            ("ink", "Draw", "Sketch freehand ink or signature"),
        )
        for tool_id, label, tip in tool_specs:
            tbtn = QPushButton(label, ribbon_bar)
            tbtn.setProperty("variant", "segmented")
            tbtn.setCheckable(True)
            tbtn.setChecked(tool_id == "select")
            tbtn.setCursor(Qt.CursorShape.PointingHandCursor)
            tbtn.setToolTip(tip)
            tbtn.setStyleSheet("padding: 4px 8px; font-size: 12px;")
            tbtn.clicked.connect(lambda _=False, tid=tool_id: self.set_annotation_tool(tid))
            self._tool_group.addButton(tbtn)
            self._tool_buttons[tool_id] = tbtn
            tb_row.addWidget(tbtn)

        # Pastel Stamp Picker Button
        stamp_btn = QPushButton("Stamp", ribbon_bar)
        stamp_btn.setProperty("variant", "segmented")
        stamp_btn.setProperty("has_menu", True)
        stamp_btn.setCheckable(True)
        stamp_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        stamp_btn.setToolTip("Click page to place a pastel status stamp")
        stamp_btn.setStyleSheet("padding: 4px 22px 4px 8px; font-size: 12px;")
        stamp_menu = QMenu(stamp_btn)
        for stamp_lbl in STAMP_PRESETS:
            s_act = stamp_menu.addAction(stamp_lbl)
            s_act.triggered.connect(lambda _=False, st=stamp_lbl: self._select_stamp_tool(st))
        stamp_btn.setMenu(stamp_menu)
        self._tool_group.addButton(stamp_btn)
        self._tool_buttons["stamp"] = stamp_btn
        tb_row.addWidget(stamp_btn)

        # Pastel Color Picker
        self._color_combo = QComboBox(ribbon_bar)
        for swatch_name, hex_val in _ANNOTATION_SWATCHES:
            self._color_combo.addItem(swatch_name, hex_val)
        self._color_combo.setToolTip("Markup pastel color")
        self._color_combo.setStyleSheet("padding: 4px 7px; font-size: 12px;")
        self._color_combo.currentIndexChanged.connect(self._on_annotation_color_changed)
        tb_row.addWidget(self._color_combo)

        undo_btn = make_button(
            text="Undo",
            variant="ghost",
            tooltip="Undo last markup on current page (Ctrl+Z)",
            on_click=self.undo_last_annotation,
            parent=ribbon_bar,
        )
        undo_btn.setStyleSheet("padding: 4px 7px; font-size: 12px;")
        tb_row.addWidget(undo_btn)

        tb_row.addStretch(1)
        outer.addWidget(ribbon_bar)

        # ==============================================================
        # 2. Main 3-Pane Studio (Pages/Search/Text | Continuous Stage | Inspector)
        # ==============================================================
        body_row = QHBoxLayout()
        body_row.setContentsMargins(0, 0, 0, 0)
        body_row.setSpacing(0)

        # Left Sidebar
        self._sidebar = QFrame(central)
        self._sidebar.setProperty("surface", "sidebar")
        self._sidebar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._sidebar.setFixedWidth(240)

        sb_layout = QVBoxLayout(self._sidebar)
        sb_layout.setContentsMargins(12, 12, 12, 12)
        sb_layout.setSpacing(8)

        self._sidebar_tabs = QTabWidget(self._sidebar)

        # Tab 1: Draggable Page Thumbnails Organizer
        pages_tab = QWidget(self._sidebar_tabs)
        pages_col = QVBoxLayout(pages_tab)
        pages_col.setContentsMargins(6, 8, 6, 6)
        pages_col.setSpacing(8)

        drag_hint = QLabel("Drag thumbnails to reorder pages", pages_tab)
        drag_hint.setProperty("role", "caption")
        pages_col.addWidget(drag_hint)

        self._thumb_list = DraggablePageThumbList(pages_tab)
        self._thumb_list.currentRowChanged.connect(self._on_thumbnail_selected)
        self._thumb_list.page_reordered.connect(self._on_pages_reordered)
        pages_col.addWidget(self._thumb_list, 1)

        page_btns_row = QHBoxLayout()
        page_btns_row.setSpacing(4)
        self._sb_rotate_btn = make_button(
            text="Rotate",
            variant="ghost",
            tooltip="Rotate active page 90° CW",
            on_click=self.rotate_current_page,
            parent=pages_tab,
        )
        self._sb_rotate_btn.setStyleSheet("padding: 4px 6px; font-size: 11px;")
        page_btns_row.addWidget(self._sb_rotate_btn, 1)

        self._sb_dup_btn = make_button(
            text="Duplicate",
            variant="ghost",
            tooltip="Duplicate active page",
            on_click=self.duplicate_current_page,
            parent=pages_tab,
        )
        self._sb_dup_btn.setStyleSheet("padding: 4px 6px; font-size: 11px;")
        page_btns_row.addWidget(self._sb_dup_btn, 1)

        self._sb_del_btn = make_button(
            text="Delete",
            variant="ghost",
            tooltip="Remove active page",
            on_click=self.delete_current_page,
            parent=pages_tab,
        )
        self._sb_del_btn.setStyleSheet("padding: 4px 6px; font-size: 11px;")
        page_btns_row.addWidget(self._sb_del_btn, 1)
        pages_col.addLayout(page_btns_row)

        self._sidebar_tabs.addTab(pages_tab, "Pages")

        # Tab 2: Full-Text Search
        search_tab = QWidget(self._sidebar_tabs)
        search_col = QVBoxLayout(search_tab)
        search_col.setContentsMargins(6, 8, 6, 6)
        search_col.setSpacing(8)

        s_row = QHBoxLayout()
        s_row.setSpacing(4)
        self._search_input = QLineEdit(search_tab)
        self._search_input.setPlaceholderText("Find keyword...")
        self._search_input.returnPressed.connect(self._on_search_triggered)
        s_row.addWidget(self._search_input, 1)

        find_btn = make_button(
            text="Find",
            variant="pastel",
            on_click=self._on_search_triggered,
            parent=search_tab,
        )
        find_btn.setStyleSheet("padding: 5px 10px; font-size: 12px;")
        s_row.addWidget(find_btn)
        search_col.addLayout(s_row)

        self._search_status_lbl = QLabel("Search across all pages in this PDF.", search_tab)
        self._search_status_lbl.setProperty("role", "caption")
        self._search_status_lbl.setWordWrap(True)
        search_col.addWidget(self._search_status_lbl)

        self._search_results_list = QListWidget(search_tab)
        self._search_results_list.setWordWrap(True)
        self._search_results_list.setStyleSheet(
            "QListWidget { border: none; background: transparent; padding: 0px; }"
        )
        self._search_results_list.itemClicked.connect(self._on_search_hit_clicked)
        search_col.addWidget(self._search_results_list, 1)

        self._sidebar_tabs.addTab(search_tab, "Search")

        # Tab 3: Live Page Text Reader
        text_tab = QWidget(self._sidebar_tabs)
        text_col = QVBoxLayout(text_tab)
        text_col.setContentsMargins(6, 8, 6, 6)
        text_col.setSpacing(8)

        text_hdr = QLabel("Extracted Text (Current Page)", text_tab)
        text_hdr.setProperty("role", "caption")
        text_col.addWidget(text_hdr)

        self._page_text_view = QPlainTextEdit(text_tab)
        self._page_text_view.setReadOnly(True)
        self._page_text_view.setPlaceholderText("Open a PDF to inspect its selectable text...")
        text_col.addWidget(self._page_text_view, 1)

        export_txt_btn = make_button(
            text="Export All Text (.txt)",
            variant="pastel",
            on_click=self.extract_text_prompt,
            parent=text_tab,
        )
        text_col.addWidget(export_txt_btn)

        self._sidebar_tabs.addTab(text_tab, "Text")
        sb_layout.addWidget(self._sidebar_tabs, 1)
        body_row.addWidget(self._sidebar)

        # Center Stage: Empty State OR Continuous Multi-Page Viewport
        self._stage_host = QWidget(central)
        stage_layout = QVBoxLayout(self._stage_host)
        stage_layout.setContentsMargins(0, 0, 0, 0)
        stage_layout.setSpacing(0)

        # Welcoming Pastel Empty-State Card
        self._empty_state_container = QWidget(self._stage_host)
        empty_outer = QVBoxLayout(self._empty_state_container)
        empty_outer.setAlignment(Qt.AlignmentFlag.AlignCenter)

        empty_card = CardFrame(padding=32, spacing=14, parent=self._empty_state_container)
        empty_card.setMaximumWidth(480)

        hero_title = QLabel("Open a PDF to Read, Annotate & Organize", empty_card)
        hero_title.setProperty("role", "title")
        hero_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hero_title.setWordWrap(True)
        empty_card.body_layout.addWidget(hero_title)

        hero_desc = QLabel(
            "Scroll seamlessly across pages, drag to pan, highlight passages in soft pastel tones, "
            "place draggable sticky notes & stamps, or reorder and merge PDFs—100% offline.",
            empty_card,
        )
        hero_desc.setProperty("role", "muted")
        hero_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hero_desc.setWordWrap(True)
        empty_card.body_layout.addWidget(hero_desc)

        empty_card.body_layout.addWidget(HorizontalDivider(empty_card))

        cta_row = QHBoxLayout()
        cta_row.setSpacing(10)
        cta_row.addStretch(1)

        open_cta = make_button(
            text="Open PDF File...",
            variant="primary",
            on_click=self.open_pdf_prompt,
            icon_name="folder",
            icon_size=15,
            parent=empty_card,
        )
        cta_row.addWidget(open_cta)

        sample_cta = make_button(
            text="Open Sample PDF",
            variant="pastel",
            tooltip="Generate and preview a multi-page Soft Modern Pastel sample PDF",
            on_click=self.open_sample_pdf,
            parent=empty_card,
        )
        cta_row.addWidget(sample_cta)
        cta_row.addStretch(1)
        empty_card.body_layout.addLayout(cta_row)

        drop_hint = QLabel(
            "Tip: Drag & drop one or more .pdf files directly onto this window.",
            empty_card,
        )
        drop_hint.setProperty("role", "caption")
        drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_card.body_layout.addWidget(drop_hint)

        empty_outer.addWidget(empty_card)
        stage_layout.addWidget(self._empty_state_container, 1)

        # Scrollable Multi-Page Document Canvas
        self._scroll_area = QScrollArea(self._stage_host)
        self._scroll_area.setWidgetResizable(False)
        self._scroll_area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll_area.verticalScrollBar().valueChanged.connect(self._on_scroll_position_changed)

        self._doc_canvas = PdfDocumentCanvas(
            pdf_service=self.pdf_service,
            tokens=self.theme_manager.tokens,
            parent=self._scroll_area,
        )
        self._doc_canvas.annotation_added.connect(self._on_annotation_added)
        self._doc_canvas.annotation_selected.connect(self._on_annotation_selected)
        self._doc_canvas.annotation_modified.connect(self._on_annotation_modified)
        self._doc_canvas.active_page_changed.connect(self._on_canvas_active_page_changed)
        self._doc_canvas.pan_requested.connect(self._on_canvas_pan_requested)

        self._scroll_area.setWidget(self._doc_canvas)
        self._scroll_area.setVisible(False)
        stage_layout.addWidget(self._scroll_area, 1)

        body_row.addWidget(self._stage_host, 1)

        # Right Pane: Page Properties & Markups Layer Inspector
        self._inspector = QFrame(central)
        self._inspector.setProperty("surface", "inspector")
        self._inspector.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._inspector.setFixedWidth(260)

        insp_layout = QVBoxLayout(self._inspector)
        insp_layout.setContentsMargins(14, 14, 14, 14)
        insp_layout.setSpacing(10)

        insp_layout.addWidget(SectionHeader("Page & Markups", parent=self._inspector))
        insp_layout.addWidget(HorizontalDivider(self._inspector))

        self._page_spec_lbl = QLabel("No document open", self._inspector)
        self._page_spec_lbl.setProperty("role", "muted")
        self._page_spec_lbl.setWordWrap(True)
        insp_layout.addWidget(self._page_spec_lbl)

        quick_grid = QHBoxLayout()
        quick_grid.setSpacing(6)
        self._insp_rot_ccw = make_button(
            text="↺ 90°",
            variant="ghost",
            tooltip="Rotate active page counter-clockwise",
            on_click=lambda: self.rotate_current_page(-90),
            parent=self._inspector,
        )
        self._insp_rot_ccw.setStyleSheet("padding: 4px 8px; font-size: 12px;")
        quick_grid.addWidget(self._insp_rot_ccw, 1)

        self._insp_rot_cw = make_button(
            text="↻ 90°",
            variant="ghost",
            tooltip="Rotate active page clockwise (Ctrl+R)",
            on_click=lambda: self.rotate_current_page(90),
            parent=self._inspector,
        )
        self._insp_rot_cw.setStyleSheet("padding: 4px 8px; font-size: 12px;")
        quick_grid.addWidget(self._insp_rot_cw, 1)
        insp_layout.addLayout(quick_grid)

        self._insp_extract_btn = make_button(
            text="Split / Extract Pages...",
            variant="pastel",
            tooltip="Save specific page numbers or ranges into a new PDF",
            on_click=self.extract_pages_prompt,
            parent=self._inspector,
        )
        insp_layout.addWidget(self._insp_extract_btn)

        insp_layout.addWidget(HorizontalDivider(self._inspector))

        ann_hdr = QLabel("Annotations on Active Page", self._inspector)
        ann_hdr.setProperty("role", "caption")
        ann_hdr.setStyleSheet("font-weight: 600;")
        insp_layout.addWidget(ann_hdr)

        self._ann_list = QListWidget(self._inspector)
        self._ann_list.setStyleSheet(
            "QListWidget { border: none; background: transparent; padding: 0px; }"
        )
        self._ann_list.itemClicked.connect(self._on_inspector_ann_clicked)
        insp_layout.addWidget(self._ann_list, 1)

        ann_actions = QHBoxLayout()
        ann_actions.setSpacing(6)
        self._del_ann_btn = make_button(
            text="Delete Selected",
            variant="ghost",
            tooltip="Delete selected annotation (Del)",
            on_click=self.delete_selected_annotation,
            parent=self._inspector,
        )
        self._del_ann_btn.setStyleSheet("padding: 4px 8px; font-size: 11px;")
        ann_actions.addWidget(self._del_ann_btn, 1)

        self._clear_ann_btn = make_button(
            text="Clear Page",
            variant="ghost",
            tooltip="Clear all annotations on active page",
            on_click=self.clear_current_page_annotations,
            parent=self._inspector,
        )
        self._clear_ann_btn.setStyleSheet("padding: 4px 8px; font-size: 11px;")
        ann_actions.addWidget(self._clear_ann_btn, 1)
        insp_layout.addLayout(ann_actions)

        body_row.addWidget(self._inspector)
        outer.addLayout(body_row, 1)

        # ==============================================================
        # 3. Bottom Telemetry Bar
        # ==============================================================
        footer_bar = QFrame(central)
        footer_bar.setProperty("surface", "footer-bar")
        footer_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        footer_bar.setFixedHeight(34)

        f_row = QHBoxLayout(footer_bar)
        f_row.setContentsMargins(16, 4, 16, 4)
        f_row.setSpacing(10)

        self._status_badge = StatusBadge("No Document", variant="lavender", parent=footer_bar)
        f_row.addWidget(self._status_badge)

        self._doc_info_lbl = QLabel("Ready to open a PDF document", footer_bar)
        self._doc_info_lbl.setProperty("role", "muted")
        f_row.addWidget(self._doc_info_lbl)

        f_row.addStretch(1)

        self._page_metrics_lbl = QLabel("Zoom: 100%", footer_bar)
        self._page_metrics_lbl.setProperty("role", "muted")
        f_row.addWidget(self._page_metrics_lbl)

        outer.addWidget(footer_bar)

    def _bind_shortcuts(self) -> None:
        QShortcut(QKeySequence.StandardKey.Open, self).activated.connect(self.open_pdf_prompt)
        QShortcut(QKeySequence.StandardKey.Save, self).activated.connect(self.save_pdf_prompt)
        QShortcut(QKeySequence.StandardKey.Find, self).activated.connect(self.focus_search_tab)
        QShortcut(QKeySequence("Ctrl+R"), self).activated.connect(lambda: self.rotate_current_page(90))
        QShortcut(QKeySequence("Ctrl+Z"), self).activated.connect(self.undo_last_annotation)
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self).activated.connect(
            self.delete_selected_annotation
        )
        QShortcut(QKeySequence(Qt.Key.Key_PageDown), self).activated.connect(self.next_page)
        QShortcut(QKeySequence(Qt.Key.Key_PageUp), self).activated.connect(self.previous_page)

    # ------------------------------------------------------------------
    # Drag-and-Drop PDF Opening / Appending & Ctrl+Wheel Zoom
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        mime = event.mimeData()
        if mime is not None and mime.hasUrls():
            for url in mime.urls():
                if url.isLocalFile() and url.toLocalFile().lower().endswith(".pdf"):
                    event.acceptProposedAction()
                    return
        super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        mime = event.mimeData()
        if mime is not None and mime.hasUrls():
            pdf_paths = [
                url.toLocalFile()
                for url in mime.urls()
                if url.isLocalFile() and url.toLocalFile().lower().endswith(".pdf")
            ]
            if pdf_paths:
                event.acceptProposedAction()
                if not self.pdf_service.is_loaded:
                    self.load_file(pdf_paths[0])
                    for extra in pdf_paths[1:]:
                        self.append_pdf_file(extra)
                else:
                    for p in pdf_paths:
                        self.append_pdf_file(p)
                return
        super().dropEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta_y = event.angleDelta().y()
            if delta_y > 0:
                self._step_zoom(1)
            elif delta_y < 0:
                self._step_zoom(-1)
            event.accept()
            return
        super().wheelEvent(event)

    # ------------------------------------------------------------------
    # Document Loading, Thumbnails & Inspector Sync
    # ------------------------------------------------------------------

    def open_pdf_prompt(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Open PDF Document",
            str(Path.home() / "Documents"),
            "PDF Documents (*.pdf)",
        )
        if not selected:
            return
        self.load_file(selected)

    def append_pdf_prompt(self) -> None:
        selected_files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select PDF File(s) to Append",
            str(Path.home() / "Documents"),
            "PDF Documents (*.pdf)",
        )
        for file_path in selected_files:
            self.append_pdf_file(file_path)

    def append_pdf_file(self, file_path: str | Path) -> bool:
        try:
            added = self.pdf_service.append_file(file_path)
        except FileOperationError as exc:
            show_error_dialog(self, "Could Not Append PDF", str(exc))
            return False

        self._has_unsaved_edits = True
        self._doc_canvas.set_viewport_width(self._scroll_area.viewport().width())
        self._rebuild_thumbnails()
        self._sync_document_state_ui()
        self._refresh_inspector_and_text()
        self.notify(
            "PDF Appended",
            f"Added {added} page(s) from '{Path(file_path).name}'.",
            level=NotificationLevel.SUCCESS,
        )
        return True

    def open_sample_pdf(self) -> bool:
        """Generate and open a multi-page Soft Modern Pastel showcase PDF."""
        try:
            pdf_bytes = build_sample_pdf_bytes()
            tmp_dir = Path(tempfile.gettempdir()) / "jaiho_simple_pdf"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            sample_path = tmp_dir / "Jaiho_Office_Showcase.pdf"
            sample_path.write_bytes(pdf_bytes)
        except Exception as exc:
            show_error_dialog(self, "Could Not Create Sample PDF", str(exc))
            return False

        return self.load_file(sample_path)

    def load_file(self, file_path: str | Path) -> bool:
        """Load `file_path` into SimplePDF and render the continuous page canvas."""
        try:
            total_pages = self.pdf_service.load_file(file_path)
        except FileOperationError as exc:
            show_error_dialog(self, "Could Not Open PDF", str(exc))
            return False

        self._current_display_idx = 0
        self._has_unsaved_edits = False
        self._search_results_list.clear()
        self._search_status_lbl.setText("Search across all pages in this PDF.")
        self._doc_canvas.clear_pixmap_cache()
        self._doc_canvas.set_selected_annotation(None)
        self._doc_canvas.set_current_page(0)
        self._doc_canvas.set_viewport_width(max(520, self._scroll_area.viewport().width()))

        self._rebuild_thumbnails()
        self._sync_document_state_ui()
        self._refresh_inspector_and_text()
        self._scroll_area.verticalScrollBar().setValue(0)

        loaded_name = self.pdf_service.primary_path.name if self.pdf_service.primary_path else "Document.pdf"
        self.set_status_message(f"Opened {loaded_name} ({total_pages} pages)", timeout_ms=2200)
        return True

    def _rebuild_thumbnails(self) -> None:
        self._thumb_list.blockSignals(True)
        try:
            self._thumb_list.clear()
            for disp_idx in range(self.pdf_service.page_count):
                thumb_px = self.pdf_service.render_thumbnail_pixmap(disp_idx, width_px=132)
                item = QListWidgetItem(f"Page {disp_idx + 1}")
                item.setIcon(QIcon(thumb_px))
                self._thumb_list.addItem(item)
            if self.pdf_service.page_count > 0:
                self._thumb_list.setCurrentRow(self._current_display_idx)
        finally:
            self._thumb_list.blockSignals(False)

    def _refresh_single_thumbnail(self, display_index: int) -> None:
        if not (0 <= display_index < self.pdf_service.page_count):
            return
        item = self._thumb_list.item(display_index)
        if item is not None:
            thumb_px = self.pdf_service.render_thumbnail_pixmap(display_index, width_px=132)
            item.setIcon(QIcon(thumb_px))

    def _refresh_inspector_and_text(self) -> None:
        total = self.pdf_service.page_count
        if total == 0:
            self._page_spec_lbl.setText("No document open")
            self._page_text_view.clear()
            self._ann_list.clear()
            return

        idx = self._current_display_idx
        pt_size = self.pdf_service.get_page_size_points(idx)
        rot = self.pdf_service.get_page_rotation(idx)
        self._page_spec_lbl.setText(
            f"Page {idx + 1} of {total}  •  "
            f"{pt_size.width() / 72.0:.1f} × {pt_size.height() / 72.0:.1f} in  •  "
            f"Rotation: {rot}°"
        )

        # Populate live extracted text tab
        self._page_text_view.setPlainText(self.pdf_service.get_page_text(idx))

        # Populate active page annotations list
        self._ann_list.blockSignals(True)
        try:
            self._ann_list.clear()
            sel_id = self._doc_canvas.selected_annotation_id
            for ann in self.pdf_service.get_annotations(idx):
                item = QListWidgetItem(ann.summary_label())
                item.setData(Qt.ItemDataRole.UserRole, ann.annotation_id)
                self._ann_list.addItem(item)
                if sel_id and ann.annotation_id == sel_id:
                    self._ann_list.setCurrentItem(item)
        finally:
            self._ann_list.blockSignals(False)

    def _sync_document_state_ui(self) -> None:
        loaded = self.pdf_service.is_loaded
        total = self.pdf_service.page_count

        self._empty_state_container.setVisible(not loaded)
        self._scroll_area.setVisible(loaded)

        self._save_btn.setEnabled(loaded)
        self._tools_btn.setEnabled(loaded)
        self._prev_btn.setEnabled(loaded and self._current_display_idx > 0)
        self._next_btn.setEnabled(loaded and (self._current_display_idx + 1) < total)
        self._sb_rotate_btn.setEnabled(loaded)
        self._sb_dup_btn.setEnabled(loaded)
        self._sb_del_btn.setEnabled(loaded and total > 1)
        self._insp_rot_ccw.setEnabled(loaded)
        self._insp_rot_cw.setEnabled(loaded)
        self._insp_extract_btn.setEnabled(loaded)
        self._del_ann_btn.setEnabled(loaded)
        self._clear_ann_btn.setEnabled(loaded)

        self._page_spin.blockSignals(True)
        try:
            self._page_spin.setRange(1, max(1, total))
            self._page_spin.setValue(self._current_display_idx + 1 if loaded else 1)
            self._page_spin.setEnabled(loaded)
        finally:
            self._page_spin.blockSignals(False)

        self._page_total_lbl.setText(f"of {total}")

        if not loaded:
            self.setWindowTitle(self.meta.display_name)
            self._status_badge.set_status("No Document", variant="lavender")
            self._doc_info_lbl.setText("Ready to open a PDF document")
        else:
            fname = self.pdf_service.primary_path.name if self.pdf_service.primary_path else "Document.pdf"
            dirty_dot = " •" if self._has_unsaved_edits else ""
            self.setWindowTitle(f"{fname}{dirty_dot} — {self.meta.display_name}")
            if self._has_unsaved_edits:
                self._status_badge.set_status("Modified", variant="butter")
            else:
                self._status_badge.set_status("Ready", variant="sage")
            self._doc_info_lbl.setText(f"{fname}  •  {total} page{'s' if total != 1 else ''}")

        self._update_footer_telemetry()

    def _update_footer_telemetry(self) -> None:
        if not self.pdf_service.is_loaded:
            self._page_metrics_lbl.setText(f"Zoom: {self._zoom_percent}%")
            return

        idx = self._current_display_idx
        pt_size = self.pdf_service.get_page_size_points(idx)
        rot = self.pdf_service.get_page_rotation(idx)
        ann_total = self.pdf_service.total_annotation_count()
        ann_str = f"  •  {ann_total} markup{'s' if ann_total != 1 else ''}" if ann_total > 0 else ""
        self._page_metrics_lbl.setText(
            f"{pt_size.width() / 72.0:.1f} × {pt_size.height() / 72.0:.1f} in  •  "
            f"{rot}°{ann_str}  •  Zoom: {self._zoom_percent}%"
        )

    # ------------------------------------------------------------------
    # Navigation, Continuous Scroll Sync, Zoom & Panels
    # ------------------------------------------------------------------

    def go_to_page(self, display_index: int, scroll_to_page: bool = True) -> None:
        if not self.pdf_service.is_loaded:
            return
        clamped = max(0, min(self.pdf_service.page_count - 1, display_index))
        self._current_display_idx = clamped
        self._doc_canvas.set_current_page(clamped)

        self._thumb_list.blockSignals(True)
        try:
            self._thumb_list.setCurrentRow(clamped)
        finally:
            self._thumb_list.blockSignals(False)

        if scroll_to_page and self._continuous_mode:
            target_rect = self._doc_canvas.page_rect(clamped)
            if not target_rect.isNull():
                self._is_syncing_scroll = True
                try:
                    self._scroll_area.verticalScrollBar().setValue(max(0, int(target_rect.top() - 16)))
                finally:
                    self._is_syncing_scroll = False

        self._sync_document_state_ui()
        self._refresh_inspector_and_text()

    def next_page(self) -> None:
        if self._current_display_idx + 1 < self.pdf_service.page_count:
            self.go_to_page(self._current_display_idx + 1)

    def previous_page(self) -> None:
        if self._current_display_idx > 0:
            self.go_to_page(self._current_display_idx - 1)

    def _on_page_spin_changed(self, page_num_1based: int) -> None:
        self.go_to_page(page_num_1based - 1)

    def _on_thumbnail_selected(self, row: int) -> None:
        if row >= 0:
            self.go_to_page(row)

    def _on_scroll_position_changed(self, scroll_y: int) -> None:
        if self._is_syncing_scroll or not self._continuous_mode or not self.pdf_service.is_loaded:
            return
        viewport_mid = float(scroll_y) + float(self._scroll_area.viewport().height()) * 0.35
        for idx in range(self.pdf_service.page_count):
            r = self._doc_canvas.page_rect(idx)
            if not r.isNull() and r.top() <= viewport_mid <= r.bottom() + 24.0:
                if idx != self._current_display_idx:
                    self._current_display_idx = idx
                    self._doc_canvas.set_current_page(idx)
                    self._thumb_list.blockSignals(True)
                    try:
                        self._thumb_list.setCurrentRow(idx)
                    finally:
                        self._thumb_list.blockSignals(False)
                    self._sync_document_state_ui()
                    self._refresh_inspector_and_text()
                break

    def _on_canvas_active_page_changed(self, page_idx: int) -> None:
        self._current_display_idx = page_idx
        self._thumb_list.blockSignals(True)
        try:
            self._thumb_list.setCurrentRow(page_idx)
        finally:
            self._thumb_list.blockSignals(False)
        self._sync_document_state_ui()
        self._refresh_inspector_and_text()

    def _on_canvas_pan_requested(self, dx_px: int, dy_px: int) -> None:
        hbar = self._scroll_area.horizontalScrollBar()
        vbar = self._scroll_area.verticalScrollBar()
        hbar.setValue(hbar.value() - dx_px)
        vbar.setValue(vbar.value() - dy_px)

    def _toggle_continuous_mode(self) -> None:
        self._continuous_mode = not self._continuous_mode
        self._continuous_btn.setChecked(self._continuous_mode)
        self._doc_canvas.set_continuous_mode(self._continuous_mode)

    def _step_zoom(self, delta: int) -> None:
        next_idx = max(0, min(len(_ZOOM_PRESETS) - 1, self._zoom_combo.currentIndex() + delta))
        self._zoom_combo.setCurrentIndex(next_idx)

    def _on_zoom_combo_changed(self, idx: int) -> None:
        val = self._zoom_combo.itemData(idx)
        if isinstance(val, int):
            self._zoom_percent = val
            self._doc_canvas.set_zoom_percent(val)
            self._update_footer_telemetry()

    def fit_page_width(self) -> None:
        if not self.pdf_service.is_loaded:
            return
        pt_size = self.pdf_service.get_page_size_points(self._current_display_idx)
        base_px_at_100 = max(100.0, pt_size.width() * (96.0 / 72.0))
        avail_w = max(320.0, float(self._scroll_area.viewport().width()) - 56.0)
        ideal_zoom = max(50, min(200, int(round((avail_w / base_px_at_100) * 100.0))))
        best_idx = min(
            range(len(_ZOOM_PRESETS)),
            key=lambda i: abs(_ZOOM_PRESETS[i] - ideal_zoom),
        )
        self._zoom_combo.setCurrentIndex(best_idx)

    def _toggle_sidebar(self) -> None:
        self._sidebar_visible = not self._sidebar_visible
        self._sidebar_btn.setChecked(self._sidebar_visible)
        self.settings.set("sidebar_visible", self._sidebar_visible)
        self._sync_panel_visibility()

    def _toggle_inspector(self) -> None:
        self._inspector_visible = not self._inspector_visible
        self._inspector_btn.setChecked(self._inspector_visible)
        self.settings.set("inspector_visible", self._inspector_visible)
        self._sync_panel_visibility()

    def _sync_panel_visibility(self) -> None:
        self._sidebar.setVisible(self._sidebar_visible)
        self._inspector.setVisible(self._inspector_visible)

    def focus_search_tab(self) -> None:
        if not self._sidebar_visible:
            self._toggle_sidebar()
        self._sidebar_tabs.setCurrentIndex(1)
        self._search_input.setFocus()
        self._search_input.selectAll()

    # ------------------------------------------------------------------
    # Full-Text Search
    # ------------------------------------------------------------------

    def _on_search_triggered(self) -> None:
        self.run_text_search(self._search_input.text())

    def run_text_search(self, query: str) -> list[SearchHit]:
        self._search_results_list.clear()
        if not self.pdf_service.is_loaded:
            self._search_status_lbl.setText("Open a PDF document first.")
            return []

        hits = self.pdf_service.search_text(query)
        if not hits:
            self._search_status_lbl.setText(f"No matches found for '{query.strip()}'.")
            return []

        for hit in hits:
            item = QListWidgetItem(f"Page {hit.display_page_index + 1}: {hit.snippet}")
            item.setData(Qt.ItemDataRole.UserRole, hit.display_page_index)
            self._search_results_list.addItem(item)

        self._search_status_lbl.setText(
            f"Found {len(hits)} match{'es' if len(hits) != 1 else ''}."
        )
        return hits

    def _on_search_hit_clicked(self, item: QListWidgetItem) -> None:
        disp_idx = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(disp_idx, int):
            self.go_to_page(disp_idx)

    # ------------------------------------------------------------------
    # Annotations, Drag Reordering & Page Operations
    # ------------------------------------------------------------------

    def set_annotation_tool(self, tool: str) -> None:
        btn = self._tool_buttons.get(tool)
        if btn is not None:
            btn.setChecked(True)
        self._doc_canvas.set_tool(tool)

    def _select_stamp_tool(self, stamp_text: str) -> None:
        self._doc_canvas.set_stamp_label(stamp_text)
        self.set_annotation_tool("stamp")

    def _on_annotation_color_changed(self, idx: int) -> None:
        hex_val = self._color_combo.itemData(idx)
        if isinstance(hex_val, str):
            self._doc_canvas.set_color(hex_val)

    def _on_annotation_added(self, page_idx: int, annotation: PdfAnnotation) -> None:
        self.pdf_service.add_annotation(page_idx, annotation)
        self._has_unsaved_edits = True
        self._sync_document_state_ui()
        self._refresh_single_thumbnail(page_idx)
        self._refresh_inspector_and_text()

    def _on_annotation_selected(self, page_idx: int, annotation_id: str) -> None:
        _ = page_idx
        self._ann_list.blockSignals(True)
        try:
            if not annotation_id:
                self._ann_list.clearSelection()
            else:
                for row in range(self._ann_list.count()):
                    it = self._ann_list.item(row)
                    if it is not None and it.data(Qt.ItemDataRole.UserRole) == annotation_id:
                        self._ann_list.setCurrentRow(row)
                        break
        finally:
            self._ann_list.blockSignals(False)

    def _on_annotation_modified(self, page_idx: int) -> None:
        self._has_unsaved_edits = True
        self._sync_document_state_ui()
        self._refresh_single_thumbnail(page_idx)
        self._refresh_inspector_and_text()

    def _on_inspector_ann_clicked(self, item: QListWidgetItem) -> None:
        ann_id = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(ann_id, str):
            self._doc_canvas.set_selected_annotation(ann_id)

    def add_quick_annotation(
        self,
        kind: AnnotationKind,
        x: float = 0.12,
        y: float = 0.16,
        w: float = 0.28,
        h: float = 0.045,
        text: str = "",
        color_hex: str = "#E8E0F7",
    ) -> PdfAnnotation | None:
        if not self.pdf_service.is_loaded:
            return None
        ann = PdfAnnotation(
            kind=kind,
            x=x,
            y=y,
            w=w,
            h=h,
            color_hex=color_hex,
            text=text,
        )
        self._doc_canvas.set_selected_annotation(ann.annotation_id)
        self._on_annotation_added(self._current_display_idx, ann)
        return ann

    def delete_selected_annotation(self) -> bool:
        if not self.pdf_service.is_loaded:
            return False
        sel_id = self._doc_canvas.selected_annotation_id
        if not sel_id:
            return False
        removed = self.pdf_service.remove_annotation(self._current_display_idx, sel_id)
        if removed:
            self._doc_canvas.set_selected_annotation(None)
            self._refresh_single_thumbnail(self._current_display_idx)
            self._refresh_inspector_and_text()
            self._update_footer_telemetry()
        return removed

    def undo_last_annotation(self) -> bool:
        if not self.pdf_service.is_loaded:
            return False
        existing = self.pdf_service.get_annotations(self._current_display_idx)
        if not existing:
            return False
        last_ann = existing[-1]
        removed = self.pdf_service.remove_annotation(
            self._current_display_idx, last_ann.annotation_id
        )
        if removed:
            if self._doc_canvas.selected_annotation_id == last_ann.annotation_id:
                self._doc_canvas.set_selected_annotation(None)
            self._doc_canvas.update()
            self._refresh_single_thumbnail(self._current_display_idx)
            self._refresh_inspector_and_text()
            self._update_footer_telemetry()
            self.set_status_message("Removed last markup", timeout_ms=1600)
        return removed

    def clear_current_page_annotations(self) -> None:
        if not self.pdf_service.is_loaded:
            return
        cleared = self.pdf_service.clear_page_annotations(self._current_display_idx)
        if cleared > 0:
            self._doc_canvas.set_selected_annotation(None)
            self._doc_canvas.update()
            self._refresh_single_thumbnail(self._current_display_idx)
            self._refresh_inspector_and_text()
            self._update_footer_telemetry()
            self.set_status_message(f"Cleared {cleared} markup(s)", timeout_ms=1800)

    def _on_pages_reordered(self, from_idx: int, to_idx: int) -> None:
        if self.pdf_service.move_page(from_idx, to_idx):
            self._current_display_idx = to_idx
            self._has_unsaved_edits = True
            self._doc_canvas.set_current_page(to_idx)
            self._doc_canvas.rebuild_layout()
            self._rebuild_thumbnails()
            self._sync_document_state_ui()
            self._refresh_inspector_and_text()
            self.set_status_message(
                f"Moved page {from_idx + 1} to position {to_idx + 1}",
                timeout_ms=1800,
            )

    def rotate_current_page(self, delta_degrees: int = 90) -> None:
        if not self.pdf_service.is_loaded:
            return
        new_deg = self.pdf_service.rotate_page(self._current_display_idx, delta_degrees)
        self._has_unsaved_edits = True
        self._doc_canvas.rebuild_layout()
        self._refresh_single_thumbnail(self._current_display_idx)
        self._sync_document_state_ui()
        self._refresh_inspector_and_text()
        self.set_status_message(f"Rotated page to {new_deg}°", timeout_ms=1600)

    def duplicate_current_page(self) -> bool:
        if not self.pdf_service.duplicate_page(self._current_display_idx):
            return False
        self._current_display_idx += 1
        self._has_unsaved_edits = True
        self._doc_canvas.set_current_page(self._current_display_idx)
        self._doc_canvas.rebuild_layout()
        self._rebuild_thumbnails()
        self._sync_document_state_ui()
        self._refresh_inspector_and_text()
        self.set_status_message("Duplicated page", timeout_ms=1600)
        return True

    def delete_current_page(self) -> bool:
        if not self.pdf_service.delete_page(self._current_display_idx):
            return False
        self._current_display_idx = max(
            0, min(self.pdf_service.page_count - 1, self._current_display_idx)
        )
        self._has_unsaved_edits = True
        self._doc_canvas.set_selected_annotation(None)
        self._doc_canvas.set_current_page(self._current_display_idx)
        self._doc_canvas.rebuild_layout()
        self._rebuild_thumbnails()
        self._sync_document_state_ui()
        self._refresh_inspector_and_text()
        self.set_status_message("Removed page from active workspace", timeout_ms=1800)
        return True

    # ------------------------------------------------------------------
    # Save, Split/Extract, Merge & Text Export
    # ------------------------------------------------------------------

    def save_pdf_prompt(self) -> bool:
        if not self.pdf_service.is_loaded:
            return False
        stem = sanitize_filename(
            self.pdf_service.primary_path.stem if self.pdf_service.primary_path else "Annotated_Document"
        )
        default_dir = (
            self.pdf_service.primary_path.parent
            if self.pdf_service.primary_path is not None
            else (Path.home() / "Documents")
        )
        selected, _ = QFileDialog.getSaveFileName(
            self,
            "Save PDF Document",
            str(default_dir / f"{stem}_edited.pdf"),
            "PDF Document (*.pdf)",
        )
        if not selected:
            return False
        return self.export_current_pdf(selected)

    def export_current_pdf(self, destination: str | Path) -> bool:
        try:
            saved_path = self.pdf_service.export_pdf(
                destination=destination,
                include_annotations=True,
            )
        except FileOperationError as exc:
            show_error_dialog(self, "PDF Export Failed", str(exc))
            return False

        self._has_unsaved_edits = False
        self._sync_document_state_ui()
        self.notify(
            "PDF Saved",
            f"Saved '{saved_path.name}' cleanly.",
            level=NotificationLevel.SUCCESS,
        )
        return True

    def extract_pages_prompt(self) -> None:
        if not self.pdf_service.is_loaded:
            return
        total = self.pdf_service.page_count
        range_spec, ok = QInputDialog.getText(
            self,
            "Extract / Split Pages",
            f"Enter 1-based page numbers or ranges (1–{total}), e.g. '1-2, 4':",
            text=f"1-{min(2, total)}",
        )
        if not ok or not range_spec.strip():
            return

        selected_out, _ = QFileDialog.getSaveFileName(
            self,
            "Save Extracted Pages As PDF",
            str(Path.home() / "Documents" / "Extracted_Pages.pdf"),
            "PDF Document (*.pdf)",
        )
        if not selected_out:
            return

        self.extract_pages_to_pdf(range_spec, selected_out)

    def extract_pages_to_pdf(self, range_spec: str, destination: str | Path) -> bool:
        try:
            indices = parse_page_range_spec(range_spec, self.pdf_service.page_count)
            saved_path = self.pdf_service.export_pdf(
                destination=destination,
                display_indices=indices,
                include_annotations=True,
            )
        except FileOperationError as exc:
            show_error_dialog(self, "Page Extraction Failed", str(exc))
            return False

        self.notify(
            "Pages Extracted",
            f"Created '{saved_path.name}' ({len(indices)} page(s)).",
            level=NotificationLevel.SUCCESS,
        )
        return True

    def merge_pdfs_prompt(self) -> None:
        selected_files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Two or More PDF Files to Merge",
            str(Path.home() / "Documents"),
            "PDF Documents (*.pdf)",
        )
        if len(selected_files) < 2:
            if selected_files:
                show_error_dialog(
                    self,
                    "Select Multiple PDFs",
                    "Please select at least two PDF files to merge together.",
                )
            return

        out_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Merged PDF",
            str(Path.home() / "Documents" / "Merged_Document.pdf"),
            "PDF Document (*.pdf)",
        )
        if not out_path:
            return

        try:
            merged = PdfDocumentService.merge_pdf_files(selected_files, out_path)
        except FileOperationError as exc:
            show_error_dialog(self, "Merge Failed", str(exc))
            return

        self.notify(
            "PDFs Merged",
            f"Merged {len(selected_files)} files into '{merged.name}'.",
            level=NotificationLevel.SUCCESS,
        )
        self.load_file(merged)

    def extract_text_prompt(self) -> None:
        if not self.pdf_service.is_loaded:
            return
        stem = sanitize_filename(
            self.pdf_service.primary_path.stem if self.pdf_service.primary_path else "Extracted_Text"
        )
        out_txt, _ = QFileDialog.getSaveFileName(
            self,
            "Export Extracted Text",
            str(Path.home() / "Documents" / f"{stem}.txt"),
            "Plain Text File (*.txt)",
        )
        if not out_txt:
            return
        self.extract_text_to_file(out_txt)

    def extract_text_to_file(self, destination: str | Path) -> bool:
        if not self.pdf_service.is_loaded:
            return False
        target = Path(destination)
        if target.suffix.lower() != ".txt":
            target = target.with_suffix(".txt")
        try:
            sections: list[str] = []
            for disp_idx in range(self.pdf_service.page_count):
                txt = self.pdf_service.get_page_text(disp_idx).strip()
                header = f"--- Page {disp_idx + 1} ---"
                sections.append(f"{header}\n{txt}" if txt else header)
            atomic_write_text(target, "\n\n".join(sections) + "\n", backup_app_id=APP_ID)
        except FileOperationError as exc:
            show_error_dialog(self, "Text Export Failed", str(exc))
            return False

        self.notify(
            "Text Extracted",
            f"Saved plain text to '{target.name}'.",
            level=NotificationLevel.SUCCESS,
        )
        return True

    def can_close(self) -> bool:
        if not self._has_unsaved_edits:
            self.pdf_service.close()
            return True
        confirmed = ask_confirmation(
            parent=self,
            title="Discard Unsaved PDF Edits?",
            message="You have unsaved markups or page changes. Close without saving?",
            confirm_text="Discard Edits",
            destructive=True,
        )
        if confirmed:
            self.pdf_service.close()
        return confirmed

    def on_theme_updated(self, theme_name: str) -> None:
        _ = theme_name
        if hasattr(self, "_doc_canvas"):
            self._doc_canvas.set_tokens(self.theme_manager.tokens)


def main() -> int:
    """Application entry point for SimplePDF.exe."""
    return bootstrap_application(
        app_id=APP_ID,
        window_factory=SimplePDFWindow,
    )


if __name__ == "__main__":
    raise SystemExit(main())