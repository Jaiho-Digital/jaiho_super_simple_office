"""100% offline OCR recognition engine, ROI cropping, and export helpers for SimpleOCR."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QPainter,
    QPen,
    QPixmap,
    QTextDocument,
    qAlpha,
    qBlue,
    qGreen,
    qRed,
    qRgba,
)

from apps.simple_pdf.pdf_engine import PdfDocumentService
from apps.simple_writer.document_model import save_qtext_document
from core.file_manager.file_ops import (
    FileOperationError,
    atomic_write_text,
    validate_input_file,
)

logger = logging.getLogger(__name__)

APP_ID = "simple_ocr"

SUPPORTED_OCR_IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}
)
SUPPORTED_OCR_EXTENSIONS: frozenset[str] = SUPPORTED_OCR_IMAGE_EXTENSIONS | frozenset({".pdf"})

OCR_LANGUAGE_CHOICES: tuple[tuple[str, str], ...] = (
    ("en-US", "English (US)"),
    ("en-GB", "English (UK)"),
    ("hi-IN", "Hindi (India)"),
    ("pa-IN", "Punjabi (India)"),
    ("fr-FR", "French"),
    ("de-DE", "German"),
    ("es-ES", "Spanish"),
    ("ja-JP", "Japanese"),
)


@dataclass(frozen=True, slots=True)
class OcrEngineInfo:
    """Describes an available offline OCR backend."""

    backend_id: str  # 'nano_ocr' | 'winrt_ocr' | 'tesseract' | 'builtin_sample'
    display_name: str
    is_available: bool


@dataclass(slots=True)
class OcrPageItem:
    """Single loaded image or PDF page queued for OCR extraction."""

    source_image: QImage
    label: str
    page_id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    crop_rect: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)
    extracted_text: str = ""
    backend_used: str = ""
    elapsed_ms: int = 0


def discover_ocr_backends() -> list[OcrEngineInfo]:
    """Probe installed local OCR backends in priority order."""
    backends: list[OcrEngineInfo] = []

    # 1. Check NanoOCR (local lightweight CRNN package)
    try:
        import nano_ocr  # type: ignore[import-not-found] # noqa: F401

        backends.append(
            OcrEngineInfo(
                backend_id="nano_ocr",
                display_name="NanoOCR (Local CRNN Neural Engine)",
                is_available=True,
            )
        )
    except Exception:
        pass

    # 2. Check Windows 10/11 built-in WinRT Windows.Media.Ocr (via PowerShell)
    if os.name == "nt" and shutil.which("powershell"):
        backends.append(
            OcrEngineInfo(
                backend_id="winrt_ocr",
                display_name="Windows Native OCR (Offline WinRT)",
                is_available=True,
            )
        )

    # 3. Check Tesseract CLI / pytesseract
    if shutil.which("tesseract") is not None:
        backends.append(
            OcrEngineInfo(
                backend_id="tesseract",
                display_name="Tesseract Local OCR",
                is_available=True,
            )
        )

    # 4. Always include built-in offline sample / fallback recognizer
    backends.append(
        OcrEngineInfo(
            backend_id="builtin_sample",
            display_name="Built-in Offline Fallback",
            is_available=True,
        )
    )
    return backends


def preprocess_image_for_ocr(
    image: QImage,
    crop_rect: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0),
    enhance_contrast: bool = True,
) -> QImage:
    """Crop to normalized ROI and optionally boost contrast for crisp OCR recognition."""
    if image.isNull() or image.width() <= 0 or image.height() <= 0:
        blank = QImage(320, 120, QImage.Format.Format_ARGB32)
        blank.fill(QColor("#FFFEFC"))
        return blank

    working = image.convertToFormat(QImage.Format.Format_ARGB32)

    cx, cy, cw, ch = crop_rect
    cx = max(0.0, min(0.95, float(cx)))
    cy = max(0.0, min(0.95, float(cy)))
    cw = max(0.05, min(1.0 - cx, float(cw)))
    ch = max(0.05, min(1.0 - cy, float(ch)))

    if cx > 0.001 or cy > 0.001 or cw < 0.999 or ch < 0.999:
        px_x = int(round(cx * working.width()))
        px_y = int(round(cy * working.height()))
        px_w = max(16, int(round(cw * working.width())))
        px_h = max(16, int(round(ch * working.height())))
        working = working.copy(QRect(px_x, px_y, px_w, px_h))

    if not enhance_contrast:
        return working

    w = working.width()
    h = working.height()
    # Gentle contrast stretch so faint ink becomes crisp against white paper
    factor = 1.24
    for y in range(h):
        for x in range(w):
            px = working.pixel(x, y)
            a = qAlpha(px)
            lum = 0.299 * qRed(px) + 0.587 * qGreen(px) + 0.114 * qBlue(px)
            stretched = int(round(max(0.0, min(255.0, factor * (lum - 128.0) + 132.0))))
            working.setPixel(x, y, qRgba(stretched, stretched, stretched, a))

    return working


def generate_sample_ocr_image() -> QImage:
    """Create a high-contrast sample document image for instant OCR verification."""
    w, h = 1100, 760
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(QColor("#FFFEFC"))

    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

    painter.setPen(QPen(QColor("#D8D5CE"), 2.0))
    painter.setBrush(QColor("#FFFEFC"))
    painter.drawRect(QRectF(28.0, 28.0, w - 56.0, h - 56.0))

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#E8E0F7"))
    painter.drawRoundedRect(QRectF(56.0, 56.0, w - 112.0, 96.0), 14.0, 14.0)

    painter.setFont(QFont("Arial", 22, QFont.Weight.Bold))
    painter.setPen(QColor("#1F1E24"))
    painter.drawText(
        QRectF(80.0, 68.0, w - 160.0, 72.0),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        "JAIHO SUPER SIMPLE OFFICE - INVOICE #2026-084",
    )

    lines = [
        "Date: November 21, 2026",
        "Location: Bengaluru, India",
        "Client: Jaiho Digital Desktop Suite",
        "",
        "1. SimpleWriter Offline Document Studio ........... $0.00",
        "2. SimpleSheets Local Spreadsheet Engine .......... $0.00",
        "3. SimpleOCR Optical Character Recognition ........ $0.00",
        "",
        "Total Amount Due: $0.00 (100% Local Offline License)",
    ]
    y_pos = 188.0
    painter.setFont(QFont("Arial", 16))
    painter.setPen(QColor("#1A1A1F"))
    for line in lines:
        if line:
            painter.drawText(
                QRectF(72.0, y_pos, w - 144.0, 42.0),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                line,
            )
        y_pos += 46.0

    painter.end()
    return img


def _run_winrt_ocr_on_png(png_path: Path, language_tag: str = "en-US") -> str:
    """Invoke Windows 10/11 built-in Windows.Media.Ocr offline via PowerShell WinRT."""
    escaped_path = str(png_path).replace("'", "''")
    escaped_lang = language_tag.replace("'", "''")
    ps_script = (
        "$ErrorActionPreference = 'Stop'; "
        "Add-Type -AssemblyName System.Runtime.WindowsRuntime; "
        "[void][Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]; "
        "[void][Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]; "
        "[void][Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]; "
        "[void][Windows.Globalization.Language, Windows.Globalization, ContentType=WindowsRuntime]; "
        "$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | "
        "Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and "
        "$_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]; "
        "function Await($WinRtTask, $ResultType) { "
        "$asTask = $asTaskGeneric.MakeGenericMethod($ResultType); "
        "$netTask = $asTask.Invoke($null, @($WinRtTask)); "
        "$netTask.Wait(-1) | Out-Null; "
        "$netTask.Result }; "
        f"$lang = New-Object Windows.Globalization.Language('{escaped_lang}'); "
        "$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang); "
        "if ($null -eq $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }; "
        "if ($null -eq $engine) { throw 'Windows OCR engine unavailable for selected language.' }; "
        f"$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync('{escaped_path}')) ([Windows.Storage.StorageFile]); "
        "$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream]); "
        "$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder]); "
        "$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap]); "
        "$ocrResult = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult]); "
        "$lines = $ocrResult.Lines | ForEach-Object { $_.Text }; "
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
        "Write-Output ($lines -join \"`n\")"
    )

    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=25,
        creationflags=creation_flags,
    )
    if proc.returncode != 0:
        err = proc.stderr.strip() or "WinRT OCR command failed."
        raise FileOperationError(err)
    return proc.stdout.strip()


def _run_tesseract_on_png(png_path: Path, language_tag: str = "en-US") -> str:
    """Invoke local Tesseract CLI if installed."""
    lang_map = {
        "en-US": "eng",
        "en-GB": "eng",
        "hi-IN": "hin",
        "pa-IN": "pan",
        "fr-FR": "fra",
        "de-DE": "deu",
        "es-ES": "spa",
        "ja-JP": "jpn",
    }
    tess_lang = lang_map.get(language_tag, "eng")
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.run(
        ["tesseract", str(png_path), "stdout", "-l", tess_lang],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=25,
        creationflags=creation_flags,
    )
    if proc.returncode != 0:
        raise FileOperationError(proc.stderr.strip() or "Tesseract OCR failed.")
    return proc.stdout.strip()


def recognize_image_text(
    image: QImage,
    backend_id: str = "auto",
    language_tag: str = "en-US",
    crop_rect: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0),
    enhance_contrast: bool = True,
) -> tuple[str, str, int]:
    """Run offline OCR on `image`, returning (extracted_text, backend_used, elapsed_ms)."""
    start_t = time.perf_counter()
    prepared = preprocess_image_for_ocr(
        image=image,
        crop_rect=crop_rect,
        enhance_contrast=enhance_contrast,
    )

    available = discover_ocr_backends()
    available_ids = [b.backend_id for b in available]

    if backend_id == "auto" or backend_id not in available_ids:
        chosen_order = [b.backend_id for b in available]
    else:
        chosen_order = [backend_id] + [b.backend_id for b in available if b.backend_id != backend_id]

    tmp_png = Path(tempfile.gettempdir()) / f"jaiho_ocr_{uuid.uuid4().hex[:10]}.png"
    try:
        if not prepared.save(str(tmp_png), "PNG"):
            raise FileOperationError("Failed to prepare temporary PNG buffer for OCR.")

        for candidate in chosen_order:
            try:
                if candidate == "nano_ocr":
                    import nano_ocr  # type: ignore[import-not-found]

                    # Support common NanoOCR predictor interfaces
                    if hasattr(nano_ocr, "predict"):
                        res = nano_ocr.predict(str(tmp_png))
                        text_out = str(res[0] if isinstance(res, tuple) else res).strip()
                        elapsed = max(1, int(round((time.perf_counter() - start_t) * 1000)))
                        return text_out, "NanoOCR", elapsed

                elif candidate == "winrt_ocr":
                    text_out = _run_winrt_ocr_on_png(tmp_png, language_tag=language_tag)
                    elapsed = max(1, int(round((time.perf_counter() - start_t) * 1000)))
                    return text_out, "Windows Native OCR", elapsed

                elif candidate == "tesseract":
                    text_out = _run_tesseract_on_png(tmp_png, language_tag=language_tag)
                    elapsed = max(1, int(round((time.perf_counter() - start_t) * 1000)))
                    return text_out, "Tesseract OCR", elapsed

                elif candidate == "builtin_sample":
                    elapsed = max(1, int(round((time.perf_counter() - start_t) * 1000)))
                    fallback_txt = (
                        "[Offline OCR Preview]\n"
                        f"Image Dimensions: {prepared.width()} x {prepared.height()} px\n"
                        "Install Windows OCR language pack or Tesseract for full bitmap recognition."
                    )
                    return fallback_txt, "Built-in Fallback", elapsed
            except Exception as exc:
                logger.debug("OCR backend '%s' skipped: %s", candidate, exc)
                continue
    finally:
        tmp_png.unlink(missing_ok=True)

    elapsed = max(1, int(round((time.perf_counter() - start_t) * 1000)))
    return "", "None", elapsed


def load_ocr_items_from_file(source_path: str | Path, pdf_dpi: int = 180) -> list[OcrPageItem]:
    """Load an image file or multi-page PDF into OcrPageItem entries ready for recognition."""
    validated = validate_input_file(
        source_path,
        allowed_extensions=SUPPORTED_OCR_EXTENSIONS,
        max_size_mb=150.0,
    )
    ext = validated.suffix.lower()

    if ext == ".pdf":
        svc = PdfDocumentService()
        try:
            total_pages = svc.load_file(validated)
            items: list[OcrPageItem] = []
            for p_idx in range(total_pages):
                pt_size = svc.get_page_size_points(p_idx)
                target_w = max(800, int(round((pt_size.width() / 72.0) * float(pdf_dpi))))
                page_img = svc.render_page_image(p_idx, target_width_px=target_w)
                items.append(
                    OcrPageItem(
                        source_image=page_img,
                        label=f"{validated.stem} — Page {p_idx + 1}",
                    )
                )
            return items
        finally:
            svc.close()

    img = QImage(str(validated))
    if img.isNull() or img.width() <= 0 or img.height() <= 0:
        raise FileOperationError(f"Could not decode image '{validated.name}'.")

    return [
        OcrPageItem(
            source_image=img.convertToFormat(QImage.Format.Format_ARGB32),
            label=validated.name,
        )
    ]


def render_ocr_thumbnail(item: OcrPageItem, width_px: int = 124) -> QPixmap:
    """Render a 2x High-DPI miniature thumbnail for the OCR page strip."""
    dpr = 2
    scaled = item.source_image.scaledToWidth(
        width_px * dpr,
        Qt.TransformationMode.SmoothTransformation,
    )
    px = QPixmap.fromImage(scaled)
    px.setDevicePixelRatio(float(dpr))
    return px


def export_ocr_text_document(
    destination: str | Path,
    text_content: str,
    title: str = "Recognized OCR Text",
) -> Path:
    """Save extracted OCR text to .txt, .md, or .docx."""
    target = Path(destination)
    if not target.suffix:
        target = target.with_suffix(".txt")

    ext = target.suffix.lower()
    if ext == ".txt":
        return atomic_write_text(target, text_content, backup_app_id=APP_ID)

    if ext in {".md", ".docx"}:
        qdoc = QTextDocument()
        qdoc.setPlainText(text_content)
        return save_qtext_document(target, title=title, source_doc=qdoc)

    raise FileOperationError(
        f"Unsupported export extension '{ext}'. Expected .txt, .md, or .docx."
    )