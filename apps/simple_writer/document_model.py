"""Document serialization, metrics, and native .docx / .md / .html / .txt I/O for SimpleWriter."""

from __future__ import annotations

import io
import math
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape as xml_escape

from PySide6.QtGui import (
    QColor,
    QFont,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
)

from core.file_manager.file_ops import (
    FileOperationError,
    atomic_write_bytes,
    atomic_write_text,
    validate_input_file,
)

APP_ID = "simple_writer"

DEFAULT_DOCUMENT_EXTENSION = ".docx"

SUPPORTED_OPEN_EXTENSIONS: frozenset[str] = frozenset(
    {".docx", ".md", ".markdown", ".html", ".htm", ".txt"}
)

_WORD_RE = re.compile(r"\b\w+(?:['’-]\w+)*\b", re.UNICODE)

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W = f"{{{_W_NS}}}"


@dataclass(frozen=True, slots=True)
class DocumentStats:
    """Live readability and length telemetry for a SimpleWriter document."""

    words: int
    characters: int
    characters_no_spaces: int
    paragraphs: int
    reading_time_minutes: int


def compute_document_stats(plain_text: str) -> DocumentStats:
    """Compute word, character, paragraph, and reading-time counts from plain text."""
    stripped = plain_text.strip()
    if not stripped:
        return DocumentStats(
            words=0,
            characters=0,
            characters_no_spaces=0,
            paragraphs=0,
            reading_time_minutes=0,
        )

    words = len(_WORD_RE.findall(plain_text))
    characters = len(plain_text)
    characters_no_spaces = sum(1 for ch in plain_text if not ch.isspace())
    paragraphs = sum(1 for line in plain_text.splitlines() if line.strip())
    reading_time = max(1, math.ceil(words / 225)) if words > 0 else 0

    return DocumentStats(
        words=words,
        characters=characters,
        characters_no_spaces=characters_no_spaces,
        paragraphs=max(1, paragraphs),
        reading_time_minutes=reading_time,
    )


def _package_ooxml_paragraphs(paragraphs_xml: list[str]) -> bytes:
    """Wrap OOXML `<w:p>` elements into a standards-compliant .docx ZIP archive."""
    if not paragraphs_xml:
        paragraphs_xml.append("<w:p/>")

    content_types_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml"
            ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""

    rels_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
                Target="word/document.xml"/>
</Relationships>
"""

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">\n'
        "  <w:body>\n"
        f"    {''.join(paragraphs_xml)}\n"
        "    <w:sectPr>\n"
        '      <w:pgSz w:w="12240" w:h="15840"/>\n'
        '      <w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/>\n'
        "    </w:sectPr>\n"
        "  </w:body>\n"
        "</w:document>\n"
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types_xml)
        zf.writestr("_rels/.rels", rels_xml)
        zf.writestr("word/document.xml", document_xml)

    return buffer.getvalue()


def build_minimal_docx_bytes(title: str, plain_text: str) -> bytes:
    """Generate a valid OOXML .docx archive from plain text lines."""
    paragraphs_xml: list[str] = []

    clean_title = title.strip()
    if clean_title:
        escaped_title = xml_escape(clean_title)
        paragraphs_xml.append(
            "<w:p>"
            '<w:pPr><w:spacing w:after="200"/></w:pPr>'
            '<w:r><w:rPr><w:b/><w:sz w:val="36"/><w:szCs w:val="36"/></w:rPr>'
            f'<w:t xml:space="preserve">{escaped_title}</w:t></w:r>'
            "</w:p>"
        )

    for line in plain_text.replace("\r\n", "\n").split("\n"):
        escaped_line = xml_escape(line)
        paragraphs_xml.append(
            "<w:p>"
            '<w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/></w:pPr>'
            f'<w:r><w:t xml:space="preserve">{escaped_line}</w:t></w:r>'
            "</w:p>"
        )

    return _package_ooxml_paragraphs(paragraphs_xml)


def build_docx_bytes_from_qtext(source_doc: QTextDocument) -> bytes:
    """Serialize a rich QTextDocument (headings, bold, italic, underline, lists, colors) to .docx."""
    paragraphs_xml: list[str] = []
    block = source_doc.begin()

    while block.isValid():
        block_fmt = block.blockFormat()
        heading_lvl = block_fmt.headingLevel()
        text_list = block.textList()

        p_props: list[str] = ['<w:spacing w:after="140" w:line="276" w:lineRule="auto"/>']
        if heading_lvl > 0:
            p_props.append("<w:keepNext/>")
        if text_list is not None or block_fmt.indent() > 0:
            indent_twips = max(720, (block_fmt.indent() + (1 if text_list else 0)) * 720)
            p_props.append(f'<w:ind w:left="{indent_twips}"/>')

        runs_xml: list[str] = []

        # Prefix list marker if block belongs to a bulleted or numbered list
        if text_list is not None:
            idx_in_list = text_list.itemNumber(block)
            marker = f"{idx_in_list + 1}. " if idx_in_list >= 0 else "• "
            if text_list.format().style().value < 0:
                marker = "• "
            runs_xml.append(f'<w:r><w:t xml:space="preserve">{xml_escape(marker)}</w:t></w:r>')

        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            if fragment.isValid():
                frag_text = fragment.text().replace("\u2028", "\n").replace("\ufdd0", "")
                if frag_text:
                    cfmt = fragment.charFormat()
                    r_props: list[str] = []

                    is_bold = cfmt.fontWeight() >= QFont.Weight.Bold or heading_lvl > 0
                    if is_bold:
                        r_props.append("<w:b/>")
                    if cfmt.fontItalic():
                        r_props.append("<w:i/>")
                    if cfmt.fontUnderline():
                        r_props.append('<w:u w:val="single"/>')
                    if cfmt.fontStrikeOut():
                        r_props.append("<w:strike/>")

                    pt_size = cfmt.fontPointSize()
                    if heading_lvl == 1 and pt_size <= 0:
                        pt_size = 20.0
                    elif heading_lvl == 2 and pt_size <= 0:
                        pt_size = 16.0
                    elif heading_lvl == 3 and pt_size <= 0:
                        pt_size = 13.5

                    if pt_size > 0:
                        half_pts = max(16, int(round(pt_size * 2)))
                        r_props.append(f'<w:sz w:val="{half_pts}"/><w:szCs w:val="{half_pts}"/>')

                    fg_color = cfmt.foreground().color()
                    if fg_color.isValid() and fg_color.name().lower() not in {"#000000", "#35343b"}:
                        hex_rgb = fg_color.name().lstrip("#").upper()
                        r_props.append(f'<w:color w:val="{hex_rgb}"/>')

                    rpr_xml = f"<w:rPr>{''.join(r_props)}</w:rPr>" if r_props else ""
                    runs_xml.append(
                        f'<w:r>{rpr_xml}<w:t xml:space="preserve">{xml_escape(frag_text)}</w:t></w:r>'
                    )
            it += 1

        paragraphs_xml.append(f"<w:p><w:pPr>{''.join(p_props)}</w:pPr>{''.join(runs_xml)}</w:p>")
        block = block.next()

    return _package_ooxml_paragraphs(paragraphs_xml)


def load_docx_into_qtext(docx_path: Path, target_doc: QTextDocument) -> str:
    """Read word/document.xml from a .docx archive and populate `target_doc` with rich formatting."""
    try:
        with zipfile.ZipFile(docx_path, mode="r") as zf:
            if "word/document.xml" not in zf.namelist():
                raise FileOperationError(
                    f"'{docx_path.name}' is missing 'word/document.xml' and cannot be opened."
                )
            xml_bytes = zf.read("word/document.xml")
    except zipfile.BadZipFile as exc:
        raise FileOperationError(f"'{docx_path.name}' is not a valid .docx archive.") from exc

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise FileOperationError(f"Could not parse XML inside '{docx_path.name}': {exc}") from exc

    target_doc.clear()
    cursor = QTextCursor(target_doc)
    cursor.beginEditBlock()

    body = root.find(f"{_W}body")
    paragraphs = body.findall(f"{_W}p") if body is not None else root.findall(f".//{_W}p")

    inferred_title = (
        docx_path.stem.replace("_", " ").replace("-", " ").strip().title()
        or "Untitled Document"
    )

    for p_idx, p_elem in enumerate(paragraphs):
        if p_idx > 0:
            cursor.insertBlock(QTextBlockFormat(), QTextCharFormat())

        for r_elem in p_elem.findall(f".//{_W}r"):
            rpr = r_elem.find(f"{_W}rPr")
            char_fmt = QTextCharFormat()

            if rpr is not None:
                if rpr.find(f"{_W}b") is not None:
                    char_fmt.setFontWeight(QFont.Weight.Bold)
                if rpr.find(f"{_W}i") is not None:
                    char_fmt.setFontItalic(True)
                if rpr.find(f"{_W}u") is not None:
                    char_fmt.setFontUnderline(True)
                if rpr.find(f"{_W}strike") is not None:
                    char_fmt.setFontStrikeOut(True)

                sz_elem = rpr.find(f"{_W}sz")
                if sz_elem is not None:
                    val_attr = sz_elem.attrib.get(f"{_W}val") or sz_elem.attrib.get("val")
                    if val_attr and val_attr.isdigit():
                        char_fmt.setFontPointSize(max(8.0, int(val_attr) / 2.0))

                col_elem = rpr.find(f"{_W}color")
                if col_elem is not None:
                    col_val = col_elem.attrib.get(f"{_W}val") or col_elem.attrib.get("val")
                    if col_val and len(col_val) == 6 and col_val.lower() != "auto":
                        qcol = QColor(f"#{col_val}")
                        if qcol.isValid():
                            char_fmt.setForeground(qcol)

            run_chunks: list[str] = []
            for child in r_elem:
                if child.tag == f"{_W}t" and child.text:
                    run_chunks.append(child.text)
                elif child.tag == f"{_W}tab":
                    run_chunks.append("\t")
                elif child.tag == f"{_W}br":
                    run_chunks.append("\n")

            if run_chunks:
                cursor.insertText("".join(run_chunks), char_fmt)

    cursor.endEditBlock()
    return inferred_title


def load_document_into_qtext(source_path: str | Path, target_doc: QTextDocument) -> tuple[Path, str]:
    """Validate and load a .docx, .md, .html, or .txt file into `target_doc`."""
    validated = validate_input_file(
        source_path,
        allowed_extensions=SUPPORTED_OPEN_EXTENSIONS,
        max_size_mb=25.0,
    )
    suffix = validated.suffix.lower()

    if suffix == ".docx":
        inferred_title = load_docx_into_qtext(validated, target_doc)
        return validated, inferred_title

    raw_text = validated.read_text(encoding="utf-8", errors="replace")
    inferred_title = validated.stem.replace("_", " ").replace("-", " ").strip() or "Untitled Document"

    if suffix in {".html", ".htm"}:
        target_doc.setHtml(raw_text)
        title_match = re.search(r"<title>(.*?)</title>", raw_text, flags=re.IGNORECASE | re.DOTALL)
        if title_match and title_match.group(1).strip():
            inferred_title = title_match.group(1).strip()
    elif suffix in {".md", ".markdown"}:
        lines = raw_text.splitlines()
        if lines and lines[0].startswith("# "):
            extracted = lines[0][2:].strip()
            if extracted:
                inferred_title = extracted
                body_md = "\n".join(lines[1:]).lstrip("\n")
                target_doc.setMarkdown(body_md)
            else:
                target_doc.setMarkdown(raw_text)
        else:
            target_doc.setMarkdown(raw_text)
    else:
        target_doc.setPlainText(raw_text)

    return validated, inferred_title


def save_qtext_document(
    destination: str | Path,
    title: str,
    source_doc: QTextDocument,
) -> Path:
    """Atomically save `source_doc` to .docx (default), .md, .html, or .txt."""
    target = Path(destination)
    if not target.suffix:
        target = target.with_suffix(DEFAULT_DOCUMENT_EXTENSION)

    suffix = target.suffix.lower()
    clean_title = title.strip() or "Untitled Document"

    if suffix == ".docx":
        docx_bytes = build_docx_bytes_from_qtext(source_doc)
        return atomic_write_bytes(target, docx_bytes, backup_app_id=APP_ID)

    if suffix in {".md", ".markdown"}:
        body_md = source_doc.toMarkdown().strip()
        payload = f"# {clean_title}\n\n{body_md}\n" if body_md else f"# {clean_title}\n"
        return atomic_write_text(target, payload, backup_app_id=APP_ID)

    if suffix in {".html", ".htm"}:
        body_html = source_doc.toHtml()
        escaped_title = xml_escape(clean_title)
        if "<title>" in body_html.lower():
            body_html = re.sub(
                r"<title>.*?</title>",
                f"<title>{escaped_title}</title>",
                body_html,
                count=1,
                flags=re.IGNORECASE | re.DOTALL,
            )
        return atomic_write_text(target, body_html, backup_app_id=APP_ID)

    if suffix == ".txt":
        plain = source_doc.toPlainText().strip()
        underline = "=" * len(clean_title)
        payload = f"{clean_title}\n{underline}\n\n{plain}\n" if plain else f"{clean_title}\n"
        return atomic_write_text(target, payload, backup_app_id=APP_ID)

    raise FileOperationError(
        f"Unsupported export extension '{suffix}'. Expected .docx, .md, .html, or .txt."
    )


def format_saved_timestamp() -> str:
    """Return a clean human-readable save timestamp."""
    return datetime.now().strftime("%H:%M")