"""
Report generation -- PDF (ReportLab), Excel (OpenPyXL) and CSV.

Every report carries the same institutional header: college logo, college name
and address, report title, the filters that produced it, the generation
timestamp and the name of the user who generated it.  That header is built once
in :class:`_ReportHeader` and reused by all eleven report types, so branding
changes in Settings propagate everywhere automatically.

File names follow the specification, e.g.
``AttendanceReport_Sem4_CSE_Aug2026.pdf``.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (Image, PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

from config.settings import (REPORT_CSV_DIR, REPORT_EXCEL_DIR, REPORT_PDF_DIR,
                             config)
from core.audit import ACTION_REPORT, log_audit
from core.database import get_db
from core.logger import get_logger
from core.validators import safe_filename

logger = get_logger("services.report")

# ---------------------------------------------------------------------------
# Palette -- mirrors config/theme.py so printed output matches the screen
# ---------------------------------------------------------------------------
NAVY = colors.HexColor("#0F2A4A")
BLUE = colors.HexColor("#1D4ED8")
LIGHT_BLUE = colors.HexColor("#DBEAFE")
GREY = colors.HexColor("#64748B")
LIGHT_GREY = colors.HexColor("#F1F5F9")
GREEN = colors.HexColor("#16A34A")
RED = colors.HexColor("#DC2626")
AMBER = colors.HexColor("#F59E0B")

STATUS_FILL = {
    "Present": "C6F6D5", "Absent": "FED7D7", "Late": "FEEBC8",
    "Leave": "BEE3F8", "Medical Leave": "E9D8FD",
}


# ===========================================================================
# Shared helpers
# ===========================================================================
def _styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("title", parent=base["Title"], fontSize=16,
                                textColor=NAVY, spaceAfter=2, alignment=TA_CENTER),
        "college": ParagraphStyle("college", parent=base["Normal"], fontSize=15,
                                  textColor=NAVY, alignment=TA_CENTER,
                                  fontName="Helvetica-Bold", spaceAfter=1),
        "address": ParagraphStyle("address", parent=base["Normal"], fontSize=8,
                                  textColor=GREY, alignment=TA_CENTER, spaceAfter=1),
        "subtitle": ParagraphStyle("subtitle", parent=base["Normal"], fontSize=10,
                                   textColor=BLUE, alignment=TA_CENTER,
                                   fontName="Helvetica-Bold"),
        "meta": ParagraphStyle("meta", parent=base["Normal"], fontSize=8,
                               textColor=GREY),
        "body": ParagraphStyle("body", parent=base["Normal"], fontSize=9),
        "cell": ParagraphStyle("cell", parent=base["Normal"], fontSize=8, leading=10),
        "footer": ParagraphStyle("footer", parent=base["Normal"], fontSize=7,
                                 textColor=GREY, alignment=TA_CENTER),
    }


def _describe_filters(filters: dict | None) -> str:
    """Render the applied filters as a readable one-liner for the header."""
    if not filters:
        return "All records"
    parts = [f"{key.replace('_', ' ').title()}: {value}"
             for key, value in filters.items()
             if value not in (None, "", "All", 0)]
    return "  |  ".join(parts) if parts else "All records"


def _report_filename(report_type: str, extension: str,
                     filters: dict | None = None) -> str:
    """``AttendanceReport_Sem4_CSE_Aug2026.pdf`` style naming.

    Every component is stripped of whitespace, because a date range filter
    reads as ``2026-06-28 to 2026-07-28`` and spaces in a filename make the
    file awkward to reference from a command line or a script.
    """
    def _token(value) -> str:
        """Collapse a filter value into a single filename-safe token."""
        text = str(value).strip()
        # "Semester 4" -> "Sem4",  "2026-06-28 to 2026-07-28" -> "2026-06-28_to_2026-07-28"
        text = text.replace("Semester ", "Sem").replace(" to ", "_to_")
        text = "".join(text.split())          # drop any remaining whitespace
        return safe_filename(text)

    parts = [safe_filename("".join(report_type.split()))]
    filters = filters or {}

    for key in ("semester", "branch", "section", "subject"):
        value = filters.get(key)
        if value and value != "All":
            parts.append(_token(value))

    period = filters.get("period") or filters.get("month")
    parts.append(_token(period) if period else datetime.now().strftime("%b%Y"))

    return "_".join(p for p in parts if p) + extension


def _record_history(report_type: str, file_format: str, path: Path,
                    filters: dict | None, user: dict | None) -> None:
    try:
        get_db().insert("report_history", {
            "report_type": report_type, "file_format": file_format,
            "file_path": str(path),
            "parameters": json.dumps(filters or {}, default=str),
            "generated_by": (user or {}).get("user_id"),
        })
    except Exception as exc:                    # noqa: BLE001
        logger.warning("Could not record report history: %s", exc)

    log_audit(user, ACTION_REPORT, "Reports", "report_history", None,
              new_value={"type": report_type, "format": file_format,
                         "file": path.name})


class _ReportHeader:
    """Draws the branded header/footer on every PDF page."""

    def __init__(self, title: str, subtitle: str, generated_by: str) -> None:
        self.title = title
        self.subtitle = subtitle
        self.generated_by = generated_by
        self.generated_at = datetime.now().strftime("%d %b %Y, %I:%M %p")

    def flowables(self, page_width: float) -> list:
        styles = _styles()
        items: list = []

        logo_path = Path(config.get("logo_path", ""))
        header_cells = []

        if logo_path.exists():
            try:
                header_cells.append(Image(str(logo_path), width=20 * mm, height=20 * mm))
            except Exception:                   # noqa: BLE001 - bad image must not break the report
                header_cells.append("")
        else:
            header_cells.append("")

        institution = [
            Paragraph(config.get("college_name", "College"), styles["college"]),
            Paragraph(config.get("college_address", ""), styles["address"]),
            Paragraph(
                f"Phone: {config.get('college_phone','')}  |  "
                f"Email: {config.get('college_email','')}  |  "
                f"Code: {config.get('college_code','')}", styles["address"]),
        ]
        header_cells.append(institution)
        header_cells.append("")     # spacer column balances the logo

        header_table = Table([header_cells], colWidths=[24 * mm, page_width - 48 * mm, 24 * mm])
        header_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ]))
        items.append(header_table)

        items.append(Spacer(1, 3 * mm))
        items.append(Table([[""]], colWidths=[page_width],
                           style=TableStyle([("LINEBELOW", (0, 0), (-1, -1), 1.2, NAVY)])))
        items.append(Spacer(1, 3 * mm))

        items.append(Paragraph(self.title, styles["title"]))
        if self.subtitle:
            items.append(Paragraph(self.subtitle, styles["subtitle"]))
        items.append(Spacer(1, 2 * mm))

        meta = Table([[
            Paragraph(f"<b>Generated on:</b> {self.generated_at}", styles["meta"]),
            Paragraph(f"<b>Generated by:</b> {self.generated_by}", styles["meta"]),
            Paragraph(f"<b>Session:</b> {config.get('current_academic_session','')}",
                      styles["meta"]),
        ]], colWidths=[page_width / 3] * 3)
        meta.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), LIGHT_GREY),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ]))
        items.append(meta)
        items.append(Spacer(1, 4 * mm))
        return items

    def page_decoration(self, canvas, doc) -> None:
        """Footer with page number and signature line -- drawn on every page."""
        canvas.saveState()
        width, height = doc.pagesize

        canvas.setStrokeColor(GREY)
        canvas.setLineWidth(0.4)
        canvas.line(15 * mm, 14 * mm, width - 15 * mm, 14 * mm)

        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(GREY)
        canvas.drawString(15 * mm, 10 * mm, config.get("college_name", ""))
        canvas.drawCentredString(
            width / 2, 10 * mm,
            "Computer-generated report - Smart Attendance Management System")
        canvas.drawRightString(width - 15 * mm, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()


def _build_pdf(path: Path, header: _ReportHeader, body: list,
               landscape_mode: bool = False) -> None:
    pagesize = landscape(A4) if landscape_mode else A4
    doc = SimpleDocTemplate(
        str(path), pagesize=pagesize,
        leftMargin=12 * mm, rightMargin=12 * mm,
        topMargin=12 * mm, bottomMargin=18 * mm,
        title=header.title, author=config.get("college_name", ""))

    page_width = pagesize[0] - 24 * mm
    story = header.flowables(page_width) + body
    doc.build(story, onFirstPage=header.page_decoration,
              onLaterPages=header.page_decoration)


def _data_table(columns: list[str], rows: list[list], page_width: float,
                col_widths: list[float] | None = None,
                status_column: int | None = None) -> Table:
    """A styled table with zebra striping and optional status colouring."""
    styles = _styles()

    header_row = [Paragraph(f"<b>{c}</b>", ParagraphStyle(
        "th", fontSize=8, textColor=colors.white, fontName="Helvetica-Bold"))
        for c in columns]

    body_rows = [[Paragraph(str(cell) if cell is not None else "", styles["cell"])
                  for cell in row] for row in rows]

    if col_widths is None:
        col_widths = [page_width / len(columns)] * len(columns)

    table = Table([header_row] + body_rows, colWidths=col_widths, repeatRows=1)

    style = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#CBD5E1")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
    ]
    for index in range(1, len(body_rows) + 1):
        if index % 2 == 0:
            style.append(("BACKGROUND", (0, index), (-1, index), LIGHT_GREY))

    if status_column is not None:
        for index, row in enumerate(rows, start=1):
            status = str(row[status_column]) if status_column < len(row) else ""
            fill = STATUS_FILL.get(status)
            if fill:
                style.append(("BACKGROUND", (status_column, index),
                              (status_column, index), colors.HexColor(f"#{fill}")))

    table.setStyle(TableStyle(style))
    return table


def _summary_cards(items: list[tuple[str, str]], page_width: float) -> Table:
    """A row of key-figure boxes above the detail table."""
    styles = _styles()
    cells = []
    for label, value in items:
        cells.append([
            Paragraph(f"<font size=13><b>{value}</b></font>",
                      ParagraphStyle("v", fontSize=13, alignment=TA_CENTER, textColor=NAVY)),
            Paragraph(f"<font size=7>{label.upper()}</font>",
                      ParagraphStyle("l", fontSize=7, alignment=TA_CENTER, textColor=GREY)),
        ])

    table = Table([cells], colWidths=[page_width / len(items)] * len(items))
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT_BLUE),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.white),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return table


# ===========================================================================
# Excel & CSV writers  (shared by every report type)
# ===========================================================================
def export_excel(columns: list[str], rows: list[list], report_type: str,
                 filters: dict | None = None, summary: list[tuple[str, str]] | None = None,
                 user: dict | None = None) -> tuple[bool, str, Path | None]:
    """Write a branded XLSX workbook."""
    REPORT_EXCEL_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_EXCEL_DIR / _report_filename(report_type, ".xlsx", filters)

    try:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = report_type[:31] or "Report"

        column_count = max(len(columns), 4)
        last_column = get_column_letter(column_count)
        thin = Side(style="thin", color="CBD5E1")
        border = Border(left=thin, right=thin, top=thin, bottom=thin)

        # -- branded header ------------------------------------------------
        def _banner(row: int, text: str, size: int, bold: bool, colour: str) -> None:
            sheet.merge_cells(f"A{row}:{last_column}{row}")
            cell = sheet[f"A{row}"]
            cell.value = text
            cell.font = Font(size=size, bold=bold, color=colour)
            cell.alignment = Alignment(horizontal="center", vertical="center")

        _banner(1, config.get("college_name", ""), 15, True, "0F2A4A")
        _banner(2, config.get("college_address", ""), 9, False, "64748B")
        _banner(3, (f"Phone: {config.get('college_phone','')}  |  "
                    f"Email: {config.get('college_email','')}  |  "
                    f"Code: {config.get('college_code','')}"), 8, False, "64748B")
        _banner(4, report_type, 13, True, "1D4ED8")
        _banner(5, _describe_filters(filters), 9, False, "64748B")
        _banner(6, (f"Generated: {datetime.now():%d %b %Y, %I:%M %p}   |   "
                    f"By: {(user or {}).get('full_name', 'System')}   |   "
                    f"Session: {config.get('current_academic_session','')}"),
                8, False, "64748B")
        sheet.row_dimensions[1].height = 22
        sheet.row_dimensions[4].height = 20

        cursor = 8

        # -- summary band --------------------------------------------------
        if summary:
            for index, (label, value) in enumerate(summary):
                label_cell = sheet.cell(row=cursor, column=index * 2 + 1, value=label)
                label_cell.font = Font(size=9, bold=True, color="64748B")
                value_cell = sheet.cell(row=cursor, column=index * 2 + 2, value=value)
                value_cell.font = Font(size=11, bold=True, color="0F2A4A")
            cursor += 2

        # -- column headings -----------------------------------------------
        header_row = cursor
        for index, name in enumerate(columns, start=1):
            cell = sheet.cell(row=header_row, column=index, value=name)
            cell.font = Font(bold=True, color="FFFFFF", size=10)
            cell.fill = PatternFill("solid", start_color="0F2A4A")
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = border
        sheet.row_dimensions[header_row].height = 26

        # -- data ------------------------------------------------------------
        status_index = next((i for i, c in enumerate(columns)
                             if c.strip().lower() == "status"), None)

        for offset, row_values in enumerate(rows, start=1):
            for index, value in enumerate(row_values, start=1):
                cell = sheet.cell(row=header_row + offset, column=index, value=value)
                cell.border = border
                cell.font = Font(size=9)
                if offset % 2 == 0:
                    cell.fill = PatternFill("solid", start_color="F8FAFC")
                if status_index is not None and index == status_index + 1:
                    fill = STATUS_FILL.get(str(value))
                    if fill:
                        cell.fill = PatternFill("solid", start_color=fill)

        # -- widths, freeze, filter -----------------------------------------
        for index, name in enumerate(columns, start=1):
            longest = max([len(str(name))] +
                          [len(str(r[index - 1])) for r in rows[:200]
                           if index - 1 < len(r)] or [10])
            sheet.column_dimensions[get_column_letter(index)].width = min(max(longest + 3, 10), 42)

        sheet.freeze_panes = sheet.cell(row=header_row + 1, column=1)
        if rows:
            sheet.auto_filter.ref = (f"A{header_row}:{get_column_letter(len(columns))}"
                                     f"{header_row + len(rows)}")

        workbook.save(path)
        _record_history(report_type, "Excel", path, filters, user)
        return True, f"Excel report saved: {path.name}", path

    except Exception as exc:                    # noqa: BLE001
        logger.error("Excel export failed: %s", exc, exc_info=True)
        return False, f"Excel export failed: {exc}", None


def export_csv(columns: list[str], rows: list[list], report_type: str,
               filters: dict | None = None,
               user: dict | None = None) -> tuple[bool, str, Path | None]:
    """Write CSV with the branding carried as comment lines above the data."""
    REPORT_CSV_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_CSV_DIR / _report_filename(report_type, ".csv", filters)

    try:
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh)
            writer.writerow([f"# {config.get('college_name','')}"])
            writer.writerow([f"# {config.get('college_address','')}"])
            writer.writerow([f"# {report_type}"])
            writer.writerow([f"# Filters: {_describe_filters(filters)}"])
            writer.writerow([f"# Generated: {datetime.now():%d %b %Y %I:%M %p} "
                             f"by {(user or {}).get('full_name','System')}"])
            writer.writerow([])
            writer.writerow(columns)
            writer.writerows(rows)

        _record_history(report_type, "CSV", path, filters, user)
        return True, f"CSV report saved: {path.name}", path

    except Exception as exc:                    # noqa: BLE001
        logger.error("CSV export failed: %s", exc, exc_info=True)
        return False, f"CSV export failed: {exc}", None


def export_pdf(columns: list[str], rows: list[list], report_type: str,
               filters: dict | None = None, summary: list[tuple[str, str]] | None = None,
               user: dict | None = None, landscape_mode: bool | None = None,
               col_widths: list[float] | None = None,
               notes: str = "") -> tuple[bool, str, Path | None]:
    """Write a branded PDF for any tabular report."""
    REPORT_PDF_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_PDF_DIR / _report_filename(report_type, ".pdf", filters)

    # Wide tables read far better in landscape.
    if landscape_mode is None:
        landscape_mode = len(columns) > 7

    try:
        header = _ReportHeader(
            title=report_type,
            subtitle=_describe_filters(filters),
            generated_by=(user or {}).get("full_name", "System"))

        pagesize = landscape(A4) if landscape_mode else A4
        page_width = pagesize[0] - 24 * mm

        body: list = []
        if summary:
            body.append(_summary_cards(summary, page_width))
            body.append(Spacer(1, 4 * mm))

        if rows:
            widths = ([page_width * w for w in col_widths] if col_widths else None)
            body.append(_data_table(
                columns, rows, page_width, widths,
                status_column=next((i for i, c in enumerate(columns)
                                    if c.strip().lower() == "status"), None)))
        else:
            body.append(Paragraph("<i>No records matched the selected filters.</i>",
                                  _styles()["body"]))

        if notes:
            body.append(Spacer(1, 4 * mm))
            body.append(Paragraph(f"<b>Note:</b> {notes}", _styles()["meta"]))

        # Signature block -- colleges need somewhere to sign the printout.
        body.append(Spacer(1, 12 * mm))
        signatures = Table([[
            "____________________\nClass Teacher",
            "____________________\nHead of Department",
            f"____________________\n{config.get('principal_name','Principal')}\nPrincipal",
        ]], colWidths=[page_width / 3] * 3)
        signatures.setStyle(TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("TEXTCOLOR", (0, 0), (-1, -1), GREY),
        ]))
        body.append(signatures)

        _build_pdf(path, header, body, landscape_mode)
        _record_history(report_type, "PDF", path, filters, user)
        return True, f"PDF report saved: {path.name}", path

    except Exception as exc:                    # noqa: BLE001
        logger.error("PDF export failed: %s", exc, exc_info=True)
        return False, f"PDF export failed: {exc}", None


# ===========================================================================
# Report builders -- turn model rows into (columns, rows, summary)
# ===========================================================================
def build_attendance_detail(records: list) -> tuple[list[str], list[list], list]:
    """Detail listing used by the daily / weekly / monthly / subject reports."""
    columns = ["Date", "Enrollment No", "Roll No", "Student Name", "Subject",
               "Branch", "Sem", "Sec", "Status", "Method", "Conf %"]

    rows = [[
        r["class_date"], r["enrollment_no"], r["roll_no"], r["student_name"],
        r["subject_name"], r["branch_code"], r["semester_number"],
        r["section_name"] or "-", r["status"], r["marked_method"],
        f"{r['confidence']:.0f}" if r["confidence"] else "-",
    ] for r in records]

    total = len(records)
    present = sum(1 for r in records if r["status"] in ("Present", "Late"))
    summary = [
        ("Total Records", str(total)),
        ("Present", str(sum(1 for r in records if r["status"] == "Present"))),
        ("Absent", str(sum(1 for r in records if r["status"] == "Absent"))),
        ("Late", str(sum(1 for r in records if r["status"] == "Late"))),
        ("Leave", str(sum(1 for r in records if r["status"] in ("Leave", "Medical Leave")))),
        ("Attendance", f"{(100.0 * present / total) if total else 0:.1f}%"),
    ]
    return columns, rows, summary


def build_student_summary(rows_in: list) -> tuple[list[str], list[list], list]:
    """Per-student consolidated percentages (Student / Branch / Semester report)."""
    columns = ["Roll No", "Enrollment No", "Student Name", "Branch", "Sem", "Sec",
               "Total", "Attended", "Absent", "Late", "Leave", "%", "Eligible"]

    threshold = float(config.get("attendance_threshold", 75))
    rows, eligible_count = [], 0

    for r in rows_in:
        percentage = float(r["percentage"] or 0)
        eligible = percentage >= threshold
        eligible_count += 1 if eligible else 0
        rows.append([
            r["roll_no"], r["enrollment_no"], r["student_name"] if "student_name" in r.keys()
            else r["full_name"],
            r["branch_code"] if "branch_code" in r.keys() else r["branch_name"],
            r["semester_number"] if "semester_number" in r.keys() else "",
            (r["section_name"] if "section_name" in r.keys() else "") or "-",
            r["total_classes"], r["attended"],
            r["absent"] if "absent" in r.keys() else "",
            r["late"] if "late" in r.keys() else "",
            r["leave_count"] if "leave_count" in r.keys() else "",
            f"{percentage:.1f}", "Yes" if eligible else "No",
        ])

    total = len(rows_in)
    average = (sum(float(r["percentage"] or 0) for r in rows_in) / total) if total else 0
    summary = [
        ("Students", str(total)),
        ("Average %", f"{average:.1f}%"),
        ("Eligible", str(eligible_count)),
        ("Defaulters", str(total - eligible_count)),
        ("Threshold", f"{threshold:.0f}%"),
    ]
    return columns, rows, summary


def build_defaulter_list(defaulters: list) -> tuple[list[str], list[list], list]:
    columns = ["Roll No", "Enrollment No", "Student Name", "Branch", "Sem", "Sec",
               "Mobile", "Total", "Attended", "Shortfall", "%"]

    threshold = float(config.get("attendance_threshold", 75))
    rows = []
    for r in defaulters:
        total = int(r["total_classes"] or 0)
        attended = int(r["attended"] or 0)
        # Classes still needed to reach the threshold if all are attended.
        needed = 0
        rate = threshold / 100.0
        if rate < 1.0:
            needed = max(0, int(-(-(rate * total - attended) / (1 - rate) // 1)))
        rows.append([
            r["roll_no"], r["enrollment_no"], r["full_name"],
            r["branch_code"], r["semester_name"], r["section_name"] or "-",
            r["mobile"] or "-", total, attended, needed,
            f"{float(r['percentage'] or 0):.1f}",
        ])

    summary = [
        ("Defaulters", str(len(defaulters))),
        ("Threshold", f"{threshold:.0f}%"),
        ("Lowest %", f"{min((float(r['percentage'] or 0) for r in defaulters), default=0):.1f}%"),
    ]
    return columns, rows, summary


def build_leave_report(leaves: list) -> tuple[list[str], list[list], list]:
    columns = ["Applied On", "Enrollment No", "Student Name", "Branch", "Sem",
               "Leave Type", "From", "To", "Days", "Status", "Certificate", "Reason"]

    rows = [[
        str(r["applied_on"])[:10], r["enrollment_no"],
        # search_leaves aliases the student's name as student_name.
        r["student_name"] if "student_name" in r.keys() else r["full_name"],
        r["branch_code"] if "branch_code" in r.keys() else "",
        r["semester_name"] if "semester_name" in r.keys() else "",
        r["leave_type"], r["from_date"], r["to_date"], r["total_days"], r["status"],
        "Yes" if r["document_path"] else "No",
        (str(r["reason"])[:60] + "...") if r["reason"] and len(str(r["reason"])) > 60
        else (r["reason"] or ""),
    ] for r in leaves]

    summary = [
        ("Applications", str(len(leaves))),
        ("Approved", str(sum(1 for r in leaves if r["status"] == "Approved"))),
        ("Pending", str(sum(1 for r in leaves if r["status"] == "Pending"))),
        ("Rejected", str(sum(1 for r in leaves if r["status"] == "Rejected"))),
        ("Total Days", str(sum(int(r["total_days"] or 0) for r in leaves))),
    ]
    return columns, rows, summary


def build_faculty_report(rows_in: list) -> tuple[list[str], list[list], list]:
    """Faculty-wise classes conducted and the attendance they achieved."""
    columns = ["Faculty Code", "Faculty Name", "Department", "Subjects",
               "Classes Held", "Records", "Present", "Attendance %"]

    rows = [[
        r["faculty_code"], r["full_name"], r["branch_name"] or "-",
        r["subject_count"], r["sessions"], r["records"], r["present"],
        f"{float(r['percentage'] or 0):.1f}",
    ] for r in rows_in]

    summary = [
        ("Faculty", str(len(rows_in))),
        ("Classes Held", str(sum(int(r["sessions"] or 0) for r in rows_in))),
        ("Average %", f"{(sum(float(r['percentage'] or 0) for r in rows_in) / len(rows_in)) if rows_in else 0:.1f}%"),
    ]
    return columns, rows, summary


def get_faculty_report_data(from_date: str | None = None,
                            to_date: str | None = None) -> list:
    """Aggregate query behind the Faculty Attendance report."""
    clauses, params = ["1=1"], []
    if from_date:
        clauses.append("ats.class_date >= ?")
        params.append(from_date)
    if to_date:
        clauses.append("ats.class_date <= ?")
        params.append(to_date)

    return get_db().fetch_all(
        f"""SELECT f.faculty_id, f.faculty_code, f.full_name, b.branch_name,
                   (SELECT COUNT(*) FROM subjects s WHERE s.faculty_id = f.faculty_id) AS subject_count,
                   COUNT(DISTINCT ats.att_session_id) AS sessions,
                   COUNT(a.attendance_id) AS records,
                   SUM(CASE WHEN a.status IN ('Present','Late') THEN 1 ELSE 0 END) AS present,
                   ROUND(100.0 * SUM(CASE WHEN a.status IN ('Present','Late') THEN 1 ELSE 0 END)
                         / NULLIF(COUNT(a.attendance_id),0), 2) AS percentage
            FROM faculty f
            LEFT JOIN branches b ON b.branch_id = f.branch_id
            LEFT JOIN attendance_sessions ats ON ats.faculty_id = f.faculty_id
            LEFT JOIN attendance a ON a.att_session_id = ats.att_session_id
            WHERE {' AND '.join(clauses)} AND f.status = 'Active'
            GROUP BY f.faculty_id ORDER BY f.full_name""", params)


def get_class_summary_data(branch_id=None, semester_id=None, section_id=None,
                           subject_id=None, from_date=None, to_date=None) -> list:
    """Per-student consolidated rows for the Branch/Semester/Class reports."""
    clauses, params = ["s.status = 'Active'"], []
    for clause, value in (("s.branch_id = ?", branch_id),
                          ("s.semester_id = ?", semester_id),
                          ("s.section_id = ?", section_id),
                          ("a.subject_id = ?", subject_id)):
        if value:
            clauses.append(clause)
            params.append(value)
    if from_date:
        clauses.append("a.class_date >= ?")
        params.append(from_date)
    if to_date:
        clauses.append("a.class_date <= ?")
        params.append(to_date)

    return get_db().fetch_all(
        f"""SELECT s.student_id, s.enrollment_no, s.roll_no,
                   s.full_name AS student_name,
                   b.branch_code, b.branch_name, sem.semester_number, sec.section_name,
                   COUNT(a.attendance_id) AS total_classes,
                   SUM(CASE WHEN a.status IN ('Present','Late') THEN 1 ELSE 0 END) AS attended,
                   SUM(CASE WHEN a.status = 'Absent' THEN 1 ELSE 0 END) AS absent,
                   SUM(CASE WHEN a.status = 'Late' THEN 1 ELSE 0 END) AS late,
                   SUM(CASE WHEN a.status IN ('Leave','Medical Leave') THEN 1 ELSE 0 END) AS leave_count,
                   ROUND(100.0 * SUM(CASE WHEN a.status IN ('Present','Late') THEN 1 ELSE 0 END)
                         / NULLIF(COUNT(a.attendance_id),0), 2) AS percentage
            FROM students s
            LEFT JOIN attendance a ON a.student_id = s.student_id
            JOIN branches b ON b.branch_id = s.branch_id
            JOIN semesters sem ON sem.semester_id = s.semester_id
            LEFT JOIN sections sec ON sec.section_id = s.section_id
            WHERE {' AND '.join(clauses)}
            GROUP BY s.student_id
            ORDER BY CAST(s.roll_no AS INTEGER), s.roll_no""", params)


# ===========================================================================
# Attendance sheet -- the printable register a class teacher signs
# ===========================================================================
def generate_attendance_sheet(att_session_id: int, records: list, session_info: dict,
                              user: dict | None = None) -> tuple[bool, str, Path | None]:
    """One-page signed register for a single conducted class."""
    REPORT_PDF_DIR.mkdir(parents=True, exist_ok=True)
    filename = safe_filename(
        f"AttendanceSheet_{session_info.get('subject_code','SUB')}_"
        f"{session_info.get('class_date','')}") + ".pdf"
    path = REPORT_PDF_DIR / filename

    try:
        header = _ReportHeader(
            title="Class Attendance Sheet",
            subtitle=(f"{session_info.get('subject_name','')} "
                      f"({session_info.get('subject_code','')})  |  "
                      f"{session_info.get('branch_name','')} - "
                      f"{session_info.get('semester_name','')} "
                      f"{session_info.get('section_name','') or ''}  |  "
                      f"{session_info.get('class_date','')} "
                      f"{session_info.get('start_time','')}"),
            generated_by=(user or {}).get("full_name", "System"))

        page_width = A4[0] - 24 * mm

        present = sum(1 for r in records if r["status"] in ("Present", "Late"))
        body = [
            _summary_cards([
                ("Total", str(len(records))),
                ("Present", str(present)),
                ("Absent", str(len(records) - present)),
                ("Faculty", session_info.get("faculty_name", "-")),
                ("Attendance", f"{(100.0*present/len(records)) if records else 0:.0f}%"),
            ], page_width),
            Spacer(1, 4 * mm),
            _data_table(
                ["#", "Roll No", "Enrollment No", "Student Name", "Status",
                 "Method", "Time", "Signature"],
                [[i, r["roll_no"], r["enrollment_no"], r["full_name"], r["status"],
                  r["marked_method"], r["marked_time"] or "-", ""]
                 for i, r in enumerate(records, start=1)],
                page_width,
                [page_width * w for w in (0.05, 0.09, 0.15, 0.26, 0.10, 0.13, 0.09, 0.13)],
                status_column=4),
            Spacer(1, 10 * mm),
        ]

        signatures = Table([[
            "____________________\nSubject Teacher",
            f"____________________\n{config.get('principal_name','Principal')}\nPrincipal",
        ]], colWidths=[page_width / 2] * 2)
        signatures.setStyle(TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("TEXTCOLOR", (0, 0), (-1, -1), GREY),
        ]))
        body.append(signatures)

        _build_pdf(path, header, body, landscape_mode=False)
        _record_history("Attendance Sheet", "PDF", path,
                        {"session": att_session_id}, user)
        return True, f"Attendance sheet saved: {path.name}", path

    except Exception as exc:                    # noqa: BLE001
        logger.error("Attendance sheet failed: %s", exc, exc_info=True)
        return False, f"Could not generate the attendance sheet: {exc}", None


# ===========================================================================
# Dispatcher used by the Reports screen
# ===========================================================================
def generate(report_type: str, data: list, file_format: str,
             filters: dict | None = None, user: dict | None = None,
             builder: str = "detail") -> tuple[bool, str, Path | None]:
    """Build and export any report in one call.

    ``builder`` selects how the raw rows are shaped:
    ``detail`` | ``summary`` | ``defaulter`` | ``leave`` | ``faculty``.
    """
    builders = {
        "detail": build_attendance_detail,
        "summary": build_student_summary,
        "defaulter": build_defaulter_list,
        "leave": build_leave_report,
        "faculty": build_faculty_report,
    }
    if builder not in builders:
        return False, f"Unknown report builder '{builder}'.", None

    try:
        columns, rows, summary = builders[builder](data)
    except Exception as exc:                    # noqa: BLE001
        logger.error("Report builder '%s' failed: %s", builder, exc, exc_info=True)
        return False, f"Could not prepare the report data: {exc}", None

    file_format = file_format.upper()
    if file_format == "PDF":
        return export_pdf(columns, rows, report_type, filters, summary, user)
    if file_format in ("EXCEL", "XLSX"):
        return export_excel(columns, rows, report_type, filters, summary, user)
    if file_format == "CSV":
        return export_csv(columns, rows, report_type, filters, user)
    return False, f"Unsupported format '{file_format}'.", None
