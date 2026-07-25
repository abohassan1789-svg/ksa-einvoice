"""Standalone sales dashboard screen (داشبورد).

Five KPI cards (customers, products, cash sales, credit sales, receipt
vouchers) plus two sales charts (total per company, total per customer). The
customer / company / date-range filters drive the three money KPIs and both
charts; the two catalog counts are always full totals.
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QDate, QEvent, Qt
from PySide6.QtWidgets import (
    QBoxLayout,
    QComboBox,
    QCompleter,
    QDateEdit,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.services.crm_dashboard_service import CrmDashboardService
from app.ui.common.theme import BORDER, GREEN, GREEN_DARK

logger = logging.getLogger(__name__)

BG = "#F1F5F9"
CARD_BG = "#FFFFFF"
TEXT_DARK = "#0F172A"
TEXT_MUTED = "#64748B"
NO_DATA_MSG = "لا توجد بيانات كافية لعرض الرسم البياني"
UNSPECIFIED = "غير محدد"
MONEY_CURRENCY = "ر.س"

# KPI cards: (key, title, badge, accent). Keys in MONEY_KEYS render as formatted
# money (2 decimals + currency); the rest are plain integer counts.
KPI_CARDS = (
    ("customers", "عدد العملاء", "C", GREEN),
    ("products", "عدد الأصناف", "P", "#2563EB"),
    ("cash_sales", "إجمالي المبيعات النقدية", "N", "#0891B2"),
    ("credit_sales", "إجمالي المبيعات الآجلة", "A", "#F59E0B"),
    ("receipt_vouchers", "إجمالي سندات القبض", "R", "#7C3AED"),
)
MONEY_KEYS = frozenset({"cash_sales", "credit_sales", "receipt_vouchers"})


def _format_money(value: float) -> str:
    """Format a money amount as ``12,345.50 ر.س`` (2 decimals + currency)."""
    try:
        return f"{float(value):,.2f} {MONEY_CURRENCY}"
    except (TypeError, ValueError):
        return f"0.00 {MONEY_CURRENCY}"


class DashboardBarRow(QWidget):
    """Single RTL horizontal bar row (money-aware)."""

    def __init__(
        self,
        label: str,
        value: float,
        max_value: float,
        bar_color: str,
        money: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._label = label.strip() or UNSPECIFIED
        self._money = money
        self._value = max(0.0, float(value))
        self._max_value = max(1.0, float(max_value))
        self.setMinimumHeight(40)

        row = QHBoxLayout(self)
        row.setDirection(QBoxLayout.RightToLeft)
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(10)

        label_widget = QLabel(self._label)
        label_widget.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        label_widget.setWordWrap(True)
        label_widget.setFixedWidth(170)
        label_widget.setToolTip(self._label)
        label_widget.setStyleSheet(f"color:{TEXT_DARK}; font-size:12px; font-weight:800;")

        track = QFrame()
        track.setFixedHeight(13)
        track.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        track.setStyleSheet("background:#E2E8F0; border:none; border-radius:6px;")
        track_layout = QHBoxLayout(track)
        track_layout.setContentsMargins(0, 0, 0, 0)
        track_layout.setSpacing(0)
        fill = QFrame()
        fill.setStyleSheet(f"background:{bar_color}; border:none; border-radius:6px;")
        # Fixed 1000-unit resolution so float money values map to int stretch.
        fill_units = max(0, min(1000, int(round(self._value / self._max_value * 1000))))
        track_layout.addWidget(fill, fill_units)
        track_layout.addStretch(1000 - fill_units)

        value_text = _format_money(self._value) if self._money else f"{int(self._value):,}"
        value_widget = QLabel(value_text)
        value_widget.setAlignment(Qt.AlignCenter)
        value_widget.setMinimumWidth(130 if self._money else 58)
        value_widget.setStyleSheet(f"color:{TEXT_DARK}; font-size:12px; font-weight:900;")

        row.addWidget(label_widget)
        row.addWidget(track, 1)
        row.addWidget(value_widget)


class DashboardBarChart(QFrame):
    """Reusable compact horizontal bar panel (money-aware)."""

    def __init__(
        self,
        title: str,
        label_key: str,
        bar_color: str = GREEN,
        max_bars: int | None = None,
        min_height: int = 300,
        money: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._title = title
        self._label_key = label_key
        self._bar_color = bar_color
        self._max_bars = max_bars
        self._money = money
        self._rows: list[dict[str, Any]] = []
        self._source_category_count = 0

        self.setObjectName("chartPanel")
        self.setMinimumHeight(min_height)
        self.setLayoutDirection(Qt.RightToLeft)
        self.setStyleSheet(
            f"QFrame#chartPanel {{ background:{CARD_BG}; border:1px solid {BORDER}; border-radius:12px; }}"
        )
        box = QVBoxLayout(self)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(10)
        self._title_label = QLabel(title)
        self._title_label.setAlignment(Qt.AlignRight)
        self._title_label.setStyleSheet(f"color:{TEXT_DARK}; font-size:15px; font-weight:900;")
        self._summary = QLabel("")
        self._summary.setAlignment(Qt.AlignRight)
        self._summary.setStyleSheet(f"color:{TEXT_MUTED}; font-size:12px; font-weight:700;")
        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(6)
        box.addWidget(self._title_label)
        box.addWidget(self._summary)
        box.addWidget(self._body, 1)

    def set_data(
        self,
        title: str | None,
        data: list[dict[str, Any]] | None,
        label_key: str | None = None,
    ) -> None:
        if title:
            self._title = title
            self._title_label.setText(title)
        if label_key:
            self._label_key = label_key
        self._rows = self._normalise_rows(data or [])
        self._build_rows()

    def _build_rows(self) -> None:
        self._clear()
        if not self._rows:
            self._summary.setText("الإجمالي: 0")
            empty = QLabel(NO_DATA_MSG)
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet(f"color:{TEXT_MUTED}; font-size:13px; font-weight:700;")
            self._body_layout.addWidget(empty, 1)
            return
        total = sum(float(row["count"]) for row in self._rows)
        max_value = max(float(row["count"]) for row in self._rows)
        total_text = _format_money(total) if self._money else f"{int(total):,}"
        self._summary.setText(
            f"الإجمالي: {total_text} | التصنيفات: {self._source_category_count:,}"
        )
        for row in self._rows:
            self._body_layout.addWidget(
                DashboardBarRow(
                    str(row["label"]),
                    float(row["count"]),
                    max_value,
                    self._bar_color,
                    money=self._money,
                )
            )
        self._body_layout.addStretch(1)

    def _normalise_rows(self, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        cast = float if self._money else int
        totals: dict[str, Any] = {}
        for item in data:
            try:
                value = cast(item.get("count", 0))
            except (TypeError, ValueError):
                value = cast(0)
            if value <= 0:
                continue
            label = str(item.get(self._label_key, "") or UNSPECIFIED).strip() or UNSPECIFIED
            totals[label] = totals.get(label, cast(0)) + value
        self._source_category_count = len(totals)
        rows = [
            {"label": label, "count": count}
            for label, count in sorted(totals.items(), key=lambda row: (-row[1], row[0]))
        ]
        if self._max_bars is not None and len(rows) > self._max_bars:
            visible_count = max(1, self._max_bars - 1)
            head = rows[:visible_count]
            tail = rows[visible_count:]
            head.append({"label": "أخرى", "count": sum(cast(row["count"]) for row in tail)})
            rows = head
        return rows

    def _clear(self) -> None:
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()


class CrmDashboardPage(QWidget):
    """Sales dashboard with KPI cards, sales filters, and two sales charts."""

    def __init__(self, service: CrmDashboardService | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service or CrmDashboardService()
        self.setLayoutDirection(Qt.RightToLeft)
        self.setStyleSheet(f"CrmDashboardPage {{ background:{BG}; }}")
        self._card_value_labels: dict[str, QLabel] = {}
        self._build_ui()
        self._load_filter_options()
        self.refresh_dashboard()

    def event(self, event: QEvent) -> bool:
        result = super().event(event)
        if event.type() == QEvent.WindowActivate:
            self.refresh_dashboard()
        return result

    # --- layout -------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(f"background:{BG}; border:none;")
        outer.addWidget(scroll)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(22, 18, 22, 22)
        layout.setSpacing(14)
        layout.addWidget(self._build_header())
        layout.addWidget(self._build_cards())
        layout.addWidget(self._build_filters())
        layout.addWidget(self._build_charts(), 1)
        scroll.setWidget(content)

    def _build_header(self) -> QWidget:
        header = QWidget()
        box = QVBoxLayout(header)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(2)
        title = QLabel("داشبورد")
        title.setAlignment(Qt.AlignRight)
        title.setStyleSheet(f"color:{GREEN_DARK}; font-size:23px; font-weight:900;")
        subtitle = QLabel("ملخص المبيعات النقدية والآجلة وسندات القبض")
        subtitle.setAlignment(Qt.AlignRight)
        subtitle.setStyleSheet(f"color:{TEXT_MUTED}; font-size:12px; font-weight:700;")
        box.addWidget(title)
        box.addWidget(subtitle)
        return header

    def _build_cards(self) -> QWidget:
        section = QWidget()
        grid = QGridLayout(section)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(10)
        for index, (key, title, badge, accent) in enumerate(KPI_CARDS):
            grid.addWidget(self._make_card(key, title, badge, accent), 0, index)
            grid.setColumnStretch(index, 1)
        return section

    def _make_card(self, key: str, title: str, badge: str, accent: str) -> QFrame:
        card = QFrame()
        card.setMinimumHeight(96)
        card.setStyleSheet(
            f"background:{CARD_BG}; border:1px solid {BORDER}; border-right:4px solid {accent}; border-radius:10px;"
        )
        box = QVBoxLayout(card)
        box.setContentsMargins(14, 12, 14, 12)
        top = QHBoxLayout()
        top.setDirection(QBoxLayout.RightToLeft)
        title_label = QLabel(title)
        title_label.setStyleSheet(f"color:{TEXT_MUTED}; font-size:12px; font-weight:800; border:none;")
        badge_label = QLabel(badge)
        badge_label.setAlignment(Qt.AlignCenter)
        badge_label.setFixedSize(26, 26)
        badge_label.setStyleSheet(f"color:{accent}; background:#F8FAFC; border:1px solid #E2E8F0; border-radius:8px;")
        top.addWidget(title_label)
        top.addStretch(1)
        top.addWidget(badge_label)
        is_money = key in MONEY_KEYS
        value = QLabel(_format_money(0) if is_money else "0")
        value.setAlignment(Qt.AlignRight)
        value.setWordWrap(False)
        value_font_size = 22 if is_money else 28
        value.setStyleSheet(
            f"color:{TEXT_DARK}; font-size:{value_font_size}px; font-weight:900; border:none;"
        )
        box.addLayout(top)
        box.addWidget(value)
        self._card_value_labels[key] = value
        return card

    def _build_filters(self) -> QFrame:
        panel = QFrame()
        panel.setStyleSheet(f"background:{CARD_BG}; border:1px solid {BORDER}; border-radius:10px;")
        row = QHBoxLayout(panel)
        row.setDirection(QBoxLayout.RightToLeft)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(8)

        self.customer_combo = self._searchable_combo()
        self.company_combo = self._searchable_combo()
        self.date_from = self._date_edit()
        self.date_to = self._date_edit()

        for label, widget in (
            ("اسم العميل", self.customer_combo),
            ("اسم الشركة", self.company_combo),
            ("من تاريخ", self.date_from),
            ("إلى تاريخ", self.date_to),
        ):
            row.addWidget(self._field(label, widget))

        refresh = QPushButton("تحديث")
        reset = QPushButton("إعادة تعيين الفلاتر")
        refresh.setCursor(Qt.PointingHandCursor)
        refresh.setFixedHeight(34)
        refresh.setStyleSheet(
            f"QPushButton {{ background:{GREEN}; color:white; border:none; border-radius:8px; "
            "padding:7px 12px; font-weight:900; }}"
            f"QPushButton:hover {{ background:{GREEN_DARK}; }}"
        )
        reset.setCursor(Qt.PointingHandCursor)
        reset.setFixedHeight(34)
        reset.setStyleSheet(
            "QPushButton { background:#E2E8F0; color:#0F172A; border:none; border-radius:8px; "
            "padding:7px 12px; font-weight:900; } QPushButton:hover { background:#CBD5E1; }"
        )
        refresh.clicked.connect(self.refresh_dashboard)
        reset.clicked.connect(self._reset_filters)
        row.addWidget(refresh)
        row.addWidget(reset)
        row.addStretch(1)
        return panel

    def _field(self, label: str, widget: QWidget) -> QWidget:
        box_widget = QWidget()
        box = QVBoxLayout(box_widget)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(3)
        text = QLabel(label)
        text.setAlignment(Qt.AlignRight)
        text.setStyleSheet(f"color:{TEXT_MUTED}; font-size:11px; font-weight:800;")
        box.addWidget(text)
        box.addWidget(widget)
        return box_widget

    def _searchable_combo(self) -> QComboBox:
        combo = QComboBox()
        combo.setFixedWidth(190)
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.NoInsert)
        combo.setStyleSheet("background:#F8FAFC; border:1px solid #CBD5E1; border-radius:7px; padding:5px;")
        combo.addItem("الكل", None)
        completer = combo.completer()
        completer.setCompletionMode(QCompleter.PopupCompletion)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        return combo

    def _date_edit(self) -> QDateEdit:
        edit = QDateEdit()
        edit.setCalendarPopup(True)
        edit.setDisplayFormat("yyyy-MM-dd")
        edit.setMinimumDate(QDate(2000, 1, 1))
        edit.setSpecialValueText("")
        edit.setDate(edit.minimumDate())
        edit.setFixedWidth(120)
        edit.setStyleSheet("background:#F8FAFC; border:1px solid #CBD5E1; border-radius:7px; padding:5px;")
        return edit

    def _build_charts(self) -> QWidget:
        section = QWidget()
        grid = QGridLayout(section)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(12)
        self._company_chart = DashboardBarChart(
            "إجمالي مبيعات كل شركة", "company", GREEN, None, 360, money=True
        )
        self._customer_chart = DashboardBarChart(
            "إجمالي مبيعات كل عميل", "customer", "#2563EB", 12, 360, money=True
        )
        grid.addWidget(self._company_chart, 0, 0)
        grid.addWidget(self._customer_chart, 0, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        return section

    # --- data ---------------------------------------------------------------
    def _load_filter_options(self) -> None:
        try:
            options = self.service.get_filter_options()
        except Exception:  # noqa: BLE001
            logger.exception("Failed to load dashboard filter options")
            options = {}
        self._fill_combo(self.customer_combo, options.get("customers", []))
        self._fill_combo(self.company_combo, options.get("companies", []))

    def _fill_combo(self, combo: QComboBox, rows: list[dict[str, Any]]) -> None:
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("الكل", None)
        for row in rows:
            combo.addItem(str(row.get("label") or UNSPECIFIED), row.get("id"))
        combo.setCurrentIndex(0)
        combo.blockSignals(False)

    def _combo_value(self, combo: QComboBox) -> Any:
        """Resolve the selected id from an editable combo by its current text."""
        index = combo.findText(combo.currentText().strip())
        if index < 0:
            return None
        return combo.itemData(index)

    def _filters(self) -> dict[str, Any]:
        filters: dict[str, Any] = {}
        if (customer := self._combo_value(self.customer_combo)) is not None:
            filters["customer"] = customer
        if (company := self._combo_value(self.company_combo)) is not None:
            filters["company"] = company
        if self.date_from.date() != self.date_from.minimumDate():
            filters["date_from"] = self.date_from.date().toString("yyyy-MM-dd")
        if self.date_to.date() != self.date_to.minimumDate():
            filters["date_to"] = self.date_to.date().toString("yyyy-MM-dd")
        return filters

    def _reset_filters(self) -> None:
        self.customer_combo.setCurrentIndex(0)
        self.company_combo.setCurrentIndex(0)
        self.date_from.setDate(self.date_from.minimumDate())
        self.date_to.setDate(self.date_to.minimumDate())
        self.refresh_dashboard()

    def refresh_dashboard(self) -> None:
        filters = self._filters()
        try:
            self.update_cards(self.service.get_dashboard_counts(filters))
            self.update_charts(
                self.service.get_sales_by_company(filters),
                self.service.get_sales_by_customer(filters),
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to refresh dashboard")
            QMessageBox.warning(self, "تعذر تحديث لوحة التحكم", "حدث خطأ أثناء تحميل بيانات لوحة التحكم.")

    def update_cards(self, counts: dict[str, Any]) -> None:
        for key, label in self._card_value_labels.items():
            raw = counts.get(key, 0)
            if key in MONEY_KEYS:
                label.setText(_format_money(raw))
                continue
            try:
                label.setText(f"{int(raw):,}")
            except (TypeError, ValueError):
                label.setText("0")

    def update_charts(
        self,
        company_data: list[dict[str, Any]],
        customer_data: list[dict[str, Any]],
    ) -> None:
        self._company_chart.set_data("إجمالي مبيعات كل شركة", company_data, "company")
        self._customer_chart.set_data("إجمالي مبيعات كل عميل", customer_data, "customer")
