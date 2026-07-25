"""PDF export helpers."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from html import escape
import os
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer


@dataclass(frozen=True)
class PdfFontNames:
    regular: str
    bold: str


def _font_candidates() -> list[tuple[Path, Path | None]]:
    windows_fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    system_root_fonts = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Fonts"
    return [
        (windows_fonts / "tahoma.ttf", windows_fonts / "tahomabd.ttf"),
        (windows_fonts / "arial.ttf", windows_fonts / "arialbd.ttf"),
        (windows_fonts / "ARIALUNI.TTF", None),
        (system_root_fonts / "tahoma.ttf", system_root_fonts / "tahomabd.ttf"),
        (system_root_fonts / "arial.ttf", system_root_fonts / "arialbd.ttf"),
        (
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ),
        (
            Path("/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf"),
            Path("/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf"),
        ),
    ]


def _register_font(name: str, path: Path) -> None:
    try:
        pdfmetrics.getFont(name)
    except KeyError:
        pdfmetrics.registerFont(TTFont(name, str(path)))


@lru_cache(maxsize=1)
def _resolve_font_names() -> PdfFontNames:
    for regular_path, bold_path in _font_candidates():
        if not regular_path.exists():
            continue
        try:
            _register_font("CRMArabic", regular_path)
            if bold_path and bold_path.exists():
                _register_font("CRMArabicBold", bold_path)
                return PdfFontNames("CRMArabic", "CRMArabicBold")
            return PdfFontNames("CRMArabic", "CRMArabic")
        except Exception:
            continue
    return PdfFontNames("Helvetica", "Helvetica-Bold")


def _contains_arabic(text: str) -> bool:
    return any(
        "\u0600" <= char <= "\u06ff" or "\u0750" <= char <= "\u077f" or "\u08a0" <= char <= "\u08ff"
        for char in text
    )


class PdfExporter:
    """Write selected report columns to a PDF table."""

    PAGE_MARGIN = 18
    PAGE_TOP_MARGIN = 24

    def resolve_font_names(self) -> PdfFontNames:
        return _resolve_font_names()

    def format_text(self, value: Any) -> str:
        text = "" if value is None else str(value)
        if not text or not _contains_arabic(text):
            return text
        try:
            import arabic_reshaper
            from bidi.algorithm import get_display
        except ImportError:
            return text
        return get_display(arabic_reshaper.reshape(text))

    def rtl_columns(self, columns: list[Any]) -> list[Any]:
        return list(reversed(columns))

    def column_widths(self, columns: list[Any], available_width: float) -> list[float]:
        if not columns:
            raise ValueError("At least one PDF column must be selected.")
        width = available_width / len(columns)
        return [width for _ in columns]

    def build_table_data(self, columns: list[Any], rows: list[dict[str, Any]]) -> list[list[str]]:
        if not columns:
            raise ValueError("At least one PDF column must be selected.")
        rtl_columns = self.rtl_columns(columns)
        data = [[self.format_text(column.label) for column in rtl_columns]]
        for row in rows:
            data.append([self.format_text(row.get(column.key)) for column in rtl_columns])
        return data

    def _table_font_size(self, columns: list[Any]) -> int:
        if len(columns) >= 16:
            return 6
        if len(columns) >= 10:
            return 7
        return 8

    def _paragraph_table_data(
        self,
        table_data: list[list[str]],
        columns: list[Any],
        font_names: PdfFontNames,
    ) -> list[list[Paragraph]]:
        font_size = self._table_font_size(columns)
        header_style = ParagraphStyle(
            "ReportTableHeader",
            fontName=font_names.bold,
            fontSize=font_size,
            leading=font_size + 2,
            alignment=1,
            textColor=colors.white,
        )
        cell_style = ParagraphStyle(
            "ReportTableCell",
            fontName=font_names.regular,
            fontSize=font_size,
            leading=font_size + 2,
            alignment=1,
        )
        return [
            [
                Paragraph(escape(cell), header_style if row_index == 0 else cell_style)
                for cell in row
            ]
            for row_index, row in enumerate(table_data)
        ]

    def export_table(
        self,
        path: str | Path,
        title: str,
        columns: list[Any],
        rows: list[dict[str, Any]],
    ) -> Path:
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        document = SimpleDocTemplate(
            str(output_path),
            pagesize=landscape(A4),
            leftMargin=self.PAGE_MARGIN,
            rightMargin=self.PAGE_MARGIN,
            topMargin=self.PAGE_TOP_MARGIN,
            bottomMargin=self.PAGE_TOP_MARGIN,
        )
        styles = getSampleStyleSheet()
        font_names = self.resolve_font_names()
        title_style = ParagraphStyle(
            "ReportTitle",
            parent=styles["Title"],
            fontName=font_names.bold,
            alignment=1,
        )
        elements = [Paragraph(self.format_text(title), title_style), Spacer(1, 12)]
        table_data = self.build_table_data(columns, rows)
        table = Table(
            self._paragraph_table_data(table_data, columns, font_names),
            colWidths=self.column_widths(columns, document.width),
            repeatRows=1,
            hAlign="RIGHT",
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#137A38")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), font_names.bold),
                    ("FONTNAME", (0, 1), (-1, -1), font_names.regular),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D1D5DB")),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                ]
            )
        )
        elements.append(table)
        document.build(elements)
        return output_path

    def export_with_summary(
        self,
        path: str | Path,
        title: str,
        subtitle_lines: list[str],
        columns: list[Any],
        rows: list[dict[str, Any]],
        summary_lines: list[str],
    ) -> Path:
        """Clean RTL PDF: title, applied-filter lines, table, summary totals."""
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        document = SimpleDocTemplate(
            str(output_path),
            pagesize=landscape(A4),
            leftMargin=self.PAGE_MARGIN,
            rightMargin=self.PAGE_MARGIN,
            topMargin=self.PAGE_TOP_MARGIN,
            bottomMargin=self.PAGE_TOP_MARGIN,
        )
        styles = getSampleStyleSheet()
        font_names = self.resolve_font_names()
        title_style = ParagraphStyle(
            "ReportTitle", parent=styles["Title"], fontName=font_names.bold, alignment=1
        )
        subtitle_style = ParagraphStyle(
            "ReportSubtitle", fontName=font_names.regular, fontSize=9, leading=13, alignment=2
        )
        summary_style = ParagraphStyle(
            "ReportSummary", fontName=font_names.bold, fontSize=10, leading=15, alignment=2
        )

        elements: list[Any] = [Paragraph(self.format_text(title), title_style), Spacer(1, 8)]
        for line in subtitle_lines:
            if line:
                elements.append(Paragraph(self.format_text(line), subtitle_style))
        elements.append(Spacer(1, 10))

        table_data = self.build_table_data(columns, rows)
        table = Table(
            self._paragraph_table_data(table_data, columns, font_names),
            colWidths=self.column_widths(columns, document.width),
            repeatRows=1,
            hAlign="RIGHT",
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#137A38")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), font_names.bold),
                    ("FONTNAME", (0, 1), (-1, -1), font_names.regular),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D1D5DB")),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                ]
            )
        )
        elements.append(table)
        elements.append(Spacer(1, 12))
        for line in summary_lines:
            if line:
                elements.append(Paragraph(self.format_text(line), summary_style))
        document.build(elements)
        return output_path
