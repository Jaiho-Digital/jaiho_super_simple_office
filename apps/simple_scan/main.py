"""SimpleScan — Soft Modern Pastel document scanner, image cleanup & PDF assembler."""

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
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from apps.simple_scan.scan_engine import (
    APP_ID,
    SCAN_FILTER_PRESETS,
    SUPPORTED_SCAN_IMAGE_EXTENSIONS,
    ScanBatchModel,
    ScannerAcquisitionService,
    ScannerDeviceInfo,
    ScanPageRecord,
    estimate_skew_angle,
    export_batch_as_images,
    export_scan_batch_to_pdf,
    render_processed_scan_image,
    render_scan_thumbnail,
)
from core.file_manager.file_ops import FileOperationError, sanitize_filename
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

_ZOOM_PRESETS: tuple[int, ...] = (50, 75, 90, 100, 125, 150, 175, 200)
_DPI_CHOICES: tuple[int, ...] = (150, 300, 600)


class DraggableScanThumbList(QListWidget):
    """Scanned page thumbnail organizer supporting drag-and-drop page reordering."""

    page_reordered = Signal(int, int)  # (from_index, to_index)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setIconSize(QSize(132, 176))
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


class InteractiveScanCanvas(QWidget):
    """High-DPI scanned page preview with optional interactive drag-to-crop overlay."""

    crop_changed = Signal(tuple)  # Emits (x, y, w, h) normalized [0.0, 1.0]

    def __init__(self, tokens: ThemeTokens = LIGHT_THEME, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tokens = tokens
        self._pixmap: QPixmap | None = None
        self._crop_mode: bool = False
        self._crop_rect: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)

        self._drag_mode: str = "none"  # 'none' | 'move' | 'tl' | 'tr' | 'bl' | 'br' | 'new'
        self._drag_start_norm: tuple[float, float] = (0.0, 0.0)
        self._orig_crop: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)

        self.setFixedSize(560, 792)
        self.setMouseTracking(True)

    def set_tokens(self, tokens: ThemeTokens) -> None:
        self._tokens = tokens
        self.update()

    def set_preview_pixmap(
        self,
        pixmap: QPixmap,
        logical_w: int,
        logical_h: int,
        crop_rect: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0),
    ) -> None:
        self._pixmap = pixmap
        self._crop_rect = crop_rect
        self.setFixedSize(max(180, logical_w), max(180, logical_h))
        self.update()

    def set_crop_mode(self, enabled: bool, crop_rect: tuple[float, float, float, float]) -> None:
        self._crop_mode = bool(enabled)
        self._crop_rect = crop_rect
        self.setCursor(
            Qt.CursorShape.CrossCursor if self._crop_mode else Qt.CursorShape.ArrowCursor
        )
        self.update()

    def _to_norm(self, pos: QPointF) -> tuple[float, float]:
        w = max(1.0, float(self.width()))
        h = max(1.0, float(self.height()))
        return (
            max(0.0, min(1.0, pos.x() / w)),
            max(0.0, min(1.0, pos.y() / h)),
        )

    def _crop_pixel_rect(self) -> QRectF:
        cx, cy, cw, ch = self._crop_rect
        w = float(self.width())
        h = float(self.height())
        return QRectF(cx * w, cy * h, max(12.0, cw * w), max(12.0, ch * h))

    def _hit_test_handle(self, pos: QPointF) -> str:
        r = self._crop_pixel_rect()
        rad = 12.0
        corners: tuple[tuple[str, QPointF], ...] = (
            ("tl", r.topLeft()),
            ("tr", r.topRight()),
            ("bl", r.bottomLeft()),
            ("br", r.bottomRight()),
        )
        for code, pt in corners:
            if abs(pos.x() - pt.x()) <= rad and abs(pos.y() - pt.y()) <= rad:
                return code
        if r.contains(pos):
            return "move"
        return "new"

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if not self._crop_mode or event.button() != Qt.MouseButton.LeftButton or self._pixmap is None:
            super().mousePressEvent(event)
            return

        pos = event.position()
        self._drag_mode = self._hit_test_handle(pos)
        self._drag_start_norm = self._to_norm(pos)
        self._orig_crop = self._crop_rect

        if self._drag_mode == "new":
            nx, ny = self._drag_start_norm
            self._crop_rect = (nx, ny, 0.02, 0.02)
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self._crop_mode:
            super().mouseMoveEvent(event)
            return

        pos = event.position()
        if self._drag_mode == "none":
            hit = self._hit_test_handle(pos)
            if hit in {"tl", "br"}:
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            elif hit in {"tr", "bl"}:
                self.setCursor(Qt.CursorShape.SizeBDiagCursor)
            elif hit == "move":
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            else:
                self.setCursor(Qt.CursorShape.CrossCursor)
            return

        nx, ny = self._to_norm(pos)
        ox, oy, ow, oh = self._orig_crop
        x1, y1 = ox, oy
        x2, y2 = ox + ow, oy + oh
        min_span = 0.06

        if self._drag_mode == "move":
            dx = nx - self._drag_start_norm[0]
            dy = ny - self._drag_start_norm[1]
            new_x = max(0.0, min(1.0 - ow, ox + dx))
            new_y = max(0.0, min(1.0 - oh, oy + dy))
            self._crop_rect = (new_x, new_y, ow, oh)
        elif self._drag_mode == "new":
            sx, sy = self._drag_start_norm
            left, right = min(sx, nx), max(sx, nx)
            top, bottom = min(sy, ny), max(sy, ny)
            self._crop_rect = (
                left,
                top,
                max(min_span, min(1.0 - left, right - left)),
                max(min_span, min(1.0 - top, bottom - top)),
            )
        elif self._drag_mode == "tl":
            left = min(nx, x2 - min_span)
            top = min(ny, y2 - min_span)
            self._crop_rect = (left, top, x2 - left, y2 - top)
        elif self._drag_mode == "tr":
            right = max(nx, x1 + min_span)
            top = min(ny, y2 - min_span)
            self._crop_rect = (x1, top, right - x1, y2 - top)
        elif self._drag_mode == "bl":
            left = min(nx, x2 - min_span)
            bottom = max(ny, y1 + min_span)
            self._crop_rect = (left, y1, x2 - left, bottom - y1)
        elif self._drag_mode == "br":
            right = max(nx, x1 + min_span)
            bottom = max(ny, y1 + min_span)
            self._crop_rect = (x1, y1, right - x1, bottom - y1)

        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._crop_mode and self._drag_mode != "none" and event.button() == Qt.MouseButton.LeftButton:
            self._drag_mode = "none"
            self.crop_changed.emit(self._crop_rect)
            self.update()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        full_rect = QRectF(0.0, 0.0, float(self.width()), float(self.height()))
        if self._pixmap is not None:
            painter.drawPixmap(0, 0, self._pixmap)
        else:
            painter.fillRect(full_rect, QColor(self._tokens.bg_surface))

        # Crisp 1px hairline border around scanned sheet
        painter.setPen(QPen(QColor(self._tokens.border_strong), 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(full_rect.adjusted(0.0, 0.0, -1.0, -1.0))

        # Interactive Pastel Crop Overlay when Crop Mode is active
        if self._crop_mode and self._pixmap is not None:
            crop_r = self._crop_pixel_rect()
            shade = QColor(24, 24, 30, 110)

            # Dim regions outside the crop box
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(shade)
            painter.drawRect(QRectF(0.0, 0.0, full_rect.width(), crop_r.top()))
            painter.drawRect(
                QRectF(0.0, crop_r.bottom(), full_rect.width(), full_rect.height() - crop_r.bottom())
            )
            painter.drawRect(QRectF(0.0, crop_r.top(), crop_r.left(), crop_r.height()))
            painter.drawRect(
                QRectF(
                    crop_r.right(),
                    crop_r.top(),
                    full_rect.width() - crop_r.right(),
                    crop_r.height(),
                )
            )

            # Crop selection frame + rule-of-thirds guides
            accent = QColor(self._tokens.accent_primary)
            painter.setPen(QPen(accent, 1.8))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(crop_r)

            grid_pen = QPen(QColor(255, 255, 255, 140), 1.0, Qt.PenStyle.DashLine)
            painter.setPen(grid_pen)
            for frac in (1.0 / 3.0, 2.0 / 3.0):
                gx = crop_r.left() + crop_r.width() * frac
                gy = crop_r.top() + crop_r.height() * frac
                painter.drawLine(QPointF(gx, crop_r.top()), QPointF(gx, crop_r.bottom()))
                painter.drawLine(QPointF(crop_r.left(), gy), QPointF(crop_r.right(), gy))

            # Corner handles
            painter.setPen(QPen(QColor("#FFFEFC"), 1.5))
            painter.setBrush(accent)
            for corner in (
                crop_r.topLeft(),
                crop_r.topRight(),
                crop_r.bottomLeft(),
                crop_r.bottomRight(),
            ):
                painter.drawEllipse(corner, 5.5, 5.5)

        painter.end()


class SimpleScanWindow(BaseWindow):
    """Main window for SimpleScan — document acquisition, cleanup, and PDF assembly."""

    def __init__(self, batch: ScanBatchModel | None = None) -> None:
        super().__init__(app_id=APP_ID, min_width=980, min_height=650)
        if self.settings.get("window_width") is None:
            self.resize(1220, 780)

        self.setAcceptDrops(True)
        self.batch: ScanBatchModel = batch or ScanBatchModel()
        self._devices: list[ScannerDeviceInfo] = []
        self._zoom_percent: int = 100
        self._crop_mode: bool = False
        self._is_syncing_controls: bool = False
        self._has_unsaved_scans: bool = False

        self._build_header_actions()
        self._build_workspace()
        self._bind_shortcuts()

        self.refresh_scanner_devices()
        self._rebuild_thumbnail_list()
        self._sync_inspector_from_active_page()
        self._render_active_preview()
        self._sync_batch_state_ui()

    def _build_header_actions(self) -> None:
        """Mount Scan Page, Import Images, Save PDF, and Export Images in the header."""
        scan_btn = make_button(
            text="Scan Page",
            variant="primary",
            tooltip="Acquire a new page from the selected scanner (F2)",
            on_click=self.scan_new_page,
            icon_name="plus",
            icon_size=14,
            parent=self,
        )
        scan_btn.setStyleSheet("padding: 4px 12px; font-size: 12px;")
        self.header_actions_layout.addWidget(scan_btn)

        import_btn = make_button(
            text="Import Images",
            variant="ghost",
            tooltip="Import existing images (.png, .jpg, .bmp, .webp) into scan batch (Ctrl+O)",
            on_click=self.import_images_prompt,
            icon_name="folder",
            icon_size=14,
            parent=self,
        )
        import_btn.setStyleSheet("padding: 4px 10px; font-size: 12px;")
        self.header_actions_layout.addWidget(import_btn)

        self._save_pdf_btn = make_button(
            text="Save PDF",
            variant="pastel",
            tooltip="Export all scanned pages as a multi-page PDF (Ctrl+S)",
            on_click=self.export_pdf_prompt,
            icon_name="check",
            icon_size=14,
            parent=self,
        )
        self._save_pdf_btn.setStyleSheet("padding: 4px 11px; font-size: 12px;")
        self.header_actions_layout.addWidget(self._save_pdf_btn)

        self._export_imgs_btn = QPushButton("Export Images", self)
        self._export_imgs_btn.setProperty("variant", "ghost")
        self._export_imgs_btn.setProperty("has_menu", True)
        self._export_imgs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._export_imgs_btn.setToolTip("Export scanned pages as individual PNG or JPEG images")
        self._export_imgs_btn.setStyleSheet("padding: 4px 22px 4px 10px; font-size: 12px;")
        img_menu = QMenu(self._export_imgs_btn)
        img_menu.addAction("Export All Pages as PNG...", lambda: self.export_images_prompt(".png"))
        img_menu.addAction("Export All Pages as JPEG...", lambda: self.export_images_prompt(".jpg"))
        self._export_imgs_btn.setMenu(img_menu)
        self.header_actions_layout.addWidget(self._export_imgs_btn)

    def _build_workspace(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)

        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ==============================================================
        # 1. Quiet Pastel Preview & Crop Toolbar Strip
        # ==============================================================
        tool_bar = QFrame(central)
        tool_bar.setProperty("surface", "app-header")
        tool_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        tb_row = QHBoxLayout(tool_bar)
        tb_row.setContentsMargins(14, 6, 14, 6)
        tb_row.setSpacing(5)

        self._rotate_ccw_btn = make_button(
            text="↺ 90°",
            variant="ghost",
            tooltip="Rotate scanned page counter-clockwise",
            on_click=lambda: self.rotate_active_page(-90),
            parent=tool_bar,
        )
        self._rotate_ccw_btn.setStyleSheet("padding: 4px 8px; font-size: 12px;")
        tb_row.addWidget(self._rotate_ccw_btn)

        self._rotate_cw_btn = make_button(
            text="↻ 90°",
            variant="ghost",
            tooltip="Rotate scanned page clockwise (Ctrl+R)",
            on_click=lambda: self.rotate_active_page(90),
            parent=tool_bar,
        )
        self._rotate_cw_btn.setStyleSheet("padding: 4px 8px; font-size: 12px;")
        tb_row.addWidget(self._rotate_cw_btn)

        self._auto_deskew_btn = make_button(
            text="Auto-Deskew",
            variant="pastel",
            tooltip="Automatically detect and straighten page tilt",
            on_click=self.apply_auto_deskew,
            parent=tool_bar,
        )
        self._auto_deskew_btn.setStyleSheet("padding: 4px 10px; font-size: 12px;")
        tb_row.addWidget(self._auto_deskew_btn)

        tb_row.addWidget(VerticalDivider(tool_bar))

        # Interactive Drag-to-Crop Mode Controls
        self._crop_toggle_btn = QPushButton("Crop Frame", tool_bar)
        self._crop_toggle_btn.setProperty("variant", "segmented")
        self._crop_toggle_btn.setCheckable(True)
        self._crop_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._crop_toggle_btn.setToolTip("Toggle interactive drag-to-crop corners on the preview")
        self._crop_toggle_btn.setStyleSheet("padding: 4px 10px; font-size: 12px;")
        self._crop_toggle_btn.clicked.connect(self._toggle_crop_mode)
        tb_row.addWidget(self._crop_toggle_btn)

        self._reset_crop_btn = make_button(
            text="Full Page",
            variant="ghost",
            tooltip="Reset crop margins to full scanned frame",
            on_click=self.reset_active_crop,
            parent=tool_bar,
        )
        self._reset_crop_btn.setStyleSheet("padding: 4px 8px; font-size: 12px;")
        tb_row.addWidget(self._reset_crop_btn)

        tb_row.addWidget(VerticalDivider(tool_bar))

        # Zoom Controls
        zoom_out_btn = make_button(
            text="−",
            variant="ghost",
            tooltip="Zoom out",
            on_click=lambda: self._step_zoom(-1),
            parent=tool_bar,
        )
        zoom_out_btn.setStyleSheet("padding: 4px 7px; font-size: 13px;")
        tb_row.addWidget(zoom_out_btn)

        self._zoom_combo = QComboBox(tool_bar)
        for z in _ZOOM_PRESETS:
            self._zoom_combo.addItem(f"{z}%", z)
        self._zoom_combo.setCurrentIndex(3)  # 100%
        self._zoom_combo.setToolTip("Preview zoom level")
        self._zoom_combo.setStyleSheet("padding: 4px 7px; font-size: 12px;")
        self._zoom_combo.currentIndexChanged.connect(self._on_zoom_changed)
        tb_row.addWidget(self._zoom_combo)

        zoom_in_btn = make_button(
            text="+",
            variant="ghost",
            tooltip="Zoom in",
            on_click=lambda: self._step_zoom(1),
            parent=tool_bar,
        )
        zoom_in_btn.setStyleSheet("padding: 4px 7px; font-size: 13px;")
        tb_row.addWidget(zoom_in_btn)

        fit_btn = make_button(
            text="Fit Page",
            variant="ghost",
            tooltip="Fit scanned page inside preview viewport",
            on_click=self.fit_page_in_view,
            parent=tool_bar,
        )
        fit_btn.setStyleSheet("padding: 4px 8px; font-size: 12px;")
        tb_row.addWidget(fit_btn)

        tb_row.addStretch(1)
        outer.addWidget(tool_bar)

        # ==============================================================
        # 2. Main 3-Pane Studio (Pages Sidebar | Preview Stage | Inspector)
        # ==============================================================
        body_row = QHBoxLayout()
        body_row.setContentsMargins(0, 0, 0, 0)
        body_row.setSpacing(0)

        # Left Pane: Scanned Pages Batch Organizer
        pages_sidebar = QFrame(central)
        pages_sidebar.setProperty("surface", "sidebar")
        pages_sidebar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        pages_sidebar.setFixedWidth(235)

        sb_layout = QVBoxLayout(pages_sidebar)
        sb_layout.setContentsMargins(14, 14, 14, 12)
        sb_layout.setSpacing(10)

        sb_layout.addWidget(SectionHeader("Scanned Pages", parent=pages_sidebar))

        drag_tip = QLabel("Drag thumbnails to reorder batch", pages_sidebar)
        drag_tip.setProperty("role", "caption")
        sb_layout.addWidget(drag_tip)

        sb_layout.addWidget(HorizontalDivider(pages_sidebar))

        self._thumb_list = DraggableScanThumbList(pages_sidebar)
        self._thumb_list.currentRowChanged.connect(self._on_thumbnail_selected)
        self._thumb_list.page_reordered.connect(self._on_pages_reordered)
        sb_layout.addWidget(self._thumb_list, 1)

        sb_layout.addWidget(HorizontalDivider(pages_sidebar))

        batch_btns = QHBoxLayout()
        batch_btns.setSpacing(4)

        self._dup_page_btn = make_button(
            text="Duplicate",
            variant="ghost",
            tooltip="Duplicate active scanned page",
            on_click=self.duplicate_active_page,
            parent=pages_sidebar,
        )
        self._dup_page_btn.setStyleSheet("padding: 4px 7px; font-size: 11px;")
        batch_btns.addWidget(self._dup_page_btn, 1)

        self._del_page_btn = make_button(
            text="Delete",
            variant="ghost",
            tooltip="Remove active page from scan batch",
            on_click=self.delete_active_page,
            parent=pages_sidebar,
        )
        self._del_page_btn.setStyleSheet("padding: 4px 7px; font-size: 11px;")
        batch_btns.addWidget(self._del_page_btn, 1)

        self._clear_batch_btn = make_button(
            text="Clear",
            variant="ghost",
            tooltip="Clear all scanned pages",
            on_click=self.clear_scan_batch,
            parent=pages_sidebar,
        )
        self._clear_batch_btn.setStyleSheet("padding: 4px 7px; font-size: 11px;")
        batch_btns.addWidget(self._clear_batch_btn)

        sb_layout.addLayout(batch_btns)
        body_row.addWidget(pages_sidebar)

        # Center Stage: Empty State OR Interactive Scan Preview Canvas
        stage_host = QWidget(central)
        stage_layout = QVBoxLayout(stage_host)
        stage_layout.setContentsMargins(0, 0, 0, 0)
        stage_layout.setSpacing(0)

        self._empty_container = QWidget(stage_host)
        empty_outer = QVBoxLayout(self._empty_container)
        empty_outer.setAlignment(Qt.AlignmentFlag.AlignCenter)

        empty_card = CardFrame(padding=32, spacing=14, parent=self._empty_container)
        empty_card.setMaximumWidth(480)

        hero_title = QLabel("Scan or Import Documents", empty_card)
        hero_title.setProperty("role", "title")
        hero_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_card.body_layout.addWidget(hero_title)

        hero_desc = QLabel(
            "Acquire pages from a connected WIA flatbed scanner, document camera, or local "
            "image files—then straighten, crop, clean up paper tones, and assemble a multi-page PDF.",
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

        scan_cta = make_button(
            text="Scan First Page",
            variant="primary",
            on_click=self.scan_new_page,
            icon_name="plus",
            icon_size=15,
            parent=empty_card,
        )
        cta_row.addWidget(scan_cta)

        import_cta = make_button(
            text="Import Image(s)...",
            variant="pastel",
            on_click=self.import_images_prompt,
            icon_name="folder",
            icon_size=15,
            parent=empty_card,
        )
        cta_row.addWidget(import_cta)
        cta_row.addStretch(1)
        empty_card.body_layout.addLayout(cta_row)

        drop_lbl = QLabel(
            "Tip: Drag & drop PNG, JPG, BMP, or WebP files anywhere on this window.",
            empty_card,
        )
        drop_lbl.setProperty("role", "caption")
        drop_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_card.body_layout.addWidget(drop_lbl)

        empty_outer.addWidget(empty_card)
        stage_layout.addWidget(self._empty_container, 1)

        self._scroll_area = QScrollArea(stage_host)
        self._scroll_area.setWidgetResizable(False)
        self._scroll_area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)

        self._scan_canvas = InteractiveScanCanvas(
            tokens=self.theme_manager.tokens,
            parent=self._scroll_area,
        )
        self._scan_canvas.crop_changed.connect(self._on_canvas_crop_changed)
        self._scroll_area.setWidget(self._scan_canvas)
        self._scroll_area.setVisible(False)
        stage_layout.addWidget(self._scroll_area, 1)

        body_row.addWidget(stage_host, 1)

        # Right Pane: Scanner Hardware & Document Cleanup Inspector
        inspector = QFrame(central)
        inspector.setProperty("surface", "inspector")
        inspector.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        inspector.setFixedWidth(295)

        insp_layout = QVBoxLayout(inspector)
        insp_layout.setContentsMargins(16, 16, 16, 16)
        insp_layout.setSpacing(11)

        refresh_dev_btn = make_button(
            text="Refresh",
            variant="ghost",
            tooltip="Re-scan for connected WIA scanners and cameras",
            on_click=self.refresh_scanner_devices,
            parent=inspector,
        )
        refresh_dev_btn.setStyleSheet("padding: 3px 8px; font-size: 11px;")
        insp_layout.addWidget(
            SectionHeader("Scanner Source", accessory=refresh_dev_btn, parent=inspector)
        )

        self._device_combo = QComboBox(inspector)
        self._device_combo.setToolTip("Select flatbed scanner, document camera, or virtual scanner")
        insp_layout.addWidget(self._device_combo)

        dpi_row = QHBoxLayout()
        dpi_row.setSpacing(8)
        dpi_lbl = QLabel("Resolution:", inspector)
        dpi_lbl.setProperty("role", "caption")
        dpi_row.addWidget(dpi_lbl)

        self._dpi_combo = QComboBox(inspector)
        for d in _DPI_CHOICES:
            self._dpi_combo.addItem(f"{d} DPI", d)
        self._dpi_combo.setCurrentIndex(1)  # 300 DPI default
        dpi_row.addWidget(self._dpi_combo, 1)
        insp_layout.addLayout(dpi_row)

        acquire_btn = make_button(
            text="Acquire Page Now",
            variant="pastel",
            tooltip="Scan a new page and append it to the batch (F2)",
            on_click=self.scan_new_page,
            icon_name="plus",
            icon_size=14,
            parent=inspector,
        )
        insp_layout.addWidget(acquire_btn)

        insp_layout.addWidget(HorizontalDivider(inspector))
        insp_layout.addWidget(SectionHeader("Page Cleanup", parent=inspector))

        preset_lbl = QLabel("Color & Document Filter", inspector)
        preset_lbl.setProperty("role", "caption")
        insp_layout.addWidget(preset_lbl)

        self._filter_combo = QComboBox(inspector)
        for fid, fname in SCAN_FILTER_PRESETS:
            self._filter_combo.addItem(fname, fid)
        self._filter_combo.currentIndexChanged.connect(self._on_filter_changed)
        insp_layout.addWidget(self._filter_combo)

        # Fine Deskew Slider
        skew_hdr = QHBoxLayout()
        skew_title = QLabel("Deskew Tilt", inspector)
        skew_title.setProperty("role", "caption")
        skew_hdr.addWidget(skew_title)
        skew_hdr.addStretch(1)
        self._skew_val_lbl = QLabel("0.0°", inspector)
        self._skew_val_lbl.setProperty("role", "caption")
        skew_hdr.addWidget(self._skew_val_lbl)
        insp_layout.addLayout(skew_hdr)

        self._skew_slider = QSlider(Qt.Orientation.Horizontal, inspector)
        self._skew_slider.setRange(-150, 150)  # tenths of a degree (-15.0 to +15.0)
        self._skew_slider.setValue(0)
        self._skew_slider.valueChanged.connect(self._on_skew_slider_changed)
        insp_layout.addWidget(self._skew_slider)

        # Brightness Slider
        br_hdr = QHBoxLayout()
        br_title = QLabel("Brightness", inspector)
        br_title.setProperty("role", "caption")
        br_hdr.addWidget(br_title)
        br_hdr.addStretch(1)
        self._brightness_val_lbl = QLabel("0", inspector)
        self._brightness_val_lbl.setProperty("role", "caption")
        br_hdr.addWidget(self._brightness_val_lbl)
        insp_layout.addLayout(br_hdr)

        self._brightness_slider = QSlider(Qt.Orientation.Horizontal, inspector)
        self._brightness_slider.setRange(-100, 100)
        self._brightness_slider.setValue(0)
        self._brightness_slider.valueChanged.connect(self._on_tone_sliders_changed)
        insp_layout.addWidget(self._brightness_slider)

        # Contrast Slider
        ct_hdr = QHBoxLayout()
        ct_title = QLabel("Contrast", inspector)
        ct_title.setProperty("role", "caption")
        ct_hdr.addWidget(ct_title)
        ct_hdr.addStretch(1)
        self._contrast_val_lbl = QLabel("0", inspector)
        self._contrast_val_lbl.setProperty("role", "caption")
        ct_hdr.addWidget(self._contrast_val_lbl)
        insp_layout.addLayout(ct_hdr)

        self._contrast_slider = QSlider(Qt.Orientation.Horizontal, inspector)
        self._contrast_slider.setRange(-100, 100)
        self._contrast_slider.setValue(0)
        self._contrast_slider.valueChanged.connect(self._on_tone_sliders_changed)
        insp_layout.addWidget(self._contrast_slider)

        self._reset_adj_btn = make_button(
            text="Reset Page Adjustments",
            variant="ghost",
            tooltip="Reset crop, rotation, deskew, brightness, and contrast",
            on_click=self.reset_active_adjustments,
            parent=inspector,
        )
        insp_layout.addWidget(self._reset_adj_btn)

        insp_layout.addStretch(1)

        insp_layout.addWidget(HorizontalDivider(inspector))

        # PDF Output Paper Size Selector
        pdf_sz_row = QHBoxLayout()
        pdf_sz_row.setSpacing(8)
        pdf_sz_lbl = QLabel("PDF Paper:", inspector)
        pdf_sz_lbl.setProperty("role", "caption")
        pdf_sz_row.addWidget(pdf_sz_lbl)

        self._pdf_paper_combo = QComboBox(inspector)
        self._pdf_paper_combo.addItem("A4 Standard", "a4")
        self._pdf_paper_combo.addItem("US Letter", "letter")
        pdf_sz_row.addWidget(self._pdf_paper_combo, 1)
        insp_layout.addLayout(pdf_sz_row)

        body_row.addWidget(inspector)
        outer.addLayout(body_row, 1)

        # ==============================================================
        # 3. Quiet Bottom Status Bar
        # ==============================================================
        footer_bar = QFrame(central)
        footer_bar.setProperty("surface", "footer-bar")
        footer_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        footer_bar.setFixedHeight(34)

        f_row = QHBoxLayout(footer_bar)
        f_row.setContentsMargins(16, 4, 16, 4)
        f_row.setSpacing(10)

        self._status_badge = StatusBadge("Ready to Scan", variant="lavender", parent=footer_bar)
        f_row.addWidget(self._status_badge)

        self._batch_info_lbl = QLabel("0 pages in batch", footer_bar)
        self._batch_info_lbl.setProperty("role", "muted")
        f_row.addWidget(self._batch_info_lbl)

        f_row.addStretch(1)

        self._page_metrics_lbl = QLabel("Zoom: 100%", footer_bar)
        self._page_metrics_lbl.setProperty("role", "muted")
        f_row.addWidget(self._page_metrics_lbl)

        outer.addWidget(footer_bar)

    def _bind_shortcuts(self) -> None:
        QShortcut(QKeySequence(Qt.Key.Key_F2), self).activated.connect(self.scan_new_page)
        QShortcut(QKeySequence.StandardKey.Open, self).activated.connect(self.import_images_prompt)
        QShortcut(QKeySequence.StandardKey.Save, self).activated.connect(self.export_pdf_prompt)
        QShortcut(QKeySequence("Ctrl+R"), self).activated.connect(lambda: self.rotate_active_page(90))
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self).activated.connect(self.delete_active_page)

    # ------------------------------------------------------------------
    # Drag-and-Drop Image Import & Ctrl+Wheel Zoom
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        mime = event.mimeData()
        if mime is not None and mime.hasUrls():
            for url in mime.urls():
                if url.isLocalFile():
                    ext = Path(url.toLocalFile()).suffix.lower()
                    if ext in SUPPORTED_SCAN_IMAGE_EXTENSIONS:
                        event.acceptProposedAction()
                        return
        super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        mime = event.mimeData()
        if mime is not None and mime.hasUrls():
            image_paths = [
                url.toLocalFile()
                for url in mime.urls()
                if url.isLocalFile()
                and Path(url.toLocalFile()).suffix.lower() in SUPPORTED_SCAN_IMAGE_EXTENSIONS
            ]
            if image_paths:
                event.acceptProposedAction()
                self.import_image_files(image_paths)
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
    # Scanner Discovery, Acquisition & Image Import
    # ------------------------------------------------------------------

    def refresh_scanner_devices(self) -> None:
        self._devices = ScannerAcquisitionService.discover_devices()
        self._device_combo.clear()
        for dev in self._devices:
            self._device_combo.addItem(dev.display_name, dev.device_id)

    def _selected_device(self) -> ScannerDeviceInfo:
        idx = self._device_combo.currentIndex()
        if 0 <= idx < len(self._devices):
            return self._devices[idx]
        return ScannerDeviceInfo(
            device_id=ScannerAcquisitionService.VIRTUAL_DEVICE_ID,
            display_name="Virtual Flatbed Scanner (A4 Sample Sheet)",
            backend="virtual",
        )

    def scan_new_page(self) -> bool:
        """Acquire a page from the active scanner device and append it to the batch."""
        device = self._selected_device()
        dpi = int(self._dpi_combo.currentData() or 300)
        next_num = len(self.batch.pages) + 1

        try:
            raw_img = ScannerAcquisitionService.acquire_page(
                device=device,
                page_number=next_num,
                dpi=dpi,
            )
        except FileOperationError as exc:
            show_error_dialog(self, "Scan Acquisition Failed", str(exc))
            return False

        record = ScanPageRecord(
            source_image=raw_img,
            title=f"Scan {next_num}",
            filter_preset="magic_color",
        )
        self.batch.add_page(record, insert_after_active=True)
        self._has_unsaved_scans = True

        self._rebuild_thumbnail_list()
        self._sync_inspector_from_active_page()
        self._render_active_preview()
        self._sync_batch_state_ui()
        self.set_status_message(f"Scanned page {next_num} ({dpi} DPI)", timeout_ms=2000)
        return True

    def import_images_prompt(self) -> None:
        selected_files, _ = QFileDialog.getOpenFileNames(
            self,
            "Import Scanned Images",
            str(Path.home() / "Pictures"),
            "Images (*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff)",
        )
        if selected_files:
            self.import_image_files(selected_files)

    def import_image_files(self, file_paths: list[str | Path]) -> int:
        """Load one or more image files from disk into the active scan batch."""
        imported_count = 0
        for fp in file_paths:
            try:
                img = ScannerAcquisitionService.load_image_file(fp)
            except FileOperationError as exc:
                show_error_dialog(self, "Image Import Failed", str(exc))
                continue

            stem_title = Path(fp).stem.replace("_", " ").strip() or f"Page {len(self.batch.pages) + 1}"
            record = ScanPageRecord(
                source_image=img,
                title=stem_title,
                filter_preset="magic_color",
            )
            self.batch.add_page(record, insert_after_active=True)
            imported_count += 1

        if imported_count > 0:
            self._has_unsaved_scans = True
            self._rebuild_thumbnail_list()
            self._sync_inspector_from_active_page()
            self._render_active_preview()
            self._sync_batch_state_ui()
            self.set_status_message(f"Imported {imported_count} image(s)", timeout_ms=2000)

        return imported_count

    # ------------------------------------------------------------------
    # Thumbnails, Preview Rendering & Inspector Sync
    # ------------------------------------------------------------------

    def _rebuild_thumbnail_list(self) -> None:
        self._thumb_list.blockSignals(True)
        try:
            self._thumb_list.clear()
            for idx, page in enumerate(self.batch.pages):
                thumb = render_scan_thumbnail(page, width_px=132)
                item = QListWidgetItem(f"Page {idx + 1}")
                item.setIcon(QIcon(thumb))
                item.setToolTip(page.title)
                self._thumb_list.addItem(item)
            if not self.batch.is_empty:
                self._thumb_list.setCurrentRow(self.batch.active_index)
        finally:
            self._thumb_list.blockSignals(False)

    def _refresh_active_thumbnail(self) -> None:
        page = self.batch.active_page
        if page is None:
            return
        idx = self.batch.active_index
        item = self._thumb_list.item(idx)
        if item is not None:
            item.setIcon(QIcon(render_scan_thumbnail(page, width_px=132)))

    def _render_active_preview(self) -> None:
        page = self.batch.active_page
        if page is None:
            return

        # Compute base display dimensions so A4 fits cleanly at 100% zoom
        src_w = max(100, page.source_image.width())
        src_h = max(100, page.source_image.height())
        if page.rotation % 360 in {90, 270}:
            src_w, src_h = src_h, src_w

        # Normalize base width to ~560px at 100% zoom regardless of 150/300/600 DPI
        base_logical_w = 560.0 * (self._zoom_percent / 100.0)
        logical_w = max(220, int(round(base_logical_w)))
        logical_h = max(220, int(round(base_logical_w * (src_h / float(src_w)))))

        dpr = max(1.0, float(self.devicePixelRatioF()))
        processed = render_processed_scan_image(page, max_width=int(round(logical_w * dpr)))
        px = QPixmap.fromImage(processed)
        px.setDevicePixelRatio(dpr)

        self._scan_canvas.set_preview_pixmap(
            pixmap=px,
            logical_w=logical_w,
            logical_h=logical_h,
            crop_rect=page.crop_rect,
        )
        self._update_footer_telemetry()

    def _sync_inspector_from_active_page(self) -> None:
        self._is_syncing_controls = True
        try:
            page = self.batch.active_page
            if page is None:
                self._filter_combo.setCurrentIndex(0)
                self._skew_slider.setValue(0)
                self._skew_val_lbl.setText("0.0°")
                self._brightness_slider.setValue(0)
                self._brightness_val_lbl.setText("0")
                self._contrast_slider.setValue(0)
                self._contrast_val_lbl.setText("0")
                return

            f_idx = self._filter_combo.findData(page.filter_preset)
            self._filter_combo.setCurrentIndex(f_idx if f_idx >= 0 else 0)

            skew_tenths = int(round(page.skew_angle * 10.0))
            self._skew_slider.setValue(skew_tenths)
            self._skew_val_lbl.setText(f"{page.skew_angle:+.1f}°" if abs(page.skew_angle) >= 0.05 else "0.0°")

            self._brightness_slider.setValue(page.brightness)
            self._brightness_val_lbl.setText(str(page.brightness))

            self._contrast_slider.setValue(page.contrast)
            self._contrast_val_lbl.setText(str(page.contrast))
        finally:
            self._is_syncing_controls = False

    def _sync_batch_state_ui(self) -> None:
        has_pages = not self.batch.is_empty
        total = len(self.batch.pages)

        self._empty_container.setVisible(not has_pages)
        self._scroll_area.setVisible(has_pages)

        for widget in (
            self._save_pdf_btn,
            self._export_imgs_btn,
            self._rotate_ccw_btn,
            self._rotate_cw_btn,
            self._auto_deskew_btn,
            self._crop_toggle_btn,
            self._reset_crop_btn,
            self._dup_page_btn,
            self._del_page_btn,
            self._clear_batch_btn,
            self._filter_combo,
            self._skew_slider,
            self._brightness_slider,
            self._contrast_slider,
            self._reset_adj_btn,
        ):
            widget.setEnabled(has_pages)

        if not has_pages:
            self.setWindowTitle(self.meta.display_name)
            self._status_badge.set_status("Ready to Scan", variant="lavender")
            self._batch_info_lbl.setText("0 pages in batch")
        else:
            dirty_dot = " •" if self._has_unsaved_scans else ""
            self.setWindowTitle(f"Scan Batch ({total} pages){dirty_dot} — {self.meta.display_name}")
            if self._has_unsaved_scans:
                self._status_badge.set_status("Unsaved Batch", variant="butter")
            else:
                self._status_badge.set_status("Exported", variant="sage")
            self._batch_info_lbl.setText(
                f"Page {self.batch.active_index + 1} of {total}"
            )

        self._update_footer_telemetry()

    def _update_footer_telemetry(self) -> None:
        page = self.batch.active_page
        if page is None:
            self._page_metrics_lbl.setText(f"Zoom: {self._zoom_percent}%")
            return

        w = page.source_image.width()
        h = page.source_image.height()
        self._page_metrics_lbl.setText(
            f"{w} × {h} px  •  Rot: {page.rotation % 360}°  •  "
            f"Skew: {page.skew_angle:.1f}°  •  Zoom: {self._zoom_percent}%"
        )

    # ------------------------------------------------------------------
    # Interactive Page Cleanup, Crop & Batch Ordering Handlers
    # ------------------------------------------------------------------

    def _on_thumbnail_selected(self, row: int) -> None:
        if row < 0 or row >= len(self.batch.pages):
            return
        self.batch.set_active_index(row)
        self._sync_inspector_from_active_page()
        self._render_active_preview()
        self._sync_batch_state_ui()

    def _on_pages_reordered(self, from_idx: int, to_idx: int) -> None:
        if self.batch.move_page(from_idx, to_idx):
            self._has_unsaved_scans = True
            self._rebuild_thumbnail_list()
            self._render_active_preview()
            self._sync_batch_state_ui()
            self.set_status_message(
                f"Moved page {from_idx + 1} to position {to_idx + 1}",
                timeout_ms=1800,
            )

    def _on_filter_changed(self, idx: int) -> None:
        if self._is_syncing_controls:
            return
        page = self.batch.active_page
        if page is None:
            return
        preset = str(self._filter_combo.itemData(idx) or "magic_color")
        page.filter_preset = preset
        self._has_unsaved_scans = True
        self._render_active_preview()
        self._refresh_active_thumbnail()
        self._sync_batch_state_ui()

    def _on_skew_slider_changed(self, tenths: int) -> None:
        angle = round(tenths / 10.0, 1)
        self._skew_val_lbl.setText(f"{angle:+.1f}°" if abs(angle) >= 0.05 else "0.0°")
        if self._is_syncing_controls:
            return
        page = self.batch.active_page
        if page is None:
            return
        page.skew_angle = angle
        self._has_unsaved_scans = True
        self._render_active_preview()
        self._refresh_active_thumbnail()

    def _on_tone_sliders_changed(self) -> None:
        b_val = self._brightness_slider.value()
        c_val = self._contrast_slider.value()
        self._brightness_val_lbl.setText(str(b_val))
        self._contrast_val_lbl.setText(str(c_val))
        if self._is_syncing_controls:
            return
        page = self.batch.active_page
        if page is None:
            return
        page.brightness = b_val
        page.contrast = c_val
        self._has_unsaved_scans = True
        self._render_active_preview()
        self._refresh_active_thumbnail()

    def rotate_active_page(self, delta_degrees: int = 90) -> None:
        page = self.batch.active_page
        if page is None:
            return
        page.rotation = (page.rotation + delta_degrees) % 360
        self._has_unsaved_scans = True
        self._render_active_preview()
        self._refresh_active_thumbnail()
        self._sync_batch_state_ui()

    def apply_auto_deskew(self) -> float:
        """Estimate tilt angle on active page and apply automatic straightening."""
        page = self.batch.active_page
        if page is None:
            return 0.0
        detected = estimate_skew_angle(page.source_image)
        page.skew_angle = round(-detected, 1)
        self._has_unsaved_scans = True
        self._sync_inspector_from_active_page()
        self._render_active_preview()
        self._refresh_active_thumbnail()
        self.set_status_message(f"Auto-deskew applied ({page.skew_angle:+.1f}°)", timeout_ms=2000)
        return page.skew_angle

    def _toggle_crop_mode(self) -> None:
        page = self.batch.active_page
        if page is None:
            self._crop_mode = False
            self._crop_toggle_btn.setChecked(False)
            return
        self._crop_mode = not self._crop_mode
        self._crop_toggle_btn.setChecked(self._crop_mode)
        self._scan_canvas.set_crop_mode(self._crop_mode, page.crop_rect)

    def _on_canvas_crop_changed(self, crop_tuple: tuple[float, float, float, float]) -> None:
        page = self.batch.active_page
        if page is None:
            return
        page.crop_rect = crop_tuple
        self._has_unsaved_scans = True
        self._refresh_active_thumbnail()
        self._sync_batch_state_ui()

    def reset_active_crop(self) -> None:
        page = self.batch.active_page
        if page is None:
            return
        page.crop_rect = (0.0, 0.0, 1.0, 1.0)
        self._crop_mode = False
        self._crop_toggle_btn.setChecked(False)
        self._scan_canvas.set_crop_mode(False, page.crop_rect)
        self._render_active_preview()
        self._refresh_active_thumbnail()

    def reset_active_adjustments(self) -> None:
        page = self.batch.active_page
        if page is None:
            return
        page.reset_adjustments()
        self._crop_mode = False
        self._crop_toggle_btn.setChecked(False)
        self._scan_canvas.set_crop_mode(False, page.crop_rect)
        self._sync_inspector_from_active_page()
        self._render_active_preview()
        self._refresh_active_thumbnail()
        self.set_status_message("Reset page adjustments", timeout_ms=1800)

    def duplicate_active_page(self) -> bool:
        if self.batch.duplicate_active_page() is None:
            return False
        self._has_unsaved_scans = True
        self._rebuild_thumbnail_list()
        self._sync_inspector_from_active_page()
        self._render_active_preview()
        self._sync_batch_state_ui()
        return True

    def delete_active_page(self) -> bool:
        if not self.batch.delete_page():
            return False
        self._has_unsaved_scans = not self.batch.is_empty
        self._rebuild_thumbnail_list()
        self._sync_inspector_from_active_page()
        self._render_active_preview()
        self._sync_batch_state_ui()
        return True

    def clear_scan_batch(self) -> None:
        if self.batch.is_empty:
            return
        confirmed = ask_confirmation(
            parent=self,
            title="Clear All Scanned Pages?",
            message="Remove all scanned pages from the current workspace?",
            confirm_text="Clear Batch",
            destructive=True,
        )
        if not confirmed:
            return
        self.batch.clear()
        self._has_unsaved_scans = False
        self._rebuild_thumbnail_list()
        self._sync_inspector_from_active_page()
        self._sync_batch_state_ui()

    def _step_zoom(self, delta: int) -> None:
        next_idx = max(0, min(len(_ZOOM_PRESETS) - 1, self._zoom_combo.currentIndex() + delta))
        self._zoom_combo.setCurrentIndex(next_idx)

    def _on_zoom_changed(self, idx: int) -> None:
        val = self._zoom_combo.itemData(idx)
        if isinstance(val, int):
            self._zoom_percent = val
            self._render_active_preview()

    def fit_page_in_view(self) -> None:
        if self.batch.is_empty:
            return
        avail_h = max(320.0, float(self._scroll_area.viewport().height()) - 48.0)
        ideal_zoom = max(50, min(200, int(round((avail_h / 792.0) * 100.0))))
        best_idx = min(
            range(len(_ZOOM_PRESETS)),
            key=lambda i: abs(_ZOOM_PRESETS[i] - ideal_zoom),
        )
        self._zoom_combo.setCurrentIndex(best_idx)

    # ------------------------------------------------------------------
    # Multi-Page PDF & Batch Image Export
    # ------------------------------------------------------------------

    def export_pdf_prompt(self) -> bool:
        if self.batch.is_empty:
            return False
        stem = sanitize_filename(self.batch.pages[0].title or "Scanned_Document")
        selected, _ = QFileDialog.getSaveFileName(
            self,
            "Save Scanned Pages as PDF",
            str(Path.home() / "Documents" / f"{stem}.pdf"),
            "PDF Document (*.pdf)",
        )
        if not selected:
            return False
        return self.export_pdf_to(selected)

    def export_pdf_to(self, destination: str | Path) -> bool:
        paper_mode = str(self._pdf_paper_combo.currentData() or "a4")
        try:
            saved_path = export_scan_batch_to_pdf(
                batch=self.batch,
                destination=destination,
                page_size_mode=paper_mode,
            )
        except FileOperationError as exc:
            show_error_dialog(self, "PDF Export Failed", str(exc))
            return False

        self._has_unsaved_scans = False
        self._sync_batch_state_ui()
        self.notify(
            "Scan Saved as PDF",
            f"Saved '{saved_path.name}' ({len(self.batch.pages)} page(s)).",
            level=NotificationLevel.SUCCESS,
        )
        return True

    def export_images_prompt(self, ext: str = ".png") -> bool:
        if self.batch.is_empty:
            return False
        chosen_dir = QFileDialog.getExistingDirectory(
            self,
            "Select Folder to Export Scanned Images",
            str(Path.home() / "Pictures"),
        )
        if not chosen_dir:
            return False
        return self.export_images_to_dir(chosen_dir, ext=ext)

    def export_images_to_dir(self, output_dir: str | Path, ext: str = ".png") -> bool:
        stem = sanitize_filename(self.batch.pages[0].title if self.batch.pages else "Scan_Page")
        try:
            written = export_batch_as_images(
                batch=self.batch,
                output_dir=output_dir,
                base_name=stem,
                ext=ext,
            )
        except FileOperationError as exc:
            show_error_dialog(self, "Image Export Failed", str(exc))
            return False

        self._has_unsaved_scans = False
        self._sync_batch_state_ui()
        self.notify(
            "Images Exported",
            f"Exported {len(written)} {ext.upper().lstrip('.')} file(s).",
            level=NotificationLevel.SUCCESS,
        )
        return True

    def can_close(self) -> bool:
        if not self._has_unsaved_scans or self.batch.is_empty:
            return True
        return ask_confirmation(
            parent=self,
            title="Discard Unsaved Scans?",
            message="You have scanned pages that have not been exported yet. Close without saving?",
            confirm_text="Discard Scans",
            destructive=True,
        )

    def on_theme_updated(self, theme_name: str) -> None:
        _ = theme_name
        if hasattr(self, "_scan_canvas"):
            self._scan_canvas.set_tokens(self.theme_manager.tokens)


def main() -> int:
    """Application entry point for SimpleScan.exe."""
    return bootstrap_application(
        app_id=APP_ID,
        window_factory=SimpleScanWindow,
    )


if __name__ == "__main__":
    raise SystemExit(main())