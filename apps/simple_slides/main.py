"""SimpleSlides — Direct on-slide 16:9 presentation editor & presenter in Soft Modern Pastel."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QMarginsF, QRect, QRectF, QSize, QSizeF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPageLayout,
    QPageSize,
    QPainter,
    QPaintEvent,
    QPdfWriter,
    QPen,
    QPixmap,
    QResizeEvent,
    QShortcut,
)
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from apps.simple_slides.presentation_model import (
    APP_ID,
    DEFAULT_PRESENTATION_EXTENSION,
    SLIDE_LAYOUTS,
    SLIDE_THEMES,
    PresentationModel,
    SlideRecord,
    SlideThemePalette,
    get_slide_theme,
    load_presentation,
    save_presentation,
)
from core.file_manager.file_ops import FileOperationError, sanitize_filename
from core.notifications.notifier import NotificationLevel
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

_SLIDE_FONTS: tuple[str, ...] = (
    "Inter",
    "Georgia",
    "Calibri",
    "Times New Roman",
    "JetBrains Mono",
)

_TITLE_SIZES: tuple[int, ...] = (24, 28, 32, 36, 40, 44, 52)
_BODY_SIZES: tuple[int, ...] = (12, 14, 16, 18, 20, 22, 24)


def _resolve_slide_colors(
    slide: SlideRecord,
    palette: SlideThemePalette,
) -> tuple[str, str, str, str]:
    """Return (bg_hex, title_hex, body_hex, accent_hex) applying per-slide custom overrides."""
    default_bg = palette.card_hex if slide.layout == "section" else palette.bg_hex
    bg_hex = slide.custom_bg_hex.strip() or default_bg
    title_hex = slide.custom_text_hex.strip() or palette.title_hex
    body_hex = slide.custom_text_hex.strip() or palette.body_hex
    accent_hex = slide.custom_accent_hex.strip() or palette.accent_hex
    return bg_hex, title_hex, body_hex, accent_hex


def _qt_align_from_slide(slide_align: str, default_flag: Qt.AlignmentFlag) -> Qt.AlignmentFlag:
    mapping: dict[str, Qt.AlignmentFlag] = {
        "left": Qt.AlignmentFlag.AlignLeft,
        "center": Qt.AlignmentFlag.AlignHCenter,
        "right": Qt.AlignmentFlag.AlignRight,
    }
    return mapping.get(slide_align, default_flag)


def render_slide_to_painter(
    painter: QPainter,
    target_rect: QRectF,
    slide: SlideRecord,
    palette: SlideThemePalette,
    slide_number: int = 1,
    total_slides: int = 1,
    is_thumbnail: bool = False,
    draw_text: bool = True,
) -> None:
    """Render a 16:9 slide into `target_rect` with proportional typography and pastel cards."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

    w = target_rect.width()
    h = target_rect.height()
    if w <= 4.0 or h <= 4.0:
        painter.restore()
        return

    scale = min(w / 960.0, h / 540.0)
    corner_r = max(3.0, 12.0 * scale)
    bg_hex, title_hex, body_hex, accent_hex = _resolve_slide_colors(slide, palette)

    # Slide background & subtle border
    painter.setPen(QPen(QColor(palette.border_hex), max(1.0, 1.2 * scale)))
    painter.setBrush(QColor(bg_hex))
    painter.drawRoundedRect(target_rect, corner_r, corner_r)

    # Top-left pastel accent bar
    bar_w = 54.0 * scale
    bar_h = max(2.0, 4.5 * scale)
    bar_x = target_rect.left() + 48.0 * scale
    bar_y = target_rect.top() + 28.0 * scale
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(accent_hex))
    painter.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), bar_h / 2.0, bar_h / 2.0)

    font_family = slide.font_family or "Inter"

    def _scaled_font(pt: float, bold: bool = False, italic: bool = False, underline: bool = False) -> QFont:
        f = QFont(font_family)
        f.setPointSizeF(max(4.5, pt * scale))
        f.setBold(bold)
        f.setItalic(italic)
        f.setUnderline(underline)
        return f

    pad_x = 52.0 * scale
    inner_w = max(20.0, w - pad_x * 2.0)

    if slide.layout in {"title", "section"}:
        h_align = _qt_align_from_slide(slide.text_align, Qt.AlignmentFlag.AlignHCenter)
        if draw_text:
            title_rect = QRectF(
                target_rect.left() + pad_x,
                target_rect.top() + h * 0.26,
                inner_w,
                h * 0.28,
            )
            painter.setFont(
                _scaled_font(
                    float(slide.title_size + 4),
                    bold=True,
                    italic=slide.italic,
                    underline=slide.underline,
                )
            )
            painter.setPen(QColor(title_hex))
            painter.drawText(
                title_rect,
                h_align | Qt.AlignmentFlag.AlignBottom | Qt.TextFlag.TextWordWrap,
                slide.title.strip() or "Untitled Slide",
            )

        pill_w = 72.0 * scale
        pill_h = max(2.0, 3.5 * scale)
        pill_y = target_rect.top() + h * 0.57
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(accent_hex))
        painter.drawRoundedRect(
            QRectF(target_rect.center().x() - pill_w / 2.0, pill_y, pill_w, pill_h),
            pill_h / 2.0,
            pill_h / 2.0,
        )

        if draw_text and slide.subtitle.strip():
            sub_rect = QRectF(
                target_rect.left() + pad_x * 1.2,
                target_rect.top() + h * 0.61,
                max(20.0, w - pad_x * 2.4),
                h * 0.22,
            )
            painter.setFont(
                _scaled_font(
                    float(slide.body_size + 1),
                    bold=slide.bold,
                    italic=slide.italic,
                    underline=slide.underline,
                )
            )
            painter.setPen(QColor(palette.muted_hex))
            painter.drawText(
                sub_rect,
                h_align | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                slide.subtitle.strip(),
            )

    elif slide.layout == "quote":
        card_margin_x = 76.0 * scale
        card_top = target_rect.top() + h * 0.19
        card_h = h * 0.54
        quote_card = QRectF(
            target_rect.left() + card_margin_x,
            card_top,
            max(30.0, w - card_margin_x * 2.0),
            card_h,
        )
        painter.setPen(QPen(QColor(palette.border_hex), max(1.0, 1.0 * scale)))
        painter.setBrush(QColor(palette.card_hex))
        painter.drawRoundedRect(quote_card, max(4.0, 16.0 * scale), max(4.0, 16.0 * scale))

        if draw_text:
            h_align = _qt_align_from_slide(slide.text_align, Qt.AlignmentFlag.AlignHCenter)
            text_box = quote_card.adjusted(28.0 * scale, 22.0 * scale, -28.0 * scale, -22.0 * scale)
            painter.setFont(
                _scaled_font(
                    float(max(18, slide.title_size - 8)),
                    bold=True,
                    italic=True,
                    underline=slide.underline,
                )
            )
            painter.setPen(QColor(title_hex))
            painter.drawText(
                text_box,
                h_align | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap,
                slide.title.strip() or "“Add your key takeaway or quote here.”",
            )

            if slide.subtitle.strip():
                attr_rect = QRectF(
                    target_rect.left() + card_margin_x,
                    quote_card.bottom() + 14.0 * scale,
                    max(30.0, w - card_margin_x * 2.0),
                    36.0 * scale,
                )
                painter.setFont(
                    _scaled_font(float(max(13, slide.body_size - 1)), bold=True)
                )
                painter.setPen(QColor(accent_hex))
                painter.drawText(
                    attr_rect,
                    h_align | Qt.AlignmentFlag.AlignTop,
                    slide.subtitle.strip(),
                )

    elif slide.layout == "two_column":
        h_align = _qt_align_from_slide(slide.text_align, Qt.AlignmentFlag.AlignLeft)
        if draw_text:
            header_rect = QRectF(
                target_rect.left() + pad_x,
                target_rect.top() + 44.0 * scale,
                inner_w,
                54.0 * scale,
            )
            painter.setFont(
                _scaled_font(
                    float(slide.title_size - 6),
                    bold=True,
                    italic=slide.italic,
                    underline=slide.underline,
                )
            )
            painter.setPen(QColor(title_hex))
            painter.drawText(
                header_rect,
                h_align | Qt.AlignmentFlag.AlignVCenter,
                slide.title.strip() or "Comparison Overview",
            )

        cols_top = target_rect.top() + 116.0 * scale
        cols_h = max(30.0, h - 164.0 * scale)
        gap = 24.0 * scale
        col_w = max(20.0, (inner_w - gap) / 2.0)

        left_card = QRectF(target_rect.left() + pad_x, cols_top, col_w, cols_h)
        right_card = QRectF(left_card.right() + gap, cols_top, col_w, cols_h)

        for card_rect, lines in ((left_card, slide.body_lines), (right_card, slide.right_lines)):
            painter.setPen(QPen(QColor(palette.border_hex), max(1.0, 1.0 * scale)))
            painter.setBrush(QColor(palette.card_hex))
            painter.drawRoundedRect(card_rect, max(4.0, 14.0 * scale), max(4.0, 14.0 * scale))

            if draw_text:
                painter.setFont(
                    _scaled_font(
                        float(slide.body_size),
                        bold=slide.bold,
                        italic=slide.italic,
                        underline=slide.underline,
                    )
                )
                painter.setPen(QColor(body_hex))
                y_cursor = card_rect.top() + 18.0 * scale
                line_step = max(14.0, (slide.body_size + 16) * scale)
                for raw_line in lines[:9]:
                    clean = raw_line.strip()
                    if not clean:
                        continue
                    if y_cursor + line_step > card_rect.bottom() - 8.0 * scale:
                        break
                    row_rect = QRectF(
                        card_rect.left() + 18.0 * scale,
                        y_cursor,
                        max(10.0, card_rect.width() - 36.0 * scale),
                        line_step,
                    )
                    painter.drawText(
                        row_rect,
                        h_align | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap,
                        f"•  {clean}",
                    )
                    y_cursor += line_step

    else:  # title_body
        h_align = _qt_align_from_slide(slide.text_align, Qt.AlignmentFlag.AlignLeft)
        header_rect = QRectF(
            target_rect.left() + pad_x,
            target_rect.top() + 44.0 * scale,
            inner_w,
            56.0 * scale,
        )
        if draw_text:
            painter.setFont(
                _scaled_font(
                    float(slide.title_size - 5),
                    bold=True,
                    italic=slide.italic,
                    underline=slide.underline,
                )
            )
            painter.setPen(QColor(title_hex))
            painter.drawText(
                header_rect,
                h_align | Qt.AlignmentFlag.AlignVCenter,
                slide.title.strip() or "Slide Heading",
            )

        div_y = header_rect.bottom() + 8.0 * scale
        painter.setPen(QPen(QColor(palette.border_hex), max(1.0, 1.0 * scale)))
        painter.drawLine(
            int(round(target_rect.left() + pad_x)),
            int(round(div_y)),
            int(round(target_rect.right() - pad_x)),
            int(round(div_y)),
        )

        if draw_text:
            y_cursor = div_y + 16.0 * scale
            if slide.subtitle.strip():
                sub_rect = QRectF(
                    target_rect.left() + pad_x,
                    y_cursor,
                    inner_w,
                    32.0 * scale,
                )
                painter.setFont(_scaled_font(float(max(12, slide.body_size - 1)), italic=True))
                painter.setPen(QColor(palette.muted_hex))
                painter.drawText(
                    sub_rect,
                    h_align | Qt.AlignmentFlag.AlignVCenter,
                    slide.subtitle.strip(),
                )
                y_cursor += 36.0 * scale

            painter.setFont(
                _scaled_font(
                    float(slide.body_size + 1),
                    bold=slide.bold,
                    italic=slide.italic,
                    underline=slide.underline,
                )
            )
            painter.setPen(QColor(body_hex))
            line_step = max(16.0, (slide.body_size + 18) * scale)
            for raw_line in slide.body_lines[:10]:
                clean = raw_line.strip()
                if not clean:
                    continue
                if y_cursor + line_step > target_rect.bottom() - 32.0 * scale:
                    break
                row_rect = QRectF(
                    target_rect.left() + pad_x + 4.0 * scale,
                    y_cursor,
                    max(10.0, inner_w - 8.0 * scale),
                    line_step,
                )
                painter.drawText(
                    row_rect,
                    h_align | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap,
                    f"•  {clean}",
                )
                y_cursor += line_step

    if not is_thumbnail:
        num_rect = QRectF(
            target_rect.right() - 120.0 * scale,
            target_rect.bottom() - 32.0 * scale,
            72.0 * scale,
            20.0 * scale,
        )
        painter.setFont(_scaled_font(10.0))
        painter.setPen(QColor(palette.muted_hex))
        painter.drawText(
            num_rect,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            f"{slide_number} / {total_slides}",
        )

    painter.restore()


def render_slide_thumbnail(
    slide: SlideRecord,
    palette: SlideThemePalette,
    width: int = 168,
    height: int = 95,
) -> QPixmap:
    """Rasterize a crisp 2x High-DPI miniature 16:9 slide preview pixmap."""
    dpr = 2
    px = QPixmap(width * dpr, height * dpr)
    px.fill(Qt.GlobalColor.transparent)

    painter = QPainter(px)
    render_slide_to_painter(
        painter=painter,
        target_rect=QRectF(1.0, 1.0, float(width * dpr - 2), float(height * dpr - 2)),
        slide=slide,
        palette=palette,
        is_thumbnail=True,
        draw_text=True,
    )
    painter.end()
    px.setDevicePixelRatio(float(dpr))
    return px


class SlideCanvasWidget(QWidget):
    """Read-only 16:9 widescreen slide stage used by full-screen presenter mode."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._slide = SlideRecord()
        self._palette = get_slide_theme("lavender_snow")
        self._slide_index = 0
        self._total_slides = 1
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(480, 270)

    def set_slide_state(
        self,
        slide: SlideRecord,
        palette: SlideThemePalette,
        slide_index: int,
        total_slides: int,
    ) -> None:
        self._slide = slide
        self._palette = palette
        self._slide_index = slide_index
        self._total_slides = max(1, total_slides)
        self.update()

    def _compute_16x9_rect(self) -> QRectF:
        pad = 16.0
        avail_w = max(160.0, float(self.width()) - pad * 2.0)
        avail_h = max(90.0, float(self.height()) - pad * 2.0)

        target_ratio = 16.0 / 9.0
        if avail_w / avail_h > target_ratio:
            slide_h = avail_h
            slide_w = slide_h * target_ratio
        else:
            slide_w = avail_w
            slide_h = slide_w / target_ratio

        left = (float(self.width()) - slide_w) / 2.0
        top = (float(self.height()) - slide_h) / 2.0
        return QRectF(left, top, slide_w, slide_h)

    def paintEvent(self, event: QPaintEvent) -> None:
        _ = event
        painter = QPainter(self)
        stage_rect = self._compute_16x9_rect()
        render_slide_to_painter(
            painter=painter,
            target_rect=stage_rect,
            slide=self._slide,
            palette=self._palette,
            slide_number=self._slide_index + 1,
            total_slides=self._total_slides,
            is_thumbnail=False,
            draw_text=True,
        )
        painter.end()


class InteractiveSlideEditorWidget(QWidget):
    """16:9 widescreen stage where users click directly on text boxes to edit on-slide."""

    slide_edited = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._slide = SlideRecord()
        self._palette = get_slide_theme("lavender_snow")
        self._slide_index = 0
        self._total_slides = 1
        self._is_syncing = False

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(540, 304)

        # Direct On-Slide Editors
        self.title_box = QLineEdit(self)
        self.title_box.setPlaceholderText("Click to add slide title...")
        self.title_box.textEdited.connect(self._on_field_edited)

        self.subtitle_box = QLineEdit(self)
        self.subtitle_box.setPlaceholderText("Click to add subtitle...")
        self.subtitle_box.textEdited.connect(self._on_field_edited)

        self.left_bullets_box = QPlainTextEdit(self)
        self.left_bullets_box.setPlaceholderText("Click to type points (one per line)...")
        self.left_bullets_box.textChanged.connect(self._on_field_edited)

        self.right_bullets_box = QPlainTextEdit(self)
        self.right_bullets_box.setPlaceholderText("Click to type right column points...")
        self.right_bullets_box.textChanged.connect(self._on_field_edited)

    def set_slide_state(
        self,
        slide: SlideRecord,
        palette: SlideThemePalette,
        slide_index: int,
        total_slides: int,
    ) -> None:
        self._slide = slide
        self._palette = palette
        self._slide_index = slide_index
        self._total_slides = max(1, total_slides)

        self._is_syncing = True
        try:
            self.title_box.setText(slide.title)
            self.subtitle_box.setText(slide.subtitle)
            self.left_bullets_box.setPlainText("\n".join(slide.body_lines))
            self.right_bullets_box.setPlainText("\n".join(slide.right_lines))
        finally:
            self._is_syncing = False

        self._reposition_and_style_editors()
        self.update()

    def _on_field_edited(self) -> None:
        if self._is_syncing:
            return
        self._slide.title = self.title_box.text()
        self._slide.subtitle = self.subtitle_box.text()
        self._slide.body_lines = [
            ln.strip() for ln in self.left_bullets_box.toPlainText().splitlines() if ln.strip()
        ]
        self._slide.right_lines = [
            ln.strip() for ln in self.right_bullets_box.toPlainText().splitlines() if ln.strip()
        ]
        self.slide_edited.emit()

    def _compute_16x9_rect(self) -> QRectF:
        pad = 14.0
        avail_w = max(200.0, float(self.width()) - pad * 2.0)
        avail_h = max(112.0, float(self.height()) - pad * 2.0)

        target_ratio = 16.0 / 9.0
        if avail_w / avail_h > target_ratio:
            slide_h = avail_h
            slide_w = slide_h * target_ratio
        else:
            slide_w = avail_w
            slide_h = slide_w / target_ratio

        left = (float(self.width()) - slide_w) / 2.0
        top = (float(self.height()) - slide_h) / 2.0
        return QRectF(left, top, slide_w, slide_h)

    @staticmethod
    def _to_qrect(rect_f: QRectF) -> QRect:
        return QRect(
            int(round(rect_f.left())),
            int(round(rect_f.top())),
            max(20, int(round(rect_f.width()))),
            max(20, int(round(rect_f.height()))),
        )

    def _box_qss(
        self,
        widget_cls: str,
        text_hex: str,
        accent_hex: str,
        font_family: str,
        font_px: int,
        bold: bool,
        italic: bool,
        underline: bool,
    ) -> str:
        weight = "700" if bold else "400"
        f_style = "italic" if italic else "normal"
        f_dec = "underline" if underline else "none"
        return (
            f"{widget_cls} {{"
            "  background: transparent;"
            "  border: 1px dashed transparent;"
            "  border-radius: 8px;"
            f"  color: {text_hex};"
            f"  font-family: '{font_family}', 'Segoe UI', sans-serif;"
            f"  font-size: {max(10, font_px)}px;"
            f"  font-weight: {weight};"
            f"  font-style: {f_style};"
            f"  text-decoration: {f_dec};"
            "  padding: 6px 10px;"
            "}"
            f"{widget_cls}:hover {{"
            f"  border: 1px dashed {accent_hex};"
            "  background-color: rgba(128, 128, 140, 0.05);"
            "}"
            f"{widget_cls}:focus {{"
            f"  border: 1.5px solid {accent_hex};"
            "  background-color: rgba(128, 128, 140, 0.07);"
            "}"
        )

    def _reposition_and_style_editors(self) -> None:
        stage = self._compute_16x9_rect()
        w = stage.width()
        h = stage.height()
        scale = min(w / 960.0, h / 540.0)
        pad_x = 52.0 * scale
        inner_w = max(40.0, w - pad_x * 2.0)

        slide = self._slide
        palette = self._palette
        _, title_hex, body_hex, accent_hex = _resolve_slide_colors(slide, palette)
        font_fam = slide.font_family or "Inter"
        layout = slide.layout

        title_px = round((slide.title_size + (4 if layout in {"title", "section"} else -4)) * scale)
        body_px = round((slide.body_size + 1) * scale)

        default_align = (
            Qt.AlignmentFlag.AlignHCenter
            if layout in {"title", "section", "quote"}
            else Qt.AlignmentFlag.AlignLeft
        )
        qt_align = _qt_align_from_slide(slide.text_align, default_align)
        self.title_box.setAlignment(qt_align)
        self.subtitle_box.setAlignment(qt_align)

        self.title_box.setStyleSheet(
            self._box_qss(
                "QLineEdit",
                title_hex,
                accent_hex,
                font_fam,
                title_px,
                bold=True,
                italic=(slide.italic or layout == "quote"),
                underline=slide.underline,
            )
        )
        self.subtitle_box.setStyleSheet(
            self._box_qss(
                "QLineEdit",
                accent_hex if layout == "quote" else palette.muted_hex,
                accent_hex,
                font_fam,
                max(11, round(slide.body_size * scale)),
                bold=(slide.bold or layout == "quote"),
                italic=(slide.italic or layout == "title_body"),
                underline=slide.underline,
            )
        )
        body_qss = self._box_qss(
            "QPlainTextEdit",
            body_hex,
            accent_hex,
            font_fam,
            body_px,
            bold=slide.bold,
            italic=slide.italic,
            underline=slide.underline,
        )
        self.left_bullets_box.setStyleSheet(body_qss)
        self.right_bullets_box.setStyleSheet(body_qss)

        if layout in {"title", "section"}:
            self.title_box.setPlaceholderText("Click to add hero title...")
            self.subtitle_box.setPlaceholderText("Click to add subtitle...")
            self.title_box.setGeometry(
                self._to_qrect(QRectF(stage.left() + pad_x, stage.top() + h * 0.34, inner_w, 64.0 * scale))
            )
            self.subtitle_box.setGeometry(
                self._to_qrect(
                    QRectF(
                        stage.left() + pad_x * 1.2,
                        stage.top() + h * 0.61,
                        max(40.0, w - pad_x * 2.4),
                        46.0 * scale,
                    )
                )
            )
            self.title_box.setVisible(True)
            self.subtitle_box.setVisible(True)
            self.left_bullets_box.setVisible(False)
            self.right_bullets_box.setVisible(False)

        elif layout == "quote":
            card_margin_x = 76.0 * scale
            card_top = stage.top() + h * 0.19
            card_h = h * 0.54
            self.title_box.setPlaceholderText("“Click to type your quote or key takeaway...”")
            self.subtitle_box.setPlaceholderText("— Author or Source")
            self.title_box.setGeometry(
                self._to_qrect(
                    QRectF(
                        stage.left() + card_margin_x + 20.0 * scale,
                        card_top + card_h * 0.28,
                        max(40.0, w - (card_margin_x + 20.0 * scale) * 2.0),
                        58.0 * scale,
                    )
                )
            )
            self.subtitle_box.setGeometry(
                self._to_qrect(
                    QRectF(
                        stage.left() + card_margin_x,
                        card_top + card_h + 12.0 * scale,
                        max(40.0, w - card_margin_x * 2.0),
                        40.0 * scale,
                    )
                )
            )
            self.title_box.setVisible(True)
            self.subtitle_box.setVisible(True)
            self.left_bullets_box.setVisible(False)
            self.right_bullets_box.setVisible(False)

        elif layout == "two_column":
            self.title_box.setPlaceholderText("Click to add comparison title...")
            self.title_box.setGeometry(
                self._to_qrect(QRectF(stage.left() + pad_x, stage.top() + 42.0 * scale, inner_w, 56.0 * scale))
            )
            cols_top = stage.top() + 122.0 * scale
            cols_h = max(60.0, h - 178.0 * scale)
            gap = 24.0 * scale
            col_w = max(40.0, (inner_w - gap) / 2.0)

            self.left_bullets_box.setGeometry(
                self._to_qrect(
                    QRectF(
                        stage.left() + pad_x + 10.0 * scale,
                        cols_top,
                        max(30.0, col_w - 20.0 * scale),
                        cols_h,
                    )
                )
            )
            self.right_bullets_box.setGeometry(
                self._to_qrect(
                    QRectF(
                        stage.left() + pad_x + col_w + gap + 10.0 * scale,
                        cols_top,
                        max(30.0, col_w - 20.0 * scale),
                        cols_h,
                    )
                )
            )
            self.title_box.setVisible(True)
            self.subtitle_box.setVisible(False)
            self.left_bullets_box.setVisible(True)
            self.right_bullets_box.setVisible(True)

        else:  # title_body
            self.title_box.setPlaceholderText("Click to add slide heading...")
            self.subtitle_box.setPlaceholderText("Optional kicker or subtitle...")
            self.title_box.setGeometry(
                self._to_qrect(QRectF(stage.left() + pad_x, stage.top() + 42.0 * scale, inner_w, 56.0 * scale))
            )
            self.subtitle_box.setGeometry(
                self._to_qrect(QRectF(stage.left() + pad_x, stage.top() + 112.0 * scale, inner_w, 36.0 * scale))
            )
            self.left_bullets_box.setGeometry(
                self._to_qrect(
                    QRectF(
                        stage.left() + pad_x,
                        stage.top() + 154.0 * scale,
                        inner_w,
                        max(80.0, h - 196.0 * scale),
                    )
                )
            )
            self.title_box.setVisible(True)
            self.subtitle_box.setVisible(True)
            self.left_bullets_box.setVisible(True)
            self.right_bullets_box.setVisible(False)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._reposition_and_style_editors()

    def paintEvent(self, event: QPaintEvent) -> None:
        _ = event
        painter = QPainter(self)
        stage_rect = self._compute_16x9_rect()
        render_slide_to_painter(
            painter=painter,
            target_rect=stage_rect,
            slide=self._slide,
            palette=self._palette,
            slide_number=self._slide_index + 1,
            total_slides=self._total_slides,
            is_thumbnail=False,
            draw_text=False,
        )
        painter.end()


class SlideshowPresenterDialog(QDialog):
    """Full-screen distraction-free slideshow viewer with keyboard & click navigation."""

    def __init__(
        self,
        deck: PresentationModel,
        start_index: int = 0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._deck = deck
        self._curr_idx = max(0, min(len(deck.slides) - 1, start_index))

        self.setWindowTitle("SimpleSlides — Presenter View")
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setStyleSheet("QDialog { background-color: #111115; }")

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        root.setSpacing(12)

        self._stage = SlideCanvasWidget(self)
        root.addWidget(self._stage, 1)

        hud = QHBoxLayout()
        hud.setSpacing(10)

        self._counter_lbl = QLabel("", self)
        self._counter_lbl.setStyleSheet("color: #CFCBD8; font-size: 12px; font-weight: 600;")
        hud.addWidget(self._counter_lbl)

        hud.addStretch(1)

        prev_btn = QPushButton("‹ Previous", self)
        prev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        prev_btn.setStyleSheet(
            "QPushButton { background: #26252E; color: #F2F0F5; border: 1px solid #3C3A47; "
            "border-radius: 9px; padding: 6px 14px; font-size: 12px; }"
            "QPushButton:hover { background: #343240; }"
        )
        prev_btn.clicked.connect(self.previous_slide)
        hud.addWidget(prev_btn)

        next_btn = QPushButton("Next ›", self)
        next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        next_btn.setStyleSheet(
            "QPushButton { background: #6F59C9; color: #FFFEFC; border: none; "
            "border-radius: 9px; padding: 6px 16px; font-size: 12px; font-weight: 600; }"
            "QPushButton:hover { background: #5F49B8; }"
        )
        next_btn.clicked.connect(self.next_slide)
        hud.addWidget(next_btn)

        exit_btn = QPushButton("Exit (Esc)", self)
        exit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        exit_btn.setStyleSheet(
            "QPushButton { background: #26252E; color: #CFCBD8; border: 1px solid #3C3A47; "
            "border-radius: 9px; padding: 6px 12px; font-size: 12px; }"
            "QPushButton:hover { background: #343240; }"
        )
        exit_btn.clicked.connect(self.accept)
        hud.addWidget(exit_btn)

        root.addLayout(hud)
        self._sync_slide()

    @property
    def current_slide_index(self) -> int:
        return self._curr_idx

    def _sync_slide(self) -> None:
        total = len(self._deck.slides)
        slide = self._deck.slides[self._curr_idx]
        self._stage.set_slide_state(slide, self._deck.palette, self._curr_idx, total)
        self._counter_lbl.setText(
            f"Slide {self._curr_idx + 1} of {total}   •   Click or Press Space to Advance"
        )

    def next_slide(self) -> None:
        if self._curr_idx + 1 < len(self._deck.slides):
            self._curr_idx += 1
            self._sync_slide()
        else:
            self.accept()

    def previous_slide(self) -> None:
        if self._curr_idx > 0:
            self._curr_idx -= 1
            self._sync_slide()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.next_slide()
        elif event.button() == Qt.MouseButton.RightButton:
            self.previous_slide()
        else:
            super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key in (
            Qt.Key.Key_Right,
            Qt.Key.Key_Down,
            Qt.Key.Key_Space,
            Qt.Key.Key_PageDown,
            Qt.Key.Key_Return,
        ):
            self.next_slide()
            return
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_PageUp, Qt.Key.Key_Backspace):
            self.previous_slide()
            return
        if key == Qt.Key.Key_Escape:
            self.accept()
            return
        super().keyPressEvent(event)


class SimpleSlidesWindow(BaseWindow):
    """PowerPoint & Google Slides-inspired direct on-slide editor in Soft Modern Pastel."""

    def __init__(self, deck: PresentationModel | None = None) -> None:
        super().__init__(app_id=APP_ID, min_width=960, min_height=640)
        if self.settings.get("window_width") is None:
            self.resize(1220, 780)

        self.deck: PresentationModel = deck or PresentationModel()
        self._current_file_path: Path | None = None
        self._is_dirty: bool = False
        self._is_syncing_ribbon: bool = False
        self._notes_visible: bool = bool(self.settings.get("notes_visible", True))

        self._build_header_actions()
        self._build_workspace()
        self._bind_shortcuts()

        self._rebuild_slide_navigator()
        self._sync_ribbon_from_active_slide()
        self._refresh_stage()
        self._sync_notes_visibility()
        self._sync_dirty_badge()

    def _build_header_actions(self) -> None:
        """Mount New, Open, Save (.pptx), Export (.pptx/.pdf), Notes toggle, and Present (F5)."""
        new_btn = make_button(
            text="New",
            variant="ghost",
            tooltip="Create a new presentation (Ctrl+N)",
            on_click=self.new_presentation,
            icon_name="plus",
            icon_size=14,
            parent=self,
        )
        new_btn.setStyleSheet("padding: 4px 9px; font-size: 12px;")
        self.header_actions_layout.addWidget(new_btn)

        open_btn = make_button(
            text="Open",
            variant="ghost",
            tooltip="Open PowerPoint (.pptx) presentation (Ctrl+O)",
            on_click=self.open_presentation_prompt,
            icon_name="folder",
            icon_size=14,
            parent=self,
        )
        open_btn.setStyleSheet("padding: 4px 9px; font-size: 12px;")
        self.header_actions_layout.addWidget(open_btn)

        save_btn = make_button(
            text="Save",
            variant="pastel",
            tooltip="Save presentation as .pptx (Ctrl+S)",
            on_click=self.save_presentation_action,
            icon_name="check",
            icon_size=14,
            parent=self,
        )
        save_btn.setStyleSheet("padding: 4px 11px; font-size: 12px;")
        self.header_actions_layout.addWidget(save_btn)

        export_btn = make_button(
            text="Export",
            variant="ghost",
            tooltip="Save As .pptx or Export 16:9 Widescreen PDF",
            on_click=self.export_presentation_as,
            parent=self,
        )
        export_btn.setStyleSheet("padding: 4px 9px; font-size: 12px;")
        self.header_actions_layout.addWidget(export_btn)

        self._notes_toggle_btn = QPushButton("Notes", self)
        self._notes_toggle_btn.setProperty("variant", "segmented")
        self._notes_toggle_btn.setCheckable(True)
        self._notes_toggle_btn.setChecked(self._notes_visible)
        self._notes_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._notes_toggle_btn.setToolTip("Show or hide speaker notes panel")
        self._notes_toggle_btn.setStyleSheet("padding: 4px 10px; font-size: 12px;")
        self._notes_toggle_btn.clicked.connect(self._toggle_notes_panel)
        self.header_actions_layout.addWidget(self._notes_toggle_btn)

        present_btn = make_button(
            text="Present",
            variant="primary",
            tooltip="Start full-screen slideshow (F5)",
            on_click=self.start_slideshow,
            parent=self,
        )
        present_btn.setStyleSheet("padding: 4px 13px; font-size: 12px;")
        self.header_actions_layout.addWidget(present_btn)

    def _build_workspace(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)

        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ==============================================================
        # 1. PowerPoint / Google Slides Formatting & Design Ribbon Bar
        # ==============================================================
        ribbon_bar = QFrame(central)
        ribbon_bar.setProperty("surface", "app-header")
        ribbon_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        rb_row = QHBoxLayout(ribbon_bar)
        rb_row.setContentsMargins(14, 6, 14, 6)
        rb_row.setSpacing(5)

        # + New Slide Dropdown
        add_slide_btn = QPushButton("+ Slide", ribbon_bar)
        add_slide_btn.setProperty("variant", "pastel")
        add_slide_btn.setProperty("has_menu", True)
        add_slide_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_slide_btn.setToolTip("Insert a new slide (Ctrl+M)")
        add_slide_btn.setStyleSheet("padding: 4px 22px 4px 10px; font-size: 12px;")
        add_menu = QMenu(add_slide_btn)
        for layout_id, layout_label in SLIDE_LAYOUTS:
            act = add_menu.addAction(layout_label)
            act.triggered.connect(lambda _=False, lid=layout_id: self.add_slide(lid))
        add_slide_btn.setMenu(add_menu)
        rb_row.addWidget(add_slide_btn)

        # Slide Layout Selector
        self._layout_combo = QComboBox(ribbon_bar)
        for lid, l_name in SLIDE_LAYOUTS:
            self._layout_combo.addItem(l_name, lid)
        self._layout_combo.setToolTip("Active slide layout")
        self._layout_combo.setStyleSheet("padding: 4px 8px; font-size: 12px;")
        self._layout_combo.currentIndexChanged.connect(self._on_layout_changed)
        rb_row.addWidget(self._layout_combo)

        rb_row.addWidget(VerticalDivider(ribbon_bar))

        # 12 Curated Pastel & Dark Deck Themes
        self._deck_theme_combo = QComboBox(ribbon_bar)
        for tid, pal in SLIDE_THEMES.items():
            self._deck_theme_combo.addItem(pal.display_name, tid)
        self._deck_theme_combo.setToolTip("Presentation pastel theme (12 built-in themes)")
        self._deck_theme_combo.setStyleSheet("padding: 4px 8px; font-size: 12px;")
        self._deck_theme_combo.currentIndexChanged.connect(self._on_deck_theme_changed)
        rb_row.addWidget(self._deck_theme_combo)

        # Custom Slide Colors Menu (Background, Text, Accent, Reset)
        colors_btn = QPushButton("Slide Colors", ribbon_bar)
        colors_btn.setProperty("variant", "ghost")
        colors_btn.setProperty("has_menu", True)
        colors_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        colors_btn.setToolTip("Customize active slide background, text, or accent color")
        colors_btn.setStyleSheet("padding: 4px 22px 4px 8px; font-size: 12px;")
        colors_menu = QMenu(colors_btn)
        colors_menu.addAction("Custom Background Color...", self._choose_slide_bg_color)
        colors_menu.addAction("Custom Text Color...", self._choose_slide_text_color)
        colors_menu.addAction("Custom Accent Color...", self._choose_slide_accent_color)
        colors_menu.addSeparator()
        colors_menu.addAction("Reset Slide Custom Colors", self._reset_slide_custom_colors)
        colors_btn.setMenu(colors_menu)
        rb_row.addWidget(colors_btn)

        rb_row.addWidget(VerticalDivider(ribbon_bar))

        # Font Family & Title / Body Font Sizes
        self._font_combo = QComboBox(ribbon_bar)
        for fam in _SLIDE_FONTS:
            self._font_combo.addItem(fam, fam)
        self._font_combo.setToolTip("Slide font family")
        self._font_combo.setStyleSheet("padding: 4px 8px; font-size: 12px;")
        self._font_combo.currentIndexChanged.connect(self._on_font_changed)
        rb_row.addWidget(self._font_combo)

        self._title_size_combo = QComboBox(ribbon_bar)
        for sz in _TITLE_SIZES:
            self._title_size_combo.addItem(f"Title {sz}", sz)
        self._title_size_combo.setToolTip("Title font size")
        self._title_size_combo.setStyleSheet("padding: 4px 7px; font-size: 12px;")
        self._title_size_combo.currentIndexChanged.connect(self._on_title_size_changed)
        rb_row.addWidget(self._title_size_combo)

        self._body_size_combo = QComboBox(ribbon_bar)
        for sz in _BODY_SIZES:
            self._body_size_combo.addItem(f"Body {sz}", sz)
        self._body_size_combo.setToolTip("Body / bullet font size")
        self._body_size_combo.setStyleSheet("padding: 4px 7px; font-size: 12px;")
        self._body_size_combo.currentIndexChanged.connect(self._on_body_size_changed)
        rb_row.addWidget(self._body_size_combo)

        rb_row.addWidget(VerticalDivider(ribbon_bar))

        # Bold, Italic, Underline
        self._bold_btn = QPushButton("B", ribbon_bar)
        self._bold_btn.setProperty("variant", "segmented")
        self._bold_btn.setCheckable(True)
        self._bold_btn.setToolTip("Bold slide body text")
        self._bold_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._bold_btn.setStyleSheet("padding: 4px 8px; font-size: 12px; font-weight: 700;")
        self._bold_btn.clicked.connect(self._on_style_toggled)
        rb_row.addWidget(self._bold_btn)

        self._italic_btn = QPushButton("I", ribbon_bar)
        self._italic_btn.setProperty("variant", "segmented")
        self._italic_btn.setCheckable(True)
        self._italic_btn.setToolTip("Italic slide text")
        self._italic_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._italic_btn.setStyleSheet("padding: 4px 9px; font-size: 12px; font-style: italic;")
        self._italic_btn.clicked.connect(self._on_style_toggled)
        rb_row.addWidget(self._italic_btn)

        self._underline_btn = QPushButton("U", ribbon_bar)
        self._underline_btn.setProperty("variant", "segmented")
        self._underline_btn.setCheckable(True)
        self._underline_btn.setToolTip("Underline slide text")
        self._underline_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._underline_btn.setStyleSheet(
            "padding: 4px 8px; font-size: 12px; text-decoration: underline;"
        )
        self._underline_btn.clicked.connect(self._on_style_toggled)
        rb_row.addWidget(self._underline_btn)

        rb_row.addWidget(VerticalDivider(ribbon_bar))

        # Alignment Pills
        for label, align_code, tip in (
            ("Left", "left", "Align slide text left"),
            ("Center", "center", "Center slide text"),
            ("Right", "right", "Align slide text right"),
        ):
            abtn = make_button(
                text=label,
                variant="ghost",
                tooltip=tip,
                on_click=lambda _=False, ac=align_code: self._set_slide_alignment(ac),
                parent=ribbon_bar,
            )
            abtn.setStyleSheet("padding: 4px 7px; font-size: 12px;")
            rb_row.addWidget(abtn)

        rb_row.addStretch(1)
        outer.addWidget(ribbon_bar)

        # ==============================================================
        # 2. Main 2-Pane Studio (Left Slide Navigator + Direct On-Slide Stage)
        # ==============================================================
        studio_row = QHBoxLayout()
        studio_row.setContentsMargins(0, 0, 0, 0)
        studio_row.setSpacing(0)

        # Left Pane: Slide Navigator Sidebar
        nav_sidebar = QFrame(central)
        nav_sidebar.setProperty("surface", "sidebar")
        nav_sidebar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        nav_sidebar.setFixedWidth(235)

        nav_layout = QVBoxLayout(nav_sidebar)
        nav_layout.setContentsMargins(14, 14, 14, 12)
        nav_layout.setSpacing(10)

        nav_layout.addWidget(SectionHeader("Slides", parent=nav_sidebar))
        nav_layout.addWidget(HorizontalDivider(nav_sidebar))

        self._slide_list = QListWidget(nav_sidebar)
        self._slide_list.setIconSize(QSize(168, 95))
        self._slide_list.setSpacing(4)
        self._slide_list.setStyleSheet(
            "QListWidget { border: none; background: transparent; padding: 0px; }"
        )
        self._slide_list.currentRowChanged.connect(self._on_slide_selected)
        nav_layout.addWidget(self._slide_list, 1)

        nav_layout.addWidget(HorizontalDivider(nav_sidebar))

        order_row = QHBoxLayout()
        order_row.setSpacing(4)

        self._move_up_btn = make_button(
            text="↑",
            variant="ghost",
            tooltip="Move slide up",
            on_click=self._move_slide_up,
            parent=nav_sidebar,
        )
        self._move_up_btn.setStyleSheet("padding: 4px 8px; font-size: 12px;")
        order_row.addWidget(self._move_up_btn)

        self._move_down_btn = make_button(
            text="↓",
            variant="ghost",
            tooltip="Move slide down",
            on_click=self._move_slide_down,
            parent=nav_sidebar,
        )
        self._move_down_btn.setStyleSheet("padding: 4px 8px; font-size: 12px;")
        order_row.addWidget(self._move_down_btn)

        dup_btn = make_button(
            text="Duplicate",
            variant="ghost",
            tooltip="Duplicate active slide (Ctrl+D)",
            on_click=self.duplicate_active_slide,
            parent=nav_sidebar,
        )
        dup_btn.setStyleSheet("padding: 4px 8px; font-size: 11px;")
        order_row.addWidget(dup_btn, 1)

        self._del_slide_btn = make_button(
            text="Delete",
            variant="ghost",
            tooltip="Delete active slide",
            on_click=self.delete_active_slide,
            parent=nav_sidebar,
        )
        self._del_slide_btn.setStyleSheet("padding: 4px 8px; font-size: 11px;")
        order_row.addWidget(self._del_slide_btn)

        nav_layout.addLayout(order_row)
        studio_row.addWidget(nav_sidebar)

        # Center Expansive Stage: Direct On-Slide Editor + Collapsible Speaker Notes
        center_col = QVBoxLayout()
        center_col.setContentsMargins(22, 16, 22, 16)
        center_col.setSpacing(12)

        self._canvas = InteractiveSlideEditorWidget(central)
        self._canvas.slide_edited.connect(self._on_on_slide_text_edited)
        center_col.addWidget(self._canvas, 6)

        self._notes_card = CardFrame(padding=12, spacing=6, parent=central)
        self._notes_card.setMaximumHeight(115)
        notes_hdr = QLabel("Speaker Notes", self._notes_card)
        notes_hdr.setProperty("role", "caption")
        notes_hdr.setStyleSheet("font-weight: 600;")
        self._notes_card.body_layout.addWidget(notes_hdr)

        self._notes_edit = QPlainTextEdit(self._notes_card)
        self._notes_edit.setPlaceholderText("Add presenter cues or speaker notes for this slide...")
        self._notes_edit.setStyleSheet(
            "QPlainTextEdit { border: none; background: transparent; padding: 0px; }"
        )
        self._notes_edit.textChanged.connect(self._on_notes_edited)
        self._notes_card.body_layout.addWidget(self._notes_edit, 1)

        center_col.addWidget(self._notes_card, 1)
        studio_row.addLayout(center_col, 1)
        outer.addLayout(studio_row, 1)

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

        self._status_badge = StatusBadge("New Deck (.pptx)", variant="lavender", parent=footer_bar)
        f_row.addWidget(self._status_badge)

        self._file_lbl = QLabel(
            "Click any text on the slide to edit directly  •  Default format: PowerPoint (.pptx)",
            footer_bar,
        )
        self._file_lbl.setProperty("role", "muted")
        f_row.addWidget(self._file_lbl)

        f_row.addStretch(1)

        self._slide_counter_lbl = QLabel("Slide 1 of 1", footer_bar)
        self._slide_counter_lbl.setProperty("role", "muted")
        f_row.addWidget(self._slide_counter_lbl)

        outer.addWidget(footer_bar)

    def _bind_shortcuts(self) -> None:
        QShortcut(QKeySequence.StandardKey.New, self).activated.connect(self.new_presentation)
        QShortcut(QKeySequence.StandardKey.Open, self).activated.connect(self.open_presentation_prompt)
        QShortcut(QKeySequence.StandardKey.Save, self).activated.connect(self.save_presentation_action)
        QShortcut(QKeySequence("Ctrl+Shift+S"), self).activated.connect(self.export_presentation_as)
        QShortcut(QKeySequence("Ctrl+M"), self).activated.connect(lambda: self.add_slide("title_body"))
        QShortcut(QKeySequence("Ctrl+D"), self).activated.connect(self.duplicate_active_slide)
        QShortcut(QKeySequence(Qt.Key.Key_F5), self).activated.connect(self.start_slideshow)

    # ------------------------------------------------------------------
    # Ribbon & Slide Synchronization
    # ------------------------------------------------------------------

    def _rebuild_slide_navigator(self) -> None:
        self._slide_list.blockSignals(True)
        try:
            self._slide_list.clear()
            palette = self.deck.palette
            for idx, slide in enumerate(self.deck.slides):
                thumb = render_slide_thumbnail(slide, palette, width=168, height=95)
                item = QListWidgetItem(f"{idx + 1}")
                item.setIcon(QIcon(thumb))
                item.setToolTip(slide.title or f"Slide {idx + 1}")
                self._slide_list.addItem(item)
            self._slide_list.setCurrentRow(self.deck.active_index)
        finally:
            self._slide_list.blockSignals(False)

        self._sync_navigator_buttons()

    def _refresh_active_thumbnail(self) -> None:
        idx = self.deck.active_index
        item = self._slide_list.item(idx)
        if item is not None:
            thumb = render_slide_thumbnail(self.deck.active_slide, self.deck.palette, width=168, height=95)
            item.setIcon(QIcon(thumb))
            item.setToolTip(self.deck.active_slide.title or f"Slide {idx + 1}")

    def _sync_navigator_buttons(self) -> None:
        idx = self.deck.active_index
        total = len(self.deck.slides)
        self._move_up_btn.setEnabled(idx > 0)
        self._move_down_btn.setEnabled(idx + 1 < total)
        self._del_slide_btn.setEnabled(total > 1)
        self._slide_counter_lbl.setText(f"Slide {idx + 1} of {total}")

    def _sync_ribbon_from_active_slide(self) -> None:
        self._is_syncing_ribbon = True
        try:
            slide = self.deck.active_slide
            layout_idx = self._layout_combo.findData(slide.layout)
            self._layout_combo.setCurrentIndex(layout_idx if layout_idx >= 0 else 1)

            theme_idx = self._deck_theme_combo.findData(self.deck.theme_id)
            self._deck_theme_combo.setCurrentIndex(theme_idx if theme_idx >= 0 else 0)

            font_idx = self._font_combo.findData(slide.font_family)
            self._font_combo.setCurrentIndex(font_idx if font_idx >= 0 else 0)

            ts_idx = self._title_size_combo.findData(slide.title_size)
            self._title_size_combo.setCurrentIndex(ts_idx if ts_idx >= 0 else 2)

            bs_idx = self._body_size_combo.findData(slide.body_size)
            self._body_size_combo.setCurrentIndex(bs_idx if bs_idx >= 0 else 2)

            self._bold_btn.setChecked(slide.bold)
            self._italic_btn.setChecked(slide.italic)
            self._underline_btn.setChecked(slide.underline)

            self._notes_edit.setPlainText(slide.speaker_notes)
        finally:
            self._is_syncing_ribbon = False

    def _toggle_notes_panel(self) -> None:
        self._notes_visible = not self._notes_visible
        self._notes_toggle_btn.setChecked(self._notes_visible)
        self.settings.set("notes_visible", self._notes_visible)
        self._sync_notes_visibility()

    def _sync_notes_visibility(self) -> None:
        self._notes_card.setVisible(self._notes_visible)

    def _on_slide_selected(self, row: int) -> None:
        if row < 0 or row >= len(self.deck.slides):
            return
        self.deck.set_active_index(row)
        self._sync_ribbon_from_active_slide()
        self._refresh_stage()
        self._sync_navigator_buttons()

    def _on_layout_changed(self, idx: int) -> None:
        if self._is_syncing_ribbon:
            return
        layout_id = str(self._layout_combo.itemData(idx) or "title_body")
        self.deck.active_slide.layout = layout_id
        self._refresh_stage()
        self._refresh_active_thumbnail()
        self._mark_dirty()

    def _on_deck_theme_changed(self, idx: int) -> None:
        if self._is_syncing_ribbon:
            return
        theme_id = str(self._deck_theme_combo.itemData(idx) or "lavender_snow")
        self.deck.theme_id = get_slide_theme(theme_id).theme_id
        self._refresh_stage()
        self._rebuild_slide_navigator()
        self._mark_dirty()

    def _on_font_changed(self, idx: int) -> None:
        if self._is_syncing_ribbon:
            return
        fam = str(self._font_combo.itemData(idx) or "Inter")
        self.deck.active_slide.font_family = fam
        self._refresh_stage()
        self._refresh_active_thumbnail()
        self._mark_dirty()

    def _on_title_size_changed(self, idx: int) -> None:
        if self._is_syncing_ribbon:
            return
        val = self._title_size_combo.itemData(idx)
        if isinstance(val, int):
            self.deck.active_slide.title_size = val
            self._refresh_stage()
            self._refresh_active_thumbnail()
            self._mark_dirty()

    def _on_body_size_changed(self, idx: int) -> None:
        if self._is_syncing_ribbon:
            return
        val = self._body_size_combo.itemData(idx)
        if isinstance(val, int):
            self.deck.active_slide.body_size = val
            self._refresh_stage()
            self._refresh_active_thumbnail()
            self._mark_dirty()

    def _on_style_toggled(self) -> None:
        if self._is_syncing_ribbon:
            return
        slide = self.deck.active_slide
        slide.bold = self._bold_btn.isChecked()
        slide.italic = self._italic_btn.isChecked()
        slide.underline = self._underline_btn.isChecked()
        self._refresh_stage()
        self._refresh_active_thumbnail()
        self._mark_dirty()

    def _set_slide_alignment(self, align_code: str) -> None:
        self.deck.active_slide.text_align = align_code
        self._refresh_stage()
        self._refresh_active_thumbnail()
        self._mark_dirty()

    def _choose_slide_bg_color(self) -> None:
        chosen = QColorDialog.getColor(
            QColor(self.deck.palette.bg_hex),
            self,
            "Choose Slide Background Color",
        )
        if chosen.isValid():
            self.deck.active_slide.custom_bg_hex = chosen.name().upper()
            self._refresh_stage()
            self._refresh_active_thumbnail()
            self._mark_dirty()

    def _choose_slide_text_color(self) -> None:
        chosen = QColorDialog.getColor(
            QColor(self.deck.palette.title_hex),
            self,
            "Choose Slide Text Color",
        )
        if chosen.isValid():
            self.deck.active_slide.custom_text_hex = chosen.name().upper()
            self._refresh_stage()
            self._refresh_active_thumbnail()
            self._mark_dirty()

    def _choose_slide_accent_color(self) -> None:
        chosen = QColorDialog.getColor(
            QColor(self.deck.palette.accent_hex),
            self,
            "Choose Slide Accent Color",
        )
        if chosen.isValid():
            self.deck.active_slide.custom_accent_hex = chosen.name().upper()
            self._refresh_stage()
            self._refresh_active_thumbnail()
            self._mark_dirty()

    def _reset_slide_custom_colors(self) -> None:
        slide = self.deck.active_slide
        slide.custom_bg_hex = ""
        slide.custom_text_hex = ""
        slide.custom_accent_hex = ""
        self._refresh_stage()
        self._refresh_active_thumbnail()
        self._mark_dirty()

    def _on_on_slide_text_edited(self) -> None:
        self._refresh_active_thumbnail()
        self._mark_dirty()

    def _on_notes_edited(self) -> None:
        if self._is_syncing_ribbon:
            return
        self.deck.active_slide.speaker_notes = self._notes_edit.toPlainText()
        self._mark_dirty()

    def _refresh_stage(self) -> None:
        self._canvas.set_slide_state(
            slide=self.deck.active_slide,
            palette=self.deck.palette,
            slide_index=self.deck.active_index,
            total_slides=len(self.deck.slides),
        )

    # ------------------------------------------------------------------
    # Slide CRUD & Reordering
    # ------------------------------------------------------------------

    def add_slide(self, layout: str = "title_body") -> None:
        self.deck.add_slide(layout=layout, insert_after_active=True)
        self._rebuild_slide_navigator()
        self._sync_ribbon_from_active_slide()
        self._refresh_stage()
        self._mark_dirty()
        self.set_status_message("Added slide", timeout_ms=1600)

    def duplicate_active_slide(self) -> None:
        self.deck.duplicate_slide()
        self._rebuild_slide_navigator()
        self._sync_ribbon_from_active_slide()
        self._refresh_stage()
        self._mark_dirty()
        self.set_status_message("Duplicated slide", timeout_ms=1600)

    def delete_active_slide(self) -> None:
        if self.deck.delete_slide():
            self._rebuild_slide_navigator()
            self._sync_ribbon_from_active_slide()
            self._refresh_stage()
            self._mark_dirty()
            self.set_status_message("Removed slide", timeout_ms=1600)

    def _move_slide_up(self) -> None:
        idx = self.deck.active_index
        if self.deck.move_slide(idx, idx - 1):
            self._rebuild_slide_navigator()
            self._refresh_stage()
            self._mark_dirty()

    def _move_slide_down(self) -> None:
        idx = self.deck.active_index
        if self.deck.move_slide(idx, idx + 1):
            self._rebuild_slide_navigator()
            self._refresh_stage()
            self._mark_dirty()

    # ------------------------------------------------------------------
    # Full-Screen Slideshow Presenter
    # ------------------------------------------------------------------

    def start_slideshow(self) -> None:
        presenter = SlideshowPresenterDialog(
            deck=self.deck,
            start_index=self.deck.active_index,
            parent=self,
        )
        presenter.showFullScreen()
        presenter.exec()

    # ------------------------------------------------------------------
    # Open, Save (.pptx default) & Export (.pptx / .pdf)
    # ------------------------------------------------------------------

    def _mark_dirty(self) -> None:
        if not self._is_dirty:
            self._is_dirty = True
            self._sync_dirty_badge()

    def _sync_dirty_badge(self) -> None:
        title_name = (
            self._current_file_path.name
            if self._current_file_path is not None
            else (self.deck.slides[0].title.strip() or "Untitled Presentation")
        )
        dirty_dot = " •" if self._is_dirty else ""
        self.setWindowTitle(f"{title_name}{dirty_dot} — {self.meta.display_name}")

        if self._is_dirty:
            self._status_badge.set_status("Unsaved changes", variant="butter")
        elif self._current_file_path is not None:
            self._status_badge.set_status(f"Saved {self._current_file_path.name}", variant="sage")
        else:
            self._status_badge.set_status("New Deck (.pptx)", variant="lavender")

    def _confirm_discard_if_dirty(self) -> bool:
        if not self._is_dirty:
            return True
        return ask_confirmation(
            parent=self,
            title="Discard Unsaved Presentation?",
            message="You have unsaved changes in the current deck. Continue without saving?",
            confirm_text="Discard Changes",
            destructive=True,
        )

    def new_presentation(self) -> None:
        if not self._confirm_discard_if_dirty():
            return
        self.deck = PresentationModel()
        self._current_file_path = None
        self._is_dirty = False
        self._rebuild_slide_navigator()
        self._sync_ribbon_from_active_slide()
        self._refresh_stage()
        self._sync_dirty_badge()
        self.set_status_message("Started new .pptx deck", timeout_ms=1800)

    def open_presentation_prompt(self) -> None:
        if not self._confirm_discard_if_dirty():
            return
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Open Presentation",
            str(Path.home() / "Documents"),
            "PowerPoint Presentation (*.pptx)",
        )
        if not selected:
            return
        self.load_file(selected)

    def load_file(self, file_path: str | Path) -> bool:
        try:
            loaded_deck = load_presentation(file_path)
        except FileOperationError as exc:
            show_error_dialog(self, "Could Not Open Presentation", str(exc))
            return False

        self.deck = loaded_deck
        self._current_file_path = Path(file_path).resolve()
        self._is_dirty = False
        self._rebuild_slide_navigator()
        self._sync_ribbon_from_active_slide()
        self._refresh_stage()
        self._sync_dirty_badge()
        self.set_status_message(f"Opened {self._current_file_path.name}", timeout_ms=2200)
        return True

    def save_presentation_action(self) -> bool:
        if self._current_file_path is None:
            return self.export_presentation_as(set_as_active=True)
        return self._write_deck_to(self._current_file_path, set_as_active=True)

    def export_presentation_as(self, set_as_active: bool = False) -> bool:
        raw_stem = (
            self._current_file_path.stem
            if self._current_file_path is not None
            else (self.deck.slides[0].title.strip() or "Untitled_Presentation")
        )
        stem = sanitize_filename(raw_stem)
        default_dir = (
            self._current_file_path.parent
            if self._current_file_path is not None
            else (Path.home() / "Documents")
        )
        default_target = str(default_dir / f"{stem}{DEFAULT_PRESENTATION_EXTENSION}")

        selected, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Save or Export Presentation",
            default_target,
            "PowerPoint Presentation (*.pptx);;PDF Widescreen Handout (*.pdf)",
        )
        if not selected:
            return False

        target = Path(selected)
        if not target.suffix:
            if "PDF" in selected_filter:
                target = target.with_suffix(".pdf")
            else:
                target = target.with_suffix(DEFAULT_PRESENTATION_EXTENSION)

        if target.suffix.lower() == ".pdf":
            return self._export_to_pdf(target)

        should_bind = set_as_active or target.suffix.lower() == ".pptx"
        return self._write_deck_to(target, set_as_active=should_bind)

    def _write_deck_to(self, target: Path, set_as_active: bool) -> bool:
        try:
            saved_path = save_presentation(target, self.deck)
        except FileOperationError as exc:
            show_error_dialog(self, "Save Failed", str(exc))
            return False

        if set_as_active:
            self._current_file_path = saved_path
            self._is_dirty = False
            self._sync_dirty_badge()

        self.notify(
            "Presentation Saved",
            f"Saved '{saved_path.name}' cleanly.",
            level=NotificationLevel.SUCCESS,
        )
        return True

    def _export_to_pdf(self, target: Path) -> bool:
        """Render every slide into a 16:9 widescreen PDF document via QPdfWriter."""
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            writer = QPdfWriter(str(target))
            writer.setPageSize(QPageSize(QSizeF(13.333, 7.5), QPageSize.Unit.Inch))
            writer.setPageMargins(QMarginsF(0.0, 0.0, 0.0, 0.0), QPageLayout.Unit.Millimeter)
            writer.setTitle(self.deck.slides[0].title or "SimpleSlides Presentation")
            writer.setCreator("SimpleSlides — Jaiho Super Simple Office")

            painter = QPainter(writer)
            try:
                total = len(self.deck.slides)
                palette = self.deck.palette
                page_rect = QRectF(0.0, 0.0, float(writer.width()), float(writer.height()))
                for idx, slide in enumerate(self.deck.slides):
                    if idx > 0:
                        writer.newPage()
                    render_slide_to_painter(
                        painter=painter,
                        target_rect=page_rect,
                        slide=slide,
                        palette=palette,
                        slide_number=idx + 1,
                        total_slides=total,
                        is_thumbnail=False,
                        draw_text=True,
                    )
            finally:
                painter.end()
        except Exception as exc:
            show_error_dialog(self, "PDF Export Failed", str(exc))
            return False

        self.notify(
            "PDF Exported",
            f"Exported '{target.name}' successfully.",
            level=NotificationLevel.SUCCESS,
        )
        return True

    def can_close(self) -> bool:
        return self._confirm_discard_if_dirty()


def main() -> int:
    """Application entry point for SimpleSlides.exe."""
    return bootstrap_application(
        app_id=APP_ID,
        window_factory=SimpleSlidesWindow,
    )


if __name__ == "__main__":
    raise SystemExit(main())