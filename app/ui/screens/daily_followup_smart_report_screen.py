"""Daily Follow-up Smart Report screen."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QObject, QSignalBlocker, QTimer, Qt, Signal
from PySide6.QtGui import QTextDocument
from PySide6.QtPrintSupport import QPrintDialog, QPrintPreviewDialog, QPrinter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDateEdit,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.reports.excel_exporter import ExcelExporter
from app.reports.pdf_exporter import PdfExporter
from app.reports.print_manager import PrintManager
from app.services.daily_followup_report_service import (
    DailyFollowupReportRequest,
    DailyFollowupReportService,
    DailyFollowupReportSettingsService,
    SmartFilterCondition,
)
from app.ui.common.theme import GREEN, GREEN_DARK, TEXT, _button_style


REPORT_TITLE = "Daily Follow-up Smart Report"


class _CustomerSearchLineEdit(QLineEdit):
    arrow_pressed = Signal(int)
    return_pressed = Signal()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.key() in (Qt.Key_Up, Qt.Key_Down):
            self.arrow_pressed.emit(event.key())
            event.accept()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.return_pressed.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class _FilterRow(QWidget):
    OPERATORS = (
        ("contains", "Contains"),
        ("equals", "Equals"),
        ("not_equals", "Not Equals"),
        ("starts_with", "Starts With"),
        ("ends_with", "Ends With"),
        ("greater_than", "Greater Than"),
        ("less_than", "Less Than"),
        ("between", "Between"),
    )

    def __init__(
        self,
        columns: list[Any],
        filter_options: dict[str, list[dict[str, Any]]],
        delete_callback,
        customer_lookup_callback=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.columns = columns
        self.filter_options = filter_options
        self.delete_callback = delete_callback
        self.customer_lookup_callback = customer_lookup_callback
        self._using_value_combo = False
        self._populating_value_combo = False
        self._customer_lookup_request_id = 0
        self._selected_customer_option: dict[str, Any] | None = None
        self._customer_lookup_completer: QCompleter | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.column_combo = QComboBox()
        self._make_combo_searchable(self.column_combo)
        for column in self.columns:
            self.column_combo.addItem(column.label, column.key)

        self.operator_combo = QComboBox()
        for code, label in self.OPERATORS:
            self.operator_combo.addItem(label, code)

        self.value_input = QLineEdit()
        self.value_input.setPlaceholderText("Value")
        self.value_combo = QComboBox()
        self.value_combo.setEditable(True)
        self.value_combo.setLineEdit(_CustomerSearchLineEdit())
        self._make_combo_searchable(self.value_combo)
        self.value_combo.lineEdit().installEventFilter(self)
        self.value_combo.lineEdit().arrow_pressed.connect(self._move_customer_lookup_selection)
        self.value_combo.lineEdit().return_pressed.connect(self._select_current_customer_completion)
        self._customer_lookup_timer = QTimer(self)
        self._customer_lookup_timer.setSingleShot(True)
        self._customer_lookup_timer.setInterval(350)
        self._customer_lookup_timer.timeout.connect(self._run_customer_lookup)
        self.value_to_input = QLineEdit()
        self.value_to_input.setPlaceholderText("To")
        self.value_to_input.setVisible(False)
        delete_button = QPushButton("Delete")
        delete_button.setStyleSheet(_button_style("#DC2626", "#B91C1C"))

        for widget in (self.column_combo, self.operator_combo, self.value_input, self.value_combo, self.value_to_input):
            widget.setMinimumHeight(34)
        self.value_combo.setVisible(False)
        delete_button.setFixedHeight(34)

        layout.addWidget(delete_button)
        layout.addWidget(self.value_to_input)
        layout.addWidget(self.value_combo, 1)
        layout.addWidget(self.value_input, 1)
        layout.addWidget(self.operator_combo)
        layout.addWidget(self.column_combo)

        self.column_combo.currentIndexChanged.connect(self._refresh_value_editor)
        self.operator_combo.currentIndexChanged.connect(self._refresh_between)
        self.value_combo.lineEdit().textChanged.connect(self._schedule_customer_lookup)
        self.value_combo.currentIndexChanged.connect(self._remember_selected_customer)
        delete_button.clicked.connect(lambda: self.delete_callback(self))
        self._refresh_value_editor()
        self._refresh_between()

    def condition(self) -> SmartFilterCondition | None:
        column_key = self.column_combo.currentData()
        if column_key not in {column.key for column in self.columns}:
            return None
        value = self._current_value()
        value_to = self.value_to_input.text().strip()
        operator = self.operator_combo.currentData()
        if value in (None, "") and operator != "between":
            return None
        if operator == "between" and (value in (None, "") or not value_to):
            return None
        return SmartFilterCondition(column_key, operator, value, value_to)

    def set_condition(self, condition: dict[str, Any]) -> None:
        column_index = self.column_combo.findData(condition.get("column"))
        if column_index >= 0:
            self.column_combo.setCurrentIndex(column_index)
        operator_index = self.operator_combo.findData(condition.get("operator"))
        if operator_index >= 0:
            self.operator_combo.setCurrentIndex(operator_index)
        value = condition.get("value")
        if self._using_value_combo:
            value_index = self.value_combo.findData(value)
            if value_index < 0 and value not in (None, ""):
                value_index = self.value_combo.findText(str(value), Qt.MatchFixedString)
            self.value_combo.setCurrentIndex(max(0, value_index))
            if value_index < 0 and value not in (None, ""):
                self.value_combo.setEditText(str(value))
        else:
            self.value_input.setText("" if value is None else str(value))
        value_to = condition.get("value_to")
        self.value_to_input.setText("" if value_to is None else str(value_to))

    def _refresh_value_editor(self) -> None:
        column_key = self.column_combo.currentData()
        if column_key == "customer_name":
            self.value_combo.setCompleter(None)
            self.value_combo.lineEdit().setCompleter(None)
            self._customer_lookup_completer = None
        else:
            if self._customer_lookup_completer is not None:
                self._customer_lookup_completer.popup().hide()
                self._customer_lookup_completer = None
            self._make_combo_searchable(self.value_combo)
        kind = ""
        for column in self.columns:
            if column.key == column_key:
                kind = column.filter_kind
                break
        options_key = {
            "status": "statuses",
            "employee": "employees",
            "area": "areas",
            "customer": "customers",
        }.get(kind)
        self.value_combo.clear()
        self._using_value_combo = bool(options_key)
        if options_key:
            self.value_combo.addItem("", None)
            for option in self.filter_options.get(options_key, []):
                self.value_combo.addItem(
                    str(option.get("label") or ""),
                    self._option_data_for_column(column_key, option),
                )
            self.value_combo.setVisible(True)
            self.value_input.setVisible(False)
        else:
            self.value_combo.setVisible(False)
            self.value_input.setVisible(True)
        if column_key == "customer_name":
            self._selected_customer_option = self._option_from_current_customer_selection()

    def _refresh_between(self) -> None:
        self.value_to_input.setVisible(self.operator_combo.currentData() == "between")

    def _current_value(self) -> Any:
        if self._using_value_combo:
            data = self.value_combo.currentData()
            text = self.value_combo.currentText().strip()
            if text in {"Loading...", "No customers found"}:
                return None
            if self.column_combo.currentData() == "customer_name":
                return data
            return text if data is None else data
        return self.value_input.text().strip()

    def _schedule_customer_lookup(self, text: str) -> None:
        if self._populating_value_combo or self.column_combo.currentData() != "customer_name":
            return
        if not callable(self.customer_lookup_callback):
            return
        self._customer_lookup_timer.start()

    def _run_customer_lookup(self) -> None:
        if not callable(self.customer_lookup_callback):
            return
        query = self.value_combo.lineEdit().text().strip()
        self._customer_lookup_request_id += 1
        request_id = self._customer_lookup_request_id
        self._set_customer_lookup_state("Loading...")
        QApplication.processEvents()
        try:
            options = self.customer_lookup_callback(query, limit=100)
        except Exception:
            options = []
        self._apply_customer_lookup_results(request_id, query, options)

    def _apply_customer_lookup_results(
        self,
        request_id: int,
        query: str,
        options: list[dict[str, Any]],
    ) -> None:
        if request_id < self._customer_lookup_request_id:
            return
        current_selection = self._selected_customer_option
        self._populating_value_combo = True
        line_edit = self.value_combo.lineEdit()
        blockers = [QSignalBlocker(self.value_combo), QSignalBlocker(line_edit)]
        try:
            self.value_combo.setCompleter(None)
            line_edit.setCompleter(None)
            self._customer_lookup_completer = None
            self.value_combo.clear()
            if not query.strip():
                self.value_combo.addItem("", None)
            for option in options[:100]:
                self._add_customer_option(option)
            if current_selection and self.value_combo.findData(current_selection.get("id")) < 0:
                self._add_customer_option(current_selection)
            if self.value_combo.count() == 0 and query.strip():
                self.value_combo.addItem("No customers found", None)
            if current_selection:
                index = self.value_combo.findData(current_selection.get("id"))
                if index >= 0:
                    self.value_combo.setCurrentIndex(index)
            elif query.strip():
                self.value_combo.setCurrentIndex(-1)
                line_edit.setText(query.strip())
            else:
                self.value_combo.setCurrentIndex(0)
        finally:
            del blockers
            self._populating_value_combo = False
        if query.strip():
            self._show_customer_lookup_completer()

    def _show_customer_lookup_completer(self) -> None:
        completer = QCompleter(self.value_combo.model(), self.value_combo)
        completer.setCompletionMode(QCompleter.UnfilteredPopupCompletion)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        popup = completer.popup()
        popup.setFocusPolicy(Qt.NoFocus)
        popup.installEventFilter(self)
        completer.activated[str].connect(self._select_customer_completion)
        line_edit = self.value_combo.lineEdit()
        line_edit.setCompleter(completer)
        self._customer_lookup_completer = completer
        self._restore_customer_search_focus()
        completer.setCompletionPrefix("")
        completer.complete()
        QTimer.singleShot(25, self._restore_customer_search_focus)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            watched is self.value_combo.lineEdit()
            and event.type() == QEvent.FocusOut
            and self.column_combo.currentData() == "customer_name"
            and self._customer_lookup_completer is not None
            and self._customer_lookup_completer.popup().isVisible()
        ):
            QTimer.singleShot(25, self._restore_customer_search_focus)
        popup = self._customer_lookup_completer.popup() if self._customer_lookup_completer else None
        if (
            event.type() == QEvent.KeyPress
            and watched in (self.value_combo.lineEdit(), popup)
            and self.column_combo.currentData() == "customer_name"
            and popup is not None
            and popup.isVisible()
            and event.key() in (Qt.Key_Up, Qt.Key_Down)
        ):
            current_row = popup.currentIndex().row()
            if current_row < 0:
                current_row = 0 if event.key() == Qt.Key_Down else popup.model().rowCount() - 1
            else:
                current_row += 1 if event.key() == Qt.Key_Down else -1
            current_row = max(0, min(current_row, popup.model().rowCount() - 1))
            popup.setCurrentIndex(popup.model().index(current_row, 0))
            QTimer.singleShot(50, self._restore_customer_search_focus)
            event.accept()
            return True
        return super().eventFilter(watched, event)

    def _move_customer_lookup_selection(self, key: int) -> None:
        if (
            self.column_combo.currentData() != "customer_name"
            or self._customer_lookup_completer is None
            or not self._customer_lookup_completer.popup().isVisible()
        ):
            return
        popup = self._customer_lookup_completer.popup()
        current_row = popup.currentIndex().row()
        if current_row < 0:
            current_row = 0 if key == Qt.Key_Down else popup.model().rowCount() - 1
        else:
            current_row += 1 if key == Qt.Key_Down else -1
        current_row = max(0, min(current_row, popup.model().rowCount() - 1))
        popup.setCurrentIndex(popup.model().index(current_row, 0))
        QTimer.singleShot(50, self._restore_customer_search_focus)

    def _select_current_customer_completion(self) -> None:
        if self._customer_lookup_completer is None:
            return
        popup = self._customer_lookup_completer.popup()
        if not popup.isVisible():
            return
        index = popup.currentIndex()
        if index.isValid():
            self._select_customer_completion(str(index.data() or ""))

    def _select_customer_completion(self, label: str) -> None:
        index = self.value_combo.findText(label, Qt.MatchFixedString)
        if index < 0 or self.value_combo.itemData(index) is None:
            return
        self.value_combo.setCurrentIndex(index)
        self._restore_customer_search_focus()

    def _restore_customer_search_focus(self) -> None:
        if self.column_combo.currentData() != "customer_name":
            return
        line_edit = self.value_combo.lineEdit()
        line_edit.setFocus(Qt.PopupFocusReason)
        line_edit.setCursorPosition(len(line_edit.text()))

    def _set_customer_lookup_state(self, label: str) -> None:
        self._populating_value_combo = True
        line_edit = self.value_combo.lineEdit()
        blockers = [QSignalBlocker(self.value_combo), QSignalBlocker(line_edit)]
        try:
            self.value_combo.clear()
            self.value_combo.addItem(label, None)
        finally:
            del blockers
            self._populating_value_combo = False

    def _add_customer_option(self, option: dict[str, Any]) -> None:
        self.value_combo.addItem(
            str(option.get("label") or ""),
            self._option_data_for_column("customer_name", option),
        )

    def _remember_selected_customer(self, *_args) -> None:
        if self._populating_value_combo or self.column_combo.currentData() != "customer_name":
            return
        self._selected_customer_option = self._option_from_current_customer_selection()

    def _option_from_current_customer_selection(self) -> dict[str, Any] | None:
        if self.column_combo.currentData() != "customer_name":
            return None
        customer_id = self.value_combo.currentData()
        if customer_id is None:
            return None
        return {
            "id": customer_id,
            "label": self.value_combo.currentText(),
            "name": "",
            "phone": "",
        }

    @staticmethod
    def _make_combo_searchable(combo: QComboBox) -> None:
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.NoInsert)
        completer = combo.completer()
        if completer is None:
            completer = QCompleter(combo.model(), combo)
            combo.setCompleter(completer)
        completer.setCompletionMode(QCompleter.PopupCompletion)
        completer.setFilterMode(Qt.MatchContains)

    @staticmethod
    def _option_data_for_column(column_key: str, option: dict[str, Any]) -> Any:
        if column_key in {"customer_id", "place_area_feddan"}:
            return option.get("id")
        if column_key == "customer_name":
            return option.get("id")
        if column_key == "customer_phone":
            return option.get("phone") or option.get("label")
        return option.get("label")


class DailyFollowupSmartReportScreen(QWidget):
    """RTL smart report UI for Daily Follow-up data."""

    def __init__(
        self,
        report_service: DailyFollowupReportService | None = None,
        settings_service: DailyFollowupReportSettingsService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.report_service = report_service or DailyFollowupReportService()
        self.settings_service = settings_service or DailyFollowupReportSettingsService()
        self.filter_options = self._load_filter_options()
        self.filter_rows: list[_FilterRow] = []
        self.current_columns: list[Any] = []
        self.current_rows: list[dict[str, Any]] = []
        self.column_checks: dict[str, QCheckBox] = {}
        self.setWindowTitle(REPORT_TITLE)
        self.setLayoutDirection(Qt.RightToLeft)
        self.resize(1450, 850)
        self._build_ui()
        self._restore_settings()

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)
        root.addWidget(self._build_main_panel(), 1)
        root.addWidget(self._build_columns_panel())

    def _build_main_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self._build_header())
        layout.addWidget(self._build_filter_bar())
        layout.addWidget(self._build_smart_filters())
        layout.addWidget(self._build_actions())
        layout.addWidget(self._build_table(), 1)
        return panel

    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setStyleSheet(
            f"QFrame {{ background:{GREEN}; border-radius:6px; }}"
            "QLabel { background:transparent; color:#FFFFFF; }"
        )
        layout = QHBoxLayout(header)
        title = QLabel(REPORT_TITLE)
        title.setStyleSheet("font-size:24px; font-weight:900;")
        layout.addWidget(title)
        layout.addStretch(1)
        return header

    def _build_filter_bar(self) -> QGroupBox:
        box = QGroupBox("Date Filters")
        box.setStyleSheet(self._group_style())
        layout = QHBoxLayout(box)
        self.from_date = QDateEdit()
        self.to_date = QDateEdit()
        for editor in (self.from_date, self.to_date):
            editor.setCalendarPopup(True)
            editor.setDisplayFormat("yyyy-MM-dd")
            editor.setSpecialValueText("")
            editor.setMinimumDate(_dt.date(1900, 1, 1))
            editor.setDate(editor.minimumDate())
            editor.setMinimumHeight(36)
        apply_button = QPushButton("Apply Filter")
        clear_button = QPushButton("Clear Filters")
        refresh_button = QPushButton("Refresh")
        apply_button.setStyleSheet(_button_style(GREEN, GREEN_DARK))
        clear_button.setStyleSheet(_button_style("#6B7280", "#4B5563"))
        refresh_button.setStyleSheet(_button_style("#FFFFFF", "#F3F4F6", "#374151"))
        apply_button.clicked.connect(self.apply_filters)
        clear_button.clicked.connect(self.clear_filters)
        refresh_button.clicked.connect(self.apply_filters)
        self.quick_search = QLineEdit()
        self.quick_search.setPlaceholderText("Quick search in loaded results")
        self.quick_search.textChanged.connect(self._apply_quick_search_to_table)
        layout.addWidget(refresh_button)
        layout.addWidget(clear_button)
        layout.addWidget(apply_button)
        layout.addWidget(self.quick_search, 1)
        layout.addWidget(QLabel("To Date"))
        layout.addWidget(self.to_date)
        layout.addWidget(QLabel("From Date"))
        layout.addWidget(self.from_date)
        return box

    def _build_smart_filters(self) -> QGroupBox:
        box = QGroupBox("Smart Filters")
        box.setStyleSheet(self._group_style())
        layout = QVBoxLayout(box)
        top = QHBoxLayout()
        self.join_mode = QComboBox()
        self.join_mode.addItems(["AND", "OR"])
        add_button = QPushButton("Add New Condition")
        add_button.setStyleSheet(_button_style(GREEN, GREEN_DARK))
        add_button.clicked.connect(self.add_filter_row)
        top.addWidget(add_button)
        top.addStretch(1)
        top.addWidget(QLabel("Condition Join Mode"))
        top.addWidget(self.join_mode)
        layout.addLayout(top)
        self.filter_rows_container = QWidget()
        self.filter_rows_layout = QVBoxLayout(self.filter_rows_container)
        self.filter_rows_layout.setContentsMargins(0, 0, 0, 0)
        self.filter_rows_layout.setSpacing(6)
        layout.addWidget(self.filter_rows_container)
        self.add_filter_row()
        return box

    def _build_actions(self) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet("QFrame { background:#FFFFFF; border:1px solid #E5EAF0; border-radius:7px; }")
        layout = QHBoxLayout(frame)
        self.count_label = QLabel("0 records")
        buttons = [
            ("Print Preview", self.print_preview),
            ("Print", self.print_report),
            ("Export Excel", self.export_excel),
            ("Export PDF", self.export_pdf),
        ]
        for text, callback in buttons:
            button = QPushButton(text)
            button.setMinimumHeight(36)
            button.setStyleSheet(_button_style(GREEN, GREEN_DARK))
            button.clicked.connect(lambda _checked=False, cb=callback: cb())
            layout.addWidget(button)
        layout.addStretch(1)
        layout.addWidget(self.count_label)
        return frame

    def _build_table(self) -> QTableWidget:
        self.table = QTableWidget()
        self.table.setSortingEnabled(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setStyleSheet(
            "QTableWidget { background:#FFFFFF; alternate-background-color:#F8FAFC; border:1px solid #E2E8F0; "
            "gridline-color:#E5E7EB; font-size:13px; }"
            f"QHeaderView::section {{ background:{GREEN}; color:#FFFFFF; font-weight:900; border:none; padding:10px 8px; }}"
        )
        return self.table

    def _build_columns_panel(self) -> QGroupBox:
        box = QGroupBox("Report Columns")
        box.setFixedWidth(285)
        box.setStyleSheet(self._group_style())
        layout = QVBoxLayout(box)
        actions = QHBoxLayout()
        select_all = QPushButton("Select All")
        unselect_all = QPushButton("Unselect All")
        restore = QPushButton("Restore Default")
        for button in (select_all, unselect_all, restore):
            button.setMinimumHeight(32)
        select_all.clicked.connect(lambda: self._set_all_columns(True))
        unselect_all.clicked.connect(lambda: self._set_all_columns(False))
        restore.clicked.connect(self.restore_default_columns)
        actions.addWidget(restore)
        actions.addWidget(unselect_all)
        actions.addWidget(select_all)
        layout.addLayout(actions)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        for column in self.report_service.columns:
            check = QCheckBox(column.label)
            check.setProperty("column_key", column.key)
            check.setChecked(column.key in DailyFollowupReportService.DEFAULT_COLUMN_KEYS)
            self.column_checks[column.key] = check
            content_layout.addWidget(check)
        content_layout.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        return box

    def add_filter_row(self) -> None:
        row = _FilterRow(
            self.report_service.columns,
            self.filter_options,
            self.remove_filter_row,
            customer_lookup_callback=self._lookup_customers,
        )
        self.filter_rows.append(row)
        self.filter_rows_layout.addWidget(row)

    def remove_filter_row(self, row: _FilterRow) -> None:
        if row in self.filter_rows:
            self.filter_rows.remove(row)
        row.setParent(None)
        row.deleteLater()

    def set_selected_columns(self, keys: list[str]) -> None:
        selected = set(keys)
        for key, check in self.column_checks.items():
            check.setChecked(key in selected)

    def selected_column_keys(self) -> list[str]:
        return [key for key, check in self.column_checks.items() if check.isChecked()]

    def restore_default_columns(self) -> None:
        self.set_selected_columns(list(DailyFollowupReportService.DEFAULT_COLUMN_KEYS))

    def clear_filters(self) -> None:
        self.from_date.setDate(self.from_date.minimumDate())
        self.to_date.setDate(self.to_date.minimumDate())
        self.join_mode.setCurrentText("AND")
        self.quick_search.clear()
        for row in list(self.filter_rows):
            self.remove_filter_row(row)
        self.add_filter_row()
        self.restore_default_columns()
        self.apply_filters()

    def apply_filters(self) -> None:
        request = self._build_request()
        try:
            result = self.report_service.fetch_report(request)
        except Exception as exc:
            QMessageBox.critical(self, "Failed to load report", str(exc))
            return
        self.current_columns = result.columns
        self.current_rows = result.rows
        self._fill_table(result.columns, result.rows)
        self._save_settings(request)

    def _build_request(self) -> DailyFollowupReportRequest:
        return DailyFollowupReportRequest(
            selected_columns=self.selected_column_keys(),
            date_from=self._date_value(self.from_date),
            date_to=self._date_value(self.to_date),
            filters=[condition for row in self.filter_rows if (condition := row.condition())],
            join_mode=self.join_mode.currentText(),
            quick_search="",
        )

    def _fill_table(self, columns: list[Any], rows: list[dict[str, Any]]) -> None:
        self.table.setSortingEnabled(False)
        self.table.clear()
        self.table.setColumnCount(len(columns))
        self.table.setHorizontalHeaderLabels([column.label for column in columns])
        visible_rows = self._filter_loaded_rows(rows)
        self.table.setRowCount(len(visible_rows))
        for row_index, row in enumerate(visible_rows):
            for column_index, column in enumerate(columns):
                value = row.get(column.key)
                item = QTableWidgetItem("" if value is None else str(value))
                item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row_index, column_index, item)
        self.table.resizeColumnsToContents()
        self.table.setSortingEnabled(True)
        self.count_label.setText(f"Result count: {len(visible_rows)}")

    def _apply_quick_search_to_table(self) -> None:
        self._fill_table(self.current_columns, self.current_rows)

    def _filter_loaded_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        text = self.quick_search.text().strip().lower()
        if not text:
            return rows
        return [
            row for row in rows
            if any(text in str(row.get(column.key) or "").lower() for column in self.current_columns)
        ]

    def export_excel(self, path: str | Path | None = None) -> None:
        if isinstance(path, bool):
            path = None
        output = Path(path) if path else self._save_path("Excel Files (*.xlsx)", "xlsx")
        if output:
            ExcelExporter().export_table(output, REPORT_TITLE, self.current_columns, self._filter_loaded_rows(self.current_rows))

    def export_pdf(self, path: str | Path | None = None) -> None:
        if isinstance(path, bool):
            path = None
        output = Path(path) if path else self._save_path("PDF Files (*.pdf)", "pdf")
        if output:
            PdfExporter().export_table(output, REPORT_TITLE, self.current_columns, self._filter_loaded_rows(self.current_rows))

    def print_preview(self) -> None:
        document = self._print_document()
        dialog = QPrintPreviewDialog(self)
        dialog.paintRequested.connect(document.print_)
        dialog.exec()

    def print_report(self) -> None:
        printer = QPrinter(QPrinter.HighResolution)
        dialog = QPrintDialog(printer, self)
        if dialog.exec() == QPrintDialog.Accepted:
            self._print_document().print_(printer)

    def _print_document(self) -> QTextDocument:
        document = QTextDocument()
        document.setHtml(
            PrintManager().build_html(REPORT_TITLE, self.current_columns, self._filter_loaded_rows(self.current_rows))
        )
        return document

    def _restore_settings(self) -> None:
        settings = self.settings_service.load()
        selected = settings.get("selected_columns")
        if isinstance(selected, list) and selected:
            self.set_selected_columns([str(key) for key in selected])
        join_mode = str(settings.get("join_mode") or "AND").upper()
        if join_mode in {"AND", "OR"}:
            self.join_mode.setCurrentText(join_mode)
        self._restore_date(self.from_date, settings.get("date_from"))
        self._restore_date(self.to_date, settings.get("date_to"))
        filters = settings.get("filters")
        if isinstance(filters, list):
            for row in list(self.filter_rows):
                self.remove_filter_row(row)
            for condition in filters:
                if not isinstance(condition, dict):
                    continue
                row = _FilterRow(
                    self.report_service.columns,
                    self.filter_options,
                    self.remove_filter_row,
                    customer_lookup_callback=self._lookup_customers,
                )
                row.set_condition(condition)
                self.filter_rows.append(row)
                self.filter_rows_layout.addWidget(row)
            if not self.filter_rows:
                self.add_filter_row()

    def _save_settings(self, request: DailyFollowupReportRequest) -> None:
        self.settings_service.save(
            {
                "selected_columns": request.selected_columns,
                "join_mode": request.join_mode,
                "date_from": request.date_from,
                "date_to": request.date_to,
                "filters": [
                    {
                        "column": condition.column,
                        "operator": condition.operator,
                        "value": condition.value,
                        "value_to": condition.value_to,
                    }
                    for condition in request.filters
                ],
                "column_order": request.selected_columns,
            }
        )

    def _load_filter_options(self) -> dict[str, list[dict[str, Any]]]:
        try:
            return self.report_service.load_filter_options()
        except Exception:
            return {"statuses": [], "employees": [], "areas": [], "customers": []}

    def _lookup_customers(self, text: str, limit: int = 100) -> list[dict[str, Any]]:
        lookup = getattr(self.report_service, "lookup_customers", None)
        if callable(lookup):
            return lookup(text, limit=limit)
        rows = self.report_service.search_customers(text, limit=limit)
        return [
            {
                "id": row.get("customer_id"),
                "label": DailyFollowupReportService._customer_option_label(row),
                "name": row.get("customer_name") or "",
                "code": row.get("customer_code") or row.get("customer_id"),
                "phone": row.get("phone_number") or "",
            }
            for row in rows[:limit]
        ]

    def _set_all_columns(self, checked: bool) -> None:
        for check in self.column_checks.values():
            check.setChecked(checked)

    def _save_path(self, file_filter: str, suffix: str) -> Path | None:
        path, _selected_filter = QFileDialog.getSaveFileName(self, "Save report", f"{REPORT_TITLE}.{suffix}", file_filter)
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
    def _restore_date(editor: QDateEdit, value: Any) -> None:
        if not value:
            return
        date = editor.date().fromString(str(value), "yyyy-MM-dd")
        if date.isValid():
            editor.setDate(date)

    @staticmethod
    def _group_style() -> str:
        return (
            "QGroupBox { background:#FFFFFF; border:1px solid #E2E8F0; border-radius:7px; margin-top:14px; }"
            f"QGroupBox::title {{ subcontrol-origin:margin; subcontrol-position:top right; right:16px; "
            f"padding:0 12px; color:{GREEN}; font-weight:900; }}"
            f"QLabel {{ color:{TEXT}; font-weight:800; }}"
        )
