"""Offline multi-format document, spreadsheet, image, and PDF conversion engine for SimpleConvert."""

from __future__ import annotations

import csv
import io
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from PySide6.QtCore import QBuffer, QIODevice, QMarginsF, QRectF, QSize, QSizeF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QPageLayout,
    QPageSize,
    QPainter,
    QPdfWriter,
    QPen,
    QTextDocument,
)

from apps.simple_pdf.pdf_engine import PdfDocumentService
from apps.simple_sheets.formula_engine import format_cell_display
from apps.simple_sheets.sheet_model import (
    CellData,
    SheetModel,
    WorkbookModel,
    load_workbook,
    save_workbook,
)
from apps.simple_writer.document_model import (
    load_document_into_qtext,
    save_qtext_document,
)
from core.file_manager.file_ops import (
    FileOperationError,
    atomic_write_bytes,
    atomic_write_text,
    sanitize_filename,
    validate_input_file,
)

APP_ID = "simple_convert"

FileCategory = Literal["document", "spreadsheet", "image", "pdf"]

_DOCUMENT_INPUT_EXTS: frozenset[str] = frozenset({".docx", ".md", ".markdown", ".html", ".htm", ".txt"})
_SPREADSHEET_INPUT_EXTS: frozenset[str] = frozenset({".xlsx", ".csv", ".tsv", ".json"})
_IMAGE_INPUT_EXTS: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp", ".ico"})
_PDF_INPUT_EXTS: frozenset[str] = frozenset({".pdf"})

ALL_SUPPORTED_INPUT_EXTENSIONS: frozenset[str] = (
    _DOCUMENT_INPUT_EXTS | _SPREADSHEET_INPUT_EXTS | _IMAGE_INPUT_EXTS | _PDF_INPUT_EXTS
)

TARGET_FORMATS_BY_CATEGORY: dict[FileCategory, tuple[tuple[str, str], ...]] = {
    "document": (
        (".pdf", "PDF Document (.pdf)"),
        (".docx", "Word Document (.docx)"),
        (".md", "Markdown (.md)"),
        (".html", "Rich HTML (.html)"),
        (".txt", "Plain Text (.txt)"),
    ),
    "spreadsheet": (
        (".xlsx", "Excel Workbook (.xlsx)"),
        (".csv", "CSV Comma-Delimited (.csv)"),
        (".tsv", "TSV Tab-Delimited (.tsv)"),
        (".json", "JSON Records (.json)"),
        (".pdf", "PDF Table Report (.pdf)"),
        (".html", "HTML Table (.html)"),
    ),
    "image": (
        (".png", "PNG Lossless (.png)"),
        (".jpg", "JPEG Photo (.jpg)"),
        (".webp", "WebP Modern (.webp)"),
        (".bmp", "Bitmap (.bmp)"),
        (".ico", "Windows Icon (.ico)"),
        (".pdf", "PDF Sheet (.pdf)"),
    ),
    "pdf": (
        (".png", "PNG Page Images (.png)"),
        (".jpg", "JPEG Page Images (.jpg)"),
        (".txt", "Extracted Plain Text (.txt)"),
    ),
}


def detect_file_category(file_path: str | Path) -> FileCategory:
    """Classify `file_path` into document, spreadsheet, image, or pdf by extension."""
    ext = Path(file_path).suffix.lower()
    if ext in _DOCUMENT_INPUT_EXTS:
        return "document"
    if ext in _SPREADSHEET_INPUT_EXTS:
        return "spreadsheet"
    if ext in _IMAGE_INPUT_EXTS:
        return "image"
    if ext in _PDF_INPUT_EXTS:
        return "pdf"
    raise FileOperationError(f"Unsupported source extension '{ext or '(none)'}'.")


def get_valid_target_formats(category: FileCategory) -> tuple[tuple[str, str], ...]:
    """Return (extension, display_label) options valid for `category`."""
    return TARGET_FORMATS_BY_CATEGORY[category]


def default_target_extension(source_path: str | Path) -> str:
    """Pick a sensible default conversion output extension distinct from the input format."""
    src = Path(source_path)
    src_ext = src.suffix.lower()
    cat = detect_file_category(src)
    candidates = [ext for ext, _ in TARGET_FORMATS_BY_CATEGORY[cat]]
    for ext in candidates:
        if ext != src_ext and not (src_ext == ".jpeg" and ext == ".jpg"):
            return ext
    return candidates[0]


def format_file_size(num_bytes: int) -> str:
    """Format byte counts into clean KB/MB telemetry."""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024.0:.1f} KB"
    return f"{num_bytes / (1024.0 * 1024.0):.2f} MB"


def _new_job_id() -> str:
    return uuid.uuid4().hex[:10]


@dataclass(slots=True)
class ConversionJob:
    """Single queued file conversion item in SimpleConvert."""

    source_path: Path
    category: FileCategory
    target_ext: str
    job_id: str = field(default_factory=_new_job_id)
    size_bytes: int = 0
    status: str = "queued"  # 'queued' | 'done' | 'error'
    output_paths: list[Path] = field(default_factory=list)
    error_message: str = ""

    @classmethod
    def from_path(cls, source_path: str | Path, target_ext: str | None = None) -> ConversionJob:
        validated = validate_input_file(
            source_path,
            allowed_extensions=ALL_SUPPORTED_INPUT_EXTENSIONS,
            max_size_mb=150.0,
        )
        cat = detect_file_category(validated)
        chosen_ext = (
            target_ext.lower()
            if target_ext and target_ext.startswith(".")
            else default_target_extension(validated)
        )
        valid_exts = {ext for ext, _ in TARGET_FORMATS_BY_CATEGORY[cat]}
        if chosen_ext not in valid_exts:
            chosen_ext = default_target_extension(validated)

        return cls(
            source_path=validated,
            category=cat,
            target_ext=chosen_ext,
            size_bytes=validated.stat().st_size,
        )


def _render_qtext_to_pdf_bytes(doc: QTextDocument, title: str) -> bytes:
    """Render a QTextDocument cleanly into an A4 PDF byte buffer."""
    qbuf = QBuffer()
    qbuf.open(QIODevice.OpenModeFlag.WriteOnly)

    writer = QPdfWriter(qbuf)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setPageMargins(QMarginsF(16.0, 16.0, 16.0, 16.0), QPageLayout.Unit.Millimeter)
    writer.setTitle(title)
    writer.setCreator("SimpleConvert — Jaiho Super Simple Office")

    painter = QPainter(writer)
    try:
        doc.drawContents(painter)
    finally:
        painter.end()

    qbuf.close()
    return bytes(qbuf.data())


def _load_spreadsheet_like_workbook(source_path: Path) -> WorkbookModel:
    """Load .xlsx, .csv, .tsv, or .json tabular records into a WorkbookModel."""
    ext = source_path.suffix.lower()
    if ext in {".xlsx", ".csv", ".tsv"}:
        return load_workbook(source_path)

    if ext == ".json":
        raw_txt = source_path.read_text(encoding="utf-8", errors="replace")
        try:
            parsed = json.loads(raw_txt)
        except json.JSONDecodeError as exc:
            raise FileOperationError(f"Invalid JSON in '{source_path.name}': {exc}") from exc

        sheet = SheetModel(name=sanitize_filename(source_path.stem)[:31] or "Data")
        if isinstance(parsed, list) and parsed:
            if all(isinstance(item, dict) for item in parsed):
                headers: list[str] = []
                seen: set[str] = set()
                for row_dict in parsed:
                    for k in row_dict:
                        k_str = str(k)
                        if k_str not in seen:
                            seen.add(k_str)
                            headers.append(k_str)
                for c_idx, h_name in enumerate(headers):
                    sheet.set_cell_input(0, c_idx, h_name)
                    sheet.update_cell_style(0, c_idx, bold=True, bg_tint="#E8E0F7")
                for r_idx, row_dict in enumerate(parsed, start=1):
                    for c_idx, h_name in enumerate(headers):
                        val = row_dict.get(h_name, "")
                        sheet.set_cell_input(r_idx, c_idx, "" if val is None else str(val))
            else:
                for r_idx, item in enumerate(parsed):
                    if isinstance(item, list):
                        for c_idx, cell_v in enumerate(item):
                            sheet.set_cell_input(r_idx, c_idx, "" if cell_v is None else str(cell_v))
                    else:
                        sheet.set_cell_input(r_idx, 0, str(item))
        elif isinstance(parsed, dict):
            sheet.set_cell_input(0, 0, "Key")
            sheet.set_cell_input(0, 1, "Value")
            sheet.update_cell_style(0, 0, bold=True)
            sheet.update_cell_style(0, 1, bold=True)
            for r_idx, (k, v) in enumerate(parsed.items(), start=1):
                sheet.set_cell_input(r_idx, 0, str(k))
                sheet.set_cell_input(r_idx, 1, json.dumps(v) if isinstance(v, (dict, list)) else str(v))
        else:
            raise FileOperationError(f"'{source_path.name}' does not contain tabular JSON data.")

        return WorkbookModel(sheets=[sheet])

    raise FileOperationError(f"Unsupported spreadsheet source '{ext}'.")


def _workbook_to_json_text(workbook: WorkbookModel) -> str:
    """Serialize active sheet rows into formatted JSON records."""
    sheet = workbook.active_sheet
    max_r, max_c = sheet.used_bounds()
    if not sheet.cells:
        return "[]\n"

    headers = [
        sheet.get_display_text(0, c).strip() or f"col_{c + 1}"
        for c in range(max_c + 1)
    ]
    records: list[dict[str, object]] = []
    for r in range(1, max_r + 1):
        row_obj: dict[str, object] = {}
        has_any = False
        for c, h_key in enumerate(headers):
            val = sheet.get_evaluated_value(r, c)
            if val is not None and val != "":
                has_any = True
            row_obj[h_key] = val
        if has_any:
            records.append(row_obj)

    if not records:
        records = [{h_key: sheet.get_evaluated_value(0, c) for c, h_key in enumerate(headers)}]

    return json.dumps(records, indent=2, ensure_ascii=False) + "\n"


def _workbook_to_html_table(workbook: WorkbookModel, title: str) -> str:
    """Render all sheets of a WorkbookModel into a clean Soft Modern Pastel HTML table document."""
    sections: list[str] = []
    for sheet in workbook.sheets:
        max_r, max_c = sheet.used_bounds()
        rows_html: list[str] = []
        if sheet.cells:
            for r in range(max_r + 1):
                tag = "th" if r == 0 else "td"
                cells_html = "".join(
                    f"<{tag}>{sheet.get_display_text(r, c)}</{tag}>"
                    for c in range(max_c + 1)
                )
                rows_html.append(f"<tr>{cells_html}</tr>")
        sections.append(f"<h2>{sheet.name}</h2><table>{''.join(rows_html)}</table>")

    return (
        "<!DOCTYPE html>\n<html><head><meta charset='utf-8'>"
        f"<title>{title}</title>"
        "<style>"
        "body { font-family: 'Segoe UI', Inter, sans-serif; background: #F8F7F4; color: #35343B; padding: 28px; }"
        "h2 { color: #6F59C9; margin-top: 24px; }"
        "table { border-collapse: collapse; width: 100%; background: #FFFEFC; border: 1px solid #E9E7E3; }"
        "th, td { border: 1px solid #E9E7E3; padding: 8px 12px; text-align: left; font-size: 13px; }"
        "th { background: #E8E0F7; font-weight: 600; }"
        "</style></head><body>"
        f"{''.join(sections)}"
        "</body></html>\n"
    )


def convert_single_file(
    source_path: str | Path,
    target_ext: str,
    output_dir: str | Path | None = None,
    jpeg_quality: int = 92,
    pdf_dpi: int = 150,
) -> list[Path]:
    """Convert `source_path` to `target_ext` inside `output_dir`, returning written output path(s)."""
    validated = validate_input_file(
        source_path,
        allowed_extensions=ALL_SUPPORTED_INPUT_EXTENSIONS,
        max_size_mb=150.0,
    )
    category = detect_file_category(validated)
    clean_ext = target_ext.lower() if target_ext.startswith(".") else f".{target_ext.lower()}"
    valid_exts = {ext for ext, _ in TARGET_FORMATS_BY_CATEGORY[category]}
    if clean_ext not in valid_exts:
        raise FileOperationError(
            f"Cannot convert '{validated.suffix}' ({category}) to '{clean_ext}'."
        )

    dest_folder = Path(output_dir).resolve() if output_dir is not None else validated.parent
    dest_folder.mkdir(parents=True, exist_ok=True)
    stem = sanitize_filename(validated.stem) or "converted"

    # Avoid overwriting the exact source file if source & target paths collide
    candidate_out = dest_folder / f"{stem}{clean_ext}"
    if candidate_out.resolve() == validated.resolve():
        candidate_out = dest_folder / f"{stem}_converted{clean_ext}"

    # ------------------------------------------------------------------
    # 1. Document Conversions (.docx, .md, .html, .txt -> .pdf, .docx, .md, .html, .txt)
    # ------------------------------------------------------------------
    if category == "document":
        qdoc = QTextDocument()
        _, title = load_document_into_qtext(validated, qdoc)
        if clean_ext == ".pdf":
            pdf_bytes = _render_qtext_to_pdf_bytes(qdoc, title=title)
            saved = atomic_write_bytes(candidate_out, pdf_bytes, backup_app_id=APP_ID)
            return [saved]
        saved = save_qtext_document(candidate_out, title=title, source_doc=qdoc)
        return [saved]

    # ------------------------------------------------------------------
    # 2. Spreadsheet & Data Conversions (.xlsx, .csv, .tsv, .json)
    # ------------------------------------------------------------------
    if category == "spreadsheet":
        wb = _load_spreadsheet_like_workbook(validated)
        if clean_ext in {".xlsx", ".csv", ".tsv"}:
            saved = save_workbook(candidate_out, wb)
            return [saved]
        if clean_ext == ".json":
            json_payload = _workbook_to_json_text(wb)
            saved = atomic_write_text(candidate_out, json_payload, backup_app_id=APP_ID)
            return [saved]
        if clean_ext == ".html":
            html_payload = _workbook_to_html_table(wb, title=stem)
            saved = atomic_write_text(candidate_out, html_payload, backup_app_id=APP_ID)
            return [saved]
        if clean_ext == ".pdf":
            qdoc = QTextDocument()
            qdoc.setHtml(_workbook_to_html_table(wb, title=stem))
            pdf_bytes = _render_qtext_to_pdf_bytes(qdoc, title=stem)
            saved = atomic_write_bytes(candidate_out, pdf_bytes, backup_app_id=APP_ID)
            return [saved]

    # ------------------------------------------------------------------
    # 3. Image Conversions (.png, .jpg, .webp, .bmp, .ico -> image or .pdf)
    # ------------------------------------------------------------------
    if category == "image":
        src_img = QImage(str(validated))
        if src_img.isNull() or src_img.width() <= 0 or src_img.height() <= 0:
            raise FileOperationError(f"Could not decode image '{validated.name}'.")

        if clean_ext == ".pdf":
            qbuf = QBuffer()
            qbuf.open(QIODevice.OpenModeFlag.WriteOnly)
            writer = QPdfWriter(qbuf)
            writer.setResolution(150)
            writer.setPageMargins(QMarginsF(0.0, 0.0, 0.0, 0.0), QPageLayout.Unit.Point)
            pt_w = max(72.0, src_img.width() * 72.0 / 150.0)
            pt_h = max(72.0, src_img.height() * 72.0 / 150.0)
            writer.setPageSize(QPageSize(QSizeF(pt_w, pt_h), QPageSize.Unit.Point))
            writer.setTitle(stem)

            painter = QPainter(writer)
            try:
                painter.drawImage(
                    QRectF(0.0, 0.0, float(writer.width()), float(writer.height())),
                    src_img,
                )
            finally:
                painter.end()
            qbuf.close()
            saved = atomic_write_bytes(candidate_out, bytes(qbuf.data()), backup_app_id=APP_ID)
            return [saved]

        working_img = src_img.convertToFormat(QImage.Format.Format_ARGB32)
        if clean_ext == ".ico":
            # Windows icon standard max 256x256
            if working_img.width() > 256 or working_img.height() > 256:
                working_img = working_img.scaled(
                    QSize(256, 256),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
        elif clean_ext in {".jpg", ".jpeg", ".bmp"}:
            # Flatten alpha onto Snow paper white so transparent PNGs don't turn black
            flat = QImage(working_img.size(), QImage.Format.Format_RGB32)
            flat.fill(QColor("#FFFEFC"))
            p = QPainter(flat)
            p.drawImage(0, 0, working_img)
            p.end()
            working_img = flat

        fmt_map = {
            ".png": "PNG",
            ".jpg": "JPEG",
            ".jpeg": "JPEG",
            ".webp": "WEBP",
            ".bmp": "BMP",
            ".ico": "ICO",
        }
        qt_fmt = fmt_map[clean_ext]
        qbuf = QBuffer()
        qbuf.open(QIODevice.OpenModeFlag.WriteOnly)
        quality = max(40, min(100, int(jpeg_quality))) if qt_fmt in {"JPEG", "WEBP"} else -1
        ok = working_img.save(qbuf, qt_fmt, quality)
        qbuf.close()
        if not ok:
            raise FileOperationError(f"Failed to encode '{candidate_out.name}' as {qt_fmt}.")

        saved = atomic_write_bytes(candidate_out, bytes(qbuf.data()), backup_app_id=APP_ID)
        return [saved]

    # ------------------------------------------------------------------
    # 4. PDF Conversions (.pdf -> .png, .jpg page images or .txt text)
    # ------------------------------------------------------------------
    if category == "pdf":
        svc = PdfDocumentService()
        try:
            total_pages = svc.load_file(validated)
            if clean_ext == ".txt":
                sections: list[str] = []
                for p_idx in range(total_pages):
                    txt = svc.get_page_text(p_idx).strip()
                    header = f"--- Page {p_idx + 1} ---"
                    sections.append(f"{header}\n{txt}" if txt else header)
                saved = atomic_write_text(
                    candidate_out,
                    "\n\n".join(sections) + "\n",
                    backup_app_id=APP_ID,
                )
                return [saved]

            qt_fmt = "JPEG" if clean_ext in {".jpg", ".jpeg"} else "PNG"
            written_pages: list[Path] = []
            for p_idx in range(total_pages):
                pt_size = svc.get_page_size_points(p_idx)
                target_px_w = max(600, int(round((pt_size.width() / 72.0) * float(pdf_dpi))))
                page_img = svc.render_page_image(p_idx, target_width_px=target_px_w)

                page_target = (
                    candidate_out
                    if total_pages == 1
                    else dest_folder / f"{stem}_page_{p_idx + 1:02d}{clean_ext}"
                )
                qbuf = QBuffer()
                qbuf.open(QIODevice.OpenModeFlag.WriteOnly)
                ok = page_img.save(qbuf, qt_fmt, 92 if qt_fmt == "JPEG" else -1)
                qbuf.close()
                if not ok:
                    raise FileOperationError(f"Failed to render PDF page {p_idx + 1} to {qt_fmt}.")
                written_pages.append(
                    atomic_write_bytes(page_target, bytes(qbuf.data()), backup_app_id=APP_ID)
                )
            return written_pages
        finally:
            svc.close()

    raise FileOperationError(f"Unsupported conversion path for '{validated.name}'.")