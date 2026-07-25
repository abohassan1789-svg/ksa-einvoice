"""Status Analysis Report screen (تقرير تحليل الحالات).

Pure UI: it builds the RTL layout, gathers filter values and renders the data
returned by ``StatusAnalysisReportService``. It never touches SQL — every value
comes from the service, which in turn calls the repository.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextDocument
from PySide6.QtPrintSupport import QPrintDialog, QPrintPreviewDialog, QPrinter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QCompleter,
    QDateEdit,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.reports.excel_exporter import ExcelExporter
from app.reports.pdf_exporter import PdfExporter
from app.reports.print_manager import PrintManager
from app.services.status_analysis_report_service import (
    StatusAnalysisReportService,
    StatusAnalysisRequest,
)
from app.ui.common.theme import BORDER, GREEN, GREEN_DARK, TEXT, _button_style
from app.ui.common.report_charts import DashboardBarChart, DashboardDonutChart

REPORT_TITLE = "تقرير تحليل الحالات"

CARD_BG = "#FFFFFF"
TEXT_MUTED = "#64748B"
ACCENTS = ("#137A38", "#2563EB", "#F59E0B", "#7C3AED", "#DC2626")


class _CheckableMenu(QMenu):
    """A menu whose checkable actions toggle without closing the popup."""

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001 - Qt event
        action = self.activeAction()
        if action is not None and action.isCheckable() and action.isEnabled():
            action.setChecked(not action.isChecked())
            return
        super().mouseReleaseEvent(event)


class MultiSelectButton(QToolButton):
    """Drop-down multi-select. Empty selection means 'all' (no filter)."""

    def __init__(self, all_label: str = "الكل", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._all_label = all_label
        self._actions: list[Any] = []
        self.setPopupMode(QToolButton.InstantPopup)
        self.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.setMinimumHeight(36)
        self.setMinimumWidth(150)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            "QToolButton { background:#F8FAFC; border:1px solid #CBD5E1; border-radius:7px; "
            "padding:6px 10px; font-weight:800; color:#111827; text-align:right; }"
            "QToolButton::menu-indicator { subcontrol-position:left center; left:6px; }"
        )
        self._menu = _CheckableMenu(self)
        self.setMenu(self._menu)
        self.setText(all_label)

    def set_options(self, options: list[dict[str, Any]]) -> None:
        self._menu.clear()
        self._actions = []
        for option in options:
            action = self._menu.addAction(str(option.get("label") or ""))
            action.setCheckable(True)
            action.setData(option.get("id"))
            action.toggled.connect(self._update_text)
            self._actions.append(action)
        self._update_text()

    def selected_ids(self) -> list[Any]:
        return [action.data() for action in self._actions if action.isChecked()]

    def clear_selection(self) -> None:
        for action in self._actions:
            action.blockSignals(True)
            action.setChecked(False)
            action.blockSignals(False)
        self._update_text()

    def _update_text(self) -> None:
        selected = sum(1 for action in self._actions if action.isChecked())
        if selected == 0 or selected == len(self._actions):
            self.setText(self._all_label)
        else:
            self.setText(f"{selected} محدد")


class StatusAnalysisReportScreen(QWidget):
    """RTL professional report for CRM status distribution analysis."""

    def __init__(
        self,
        service: StatusAnalysisReportService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service or StatusAnalysisReportService()
        self.filter_options = self._load_filter_options()
        self.company_name = self.service.company_name()
        self.current_result = None
        self._card_values: dict[str, QLabel] = {}
        self.setWindowTitle(REPORT_TITLE)
        self.setLayoutDirection(Qt.RightToLeft)
        self.resize(1450, 880)
        self._build_ui()
        self.apply_filters()

    # --- layout -------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)
        layout.addWidget(self._build_header())
        layout.addWidget(self._build_filters())
        layout.addWidget(self._build_actions())
        layout.addWidget(self._build_summary_cards())
        layout.addWidget(self._build_main_row(), 1)
        layout.addWidget(self._build_charts())
        scroll.setWidget(content)

    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setStyleSheet(
            f"QFrame {{ background:{GREEN}; border-radius:8px; }}"
            "QLabel { background:transparent; color:#FFFFFF; }"
        )
        layout = QVBoxLayout(header)
        layout.setContentsMargins(18, 12, 18, 12)
        layout.setSpacing(2)
        title = QLabel(REPORT_TITLE)
        title.setStyleSheet("font-size:24px; font-weight:900;")
        subtitle = QLabel("تحليل إحصائي لتوزيع حالات المتابعة اليومية")
        subtitle.setStyleSheet("font-size:13px; font-weight:700; color:#E8FBEF;")
        layout.addWidget(title)
        layout.addWidget(subtitle)
        return header

    def _build_filters(self) -> QGroupBox:
        box = QGroupBox("الفلاتر")
        box.setStyleSheet(self._group_style())
        grid = QGridLayout(box)
        grid.setContentsMargins(14, 18, 14, 14)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)

        self.from_date = self._date_edit()
        self.to_date = self._date_edit()
        self.status_select = MultiSelectButton("الكل")
        self.status_select.set_options(self.filter_options.get("statuses", []))
        self.employee_select = MultiSelectButton("الكل")
        self.employee_select.set_options(self.filter_options.get("employees", []))
        self.area_select = MultiSelectButton("الكل")
        self.area_select.set_options(self.filter_options.get("areas", []))
        self.customer_combo = self._customer_combo()

        fields = [
            ("من تاريخ", self.from_date),
            ("إلى تاريخ", self.to_date),
            ("الحالة", self.status_select),
            ("الموظف", self.employee_select),
            ("المنطقة", self.area_select),
            ("العميل", self.customer_combo),
        ]
        for index, (label, widget) in enumerate(fields):
            grid.addWidget(self._field(label, widget), 0, index)
            grid.setColumnStretch(index, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        search_button = QPushButton("بحث")
        reset_button = QPushButton("إعادة تعيين الفلاتر")
        refresh_button = QPushButton("تحديث")
        search_button.setStyleSheet(_button_style(GREEN, GREEN_DARK))
        refresh_button.setStyleSheet(_button_style(GREEN, GREEN_DARK))
        reset_button.setStyleSheet(_button_style("#6B7280", "#4B5563"))
        for button in (search_button, reset_button, refresh_button):
            button.setMinimumHeight(36)
            button.setCursor(Qt.PointingHandCursor)
        search_button.clicked.connect(self.apply_filters)
        refresh_button.clicked.connect(self.apply_filters)
        reset_button.clicked.connect(self.reset_filters)
        buttons.addWidget(search_button)
        buttons.addWidget(refresh_button)
        buttons.addWidget(reset_button)
        buttons.addStretch(1)
        grid.addLayout(buttons, 1, 0, 1, len(fields))
        return box

    def _build_actions(self) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(f"QFrame {{ background:{CARD_BG}; border:1px solid {BORDER}; border-radius:8px; }}")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(12, 8, 12, 8)
        self.count_label = QLabel("")
        self.count_label.setStyleSheet(f"color:{TEXT}; font-weight:800; border:none;")
        exports = [
            ("تصدير Excel", self.export_excel),
            ("تصدير PDF", self.export_pdf),
            ("معاينة الطباعة", self.print_preview),
            ("طباعة", self.print_report),
        ]
        for text, callback in exports:
            button = QPushButton(text)
            button.setMinimumHeight(36)
            button.setCursor(Qt.PointingHandCursor)
            button.setStyleSheet(_button_style(GREEN, GREEN_DARK))
            button.clicked.connect(lambda _checked=False, cb=callback: cb())
            layout.addWidget(button)
        layout.addStretch(1)
        layout.addWidget(self.count_label)
        return frame

    def _build_summary_cards(self) -> QWidget:
        section = QWidget()
        grid = QGridLayout(section)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(10)
        cards = [
            ("total_count", "إجمالي العدد", ACCENTS[0]),
            ("total_calls", "إجمالي الاتصالات", ACCENTS[1]),
            ("overall_percentage", "النسبة", ACCENTS[2]),
            ("highest", "أعلى حالة", ACCENTS[3]),
            ("lowest", "أقل حالة", ACCENTS[4]),
        ]
        for index, (key, title, accent) in enumerate(cards):
            grid.addWidget(self._make_card(key, title, accent), 0, index)
            grid.setColumnStretch(index, 1)
        return section

    def _make_card(self, key: str, title: str, accent: str) -> QFrame:
        card = QFrame()
        card.setMinimumHeight(92)
        card.setStyleSheet(
            f"background:{CARD_BG}; border:1px solid {BORDER}; border-right:4px solid {accent}; border-radius:10px;"
        )
        box = QVBoxLayout(card)
        box.setContentsMargins(14, 12, 14, 12)
        box.setSpacing(4)
        title_label = QLabel(title)
        title_label.setStyleSheet(f"color:{TEXT_MUTED}; font-size:12px; font-weight:800; border:none;")
        value_label = QLabel("—")
        value_label.setWordWrap(True)
        value_label.setStyleSheet(f"color:{accent}; font-size:22px; font-weight:900; border:none;")
        box.addWidget(title_label)
        box.addWidget(value_label)
        self._card_values[key] = value_label
        return card

    def _build_main_row(self) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(self._build_table(), 3)
        layout.addWidget(self._build_insights_panel(), 1)
        return row

    def _build_table(self) -> QWidget:
        wrapper = QFrame()
        wrapper.setStyleSheet(f"QFrame {{ background:{CARD_BG}; border:1px solid {BORDER}; border-radius:10px; }}")
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(10, 10, 10, 10)
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(
            ["م", "الحالة", "العدد", "إجمالي الاتصالات", "النسبة"]
        )
        self.table.setSortingEnabled(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(360)
        self.table.setStyleSheet(
            "QTableWidget { background:#FFFFFF; alternate-background-color:#F8FAFC; border:none; "
            "gridline-color:#E5E7EB; font-size:13px; }"
            f"QHeaderView::section {{ background:{GREEN}; color:#FFFFFF; font-weight:900; border:none; padding:10px 8px; }}"
        )
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        layout.addWidget(self.table)
        return wrapper

    def _build_insights_panel(self) -> QGroupBox:
        box = QGroupBox("التحليل الذكي")
        box.setStyleSheet(self._group_style())
        box.setMinimumWidth(300)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(12, 18, 12, 12)
        layout.setSpacing(8)
        self.insight_summary = QLabel("")
        self.insight_summary.setWordWrap(True)
        self.insight_summary.setAlignment(Qt.AlignRight | Qt.AlignTop)
        self.insight_summary.setStyleSheet(
            f"color:{TEXT}; font-size:13px; font-weight:700; background:#F1F5F9; "
            "border:1px solid #E2E8F0; border-radius:8px; padding:10px;"
        )
        layout.addWidget(self.insight_summary)

        self.insight_top = QLabel("")
        self.insight_top.setWordWrap(True)
        self.insight_top.setAlignment(Qt.AlignRight | Qt.AlignTop)
        self.insight_top.setTextFormat(Qt.RichText)
        self.insight_top.setStyleSheet(f"color:{TEXT}; font-size:13px; border:none;")
        layout.addWidget(self.insight_top)

        self.insight_zero = QLabel("")
        self.insight_zero.setWordWrap(True)
        self.insight_zero.setAlignment(Qt.AlignRight | Qt.AlignTop)
        self.insight_zero.setTextFormat(Qt.RichText)
        self.insight_zero.setStyleSheet(f"color:{TEXT}; font-size:13px; border:none;")
        layout.addWidget(self.insight_zero)
        layout.addStretch(1)
        return box

    def _build_charts(self) -> QWidget:
        section = QWidget()
        grid = QGridLayout(section)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(12)
        self.pie_chart = DashboardDonutChart(
            "النسبة حسب الحالة", "status_name", max_bars=10, min_height=300
        )
        self.bar_chart = DashboardBarChart(
            "العدد حسب الحالة", "status_name", bar_color=GREEN, max_bars=10, min_height=300
        )
        grid.addWidget(self.pie_chart, 0, 0)
        grid.addWidget(self.bar_chart, 0, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        return section

    # --- field helpers ------------------------------------------------------
    def _field(self, label: str, widget: QWidget) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        text = QLabel(label)
        text.setAlignment(Qt.AlignRight)
        text.setStyleSheet(f"color:{TEXT_MUTED}; font-size:12px; font-weight:800;")
        widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addWidget(text)
        layout.addWidget(widget)
        return box

    def _date_edit(self) -> QDateEdit:
        edit = QDateEdit()
        edit.setCalendarPopup(True)
        edit.setDisplayFormat("yyyy-MM-dd")
        edit.setSpecialValueText("")
        edit.setMinimumDate(_dt.date(1900, 1, 1))
        edit.setDate(edit.minimumDate())
        edit.setMinimumHeight(36)
        edit.setStyleSheet("background:#F8FAFC; border:1px solid #CBD5E1; border-radius:7px; padding:5px;")
        return edit

    def _customer_combo(self) -> QComboBox:
        combo = QComboBox()
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.NoInsert)
        combo.setMinimumHeight(36)
        combo.setStyleSheet("background:#F8FAFC; border:1px solid #CBD5E1; border-radius:7px; padding:5px;")
        combo.addItem("الكل", None)
        for option in self.filter_options.get("customers", []):
            combo.addItem(str(option.get("label") or ""), option.get("id"))
        completer = combo.completer()
        if completer is not None:
            completer.setCompletionMode(QCompleter.PopupCompletion)
            completer.setFilterMode(Qt.MatchContains)
        return combo

    # --- data flow ----------------------------------------------------------
    def _build_request(self) -> StatusAnalysisRequest:
        customer_id = self.customer_combo.currentData()
        return StatusAnalysisRequest(
            date_from=self._date_value(self.from_date),
            date_to=self._date_value(self.to_date),
            status_ids=tuple(self.status_select.selected_ids()),
            employee_ids=tuple(self.employee_select.selected_ids()),
            area_values=tuple(self.area_select.selected_ids()),
            customer_ids=(customer_id,) if customer_id is not None else (),
        )

    def apply_filters(self) -> None:
        request = self._build_request()
        try:
            result = self.service.fetch_report(request)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "تعذر تحميل التقرير", str(exc))
            return
        self.current_result = result
        self._fill_table(result)
        self._fill_summary(result)
        self._fill_insights(result)
        self._fill_charts(result)
        self.count_label.setText(f"عدد الحالات: {len(result.rows):,}")

    def reset_filters(self) -> None:
        self.from_date.setDate(self.from_date.minimumDate())
        self.to_date.setDate(self.to_date.minimumDate())
        self.status_select.clear_selection()
        self.employee_select.clear_selection()
        self.area_select.clear_selection()
        self.customer_combo.setCurrentIndex(0)
        self.apply_filters()

    def _fill_table(self, result) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(result.rows))
        for row_index, row in enumerate(result.rows):
            cells = [
                (str(row["row_number"]), Qt.AlignCenter),
                (row["status_name"], Qt.AlignRight | Qt.AlignVCenter),
                (f"{row['count']:,}", Qt.AlignCenter),
                (f"{row['total_calls']:,}", Qt.AlignCenter),
                (row["percentage_label"], Qt.AlignCenter),
            ]
            for column_index, (text, alignment) in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setTextAlignment(alignment)
                if column_index == 2:  # numeric sort on العدد
                    item.setData(Qt.UserRole, row["count"])
                self.table.setItem(row_index, column_index, item)
        self.table.setSortingEnabled(True)
        self.table.resizeColumnsToContents()

    def _fill_summary(self, result) -> None:
        summary = result.summary
        self._card_values["total_count"].setText(f"{summary['total_count']:,}")
        self._card_values["total_calls"].setText(f"{summary['total_calls']:,}")
        self._card_values["overall_percentage"].setText(summary["overall_percentage_label"])
        highest = summary.get("highest")
        lowest = summary.get("lowest_non_zero")
        self._card_values["highest"].setText(
            f"{highest['status_name']}\n{highest['count']:,} ({highest['percentage_label']})"
            if highest and highest["count"] > 0
            else "—"
        )
        self._card_values["lowest"].setText(
            f"{lowest['status_name']}\n{lowest['count']:,} ({lowest['percentage_label']})"
            if lowest
            else "—"
        )

    def _fill_insights(self, result) -> None:
        insights = result.insights
        self.insight_summary.setText("\n".join(f"• {text}" for text in insights["texts"]))

        top_rows = "".join(
            f"<div>{index}. {row['status_name']} — "
            f"<b>{row['count']:,}</b> ({row['percentage_label']})</div>"
            for index, row in enumerate(insights["top5"], start=1)
            if row["count"] > 0
        )
        self.insight_top.setText(
            f"<b style='color:{GREEN};'>أعلى 5 حالات</b>{top_rows or '<div>لا توجد بيانات</div>'}"
        )

        zero = insights["zero_statuses"]
        if zero:
            zero_rows = "".join(f"<div>• {row['status_name']}</div>" for row in zero)
            self.insight_zero.setText(
                f"<b style='color:#DC2626;'>حالات بدون متابعات ({len(zero)})</b>{zero_rows}"
            )
        else:
            self.insight_zero.setText(
                "<b style='color:#DC2626;'>حالات بدون متابعات</b><div>لا يوجد</div>"
            )

    def _fill_charts(self, result) -> None:
        data = [
            {"status_name": row["status_name"], "count": row["count"]}
            for row in result.rows
        ]
        self.pie_chart.set_data("النسبة حسب الحالة", data, "status_name")
        self.bar_chart.set_data("العدد حسب الحالة", data, "status_name")

    # --- exports ------------------------------------------------------------
    def _applied_filter_lines(self) -> list[str]:
        lines: list[str] = []
        if self.company_name:
            lines.append(self.company_name)
        date_from = self._date_value(self.from_date)
        date_to = self._date_value(self.to_date)
        lines.append(
            f"الفترة: من {date_from or 'البداية'} إلى {date_to or 'النهاية'}"
        )
        status_text = self._selected_labels(self.status_select)
        if status_text:
            lines.append(f"الحالات: {status_text}")
        employee_text = self._selected_labels(self.employee_select)
        if employee_text:
            lines.append(f"الموظفون: {employee_text}")
        area_text = self._selected_labels(self.area_select)
        if area_text:
            lines.append(f"المناطق: {area_text}")
        if self.customer_combo.currentData() is not None:
            lines.append(f"العميل: {self.customer_combo.currentText()}")
        return lines

    def _summary_lines(self) -> list[str]:
        if self.current_result is None:
            return []
        summary = self.current_result.summary
        lines = [
            f"إجمالي العدد: {summary['total_count']:,}",
            f"إجمالي الاتصالات: {summary['total_calls']:,}",
            f"النسبة الإجمالية: {summary['overall_percentage_label']}",
        ]
        highest = summary.get("highest")
        if highest and highest["count"] > 0:
            lines.append(
                f"أعلى حالة: {highest['status_name']} ({highest['count']:,} - {highest['percentage_label']})"
            )
        lowest = summary.get("lowest_non_zero")
        if lowest:
            lines.append(
                f"أقل حالة: {lowest['status_name']} ({lowest['count']:,} - {lowest['percentage_label']})"
            )
        return lines

    def export_excel(self) -> None:
        if not self._has_data():
            return
        output = self._save_path("Excel Files (*.xlsx)", "xlsx")
        if output:
            ExcelExporter().export_with_summary(
                output, REPORT_TITLE, self._applied_filter_lines(),
                self.current_result.columns, self.current_result.export_rows,
                self._summary_lines(),
            )
            QMessageBox.information(self, "تم التصدير", f"تم حفظ الملف:\n{output}")

    def export_pdf(self) -> None:
        if not self._has_data():
            return
        output = self._save_path("PDF Files (*.pdf)", "pdf")
        if output:
            PdfExporter().export_with_summary(
                output, REPORT_TITLE, self._applied_filter_lines(),
                self.current_result.columns, self.current_result.export_rows,
                self._summary_lines(),
            )
            QMessageBox.information(self, "تم التصدير", f"تم حفظ الملف:\n{output}")

    def print_preview(self) -> None:
        if not self._has_data():
            return
        document = self._print_document()
        dialog = QPrintPreviewDialog(self)
        dialog.paintRequested.connect(document.print_)
        dialog.exec()

    def print_report(self) -> None:
        if not self._has_data():
            return
        printer = QPrinter(QPrinter.HighResolution)
        dialog = QPrintDialog(printer, self)
        if dialog.exec() == QPrintDialog.Accepted:
            self._print_document().print_(printer)

    def _print_document(self) -> QTextDocument:
        document = QTextDocument()
        document.setHtml(
            PrintManager().build_html_with_summary(
                REPORT_TITLE, self._applied_filter_lines(),
                self.current_result.columns, self.current_result.export_rows,
                self._summary_lines(),
            )
        )
        return document

    # --- small helpers ------------------------------------------------------
    def _has_data(self) -> bool:
        if self.current_result is None or not self.current_result.export_rows:
            QMessageBox.warning(self, "لا توجد بيانات", "لا توجد بيانات لعرضها أو تصديرها.")
            return False
        return True

    def _load_filter_options(self) -> dict[str, list[dict[str, Any]]]:
        try:
            return self.service.load_filter_options()
        except Exception:  # noqa: BLE001
            return {"statuses": [], "employees": [], "areas": [], "customers": []}

    @staticmethod
    def _selected_labels(button: MultiSelectButton) -> str:
        labels = [
            action.text() for action in button._actions if action.isChecked()  # noqa: SLF001
        ]
        return "، ".join(labels)

    def _save_path(self, file_filter: str, suffix: str) -> Path | None:
        path, _selected = QFileDialog.getSaveFileName(
            self, "حفظ التقرير", f"{REPORT_TITLE}.{suffix}", file_filter
        )
        if not path:
            return None
        output = Path(path)
        if output.suffix.lower() != f".{suffix}":
            output = output.with_suffix(f".{suffix}")
        return output

    @staticmethod
    def _date_value(editor: QDateEdit) -> str | None:
        if editor.date() == editor.minimumDate():
            return None
        return editor.date().toString("yyyy-MM-dd")

    @staticmethod
    def _group_style() -> str:
        return (
            "QGroupBox { background:#FFFFFF; border:1px solid #E2E8F0; border-radius:8px; margin-top:14px; }"
            f"QGroupBox::title {{ subcontrol-origin:margin; subcontrol-position:top right; right:16px; "
            f"padding:0 12px; color:{GREEN}; font-weight:900; }}"
            f"QLabel {{ color:{TEXT}; font-weight:800; }}"
        )
