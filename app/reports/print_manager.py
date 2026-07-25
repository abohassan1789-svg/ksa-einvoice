"""Print and print-preview helpers."""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any


class PrintManager:
    """Build a printable HTML representation of selected report columns."""

    def build_html(self, title: str, columns: list[Any], rows: list[dict[str, Any]]) -> str:
        headers = "".join(f"<th>{escape(str(column.label))}</th>" for column in columns)
        body = []
        for row in rows:
            cells = "".join(
                f"<td>{escape('' if row.get(column.key) is None else str(row.get(column.key)))}</td>"
                for column in columns
            )
            body.append(f"<tr>{cells}</tr>")
        return (
            "<!doctype html><html lang=\"ar\" dir=\"rtl\"><head><meta charset=\"utf-8\">"
            f"<title>{escape(title)}</title>"
            "<style>"
            "body{font-family:'Segoe UI',Tahoma,Arial,sans-serif;margin:24px;color:#111827;}"
            "h1{font-size:22px;margin:0 0 14px;text-align:center;}"
            "table{width:100%;border-collapse:collapse;table-layout:auto;}"
            "th{background:#137A38;color:white;font-weight:800;}"
            "th,td{border:1px solid #D1D5DB;padding:8px;text-align:center;vertical-align:top;}"
            "td{white-space:pre-wrap;word-break:break-word;}"
            "</style></head><body>"
            f"<h1>{escape(title)}</h1>"
            f"<table><thead><tr>{headers}</tr></thead><tbody>{''.join(body)}</tbody></table>"
            "</body></html>"
        )

    def build_html_with_summary(
        self,
        title: str,
        subtitle_lines: list[str],
        columns: list[Any],
        rows: list[dict[str, Any]],
        summary_lines: list[str],
    ) -> str:
        """Printable RTL HTML with a title, applied filters, table and totals."""
        headers = "".join(f"<th>{escape(str(column.label))}</th>" for column in columns)
        body = []
        for row in rows:
            cells = "".join(
                f"<td>{escape('' if row.get(column.key) is None else str(row.get(column.key)))}</td>"
                for column in columns
            )
            body.append(f"<tr>{cells}</tr>")
        subtitle_html = "".join(
            f"<div class='filter'>{escape(str(line))}</div>" for line in subtitle_lines if line
        )
        summary_html = "".join(
            f"<div class='summary'>{escape(str(line))}</div>" for line in summary_lines if line
        )
        return (
            "<!doctype html><html lang=\"ar\" dir=\"rtl\"><head><meta charset=\"utf-8\">"
            f"<title>{escape(title)}</title>"
            "<style>"
            "body{font-family:'Segoe UI',Tahoma,Arial,sans-serif;margin:24px;color:#111827;}"
            "h1{font-size:22px;margin:0 0 8px;text-align:center;color:#137A38;}"
            ".filter{text-align:right;font-size:12px;color:#374151;margin:2px 0;}"
            ".summary{text-align:right;font-size:13px;font-weight:800;margin:4px 0;}"
            ".bar{margin:10px 0;border-top:2px solid #137A38;}"
            "table{width:100%;border-collapse:collapse;table-layout:auto;}"
            "th{background:#137A38;color:white;font-weight:800;}"
            "th,td{border:1px solid #D1D5DB;padding:8px;text-align:center;vertical-align:middle;}"
            "td{white-space:pre-wrap;word-break:break-word;}"
            "</style></head><body>"
            f"<h1>{escape(title)}</h1>"
            f"{subtitle_html}"
            "<div class='bar'></div>"
            f"<table><thead><tr>{headers}</tr></thead><tbody>{''.join(body)}</tbody></table>"
            "<div class='bar'></div>"
            f"{summary_html}"
            "</body></html>"
        )

    def save_html(
        self,
        path: str | Path,
        title: str,
        columns: list[Any],
        rows: list[dict[str, Any]],
    ) -> Path:
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(self.build_html(title, columns, rows), encoding="utf-8")
        return output_path
