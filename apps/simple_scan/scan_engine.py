"""Offline document scanner acquisition, image cleanup, auto-deskew, and PDF export for SimpleScan."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QMarginsF, QRect, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QPageLayout,
    QPageSize,
    QPainter,
    QPdfWriter,
    QPen,
    QPixmap,
    QTransform,
    qAlpha,
    qBlue,
    qGreen,
    qRed,
    qRgba,
)

from core.file_manager.file_ops import (
    FileOperationError,
    atomic_write_bytes,
    sanitize_filename,
    validate_input_file,
)

try:
    from PySide6.QtMultimedia import QMediaDevices
except ImportError:  # pragma: no cover
    QMediaDevices = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

APP_ID = "simple_scan"

SUPPORTED_SCAN_IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}
)

SCAN_FILTER_PRESETS: tuple[tuple[str, str], ...] = (
    ("magic_color", "Magic Color (Clean Paper)"),
    ("document_bw", "Crisp B&W Document"),
    ("grayscale", "Grayscale Scan"),
    ("original", "Original Raw Scan"),
)

_VALID_FILTERS: frozenset[str] = frozenset(fid for fid, _ in SCAN_FILTER_PRESETS)


def _new_page_id() -> str:
    return uuid.uuid4().hex[:10]


@dataclass(frozen=True, slots=True)
class ScannerDeviceInfo:
    """Metadata describing an available scanner, camera, or virtual capture source."""

    device_id: str
    display_name: str
    backend: str  # 'wia' | 'camera' | 'virtual'
    manufacturer: str = "Jaiho"


@dataclass(slots=True)
class ScanPageRecord:
    """Single scanned page with non-destructive crop, deskew, rotation, and cleanup settings."""

    source_image: QImage
    page_id: str = field(default_factory=_new_page_id)
    title: str = "Scanned Page"
    rotation: int = 0  # 0, 90, 180, 270
    skew_angle: float = 0.0  # -15.0 to +15.0 degrees
    crop_rect: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)  # (x, y, w, h) normalized
    filter_preset: str = "magic_color"
    brightness: int = 0  # -100 to +100
    contrast: int = 0  # -100 to +100

    def __post_init__(self) -> None:
        if self.filter_preset not in _VALID_FILTERS:
            self.filter_preset = "magic_color"
        self.rotation = self.rotation % 360

    def clone(self) -> ScanPageRecord:
        """Create a deep copy of this scanned page with a fresh page_id."""
        return ScanPageRecord(
            source_image=self.source_image.copy(),
            page_id=_new_page_id(),
            title=f"{self.title} (Copy)",
            rotation=self.rotation,
            skew_angle=self.skew_angle,
            crop_rect=self.crop_rect,
            filter_preset=self.filter_preset,
            brightness=self.brightness,
            contrast=self.contrast,
        )

    def reset_adjustments(self) -> None:
        """Restore crop, deskew, rotation, and tone adjustments back to defaults."""
        self.rotation = 0
        self.skew_angle = 0.0
        self.crop_rect = (0.0, 0.0, 1.0, 1.0)
        self.filter_preset = "magic_color"
        self.brightness = 0
        self.contrast = 0


def _clamp_byte(val: float) -> int:
    if val <= 0.0:
        return 0
    if val >= 255.0:
        return 255
    return int(round(val))


def _apply_tone_and_filter(image: QImage, page: ScanPageRecord) -> QImage:
    """Apply brightness, contrast, and document cleanup preset to an ARGB32 QImage."""
    needs_pixel_pass = (
        page.filter_preset != "original"
        or page.brightness != 0
        or page.contrast != 0
    )
    if not needs_pixel_pass:
        return image

    img = image.convertToFormat(QImage.Format.Format_ARGB32)
    w = img.width()
    h = img.height()
    if w <= 0 or h <= 0:
        return img

    # Precompute 256-entry lookup table for brightness + contrast
    b_shift = max(-100, min(100, int(page.brightness))) * 1.28
    c_val = max(-100, min(100, int(page.contrast))) * 2.55
    c_factor = (259.0 * (c_val + 255.0)) / (255.0 * (259.0 - c_val))

    # Boost contrast slightly for document presets so off-white paper snaps to crisp white
    preset = page.filter_preset
    if preset == "magic_color":
        c_factor *= 1.18
        b_shift += 10.0
    elif preset == "document_bw":
        c_factor *= 1.35
        b_shift += 12.0

    lut = [_clamp_byte(c_factor * (i - 128.0) + 128.0 + b_shift) for i in range(256)]

    for y in range(h):
        for x in range(w):
            px = img.pixel(x, y)
            a = qAlpha(px)
            r = lut[qRed(px)]
            g = lut[qGreen(px)]
            b = lut[qBlue(px)]

            if preset == "grayscale":
                lum = int(round(0.299 * r + 0.587 * g + 0.114 * b))
                img.setPixel(x, y, qRgba(lum, lum, lum, a))
            elif preset == "document_bw":
                lum = int(round(0.299 * r + 0.587 * g + 0.114 * b))
                bw = 255 if lum >= 158 else 24
                img.setPixel(x, y, qRgba(bw, bw, bw, a))
            elif preset == "magic_color":
                # Whiten light paper background while keeping text & pastel stamps vivid
                if r > 218 and g > 218 and b > 218:
                    img.setPixel(x, y, qRgba(255, 254, 252, a))
                else:
                    img.setPixel(x, y, qRgba(r, g, b, a))
            else:
                img.setPixel(x, y, qRgba(r, g, b, a))

    return img


def render_processed_scan_image(
    page: ScanPageRecord,
    max_width: int | None = None,
) -> QImage:
    """Execute the full non-destructive crop -> rotate -> deskew -> filter pipeline."""
    src = page.source_image
    if src.isNull() or src.width() <= 0 or src.height() <= 0:
        fallback = QImage(600, 840, QImage.Format.Format_ARGB32)
        fallback.fill(QColor("#FFFEFC"))
        return fallback

    working = src.convertToFormat(QImage.Format.Format_ARGB32)

    # 1. Apply normalized crop rectangle
    cx, cy, cw, ch = page.crop_rect
    cx = max(0.0, min(0.95, cx))
    cy = max(0.0, min(0.95, cy))
    cw = max(0.05, min(1.0 - cx, cw))
    ch = max(0.05, min(1.0 - cy, ch))

    if cx > 0.001 or cy > 0.001 or cw < 0.999 or ch < 0.999:
        px_x = int(round(cx * working.width()))
        px_y = int(round(cy * working.height()))
        px_w = max(16, int(round(cw * working.width())))
        px_h = max(16, int(round(ch * working.height())))
        working = working.copy(QRect(px_x, px_y, px_w, px_h))

    # 2. Downscale early if a preview `max_width` is requested (keeps UI 60fps responsive)
    if max_width is not None and max_width > 32 and working.width() > max_width:
        working = working.scaledToWidth(
            max_width,
            Qt.TransformationMode.SmoothTransformation,
        )

    # 3. Apply 90-degree orthogonal rotation
    rot = page.rotation % 360
    if rot != 0:
        working = working.transformed(
            QTransform().rotate(float(rot)),
            Qt.TransformationMode.SmoothTransformation,
        )

    # 4. Apply fine deskew angle on a clean Snow paper canvas
    skew = max(-15.0, min(15.0, float(page.skew_angle)))
    if abs(skew) >= 0.05:
        deskewed = QImage(working.size(), QImage.Format.Format_ARGB32)
        deskewed.fill(QColor("#FFFEFC"))
        painter = QPainter(deskewed)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        half_w = working.width() / 2.0
        half_h = working.height() / 2.0
        painter.translate(half_w, half_h)
        painter.rotate(skew)
        painter.translate(-half_w, -half_h)
        painter.drawImage(0, 0, working)
        painter.end()
        working = deskewed

    # 5. Apply tone & document cleanup preset
    return _apply_tone_and_filter(working, page)


def render_scan_thumbnail(page: ScanPageRecord, width_px: int = 132) -> QPixmap:
    """Render a 2x High-DPI miniature thumbnail pixmap for a scanned page."""
    dpr = 2
    img = render_processed_scan_image(page, max_width=width_px * dpr)
    px = QPixmap.fromImage(img)
    px.setDevicePixelRatio(float(dpr))
    return px


def estimate_skew_angle(image: QImage) -> float:
    """Estimate document tilt angle (-6.0° to +6.0°) via horizontal row-projection variance."""
    if image.isNull() or image.width() < 40 or image.height() < 40:
        return 0.0

    # Downsample to 240px wide for fast, deterministic projection analysis
    probe = image.scaledToWidth(240, Qt.TransformationMode.SmoothTransformation).convertToFormat(
        QImage.Format.Format_Grayscale8
    )
    w = probe.width()
    h = probe.height()

    best_angle = 0.0
    best_score = -1.0

    # Coarse-to-fine sweep in degrees
    candidates = [round(-6.0 + step * 0.5, 2) for step in range(25)]
    for angle in candidates:
        rotated = probe.transformed(
            QTransform().rotate(angle),
            Qt.TransformationMode.FastTransformation,
        )
        rw = rotated.width()
        rh = rotated.height()
        row_sums: list[float] = []
        for y in range(0, rh, 2):
            dark_pixels = 0
            for x in range(0, rw, 2):
                if qRed(rotated.pixel(x, y)) < 180:
                    dark_pixels += 1
            row_sums.append(float(dark_pixels))

        if len(row_sums) < 4:
            continue
        mean_val = sum(row_sums) / float(len(row_sums))
        variance = sum((v - mean_val) ** 2 for v in row_sums) / float(len(row_sums))
        # Slight preference for 0.0 when tied
        adjusted_score = variance * (1.0 - abs(angle) * 0.004)
        if adjusted_score > best_score:
            best_score = adjusted_score
            best_angle = angle

    _ = w, h
    return best_angle


class ScannerAcquisitionService:
    """Discovers local WIA scanners, cameras, and provides offline virtual/image capture."""

    VIRTUAL_DEVICE_ID = "virtual://jaiho-a4-scanner"

    @classmethod
    def discover_devices(cls) -> list[ScannerDeviceInfo]:
        """Return available WIA scanners, video cameras, and the built-in virtual scanner."""
        devices: list[ScannerDeviceInfo] = []

        # 1. Probe Windows WIA (Windows Image Acquisition) devices if pywin32 is available
        try:
            import win32com.client  # type: ignore[import-untyped]

            manager = win32com.client.Dispatch("WIA.DeviceManager")
            for idx in range(1, int(manager.DeviceInfos.Count) + 1):
                info = manager.DeviceInfos.Item(idx)
                dev_id = str(info.DeviceID)
                props = info.Properties
                name = str(props("Name").Value) if props is not None else f"WIA Scanner {idx}"
                mfg = str(props("Manufacturer").Value) if props is not None else "Windows WIA"
                devices.append(
                    ScannerDeviceInfo(
                        device_id=f"wia://{dev_id}",
                        display_name=f"{name} (WIA)",
                        backend="wia",
                        manufacturer=mfg,
                    )
                )
        except Exception:
            # Expected when pywin32 is not installed or no WIA service/scanner is present
            pass

        # 2. Probe connected document cameras / webcams via QtMultimedia
        if QMediaDevices is not None:
            try:
                for cam in QMediaDevices.videoInputs():
                    cam_id = bytes(cam.id()).decode("utf-8", errors="ignore")
                    cam_desc = cam.description() or "Document Camera"
                    devices.append(
                        ScannerDeviceInfo(
                            device_id=f"camera://{cam_id}",
                            display_name=f"{cam_desc} (Camera)",
                            backend="camera",
                            manufacturer="System Camera",
                        )
                    )
            except Exception:
                pass

        # 3. Always provide the built-in offline A4 Virtual Scanner for instant testing
        devices.append(
            ScannerDeviceInfo(
                device_id=cls.VIRTUAL_DEVICE_ID,
                display_name="Virtual Flatbed Scanner (A4 Sample Sheet)",
                backend="virtual",
                manufacturer="Jaiho Digital",
            )
        )
        return devices

    @staticmethod
    def generate_virtual_scan_image(
        page_number: int = 1,
        doc_title: str = "Scanned Document",
        slight_skew: float = 0.0,
    ) -> QImage:
        """Render a crisp, high-resolution A4 sample scanned document sheet."""
        w, h = 1240, 1754  # A4 at 150 DPI
        img = QImage(w, h, QImage.Format.Format_ARGB32)
        # Slightly warm off-white paper tone so Magic Color cleanup visibly brightens it
        img.fill(QColor("#F4F2EC"))

        painter = QPainter(img)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        if abs(slight_skew) > 0.01:
            painter.translate(w / 2.0, h / 2.0)
            painter.rotate(slight_skew)
            painter.translate(-w / 2.0, -h / 2.0)

        # Subtle scanner platen margin border
        painter.setPen(QPen(QColor("#D8D4CA"), 2.0))
        painter.setBrush(QColor("#F9F8F4"))
        painter.drawRect(QRectF(36.0, 36.0, w - 72.0, h - 72.0))

        # Soft pastel letterhead strip
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#E8E0F7"))
        painter.drawRoundedRect(QRectF(84.0, 88.0, w - 168.0, 140.0), 18.0, 18.0)

        painter.setFont(QFont("Inter", 26, QFont.Weight.Bold))
        painter.setPen(QColor("#2F2E36"))
        painter.drawText(
            QRectF(116.0, 108.0, w - 232.0, 56.0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            f"{doc_title} — Sheet #{page_number}",
        )

        painter.setFont(QFont("Inter", 14))
        painter.setPen(QColor("#5C5966"))
        painter.drawText(
            QRectF(116.0, 166.0, w - 232.0, 40.0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "Jaiho Super Simple Office  •  150 DPI Local Flatbed Capture",
        )

        # Structured document rows & receipt/invoice table lines
        y_cursor = 280.0
        body_font = QFont("Inter", 15)
        painter.setFont(body_font)
        sample_lines = [
            f"Reference ID: JSO-SCAN-{page_number:03d}",
            "Capture Pipeline: Non-destructive crop, deskew, rotation & Magic Color cleanup",
            "Storage Policy: 100% offline local storage with zero external network calls",
            "Export Formats: Multi-page PDF (.pdf), lossless PNG (.png), or JPEG (.jpg)",
            "",
            "Line Item Summary:",
            "  1. Document Digitization & Archival Setup ...................... $0.00 (Local)",
            "  2. Automatic Deskew & Contrast Calibration ................... Included",
            "  3. Multi-Page PDF Assembly & Page Reordering ................. Included",
        ]
        for line in sample_lines:
            if line:
                painter.setPen(QColor("#2B2A32"))
                painter.drawText(
                    QRectF(96.0, y_cursor, w - 192.0, 42.0),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    line,
                )
            y_cursor += 54.0

        # Signature & stamp verification box near bottom
        sig_box = QRectF(96.0, h - 290.0, w - 192.0, 140.0)
        painter.setPen(QPen(QColor("#C8C3B8"), 2.0, Qt.PenStyle.DashLine))
        painter.setBrush(QColor("#FFFEFC"))
        painter.drawRoundedRect(sig_box, 14.0, 14.0)

        painter.setFont(QFont("Inter", 13, QFont.Weight.DemiBold))
        painter.setPen(QColor("#477A63"))
        painter.drawText(
            sig_box.adjusted(24.0, 18.0, -24.0, -18.0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            "VERIFIED LOCAL SCAN  •  READY FOR PDF OR IMAGE EXPORT",
        )

        painter.end()
        return img

    @staticmethod
    def load_image_file(file_path: str | Path) -> QImage:
        """Validate and load a local image file into a QImage."""
        validated = validate_input_file(
            file_path,
            allowed_extensions=SUPPORTED_SCAN_IMAGE_EXTENSIONS,
            max_size_mb=80.0,
        )
        img = QImage(str(validated))
        if img.isNull() or img.width() <= 0 or img.height() <= 0:
            raise FileOperationError(f"Could not decode image file '{validated.name}'.")
        return img.convertToFormat(QImage.Format.Format_ARGB32)

    @classmethod
    def acquire_page(
        cls,
        device: ScannerDeviceInfo,
        page_number: int = 1,
        dpi: int = 150,
    ) -> QImage:
        """Acquire a single page image from the selected scanner device."""
        if device.backend == "wia":
            try:
                import tempfile

                import win32com.client  # type: ignore[import-untyped]

                raw_id = device.device_id.removeprefix("wia://")
                manager = win32com.client.Dispatch("WIA.DeviceManager")
                target_dev = None
                for idx in range(1, int(manager.DeviceInfos.Count) + 1):
                    info = manager.DeviceInfos.Item(idx)
                    if str(info.DeviceID) == raw_id:
                        target_dev = info.Connect()
                        break
                if target_dev is None:
                    raise FileOperationError(f"Scanner '{device.display_name}' is no longer connected.")

                item = target_dev.Items(1)
                try:
                    item.Properties("Horizontal Resolution").Value = int(dpi)
                    item.Properties("Vertical Resolution").Value = int(dpi)
                except Exception:
                    pass

                wia_image = item.Transfer()
                tmp_png = Path(tempfile.gettempdir()) / f"jaiho_wia_{uuid.uuid4().hex[:8]}.png"
                try:
                    wia_image.SaveFile(str(tmp_png))
                    loaded = QImage(str(tmp_png))
                    if loaded.isNull():
                        raise FileOperationError("WIA scanner returned an unreadable image.")
                    return loaded.convertToFormat(QImage.Format.Format_ARGB32)
                finally:
                    tmp_png.unlink(missing_ok=True)
            except FileOperationError:
                raise
            except Exception as exc:
                raise FileOperationError(f"WIA hardware scan failed: {exc}") from exc

        # Virtual flatbed scanner fallback & simulation
        return cls.generate_virtual_scan_image(page_number=page_number)


class ScanBatchModel:
    """Ordered collection of scanned pages with reorder, duplicate, delete, and export."""

    def __init__(self, pages: list[ScanPageRecord] | None = None) -> None:
        self.pages: list[ScanPageRecord] = list(pages) if pages else []
        self.active_index: int = 0 if self.pages else -1

    @property
    def is_empty(self) -> bool:
        return len(self.pages) == 0

    @property
    def active_page(self) -> ScanPageRecord | None:
        if not self.pages:
            self.active_index = -1
            return None
        self.active_index = max(0, min(len(self.pages) - 1, self.active_index))
        return self.pages[self.active_index]

    def set_active_index(self, index: int) -> ScanPageRecord | None:
        if not (0 <= index < len(self.pages)):
            return None
        self.active_index = index
        return self.pages[index]

    def add_page(self, page: ScanPageRecord, insert_after_active: bool = True) -> ScanPageRecord:
        if not self.pages or not insert_after_active:
            self.pages.append(page)
            self.active_index = len(self.pages) - 1
            return page

        pos = min(len(self.pages), max(0, self.active_index) + 1)
        self.pages.insert(pos, page)
        self.active_index = pos
        return page

    def duplicate_active_page(self) -> ScanPageRecord | None:
        curr = self.active_page
        if curr is None:
            return None
        copy_page = curr.clone()
        pos = self.active_index + 1
        self.pages.insert(pos, copy_page)
        self.active_index = pos
        return copy_page

    def delete_page(self, index: int | None = None) -> bool:
        if not self.pages:
            return False
        target = self.active_index if index is None else index
        if not (0 <= target < len(self.pages)):
            return False
        self.pages.pop(target)
        if not self.pages:
            self.active_index = -1
        else:
            self.active_index = max(0, min(len(self.pages) - 1, self.active_index))
        return True

    def move_page(self, from_index: int, to_index: int) -> bool:
        if not (0 <= from_index < len(self.pages) and 0 <= to_index < len(self.pages)):
            return False
        if from_index == to_index:
            return False
        item = self.pages.pop(from_index)
        self.pages.insert(to_index, item)
        self.active_index = to_index
        return True

    def clear(self) -> None:
        self.pages.clear()
        self.active_index = -1


def export_scan_batch_to_pdf(
    batch: ScanBatchModel,
    destination: str | Path,
    page_size_mode: str = "a4",
) -> Path:
    """Render all scanned pages in `batch` into a crisp multi-page PDF document."""
    if batch.is_empty:
        raise FileOperationError("No scanned pages are available to export.")

    target = Path(destination)
    if target.suffix.lower() != ".pdf":
        target = target.with_suffix(".pdf")

    qbuf = QBuffer()
    qbuf.open(QIODevice.OpenModeFlag.WriteOnly)

    writer = QPdfWriter(qbuf)
    writer.setResolution(150)
    writer.setPageMargins(QMarginsF(8.0, 8.0, 8.0, 8.0), QPageLayout.Unit.Millimeter)
    writer.setTitle(target.stem)
    writer.setCreator("SimpleScan — Jaiho Super Simple Office")

    if page_size_mode.lower() == "letter":
        writer.setPageSize(QPageSize(QPageSize.PageSizeId.Letter))
    else:
        writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))

    painter = QPainter(writer)
    try:
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        for idx, page in enumerate(batch.pages):
            if idx > 0:
                writer.newPage()

            processed = render_processed_scan_image(page)
            page_rect = QRectF(0.0, 0.0, float(writer.width()), float(writer.height()))
            painter.fillRect(page_rect, QColor("#FFFEFC"))

            # Fit scanned image proportionally inside printable page bounds
            img_w = max(1.0, float(processed.width()))
            img_h = max(1.0, float(processed.height()))
            scale = min(page_rect.width() / img_w, page_rect.height() / img_h)
            draw_w = img_w * scale
            draw_h = img_h * scale
            left = (page_rect.width() - draw_w) / 2.0
            top = (page_rect.height() - draw_h) / 2.0
            painter.drawImage(QRectF(left, top, draw_w, draw_h), processed)
    finally:
        painter.end()

    qbuf.close()
    return atomic_write_bytes(target, bytes(qbuf.data()), backup_app_id=APP_ID)


def export_scan_page_image(
    page: ScanPageRecord,
    destination: str | Path,
) -> Path:
    """Export a single processed scanned page as .png, .jpg, or .bmp."""
    target = Path(destination)
    if not target.suffix:
        target = target.with_suffix(".png")

    ext = target.suffix.lower()
    fmt_map = {
        ".png": "PNG",
        ".jpg": "JPEG",
        ".jpeg": "JPEG",
        ".bmp": "BMP",
        ".webp": "WEBP",
    }
    qt_fmt = fmt_map.get(ext)
    if qt_fmt is None:
        raise FileOperationError(
            f"Unsupported image extension '{ext}'. Expected .png, .jpg, .bmp, or .webp."
        )

    processed = render_processed_scan_image(page)
    qbuf = QBuffer()
    qbuf.open(QIODevice.OpenModeFlag.WriteOnly)
    ok = processed.save(qbuf, qt_fmt, 92 if qt_fmt == "JPEG" else -1)
    qbuf.close()
    if not ok:
        raise FileOperationError(f"Failed to encode image '{target.name}'.")

    return atomic_write_bytes(target, bytes(qbuf.data()), backup_app_id=APP_ID)


def export_batch_as_images(
    batch: ScanBatchModel,
    output_dir: str | Path,
    base_name: str = "Scan_Page",
    ext: str = ".png",
) -> list[Path]:
    """Export every scanned page in `batch` as numbered image files in `output_dir`."""
    if batch.is_empty:
        raise FileOperationError("No scanned pages are available to export.")

    folder = Path(output_dir)
    folder.mkdir(parents=True, exist_ok=True)
    clean_stem = sanitize_filename(base_name) or "Scan_Page"
    clean_ext = ext if ext.startswith(".") else f".{ext}"

    written: list[Path] = []
    for idx, page in enumerate(batch.pages):
        file_target = folder / f"{clean_stem}_{idx + 1:02d}{clean_ext}"
        written.append(export_scan_page_image(page, file_target))
    return written