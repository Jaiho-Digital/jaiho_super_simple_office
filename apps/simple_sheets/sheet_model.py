"""Multi-sheet workbook model, dependency recalculation, and .xlsx / .csv I/O for SimpleSheets."""

from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape as xml_escape

from apps.simple_sheets.formula_engine import (
    CellCoord,
    CellScalar,
    evaluate_cell_input,
    extract_dependencies,
    format_cell_display,
    format_cell_ref,
    parse_cell_ref,
    parse_literal_value,
)
from core.file_manager.file_ops import (
    FileOperationError,
    atomic_write_bytes,
    atomic_write_text,
    validate_input_file,
)

APP_ID = "simple_sheets"

DEFAULT_WORKBOOK_EXTENSION = ".xlsx"
SUPPORTED_OPEN_EXTENSIONS: frozenset[str] = frozenset({".xlsx", ".csv", ".tsv"})

_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_M = f"{{{_MAIN_NS}}}"
_R = f"{{{_REL_NS}}}"
_PR = f"{{{_PKG_REL_NS}}}"


@dataclass(slots=True)
class CellData:
    """Mutable state and formatting metadata for a single spreadsheet cell."""

    raw_input: str = ""
    evaluated_value: CellScalar = None
    bold: bool = False
    italic: bool = False
    align: str = "auto"  # 'auto' | 'left' | 'center' | 'right'
    number_format: str = "general"  # 'general' | 'number' | 'currency' | 'percent' | 'integer'
    bg_tint: str = ""  # Optional pastel hex background tint (e.g. '#E8E0F7')

    @property
    def is_formula(self) -> bool:
        return self.raw_input.strip().startswith("=")

    @property
    def is_empty(self) -> bool:
        return (
            self.raw_input == ""
            and not self.bold
            and not self.italic
            and self.align == "auto"
            and self.number_format == "general"
            and self.bg_tint == ""
        )

    def display_text(self) -> str:
        return format_cell_display(self.evaluated_value, number_format=self.number_format)


@dataclass(frozen=True, slots=True)
class SelectionSummary:
    """Live status-bar aggregation metrics for currently selected grid cells."""

    non_empty_count: int
    numeric_count: int
    total: float | None
    average: float | None
    minimum: float | None
    maximum: float | None


class SheetModel:
    """Single worksheet containing sparse cells, dependency graph, and dimension overrides."""

    def __init__(self, name: str = "Sheet1", rows: int = 100, cols: int = 26) -> None:
        self.name: str = name.strip() or "Sheet1"
        self.rows: int = max(10, int(rows))
        self.cols: int = max(5, int(cols))
        self.cells: dict[CellCoord, CellData] = {}
        self.col_widths: dict[int, int] = {}
        self.row_heights: dict[int, int] = {}

        # Forward and reverse dependency maps for fast incremental recalculation
        self._deps_of: dict[CellCoord, set[CellCoord]] = {}
        self._dependents_of: dict[CellCoord, set[CellCoord]] = {}

    def ensure_capacity(self, min_rows: int, min_cols: int) -> None:
        """Expand grid dimensions if imported data exceeds default row/column counts."""
        if min_rows > self.rows:
            self.rows = min_rows
        if min_cols > self.cols:
            self.cols = min_cols

    def get_cell(self, row: int, col: int) -> CellData | None:
        """Return CellData at (row, col), or None if untouched."""
        return self.cells.get((row, col))

    def get_raw_input(self, row: int, col: int) -> str:
        cell = self.cells.get((row, col))
        return cell.raw_input if cell is not None else ""

    def get_evaluated_value(self, row: int, col: int) -> CellScalar:
        cell = self.cells.get((row, col))
        return cell.evaluated_value if cell is not None else None

    def get_display_text(self, row: int, col: int) -> str:
        cell = self.cells.get((row, col))
        return cell.display_text() if cell is not None else ""

    def _update_dependencies(self, coord: CellCoord, raw_input: str) -> None:
        """Refresh forward and reverse formula dependency edges for `coord`."""
        old_deps = self._deps_of.pop(coord, set())
        for prev_target in old_deps:
            rev = self._dependents_of.get(prev_target)
            if rev is not None:
                rev.discard(coord)
                if not rev:
                    del self._dependents_of[prev_target]

        new_deps = extract_dependencies(raw_input)
        if new_deps:
            self._deps_of[coord] = new_deps
            for target in new_deps:
                self._dependents_of.setdefault(target, set()).add(coord)

    def _collect_downstream_order(self, root: CellCoord) -> list[CellCoord]:
        """Return all transitive dependents of `root` in breadth-first evaluation order."""
        visited: set[CellCoord] = set()
        order: list[CellCoord] = []
        queue: list[CellCoord] = list(self._dependents_of.get(root, ()))
        while queue:
            curr = queue.pop(0)
            if curr in visited:
                continue
            visited.add(curr)
            order.append(curr)
            for nxt in self._dependents_of.get(curr, ()):
                if nxt not in visited:
                    queue.append(nxt)
        return order

    def _evaluate_single_cell(self, coord: CellCoord, active_stack: set[CellCoord]) -> CellScalar:
        """Evaluate `coord` recursively with circular-reference protection."""
        cell = self.cells.get(coord)
        if cell is None or cell.raw_input == "":
            return None

        if not cell.is_formula:
            cell.evaluated_value = parse_literal_value(cell.raw_input)
            return cell.evaluated_value

        if coord in active_stack:
            cell.evaluated_value = "#CYCLE!"
            return cell.evaluated_value

        active_stack.add(coord)
        try:
            def _resolve(ref_coord: CellCoord) -> CellScalar:
                if ref_coord in active_stack:
                    return "#CYCLE!"
                target_cell = self.cells.get(ref_coord)
                if target_cell is None:
                    return None
                if target_cell.is_formula:
                    return self._evaluate_single_cell(ref_coord, active_stack)
                return target_cell.evaluated_value

            cell.evaluated_value = evaluate_cell_input(cell.raw_input, _resolve)
            return cell.evaluated_value
        finally:
            active_stack.discard(coord)

    def set_cell_input(self, row: int, col: int, raw_input: str) -> set[CellCoord]:
        """Set raw input on (row, col), recalculate dependents, and return changed coords."""
        self.ensure_capacity(row + 1, col + 1)
        coord: CellCoord = (row, col)
        cell = self.cells.get(coord)
        if cell is None:
            if raw_input == "":
                return set()
            cell = CellData()
            self.cells[coord] = cell

        cell.raw_input = raw_input
        self._update_dependencies(coord, raw_input)

        changed: set[CellCoord] = {coord}
        self._evaluate_single_cell(coord, set())

        for dep_coord in self._collect_downstream_order(coord):
            self._evaluate_single_cell(dep_coord, set())
            changed.add(dep_coord)

        if cell.is_empty:
            self.cells.pop(coord, None)

        return changed

    def update_cell_style(
        self,
        row: int,
        col: int,
        *,
        bold: bool | None = None,
        italic: bool | None = None,
        align: str | None = None,
        number_format: str | None = None,
        bg_tint: str | None = None,
    ) -> CellData:
        """Apply formatting properties to (row, col), creating an empty styled cell if needed."""
        self.ensure_capacity(row + 1, col + 1)
        coord: CellCoord = (row, col)
        cell = self.cells.get(coord)
        if cell is None:
            cell = CellData()
            self.cells[coord] = cell

        if bold is not None:
            cell.bold = bool(bold)
        if italic is not None:
            cell.italic = bool(italic)
        if align is not None:
            cell.align = align
        if number_format is not None:
            cell.number_format = number_format
        if bg_tint is not None:
            cell.bg_tint = bg_tint

        if cell.is_empty:
            self.cells.pop(coord, None)
        return cell

    def clear_cell(self, row: int, col: int) -> set[CellCoord]:
        """Clear both value and formatting on (row, col) and recalculate dependents."""
        coord: CellCoord = (row, col)
        existing = self.cells.get(coord)
        if existing is None:
            return set()
        existing.bold = False
        existing.italic = False
        existing.align = "auto"
        existing.number_format = "general"
        existing.bg_tint = ""
        return self.set_cell_input(row, col, "")

    def recalculate_all(self) -> None:
        """Rebuild dependency graph and re-evaluate every populated cell in the sheet."""
        self._deps_of.clear()
        self._dependents_of.clear()
        for coord, cell in list(self.cells.items()):
            self._update_dependencies(coord, cell.raw_input)
        for coord in list(self.cells.keys()):
            self._evaluate_single_cell(coord, set())

    def used_bounds(self) -> tuple[int, int]:
        """Return (max_row_inclusive, max_col_inclusive) containing data, or (0, 0) if blank."""
        if not self.cells:
            return (0, 0)
        max_r = max(r for (r, _c) in self.cells)
        max_c = max(c for (_r, c) in self.cells)
        return (max_r, max_c)

    def compute_selection_summary(self, coords: list[CellCoord]) -> SelectionSummary:
        """Compute live Count, Numeric Count, Sum, Average, Min, and Max for `coords`."""
        non_empty = 0
        numbers: list[float] = []
        for coord in coords:
            cell = self.cells.get(coord)
            if cell is None or cell.evaluated_value is None or cell.evaluated_value == "":
                continue
            non_empty += 1
            val = cell.evaluated_value
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                numbers.append(float(val))

        if not numbers:
            return SelectionSummary(
                non_empty_count=non_empty,
                numeric_count=0,
                total=None,
                average=None,
                minimum=None,
                maximum=None,
            )

        total = sum(numbers)
        return SelectionSummary(
            non_empty_count=non_empty,
            numeric_count=len(numbers),
            total=total,
            average=total / float(len(numbers)),
            minimum=min(numbers),
            maximum=max(numbers),
        )


class WorkbookModel:
    """Container managing one or more named worksheets in SimpleSheets."""

    def __init__(self, sheets: list[SheetModel] | None = None) -> None:
        self.sheets: list[SheetModel] = sheets if sheets else [SheetModel(name="Sheet1")]
        self.active_index: int = 0

    @property
    def active_sheet(self) -> SheetModel:
        self.active_index = max(0, min(len(self.sheets) - 1, self.active_index))
        return self.sheets[self.active_index]

    def set_active_sheet(self, index: int) -> SheetModel:
        if not (0 <= index < len(self.sheets)):
            raise IndexError(f"Sheet index {index} out of range.")
        self.active_index = index
        return self.sheets[index]

    def add_sheet(self, name: str | None = None) -> SheetModel:
        existing_names = {s.name.lower() for s in self.sheets}
        if name and name.strip():
            candidate = name.strip()
        else:
            counter = len(self.sheets) + 1
            candidate = f"Sheet{counter}"
            while candidate.lower() in existing_names:
                counter += 1
                candidate = f"Sheet{counter}"

        sheet = SheetModel(name=candidate)
        self.sheets.append(sheet)
        self.active_index = len(self.sheets) - 1
        return sheet

    def rename_sheet(self, index: int, new_name: str) -> None:
        if not (0 <= index < len(self.sheets)):
            raise IndexError(f"Sheet index {index} out of range.")
        cleaned = re.sub(r"[\[\]:*?/\\]", "_", new_name.strip())[:31]
        if cleaned:
            self.sheets[index].name = cleaned

    def remove_sheet(self, index: int) -> bool:
        if len(self.sheets) <= 1 or not (0 <= index < len(self.sheets)):
            return False
        self.sheets.pop(index)
        self.active_index = max(0, min(len(self.sheets) - 1, self.active_index))
        return True


# ----------------------------------------------------------------------
# Native Multi-Sheet .xlsx (Excel OpenXML) & .csv Import / Export
# ----------------------------------------------------------------------


def _build_xlsx_bytes(workbook: WorkbookModel) -> bytes:
    """Serialize a multi-sheet WorkbookModel into a valid Excel .xlsx OpenXML archive."""
    shared_strings: list[str] = []
    shared_index: dict[str, int] = {}

    def _intern_string(text: str) -> int:
        idx = shared_index.get(text)
        if idx is None:
            idx = len(shared_strings)
            shared_strings.append(text)
            shared_index[text] = idx
        return idx

    # Build style lookup table: (bold, italic, bg_hex_no_hash, num_fmt_id) -> style_index
    # Base style 0 = normal default
    font_list: list[tuple[bool, bool]] = [(False, False)]
    font_map: dict[tuple[bool, bool], int] = {(False, False): 0}

    fill_list: list[str] = ["none", "gray125"]  # Excel requires fills 0 and 1 reserved
    fill_map: dict[str, int] = {"": 0}

    xf_list: list[tuple[int, int, int]] = [(0, 0, 0)]  # (fontId, fillId, numFmtId)
    xf_map: dict[tuple[int, int, int], int] = {(0, 0, 0): 0}

    num_fmt_code_map: dict[str, int] = {
        "general": 0,
        "integer": 3,      # #,##0
        "number": 4,       # #,##0.00
        "currency": 164,   # custom "$#,##0.00"
        "percent": 10,     # 0.00%
    }

    def _get_style_idx(cell: CellData) -> int:
        fkey = (cell.bold, cell.italic)
        fid = font_map.get(fkey)
        if fid is None:
            fid = len(font_list)
            font_list.append(fkey)
            font_map[fkey] = fid

        clean_bg = cell.bg_tint.strip().lstrip("#").upper()
        if len(clean_bg) != 6:
            clean_bg = ""
        fill_id = fill_map.get(clean_bg)
        if fill_id is None:
            fill_id = len(fill_list)
            fill_list.append(clean_bg)
            fill_map[clean_bg] = fill_id

        nf_id = num_fmt_code_map.get(cell.number_format, 0)
        xf_key = (fid, fill_id, nf_id)
        xf_id = xf_map.get(xf_key)
        if xf_id is None:
            xf_id = len(xf_list)
            xf_list.append(xf_key)
            xf_map[xf_key] = xf_id
        return xf_id

    sheet_xml_blobs: list[str] = []
    for sheet in workbook.sheets:
        # Group populated cells by row
        rows_map: dict[int, list[tuple[int, CellData]]] = {}
        for (r, c), cell in sorted(sheet.cells.items()):
            if not cell.is_empty:
                rows_map.setdefault(r, []).append((c, cell))

        cols_xml = ""
        if sheet.col_widths:
            col_entries: list[str] = []
            for c_idx, px_w in sorted(sheet.col_widths.items()):
                char_w = max(4.0, round(px_w / 7.5, 2))
                col_entries.append(
                    f'<col min="{c_idx + 1}" max="{c_idx + 1}" width="{char_w}" customWidth="1"/>'
                )
            cols_xml = f"<cols>{''.join(col_entries)}</cols>"

        row_xml_parts: list[str] = []
        for r_idx in sorted(rows_map.keys()):
            cell_parts: list[str] = []
            for c_idx, cell in rows_map[r_idx]:
                ref = format_cell_ref(r_idx, c_idx)
                s_idx = _get_style_idx(cell)
                s_attr = f' s="{s_idx}"' if s_idx > 0 else ""
                val = cell.evaluated_value

                if cell.is_formula:
                    formula_body = xml_escape(cell.raw_input.strip()[1:])
                    if isinstance(val, bool):
                        b_num = "1" if val else "0"
                        cell_parts.append(
                            f'<c r="{ref}" t="b"{s_attr}><f>{formula_body}</f><v>{b_num}</v></c>'
                        )
                    elif isinstance(val, (int, float)):
                        cell_parts.append(
                            f'<c r="{ref}"{s_attr}><f>{formula_body}</f><v>{val}</v></c>'
                        )
                    else:
                        str_cached = xml_escape(str(val or ""))
                        cell_parts.append(
                            f'<c r="{ref}" t="str"{s_attr}><f>{formula_body}</f><v>{str_cached}</v></c>'
                        )
                else:
                    if val is None or val == "":
                        if s_idx > 0:
                            cell_parts.append(f'<c r="{ref}"{s_attr}/>')
                    elif isinstance(val, bool):
                        b_num = "1" if val else "0"
                        cell_parts.append(f'<c r="{ref}" t="b"{s_attr}><v>{b_num}</v></c>')
                    elif isinstance(val, (int, float)):
                        cell_parts.append(f'<c r="{ref}"{s_attr}><v>{val}</v></c>')
                    else:
                        sst_id = _intern_string(str(val))
                        cell_parts.append(f'<c r="{ref}" t="s"{s_attr}><v>{sst_id}</v></c>')

            if cell_parts:
                row_xml_parts.append(f'<row r="{r_idx + 1}">{"".join(cell_parts)}</row>')

        sheet_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<worksheet xmlns="{_MAIN_NS}">'
            f"{cols_xml}"
            f"<sheetData>{''.join(row_xml_parts)}</sheetData>"
            "</worksheet>\n"
        )
        sheet_xml_blobs.append(sheet_xml)

    # Assemble styles.xml
    fonts_xml = "".join(
        "<font>"
        f"{'<b/>' if b else ''}"
        f"{'<i/>' if it else ''}"
        '<sz val="11"/><name val="Calibri"/>'
        "</font>"
        for (b, it) in font_list
    )
    fills_xml_items: list[str] = []
    for fill_spec in fill_list:
        if fill_spec in {"none", "gray125"}:
            fills_xml_items.append(f'<fill><patternFill patternType="{fill_spec}"/></fill>')
        else:
            fills_xml_items.append(
                '<fill><patternFill patternType="solid">'
                f'<fgColor rgb="FF{fill_spec}"/><bgColor indexed="64"/>'
                "</patternFill></fill>"
            )
    xfs_xml = "".join(
        f'<xf numFmtId="{nf_id}" fontId="{f_id}" fillId="{fl_id}" borderId="0" xfId="0"'
        + (' applyFont="1"' if f_id > 0 else "")
        + (' applyFill="1"' if fl_id > 0 else "")
        + (' applyNumberFormat="1"' if nf_id > 0 else "")
        + "/>"
        for (f_id, fl_id, nf_id) in xf_list
    )

    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<styleSheet xmlns="{_MAIN_NS}">'
        '<numFmts count="1"><numFmt numFmtId="164" formatCode="&quot;$&quot;#,##0.00"/></numFmts>'
        f'<fonts count="{len(font_list)}">{fonts_xml}</fonts>'
        f'<fills count="{len(fill_list)}">{"".join(fills_xml_items)}</fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        f'<cellXfs count="{len(xf_list)}">{xfs_xml}</cellXfs>'
        "</styleSheet>\n"
    )

    sst_items = "".join(
        f'<si><t xml:space="preserve">{xml_escape(s)}</t></si>' for s in shared_strings
    )
    shared_strings_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<sst xmlns="{_MAIN_NS}" count="{len(shared_strings)}" uniqueCount="{len(shared_strings)}">'
        f"{sst_items}</sst>\n"
    )

    sheets_decl = "".join(
        f'<sheet name="{xml_escape(s.name)}" sheetId="{idx + 1}" r:id="rId{idx + 1}"/>'
        for idx, s in enumerate(workbook.sheets)
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<workbook xmlns="{_MAIN_NS}" xmlns:r="{_REL_NS}">'
        f"<sheets>{sheets_decl}</sheets>"
        "</workbook>\n"
    )

    wb_rels_items: list[str] = []
    for idx in range(len(workbook.sheets)):
        wb_rels_items.append(
            f'<Relationship Id="rId{idx + 1}" '
            f'Type="{_REL_NS}/worksheet" Target="worksheets/sheet{idx + 1}.xml"/>'
        )
    styles_rid = len(workbook.sheets) + 1
    sst_rid = len(workbook.sheets) + 2
    wb_rels_items.append(
        f'<Relationship Id="rId{styles_rid}" Type="{_REL_NS}/styles" Target="styles.xml"/>'
    )
    wb_rels_items.append(
        f'<Relationship Id="rId{sst_rid}" Type="{_REL_NS}/sharedStrings" Target="sharedStrings.xml"/>'
    )
    workbook_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<Relationships xmlns="{_PKG_REL_NS}">{"".join(wb_rels_items)}</Relationships>\n'
    )

    sheet_overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{idx + 1}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for idx in range(len(workbook.sheets))
    )
    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        f"{sheet_overrides}"
        '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '<Override PartName="/xl/sharedStrings.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
        "</Types>\n"
    )

    root_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<Relationships xmlns="{_PKG_REL_NS}">'
        f'<Relationship Id="rId1" Type="{_REL_NS}/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>\n"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types_xml)
        zf.writestr("_rels/.rels", root_rels_xml)
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
        zf.writestr("xl/styles.xml", styles_xml)
        zf.writestr("xl/sharedStrings.xml", shared_strings_xml)
        for idx, blob in enumerate(sheet_xml_blobs):
            zf.writestr(f"xl/worksheets/sheet{idx + 1}.xml", blob)

    return buf.getvalue()


def _parse_xlsx_bytes(xlsx_path: Path) -> WorkbookModel:
    """Load a multi-sheet .xlsx archive into a hydrated WorkbookModel."""
    try:
        with zipfile.ZipFile(xlsx_path, mode="r") as zf:
            names = set(zf.namelist())
            if "xl/workbook.xml" not in names:
                raise FileOperationError(f"'{xlsx_path.name}' is missing 'xl/workbook.xml'.")

            # 1. Shared strings table (optional)
            shared_strings: list[str] = []
            if "xl/sharedStrings.xml" in names:
                sst_root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
                for si in sst_root.findall(f"{_M}si"):
                    text_runs = [t.text or "" for t in si.findall(f".//{_M}t")]
                    shared_strings.append("".join(text_runs))

            # 2. Styles table (bold, italic, pastel fill, number format)
            xf_styles: list[tuple[bool, bool, str, str]] = []
            if "xl/styles.xml" in names:
                st_root = ET.fromstring(zf.read("xl/styles.xml"))
                fonts: list[tuple[bool, bool]] = []
                for f_el in st_root.findall(f".//{_M}fonts/{_M}font"):
                    is_b = f_el.find(f"{_M}b") is not None
                    is_i = f_el.find(f"{_M}i") is not None
                    fonts.append((is_b, is_i))

                fills: list[str] = []
                for fl_el in st_root.findall(f".//{_M}fills/{_M}fill"):
                    fg = fl_el.find(f".//{_M}fgColor")
                    rgb = fg.attrib.get("rgb", "") if fg is not None else ""
                    hex_tint = f"#{rgb[-6:]}" if len(rgb) >= 6 and rgb[-6:].upper() != "000000" else ""
                    fills.append(hex_tint)

                for xf_el in st_root.findall(f".//{_M}cellXfs/{_M}xf"):
                    f_id = int(xf_el.attrib.get("fontId", "0") or 0)
                    fl_id = int(xf_el.attrib.get("fillId", "0") or 0)
                    nf_id = int(xf_el.attrib.get("numFmtId", "0") or 0)
                    b_flag, i_flag = fonts[f_id] if 0 <= f_id < len(fonts) else (False, False)
                    bg_hex = fills[fl_id] if 0 <= fl_id < len(fills) else ""
                    nf_name = "general"
                    if nf_id in {9, 10}:
                        nf_name = "percent"
                    elif nf_id in {5, 6, 7, 8, 44, 164}:
                        nf_name = "currency"
                    elif nf_id in {2, 4}:
                        nf_name = "number"
                    elif nf_id in {1, 3}:
                        nf_name = "integer"
                    xf_styles.append((b_flag, i_flag, bg_hex, nf_name))

            # 3. Map relationship IDs -> worksheet ZIP paths
            rid_to_target: dict[str, str] = {}
            if "xl/_rels/workbook.xml.rels" in names:
                rels_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
                for rel in rels_root.findall(f"{_PR}Relationship"):
                    rid = rel.attrib.get("Id", "")
                    target = rel.attrib.get("Target", "").lstrip("/")
                    if not target.startswith("xl/"):
                        target = f"xl/{target}"
                    rid_to_target[rid] = target

            wb_root = ET.fromstring(zf.read("xl/workbook.xml"))
            sheet_elems = wb_root.findall(f".//{_M}sheets/{_M}sheet")
            loaded_sheets: list[SheetModel] = []

            for idx, s_el in enumerate(sheet_elems):
                s_name = s_el.attrib.get("name", f"Sheet{idx + 1}")
                rid = s_el.attrib.get(f"{_R}id", "")
                target_path = rid_to_target.get(rid, f"xl/worksheets/sheet{idx + 1}.xml")
                sheet = SheetModel(name=s_name)

                if target_path in names:
                    ws_root = ET.fromstring(zf.read(target_path))
                    for c_el in ws_root.findall(f".//{_M}sheetData/{_M}row/{_M}c"):
                        ref = c_el.attrib.get("r", "")
                        if not ref:
                            continue
                        try:
                            row_idx, col_idx = parse_cell_ref(ref)
                        except Exception:
                            continue

                        cell_type = c_el.attrib.get("t", "")
                        style_idx = int(c_el.attrib.get("s", "0") or 0)
                        f_el = c_el.find(f"{_M}f")
                        v_el = c_el.find(f"{_M}v")
                        is_el = c_el.find(f"{_M}is/{_M}t")

                        raw_str = ""
                        if f_el is not None and f_el.text is not None:
                            raw_str = f"={f_el.text}"
                        elif cell_type == "s" and v_el is not None and v_el.text is not None:
                            s_pos = int(v_el.text)
                            raw_str = shared_strings[s_pos] if 0 <= s_pos < len(shared_strings) else ""
                        elif cell_type == "inlineStr" and is_el is not None and is_el.text is not None:
                            raw_str = is_el.text
                        elif cell_type == "b" and v_el is not None and v_el.text is not None:
                            raw_str = "TRUE" if v_el.text.strip() == "1" else "FALSE"
                        elif v_el is not None and v_el.text is not None:
                            raw_str = v_el.text

                        sheet.ensure_capacity(row_idx + 1, col_idx + 1)
                        cell_obj = CellData(raw_input=raw_str)
                        if 0 <= style_idx < len(xf_styles):
                            b_f, i_f, bg_h, nf_n = xf_styles[style_idx]
                            cell_obj.bold = b_f
                            cell_obj.italic = i_f
                            cell_obj.bg_tint = bg_h
                            cell_obj.number_format = nf_n
                        if not cell_obj.is_empty:
                            sheet.cells[(row_idx, col_idx)] = cell_obj

                sheet.recalculate_all()
                loaded_sheets.append(sheet)

            return WorkbookModel(sheets=loaded_sheets or None)
    except zipfile.BadZipFile as exc:
        raise FileOperationError(f"'{xlsx_path.name}' is not a valid .xlsx workbook.") from exc
    except ET.ParseError as exc:
        raise FileOperationError(f"Corrupt XML inside '{xlsx_path.name}': {exc}") from exc


def save_workbook(
    destination: str | Path,
    workbook: WorkbookModel,
    sheet_index: int | None = None,
) -> Path:
    """Atomically save `workbook` to .xlsx (default, multi-sheet) or .csv / .tsv."""
    target = Path(destination)
    if not target.suffix:
        target = target.with_suffix(DEFAULT_WORKBOOK_EXTENSION)

    suffix = target.suffix.lower()
    if suffix == ".xlsx":
        payload = _build_xlsx_bytes(workbook)
        return atomic_write_bytes(target, payload, backup_app_id=APP_ID)

    if suffix in {".csv", ".tsv"}:
        delimiter = "\t" if suffix == ".tsv" else ","
        sheet = (
            workbook.sheets[sheet_index]
            if sheet_index is not None and 0 <= sheet_index < len(workbook.sheets)
            else workbook.active_sheet
        )
        max_r, max_c = sheet.used_bounds()
        buf = io.StringIO()
        writer = csv.writer(buf, delimiter=delimiter, lineterminator="\n")
        if sheet.cells:
            for r in range(max_r + 1):
                row_vals = [sheet.get_display_text(r, c) for c in range(max_c + 1)]
                writer.writerow(row_vals)
        return atomic_write_text(target, buf.getvalue(), backup_app_id=APP_ID)

    raise FileOperationError(
        f"Unsupported spreadsheet extension '{suffix}'. Expected .xlsx, .csv, or .tsv."
    )


def load_workbook(source_path: str | Path) -> WorkbookModel:
    """Validate and load a .xlsx, .csv, or .tsv file into a WorkbookModel."""
    validated = validate_input_file(
        source_path,
        allowed_extensions=SUPPORTED_OPEN_EXTENSIONS,
        max_size_mb=25.0,
    )
    suffix = validated.suffix.lower()
    if suffix == ".xlsx":
        return _parse_xlsx_bytes(validated)

    delimiter = "\t" if suffix == ".tsv" else ","
    raw_text = validated.read_text(encoding="utf-8", errors="replace")
    sheet_title = re.sub(r"[\[\]:*?/\\]", "_", validated.stem)[:31] or "Sheet1"
    sheet = SheetModel(name=sheet_title)

    reader = csv.reader(io.StringIO(raw_text), delimiter=delimiter)
    for r_idx, row in enumerate(reader):
        for c_idx, val in enumerate(row):
            if val != "":
                sheet.ensure_capacity(r_idx + 1, c_idx + 1)
                sheet.cells[(r_idx, c_idx)] = CellData(raw_input=val)

    sheet.recalculate_all()
    return WorkbookModel(sheets=[sheet])