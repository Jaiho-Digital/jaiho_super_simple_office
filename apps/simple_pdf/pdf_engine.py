"""Offline PDF rendering, interactive annotations, page organizer, and search engine for SimplePDF."""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from PySide6.QtCore import QBuffer, QIODevice, QMarginsF, QPointF, QRectF, QSize, QSizeF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QPageLayout,
    QPageSize,
    QPainter,
    QPainterPath,
    QPdfWriter,
    QPen,
    QPixmap,
    QTransform,
)

from core.file_manager.file_ops import (
    FileOperationError,
    atomic_write_bytes,
    validate_input_file,
)

try:
    from PySide6.QtPdf import QPdfDocument
except ImportError:  # pragma: no cover
    QPdfDocument = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

APP_ID = "simple_pdf"
SUPPORTED_PDF_EXTENSIONS: frozenset[str] = frozenset({".pdf"})

AnnotationKind = Literal[
    "highlight",
    "underline",
    "note",
    "text",
    "rect",
    "arrow",
    "stamp",
    "ink",
]

STAMP_PRESETS: dict[str, tuple[str, str, str]] = {
    "APPROVED": ("#DDEFE8", "#2E6F56", "#9FD4BE"),
    "REVIEWED": ("#E8E0F7", "#5943B5", "#C3B2EE"),
    "CONFIDENTIAL": ("#F7DFE7", "#A83B56", "#E8AFC2"),
    "DRAFT": ("#F8EBCF", "#8A6218", "#E6CE96"),
    "FINAL": ("#DFEAF8", "#2F5E96", "#ACC9EB"),
}


def _new_id() -> str:
    return uuid.uuid4().hex[:10]


@dataclass(slots=True)
class PdfAnnotation:
    """Normalized (0.0–1.0 page-relative) draggable & resizable annotation overlay."""

    annotation_id: str = field(default_factory=_new_id)
    kind: AnnotationKind = "highlight"
    # Normalized bounding box in [0.0, 1.0] relative to page width & height
    x: float = 0.10
    y: float = 0.12
    w: float = 0.28
    h: float = 0.045
    color_hex: str = "#E8E0F7"
    text: str = ""
    # Normalized points for 'ink' strokes (relative to [0.0, 1.0] page space)
    points: list[tuple[float, float]] = field(default_factory=list)

    def bounds(self) -> QRectF:
        """Return normalized [0.0, 1.0] bounding box."""
        return QRectF(self.x, self.y, max(0.015, self.w), max(0.012, self.h))

    def contains_norm_point(self, nx: float, ny: float, pad: float = 0.012) -> bool:
        """Return True if normalized point (nx, ny) hits this annotation."""
        r = self.bounds().adjusted(-pad, -pad, pad, pad)
        return r.contains(QPointF(nx, ny))

    def hits_bottom_right_handle(self, nx: float, ny: float, radius: float = 0.022) -> bool:
        """Return True if normalized point (nx, ny) hits the bottom-right resize grip."""
        if self.kind == "note":
            return False
        br_x = self.x + max(0.015, self.w)
        br_y = self.y + max(0.012, self.h)
        return abs(nx - br_x) <= radius and abs(ny - br_y) <= radius

    def move_by(self, dx: float, dy: float) -> None:
        """Translate annotation within page bounds [0.0, 1.0], scaling ink points if present."""
        eff_w = max(0.015, self.w)
        eff_h = max(0.012, self.h)
        new_x = max(0.0, min(1.0 - eff_w, self.x + dx))
        new_y = max(0.0, min(1.0 - eff_h, self.y + dy))
        actual_dx = new_x - self.x
        actual_dy = new_y - self.y
        self.x = new_x
        self.y = new_y

        if self.points and (abs(actual_dx) > 1e-7 or abs(actual_dy) > 1e-7):
            self.points = [
                (
                    max(0.0, min(1.0, px + actual_dx)),
                    max(0.0, min(1.0, py + actual_dy)),
                )
                for px, py in self.points
            ]

    def resize_to(self, new_w: float, new_h: float) -> None:
        """Resize annotation bounding box and proportionally remap ink points if present."""
        old_w = max(0.015, self.w)
        old_h = max(0.012, self.h)
        clamped_w = max(0.02, min(1.0 - self.x, new_w))
        clamped_h = max(0.015, min(1.0 - self.y, new_h))

        if self.points:
            sx = clamped_w / old_w
            sy = clamped_h / old_h
            self.points = [
                (
                    self.x + (px - self.x) * sx,
                    self.y + (py - self.y) * sy,
                )
                for px, py in self.points
            ]

        self.w = clamped_w
        self.h = clamped_h

    def summary_label(self) -> str:
        """Return a concise human-readable label for the Annotations inspector list."""
        kind_titles: dict[str, str] = {
            "highlight": "Highlight",
            "underline": "Underline",
            "note": "Sticky Note",
            "text": "Text Box",
            "rect": "Callout Box",
            "arrow": "Arrow",
            "stamp": "Stamp",
            "ink": "Ink / Signature",
        }
        prefix = kind_titles.get(self.kind, self.kind.title())
        detail = self.text.strip()
        if detail:
            short = detail[:28] + ("…" if len(detail) > 28 else "")
            return f"{prefix}: {short}"
        return prefix


@dataclass(slots=True)
class PageSlot:
    """Represents one logical page in the active workspace (supports multi-PDF merge & reorder)."""

    slot_id: str = field(default_factory=_new_id)
    doc_index: int = 0
    source_page_index: int = 0
    rotation: int = 0  # 0, 90, 180, 270
    annotations: list[PdfAnnotation] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SearchHit:
    """Single keyword match discovered inside the active document."""

    display_page_index: int
    snippet: str


def paint_annotations_on_page(
    painter: QPainter,
    page_rect: QRectF,
    annotations: list[PdfAnnotation],
    selected_id: str | None = None,
) -> None:
    """Render all annotations and selection/resize handles onto `page_rect`."""
    if not annotations:
        return

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

    pw = page_rect.width()
    ph = page_rect.height()
    ox = page_rect.left()
    oy = page_rect.top()
    scale = max(0.35, pw / 612.0)

    for ann in annotations:
        is_selected = selected_id is not None and ann.annotation_id == selected_id
        box = QRectF(
            ox + ann.x * pw,
            oy + ann.y * ph,
            max(8.0, ann.w * pw),
            max(6.0, ann.h * ph),
        )

        if ann.kind == "highlight":
            fill = QColor(ann.color_hex or "#E8E0F7")
            fill.setAlpha(135)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(box, 4.0 * scale, 4.0 * scale)

        elif ann.kind == "underline":
            line_col = QColor(ann.color_hex or "#6F59C9")
            pen = QPen(line_col, max(1.8, 2.5 * scale))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            y_line = box.bottom() - 2.0 * scale
            painter.drawLine(QPointF(box.left(), y_line), QPointF(box.right(), y_line))

        elif ann.kind == "note":
            badge_sz = max(20.0, 26.0 * scale)
            badge = QRectF(box.left(), box.top(), badge_sz, badge_sz)
            bg = QColor(ann.color_hex or "#F8EBCF")
            painter.setPen(QPen(QColor("#8A6218"), max(1.2, 1.4 * scale)))
            painter.setBrush(bg)
            painter.drawRoundedRect(badge, 7.0 * scale, 7.0 * scale)

            # Horizontal lines icon inside sticky badge
            painter.setPen(QPen(QColor("#35343B"), max(1.1, 1.3 * scale)))
            pad = badge_sz * 0.27
            for idx_line in range(3):
                ly = badge.top() + pad + idx_line * (badge_sz - 2.0 * pad) / 2.0
                rx = badge.right() - (pad * 1.45 if idx_line == 2 else pad)
                painter.drawLine(QPointF(badge.left() + pad, ly), QPointF(rx, ly))

            if ann.text.strip():
                callout_w = max(84.0 * scale, box.width() - badge_sz - 6.0 * scale)
                callout_h = max(badge_sz, box.height())
                callout = QRectF(badge.right() + 6.0 * scale, box.top(), callout_w, callout_h)
                card_bg = QColor(ann.color_hex or "#F8EBCF")
                card_bg.setAlpha(240)
                painter.setPen(QPen(QColor("#D8C9A8"), max(1.0, 1.0 * scale)))
                painter.setBrush(card_bg)
                painter.drawRoundedRect(callout, 8.0 * scale, 8.0 * scale)

                f = QFont("Inter")
                f.setPointSizeF(max(6.5, 9.2 * scale))
                painter.setFont(f)
                painter.setPen(QColor("#35343B"))
                painter.drawText(
                    callout.adjusted(8.0 * scale, 5.0 * scale, -8.0 * scale, -5.0 * scale),
                    Qt.AlignmentFlag.AlignLeft
                    | Qt.AlignmentFlag.AlignVCenter
                    | Qt.TextFlag.TextWordWrap,
                    ann.text.strip(),
                )

        elif ann.kind == "text":
            f = QFont("Inter")
            f.setPointSizeF(max(7.5, 12.0 * scale))
            f.setBold(True)
            painter.setFont(f)
            painter.setPen(QColor(ann.color_hex or "#35343B"))
            painter.drawText(
                box.adjusted(4.0 * scale, 2.0 * scale, -4.0 * scale, -2.0 * scale),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                ann.text.strip() or "Text Callout",
            )

        elif ann.kind == "rect":
            stroke_col = QColor(ann.color_hex or "#6F59C9")
            fill_col = QColor(ann.color_hex or "#E8E0F7")
            fill_col.setAlpha(45)
            painter.setPen(QPen(stroke_col, max(1.5, 2.0 * scale)))
            painter.setBrush(fill_col)
            painter.drawRoundedRect(box, 8.0 * scale, 8.0 * scale)

        elif ann.kind == "arrow":
            stroke_col = QColor(ann.color_hex or "#6F59C9")
            pen = QPen(stroke_col, max(1.8, 2.4 * scale))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            p_start = QPointF(box.left(), box.bottom())
            p_end = QPointF(box.right(), box.top())
            painter.drawLine(p_start, p_end)

            # Arrowhead triangle at p_end
            dx = p_end.x() - p_start.x()
            dy = p_end.y() - p_start.y()
            length = max(1.0, (dx * dx + dy * dy) ** 0.5)
            ux, uy = dx / length, dy / length
            head_len = max(8.0, 12.0 * scale)
            perp_x, perp_y = -uy, ux
            p1 = QPointF(
                p_end.x() - ux * head_len + perp_x * (head_len * 0.45),
                p_end.y() - uy * head_len + perp_y * (head_len * 0.45),
            )
            p2 = QPointF(
                p_end.x() - ux * head_len - perp_x * (head_len * 0.45),
                p_end.y() - uy * head_len - perp_y * (head_len * 0.45),
            )
            painter.setBrush(stroke_col)
            painter.drawPolygon([p_end, p1, p2])

        elif ann.kind == "stamp":
            label = (ann.text.strip() or "APPROVED").upper()
            bg_h, fg_h, bd_h = STAMP_PRESETS.get(
                label,
                (ann.color_hex or "#DDEFE8", "#2E6F56", "#9FD4BE"),
            )
            painter.setPen(QPen(QColor(bd_h), max(1.5, 2.0 * scale)))
            painter.setBrush(QColor(bg_h))
            painter.drawRoundedRect(box, 8.0 * scale, 8.0 * scale)

            st_font = QFont("Inter")
            st_font.setPointSizeF(max(7.0, 11.5 * scale))
            st_font.setBold(True)
            st_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.2 * scale)
            painter.setFont(st_font)
            painter.setPen(QColor(fg_h))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, label)

        elif ann.kind == "ink" and len(ann.points) >= 2:
            path = QPainterPath()
            first_x, first_y = ann.points[0]
            path.moveTo(ox + first_x * pw, oy + first_y * ph)
            for px_n, py_n in ann.points[1:]:
                path.lineTo(ox + px_n * pw, oy + py_n * ph)

            stroke_col = QColor(ann.color_hex or "#6F59C9")
            stroke_col.setAlpha(225)
            pen = QPen(stroke_col, max(1.8, 2.6 * scale))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)

        # Draw interactive selection frame & bottom-right resize grip when selected
        if is_selected:
            sel_rect = box.adjusted(-2.0, -2.0, 2.0, 2.0)
            painter.setPen(QPen(QColor("#6F59C9"), 1.4, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(sel_rect, 4.0, 4.0)

            if ann.kind != "note":
                grip_sz = 8.0
                grip = QRectF(
                    sel_rect.right() - grip_sz / 2.0,
                    sel_rect.bottom() - grip_sz / 2.0,
                    grip_sz,
                    grip_sz,
                )
                painter.setPen(QPen(QColor("#FFFEFC"), 1.2))
                painter.setBrush(QColor("#6F59C9"))
                painter.drawEllipse(grip)

    painter.restore()


def build_sample_pdf_bytes(pages: list[tuple[str, list[str]]] | None = None) -> bytes:
    """Create a multi-page Soft Modern Pastel executive showcase PDF using QPdfWriter."""
    if not pages:
        pages = [
            (
                "Jaiho Super Simple Office — Architecture & Design Brief",
                [
                    "100% Offline Local-First Desktop Productivity Suite built in Bengaluru.",
                    "Unified Soft Modern Pastel aesthetic with zero cloud lock-in or telemetry.",
                    "Try dragging annotations directly on the page, or drag page thumbnails to reorder.",
                    "Use the top ribbon to highlight text, add sticky notes, stamps, shapes, or ink.",
                ],
            ),
            (
                "Phase 4 — PDF & Document Studio Capabilities",
                [
                    "Continuous Multi-Page Reader with smooth Ctrl+Wheel zoom and Hand Pan drag.",
                    "Interactive Annotation Canvas: click, drag-move, and resize any markup element.",
                    "Full-Text Keyword Search across every page with instant jump snippets.",
                    "Drag-and-drop PDF Merger, Page Range Splitter, Rotation, and Text Extraction.",
                ],
            ),
            (
                "Keyboard Shortcuts & Studio Workflow",
                [
                    "Ctrl + O : Open any PDF document (or drag-and-drop onto the window).",
                    "Ctrl + F : Search text across all pages in the left Search inspector.",
                    "Ctrl + R : Rotate the active page 90 degrees clockwise.",
                    "Delete   : Remove the currently selected annotation from the page.",
                ],
            ),
        ]

    qbuf = QBuffer()
    qbuf.open(QIODevice.OpenModeFlag.WriteOnly)

    writer = QPdfWriter(qbuf)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.Letter))
    writer.setResolution(96)
    writer.setPageMargins(QMarginsF(14.0, 14.0, 14.0, 14.0), QPageLayout.Unit.Millimeter)
    writer.setTitle(pages[0][0] or "SimplePDF Document")
    writer.setCreator("SimplePDF — Jaiho Super Simple Office")

    pastel_headers = ("#E8E0F7", "#DDEFE8", "#DFEAF8")
    pastel_accents = ("#6F59C9", "#477A63", "#3E6FA8")

    painter = QPainter(writer)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = float(writer.width())
        h = float(writer.height())
        for idx, (heading, lines) in enumerate(pages):
            if idx > 0:
                writer.newPage()

            painter.fillRect(QRectF(0.0, 0.0, w, h), QColor("#FFFEFC"))

            # Pastel Hero Header Card at top of each page
            banner_bg = pastel_headers[idx % len(pastel_headers)]
            accent_col = pastel_accents[idx % len(pastel_accents)]
            banner_rect = QRectF(18.0, 18.0, w - 36.0, 108.0)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(banner_bg))
            painter.drawRoundedRect(banner_rect, 14.0, 14.0)

            # Accent pill badge
            painter.setBrush(QColor(accent_col))
            painter.drawRoundedRect(QRectF(36.0, 34.0, 48.0, 5.0), 2.5, 2.5)

            title_font = QFont("Inter", 17, QFont.Weight.Bold)
            painter.setFont(title_font)
            painter.setPen(QColor("#35343B"))
            painter.drawText(
                QRectF(36.0, 46.0, w - 72.0, 66.0),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap,
                heading,
            )

            # Structured Content Cards
            y_pos = 148.0
            body_font = QFont("Inter", 11)
            painter.setFont(body_font)
            for line in lines:
                card_r = QRectF(18.0, y_pos, w - 36.0, 54.0)
                painter.setPen(QPen(QColor("#E9E7E3"), 1.0))
                painter.setBrush(QColor("#F8F7F4"))
                painter.drawRoundedRect(card_r, 10.0, 10.0)

                painter.setPen(QColor("#35343B"))
                painter.drawText(
                    card_r.adjusted(18.0, 6.0, -18.0, -6.0),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap,
                    line,
                )
                y_pos += 68.0

            # Footer rule & page counter
            painter.setPen(QPen(QColor("#E9E7E3"), 1.0))
            painter.drawLine(18, int(h - 42), int(w - 18), int(h - 42))

            footer_font = QFont("Inter", 9)
            painter.setFont(footer_font)
            painter.setPen(QColor("#77747E"))
            painter.drawText(
                QRectF(18.0, h - 36.0, w - 36.0, 24.0),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                "Jaiho Super Simple Office  •  SimplePDF",
            )
            painter.drawText(
                QRectF(18.0, h - 36.0, w - 36.0, 24.0),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"Page {idx + 1} of {len(pages)}",
            )
    finally:
        painter.end()

    qbuf.close()
    return bytes(qbuf.data())


class PdfDocumentService:
    """Manages one or more loaded PDFs, page slot ordering, annotations, search, and export."""

    def __init__(self) -> None:
        self._docs: list[QPdfDocument] = []
        self._doc_paths: list[Path] = []
        self._slots: list[PageSlot] = []
        self._text_cache: dict[tuple[int, int], str] = {}

    @property
    def primary_path(self) -> Path | None:
        return self._doc_paths[0] if self._doc_paths else None

    @property
    def is_loaded(self) -> bool:
        return bool(self._slots)

    @property
    def page_count(self) -> int:
        return len(self._slots)

    @property
    def slots(self) -> list[PageSlot]:
        return self._slots

    def close(self) -> None:
        for doc in self._docs:
            doc.close()
        self._docs.clear()
        self._doc_paths.clear()
        self._slots.clear()
        self._text_cache.clear()

    def _open_qpdf_instance(self, source_path: str | Path) -> tuple[Path, QPdfDocument]:
        validated = validate_input_file(
            source_path,
            allowed_extensions=SUPPORTED_PDF_EXTENSIONS,
            max_size_mb=150.0,
        )
        if QPdfDocument is None:
            raise FileOperationError("QtPdf module is not available in this Python environment.")

        doc = QPdfDocument(None)
        err = doc.load(str(validated))
        if err != QPdfDocument.Error.None_:
            doc.close()
            raise FileOperationError(f"Could not open '{validated.name}' (PDF error: {err.name}).")

        if int(doc.pageCount()) <= 0:
            doc.close()
            raise FileOperationError(f"'{validated.name}' contains zero readable pages.")

        return validated, doc

    def load_file(self, source_path: str | Path) -> int:
        """Close any open document and load `source_path` as the active PDF."""
        validated, doc = self._open_qpdf_instance(source_path)
        self.close()
        self._docs.append(doc)
        self._doc_paths.append(validated)
        for p_idx in range(int(doc.pageCount())):
            self._slots.append(PageSlot(doc_index=0, source_page_index=p_idx))
        return len(self._slots)

    def append_file(self, source_path: str | Path) -> int:
        """Append all pages from another PDF onto the end of the active workspace."""
        if not self.is_loaded:
            return self.load_file(source_path)
        validated, doc = self._open_qpdf_instance(source_path)
        doc_idx = len(self._docs)
        self._docs.append(doc)
        self._doc_paths.append(validated)
        added = int(doc.pageCount())
        for p_idx in range(added):
            self._slots.append(PageSlot(doc_index=doc_idx, source_page_index=p_idx))
        return added

    def get_slot(self, display_index: int) -> PageSlot:
        if not (0 <= display_index < len(self._slots)):
            raise IndexError(f"Page index {display_index} out of range.")
        return self._slots[display_index]

    def get_page_rotation(self, display_index: int) -> int:
        if not (0 <= display_index < len(self._slots)):
            return 0
        return self._slots[display_index].rotation % 360

    def rotate_page(self, display_index: int, delta_degrees: int = 90) -> int:
        if not (0 <= display_index < len(self._slots)):
            return 0
        slot = self._slots[display_index]
        slot.rotation = (slot.rotation + delta_degrees) % 360
        return slot.rotation

    def move_page(self, from_index: int, to_index: int) -> bool:
        """Drag-and-drop reorder a page from `from_index` to `to_index`."""
        if not (0 <= from_index < len(self._slots) and 0 <= to_index < len(self._slots)):
            return False
        if from_index == to_index:
            return False
        slot = self._slots.pop(from_index)
        self._slots.insert(to_index, slot)
        return True

    def duplicate_page(self, display_index: int) -> bool:
        """Duplicate the page at `display_index` immediately after itself."""
        if not (0 <= display_index < len(self._slots)):
            return False
        src = self._slots[display_index]
        copied_anns = [
            PdfAnnotation(
                kind=a.kind,
                x=a.x,
                y=a.y,
                w=a.w,
                h=a.h,
                color_hex=a.color_hex,
                text=a.text,
                points=list(a.points),
            )
            for a in src.annotations
        ]
        clone = PageSlot(
            doc_index=src.doc_index,
            source_page_index=src.source_page_index,
            rotation=src.rotation,
            annotations=copied_anns,
        )
        self._slots.insert(display_index + 1, clone)
        return True

    def delete_page(self, display_index: int) -> bool:
        """Remove `display_index` from the active page list (keeps at least 1 page)."""
        if len(self._slots) <= 1 or not (0 <= display_index < len(self._slots)):
            return False
        self._slots.pop(display_index)
        return True

    def get_page_size_points(self, display_index: int) -> QSizeF:
        """Return effective (width, height) in PDF points accounting for rotation."""
        if not (0 <= display_index < len(self._slots)):
            return QSizeF(612.0, 792.0)
        slot = self._slots[display_index]
        doc = self._docs[slot.doc_index]
        pt = doc.pagePointSize(slot.source_page_index)
        w = max(72.0, float(pt.width()))
        h = max(72.0, float(pt.height()))
        if slot.rotation in {90, 270}:
            return QSizeF(h, w)
        return QSizeF(w, h)

    def render_page_image(
        self,
        display_index: int,
        target_width_px: int,
        include_annotations: bool = True,
        selected_annotation_id: str | None = None,
    ) -> QImage:
        """Render `display_index` into a crisp ARGB32 QImage on a Snow paper background."""
        if not (0 <= display_index < len(self._slots)):
            blank = QImage(max(100, target_width_px), 140, QImage.Format.Format_ARGB32)
            blank.fill(QColor("#FFFEFC"))
            return blank

        slot = self._slots[display_index]
        doc = self._docs[slot.doc_index]
        raw_pt = doc.pagePointSize(slot.source_page_index)
        raw_w = max(72.0, float(raw_pt.width()))
        raw_h = max(72.0, float(raw_pt.height()))

        if slot.rotation in {90, 270}:
            render_h = max(64, int(round(target_width_px)))
            render_w = max(64, int(round(render_h * (raw_w / raw_h))))
        else:
            render_w = max(64, int(round(target_width_px)))
            render_h = max(64, int(round(render_w * (raw_h / raw_w))))

        base_img = doc.render(slot.source_page_index, QSize(render_w, render_h))
        if base_img.isNull():
            base_img = QImage(render_w, render_h, QImage.Format.Format_ARGB32)
            base_img.fill(QColor("#FFFEFC"))

        composed = QImage(base_img.size(), QImage.Format.Format_ARGB32_Premultiplied)
        composed.fill(QColor("#FFFEFC"))
        p = QPainter(composed)
        p.drawImage(0, 0, base_img)
        p.end()

        if slot.rotation != 0:
            transform = QTransform().rotate(float(slot.rotation))
            composed = composed.transformed(transform, Qt.TransformationMode.SmoothTransformation)

        if include_annotations and slot.annotations:
            ann_painter = QPainter(composed)
            try:
                paint_annotations_on_page(
                    painter=ann_painter,
                    page_rect=QRectF(0.0, 0.0, float(composed.width()), float(composed.height())),
                    annotations=slot.annotations,
                    selected_id=selected_annotation_id,
                )
            finally:
                ann_painter.end()

        return composed

    def render_thumbnail_pixmap(self, display_index: int, width_px: int = 132) -> QPixmap:
        """Render a 2x High-DPI miniature thumbnail for the page organizer."""
        dpr = 2
        img = self.render_page_image(
            display_index=display_index,
            target_width_px=width_px * dpr,
            include_annotations=True,
        )
        px = QPixmap.fromImage(img)
        px.setDevicePixelRatio(float(dpr))
        return px

    # ------------------------------------------------------------------
    # Text Extraction & Search
    # ------------------------------------------------------------------

    def get_page_text(self, display_index: int) -> str:
        if not (0 <= display_index < len(self._slots)):
            return ""
        slot = self._slots[display_index]
        cache_key = (slot.doc_index, slot.source_page_index)
        cached = self._text_cache.get(cache_key)
        if cached is not None:
            return cached
        doc = self._docs[slot.doc_index]
        selection = doc.getAllText(slot.source_page_index)
        extracted = selection.text() if selection is not None else ""
        self._text_cache[cache_key] = extracted
        return extracted

    def search_text(self, query: str, max_hits: int = 150) -> list[SearchHit]:
        needle = query.strip()
        if not needle or not self._slots:
            return []

        pattern = re.compile(re.escape(needle), re.IGNORECASE)
        hits: list[SearchHit] = []
        for disp_idx in range(len(self._slots)):
            page_txt = " ".join(self.get_page_text(disp_idx).split())
            if not page_txt:
                continue
            for match in pattern.finditer(page_txt):
                start = max(0, match.start() - 26)
                end = min(len(page_txt), match.end() + 34)
                prefix = "…" if start > 0 else ""
                suffix = "…" if end < len(page_txt) else ""
                snippet = f"{prefix}{page_txt[start:end]}{suffix}"
                hits.append(SearchHit(display_page_index=disp_idx, snippet=snippet))
                if len(hits) >= max_hits:
                    return hits
        return hits

    # ------------------------------------------------------------------
    # Annotation Management
    # ------------------------------------------------------------------

    def get_annotations(self, display_index: int) -> list[PdfAnnotation]:
        if not (0 <= display_index < len(self._slots)):
            return []
        return list(self._slots[display_index].annotations)

    def total_annotation_count(self) -> int:
        return sum(len(s.annotations) for s in self._slots)

    def add_annotation(self, display_index: int, annotation: PdfAnnotation) -> PdfAnnotation:
        slot = self.get_slot(display_index)
        slot.annotations.append(annotation)
        return annotation

    def remove_annotation(self, display_index: int, annotation_id: str) -> bool:
        if not (0 <= display_index < len(self._slots)):
            return False
        slot = self._slots[display_index]
        before = len(slot.annotations)
        slot.annotations = [a for a in slot.annotations if a.annotation_id != annotation_id]
        return len(slot.annotations) < before

    def clear_page_annotations(self, display_index: int) -> int:
        if not (0 <= display_index < len(self._slots)):
            return 0
        slot = self._slots[display_index]
        count = len(slot.annotations)
        slot.annotations.clear()
        return count

    # ------------------------------------------------------------------
    # Flattened PDF Export & Multi-File Merge
    # ------------------------------------------------------------------

    def export_pdf(
        self,
        destination: str | Path,
        display_indices: list[int] | None = None,
        include_annotations: bool = True,
    ) -> Path:
        """Render the specified (or all) workspace pages into a flattened .pdf file."""
        if not self.is_loaded:
            raise FileOperationError("No PDF document is currently open.")

        indices = (
            [i for i in display_indices if 0 <= i < len(self._slots)]
            if display_indices is not None
            else list(range(len(self._slots)))
        )
        if not indices:
            raise FileOperationError("At least one valid page must be selected for export.")

        target = Path(destination)
        if target.suffix.lower() != ".pdf":
            target = target.with_suffix(".pdf")

        qbuf = QBuffer()
        qbuf.open(QIODevice.OpenModeFlag.WriteOnly)

        writer = QPdfWriter(qbuf)
        writer.setResolution(150)
        writer.setPageMargins(QMarginsF(0.0, 0.0, 0.0, 0.0), QPageLayout.Unit.Point)
        writer.setCreator("SimplePDF — Jaiho Super Simple Office")

        first_pt = self.get_page_size_points(indices[0])
        writer.setPageSize(QPageSize(first_pt, QPageSize.Unit.Point))

        painter = QPainter(writer)
        try:
            for out_idx, disp_idx in enumerate(indices):
                pt_size = self.get_page_size_points(disp_idx)
                if out_idx > 0:
                    writer.setPageSize(QPageSize(pt_size, QPageSize.Unit.Point))
                    writer.newPage()

                target_px_w = max(600, int(round((pt_size.width() / 72.0) * 150.0)))
                page_img = self.render_page_image(
                    display_index=disp_idx,
                    target_width_px=target_px_w,
                    include_annotations=include_annotations,
                )
                dest_rect = QRectF(0.0, 0.0, float(writer.width()), float(writer.height()))
                painter.drawImage(dest_rect, page_img)
        finally:
            painter.end()

        qbuf.close()
        return atomic_write_bytes(target, bytes(qbuf.data()), backup_app_id=APP_ID)

    @staticmethod
    def merge_pdf_files(source_paths: list[str | Path], destination: str | Path) -> Path:
        """Merge two or more PDF files sequentially into a single output PDF."""
        if len(source_paths) < 2:
            raise FileOperationError("Select at least two PDF files to merge.")

        svc = PdfDocumentService()
        try:
            svc.load_file(source_paths[0])
            for extra_path in source_paths[1:]:
                svc.append_file(extra_path)
            return svc.export_pdf(destination, include_annotations=True)
        finally:
            svc.close()


def parse_page_range_spec(spec: str, total_pages: int) -> list[int]:
    """Parse a 1-based page specification like '1-3, 5' into 0-based page indices."""
    cleaned = spec.strip()
    if not cleaned:
        raise FileOperationError("Enter a page range such as '1-3' or '1, 4, 6'.")

    indices: list[int] = []
    seen: set[int] = set()
    for token in cleaned.split(","):
        part = token.strip()
        if not part:
            continue
        if "-" in part:
            bounds = part.split("-", 1)
            if not bounds[0].strip().isdigit() or not bounds[1].strip().isdigit():
                raise FileOperationError(f"Invalid page range '{part}'.")
            start_p = int(bounds[0].strip())
            end_p = int(bounds[1].strip())
            if start_p < 1 or end_p > total_pages or start_p > end_p:
                raise FileOperationError(
                    f"Page range '{part}' is outside valid bounds (1–{total_pages})."
                )
            for p_num in range(start_p, end_p + 1):
                zero_idx = p_num - 1
                if zero_idx not in seen:
                    seen.add(zero_idx)
                    indices.append(zero_idx)
        else:
            if not part.isdigit():
                raise FileOperationError(f"Invalid page number '{part}'.")
            p_num = int(part)
            if p_num < 1 or p_num > total_pages:
                raise FileOperationError(
                    f"Page {p_num} is outside valid bounds (1–{total_pages})."
                )
            zero_idx = p_num - 1
            if zero_idx not in seen:
                seen.add(zero_idx)
                indices.append(zero_idx)

    if not indices:
        raise FileOperationError("No valid page numbers were found in the range.")
    return indices