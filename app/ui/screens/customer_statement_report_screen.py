"""Customer Statement report screen (كشف حساب العميل).

Pure UI: it builds the RTL layout, gathers the filter values and renders the data
returned by ``CustomerStatementReportService``. It never touches SQL — every
value comes from the service, which in turn calls the repository. The report is
strictly read-only: nothing here inserts, updates or deletes any record.

The visual design, filter card, action buttons, header lines, table styling and
empty-state are the same ones used by the Status Analysis / Daily Follow-up
reports. Excel export still goes through the shared ``ExcelExporter``.

**Printing does not** (changed 2026-07-16, at the user's request for three print
templates). معاينة / طباعة / تصدير PDF now open a template picker and render
through :mod:`app.ui.screens.customer_statement_print` — one HTML per template,
drawn by Chromium for preview, print and PDF alike. The shared ``PrintManager``
(``QTextDocument``) and ``PdfExporter`` (ReportLab) are no longer used *here*:
they were two renderers behind one report, and ``QTextDocument`` cannot render
the approved designs at all — it ignores ``display:flex`` and drops every
``border-radius``. Both modules are untouched and still serve the other reports.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
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
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.reports.excel_exporter import ExcelExporter
from app.services.customer_statement_report_service import (
    CustomerStatementReportService,
    CustomerStatementRequest,
    CustomerStatementValidationError,
    EMPTY_MESSAGE,
)
from app.ui.common.theme import BORDER, GREEN, GREEN_DARK, TEXT, _button_style

REPORT_TITLE = "كشف حساب العميل"

CARD_BG = "#FFFFFF"
TEXT_MUTED = "#64748B"


class CustomerStatementReportScreen(QWidget):
    """RTL read-only customer statement (sales invoices + receipt vouchers)."""

    def __init__(
        self,
        service: CustomerStatementReportService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service or CustomerStatementReportService()
        self.customer_options = self._load_customer_options()
        self.company_options = self._load_company_options()
        self.company_info = self._load_company_info()
        self.current_result = None
        self.setWindowTitle(REPORT_TITLE)
        self.setLayoutDirection(Qt.RightToLeft)
        self.resize(1350, 840)
        self._build_ui()

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
        layout.addWidget(self._build_table(), 1)
        layout.addWidget(self._build_totals())
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
        subtitle = QLabel("كشف حساب تفصيلي بحركات الفواتير وسندات القبض للعميل")
        subtitle.setStyleSheet("font-size:13px; font-weight:700; color:#E8FBEF;")
        layout.addWidget(title)
        layout.addWidget(subtitle)
        company = self.company_info.get("name") if self.company_info else None
        if company:
            company_label = QLabel(company)
            company_label.setStyleSheet("font-size:12px; font-weight:700; color:#D1FAE5;")
            layout.addWidget(company_label)
        return header

    def _build_filters(self) -> QGroupBox:
        box = QGroupBox("الفلاتر")
        box.setStyleSheet(self._group_style())
        grid = QGridLayout(box)
        grid.setContentsMargins(14, 18, 14, 14)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)

        self.customer_combo = self._searchable_combo(self.customer_options)
        self.company_combo = self._searchable_combo(self.company_options)
        self.from_date = self._date_edit()
        self.to_date = self._date_edit()

        fields = [
            ("العميل", self.customer_combo),
            ("الشركة", self.company_combo),
            ("من تاريخ", self.from_date),
            ("إلى تاريخ", self.to_date),
        ]
        for index, (label, widget) in enumerate(fields):
            grid.addWidget(self._field(label, widget), 0, index)
            grid.setColumnStretch(index, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        search_button = QPushButton("بحث / عرض")
        reset_button = QPushButton("مسح الفلاتر")
        search_button.setStyleSheet(_button_style(GREEN, GREEN_DARK))
        reset_button.setStyleSheet(_button_style("#6B7280", "#4B5563"))
        for button in (search_button, reset_button):
            button.setMinimumHeight(36)
            button.setCursor(Qt.PointingHandCursor)
        search_button.clicked.connect(self.apply_filters)
        reset_button.clicked.connect(self.reset_filters)
        buttons.addWidget(search_button)
        buttons.addWidget(reset_button)
        buttons.addStretch(1)
        grid.addLayout(buttons, 1, 0, 1, len(fields) + 1)
        return box

    def _build_actions(self) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(f"QFrame {{ background:{CARD_BG}; border:1px solid {BORDER}; border-radius:8px; }}")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(12, 8, 12, 8)
        self.count_label = QLabel("")
        self.count_label.setStyleSheet(f"color:{TEXT}; font-weight:800; border:none;")
        actions = [
            ("معاينة", self.print_preview),
            ("طباعة", self.print_report),
            ("PDF", self.export_pdf),
            ("Excel", self.export_excel),
            ("إغلاق", self.close_report),
        ]
        for text, callback in actions:
            button = QPushButton(text)
            button.setMinimumHeight(36)
            button.setCursor(Qt.PointingHandCursor)
            style = _button_style("#6B7280", "#4B5563") if text == "إغلاق" else _button_style(GREEN, GREEN_DARK)
            button.setStyleSheet(style)
            button.clicked.connect(lambda _checked=False, cb=callback: cb())
            layout.addWidget(button)
        layout.addStretch(1)
        layout.addWidget(self.count_label)
        return frame

    def _build_table(self) -> QWidget:
        wrapper = QFrame()
        wrapper.setStyleSheet(f"QFrame {{ background:{CARD_BG}; border:1px solid {BORDER}; border-radius:10px; }}")
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(10, 10, 10, 10)
        self.table = QTableWidget()
        self.table.setColumnCount(len(self.service.columns))
        self.table.setHorizontalHeaderLabels([column.label for column in self.service.columns])
        # Sorting is intentionally OFF so the displayed order matches the stable
        # service order (date, invoice-before-payment, id) used by all exports.
        self.table.setSortingEnabled(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(420)
        self.table.setStyleSheet(
            "QTableWidget { background:#FFFFFF; alternate-background-color:#F8FAFC; border:none; "
            "gridline-color:#E5E7EB; font-size:13px; }"
            f"QHeaderView::section {{ background:{GREEN}; color:#FFFFFF; font-weight:900; border:none; padding:10px 8px; }}"
        )
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        self.empty_label = QLabel(EMPTY_MESSAGE)
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet(
            f"color:{TEXT_MUTED}; font-size:15px; font-weight:800; padding:18px; border:none;"
        )
        self.empty_label.setVisible(False)
        layout.addWidget(self.table)
        layout.addWidget(self.empty_label)
        return wrapper

    def _build_totals(self) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(
            f"QFrame {{ background:#EAF6EF; border:1px solid #BFE6CF; border-radius:8px; }}"
            "QLabel { border:none; background:transparent; }"
        )
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(26)
        self.total_debit_label = self._total_label("إجمالي المدين")
        self.total_credit_label = self._total_label("إجمالي الدائن")
        self.difference_label = self._total_label("الفرق")
        layout.addWidget(self.difference_label)
        layout.addWidget(self.total_credit_label)
        layout.addWidget(self.total_debit_label)
        layout.addStretch(1)
        return frame

    def _total_label(self, caption: str) -> QLabel:
        label = QLabel(f"{caption}: —")
        label.setProperty("caption", caption)
        label.setStyleSheet(f"color:{TEXT}; font-size:14px; font-weight:900;")
        return label

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

    def _searchable_combo(self, options: list[dict[str, Any]]) -> QComboBox:
        """Searchable selector whose first item ('الكل', data None) means no
        filter — so a search with nothing selected returns the full view."""
        combo = QComboBox()
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.NoInsert)
        combo.setMinimumHeight(36)
        combo.setStyleSheet("background:#F8FAFC; border:1px solid #CBD5E1; border-radius:7px; padding:5px;")
        combo.addItem("الكل", None)
        for option in options:
            combo.addItem(str(option.get("label") or ""), option.get("id"))
        completer = combo.completer()
        if completer is not None:
            completer.setCompletionMode(QCompleter.PopupCompletion)
            completer.setFilterMode(Qt.MatchContains)
        return combo

    # --- data flow ----------------------------------------------------------
    def _build_request(self) -> CustomerStatementRequest:
        return CustomerStatementRequest(
            customer_id=self.customer_combo.currentData(),
            company_id=self.company_combo.currentData(),
            date_from=self._date_value(self.from_date),
            date_to=self._date_value(self.to_date),
        )

    def apply_filters(self) -> None:
        try:
            result = self.service.fetch_report(self._build_request())
        except CustomerStatementValidationError as exc:
            QMessageBox.warning(self, "تحقق من الفلاتر", exc.message)
            return
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "تعذر تحميل التقرير", str(exc))
            return
        self.current_result = result
        self._fill_table(result)
        self._fill_totals(result)
        self.count_label.setText(f"عدد الحركات: {len(result.export_rows):,}")

    def reset_filters(self) -> None:
        self.customer_combo.setCurrentIndex(0)
        self.company_combo.setCurrentIndex(0)
        self.from_date.setDate(self.from_date.minimumDate())
        self.to_date.setDate(self.to_date.minimumDate())
        self.current_result = None
        self.table.setRowCount(0)
        self.table.setVisible(True)
        self.empty_label.setVisible(False)
        for label in (self.total_debit_label, self.total_credit_label, self.difference_label):
            label.setText(f"{label.property('caption')}: —")
        self.count_label.setText("")

    def _fill_table(self, result) -> None:
        columns = result.columns
        rows = result.export_rows
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column_index, column in enumerate(columns):
                value = row.get(column.key)
                item = QTableWidgetItem("" if value is None else str(value))
                if column.key in ("debit", "credit"):
                    item.setTextAlignment(Qt.AlignCenter)
                elif column.key == "description":
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                else:
                    item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row_index, column_index, item)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)
        self.empty_label.setVisible(result.is_empty)
        self.table.setVisible(not result.is_empty)

    def _fill_totals(self, result) -> None:
        summary = result.summary
        self.total_debit_label.setText(f"إجمالي المدين: {summary['total_debit_label']}")
        self.total_credit_label.setText(f"إجمالي الدائن: {summary['total_credit_label']}")
        self.difference_label.setText(f"الفرق: {summary['difference_label']}")

    # --- exports (shared framework) ----------------------------------------
    def _applied_filter_lines(self) -> list[str]:
        lines: list[str] = []
        if self.company_info.get("name"):
            lines.append(self.company_info["name"])
        if self.company_info.get("tax_number"):
            lines.append(f"الرقم الضريبي: {self.company_info['tax_number']}")
        lines.append(f"العميل: {self._combo_label(self.customer_combo)}")
        lines.append(f"الشركة: {self._combo_label(self.company_combo)}")
        date_from = self._date_value(self.from_date)
        date_to = self._date_value(self.to_date)
        lines.append(f"الفترة: من {date_from or 'البداية'} إلى {date_to or 'النهاية'}")
        lines.append(f"تاريخ الإصدار: {_dt.datetime.now():%Y-%m-%d %H:%M}")
        return lines

    def _summary_lines(self) -> list[str]:
        if self.current_result is None:
            return []
        summary = self.current_result.summary
        lines = [
            f"إجمالي المدين: {summary['total_debit_label']}",
            f"إجمالي الدائن: {summary['total_credit_label']}",
            f"الفرق: {summary['difference_label']}",
        ]
        if self.current_result.is_empty:
            lines.insert(0, EMPTY_MESSAGE)
        return lines

    def _export_rows(self) -> list[dict[str, Any]]:
        return self.current_result.export_rows if self.current_result else []

    def export_excel(self) -> None:
        if not self._ensure_ran():
            return
        output = self._save_path("Excel Files (*.xlsx)", "xlsx")
        if output:
            ExcelExporter().export_with_summary(
                output, REPORT_TITLE, self._applied_filter_lines(),
                self.current_result.columns, self._export_rows(), self._summary_lines(),
            )
            QMessageBox.information(self, "تم التصدير", f"تم حفظ الملف:\n{output}")

    # --- printing (three templates, picked per action) ----------------------
    def _statement_print_data(self) -> dict[str, Any]:
        """Reshape the current result into what the print templates consume.

        Every value here already exists in the service's result or the filter
        widgets — nothing is derived except النموذج الثاني's running balance, which
        the template computes from these same debit/credit figures.

        ``company`` is the letterhead: the row of the company picked in the filter,
        or ``None`` for «الكل». It comes from ``companies``, NOT from the
        ``company_name`` / ``tax_number`` above — those read the empty legacy
        ``company_settings``, which is why the printed statement carried no company
        at all. They stay only for the screen header and the export filter lines.
        """
        result = self.current_result
        return {
            "title": REPORT_TITLE,
            "company_name": self.company_info.get("name", ""),
            "tax_number": self.company_info.get("tax_number", ""),
            "company": self.service.company_letterhead(self.company_combo.currentData()),
            "customer_label": self._combo_label(self.customer_combo),
            "company_label": self._combo_label(self.company_combo),
            "date_from_label": self._date_value(self.from_date) or "البداية",
            "date_to_label": self._date_value(self.to_date) or "النهاية",
            # No `issued_at`: all three templates dropped «تاريخ الإصدار» at the
            # user's request (2026-07-17). The Excel export still stamps its own —
            # that is a filter line on a different artefact, not one of the three.
            # The numeric rows (Decimal debit/credit), not export_rows: the money
            # is formatted by the templates and the running balance is summed from
            # it, so it must not arrive pre-stringified.
            "rows": result.rows if result else [],
            "summary": result.summary if result else {},
            "is_empty": result.is_empty if result else True,
            "empty_message": EMPTY_MESSAGE,
        }

    def _choose_statement_builder(self, action_label: str):
        from app.ui.screens.customer_statement_print import choose_statement_builder

        return choose_statement_builder(self, action_label)

    def export_pdf(self) -> None:
        if not self._ensure_ran():
            return
        from app.ui.screens.customer_statement_print import export_statement_to_pdf

        builder = self._choose_statement_builder("تصدير PDF")
        if builder is None:
            return
        export_statement_to_pdf(self, self._statement_print_data(), html_builder=builder)

    def print_preview(self) -> None:
        if not self._ensure_ran():
            return
        from app.ui.screens.customer_statement_print import (
            CustomerStatementPreviewDialog,
        )

        builder = self._choose_statement_builder("المعاينة")
        if builder is None:
            return
        dialog = CustomerStatementPreviewDialog(
            self._statement_print_data(), parent=self, html_builder=builder
        )
        dialog.exec()

    def print_report(self) -> None:
        if not self._ensure_ran():
            return
        from app.ui.screens.customer_statement_print import print_statement

        builder = self._choose_statement_builder("الطباعة")
        if builder is None:
            return
        print_statement(self, self._statement_print_data(), html_builder=builder)

    def close_report(self) -> None:
        window = self.window()
        if window is not None:
            window.close()

    # --- small helpers ------------------------------------------------------
    def _ensure_ran(self) -> bool:
        """Preview/print/export require a search to have run first. An empty
        result is allowed (it prints the empty-state message safely)."""
        if self.current_result is None:
            QMessageBox.warning(self, "لا توجد بيانات", "الرجاء تنفيذ البحث أولاً.")
            return False
        return True

    @staticmethod
    def _combo_label(combo: QComboBox) -> str:
        """The selected label, or 'الكل' when nothing specific is chosen."""
        return "الكل" if combo.currentData() is None else combo.currentText()

    def _load_customer_options(self) -> list[dict[str, Any]]:
        try:
            return self.service.load_customer_options()
        except Exception:  # noqa: BLE001
            return []

    def _load_company_options(self) -> list[dict[str, Any]]:
        try:
            return self.service.load_company_options()
        except Exception:  # noqa: BLE001
            return []

    def _load_company_info(self) -> dict[str, str]:
        try:
            return self.service.company_info()
        except Exception:  # noqa: BLE001
            return {}

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
