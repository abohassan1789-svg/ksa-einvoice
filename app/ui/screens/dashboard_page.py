"""Existing home dashboard embedded in the main window content area."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.services.dashboard_service import DashboardService
from app.ui.common.theme import BORDER, GREEN, GREEN_DARK

BG = "#F1F5F9"
CARD_BG = "#FFFFFF"
TEXT_DARK = "#0F172A"
TEXT_MUTED = "#64748B"
NO_DATA_MSG = "لا توجد بيانات كافية لعرض الرسم البياني"
UNSPECIFIED = "غير محدد"
MONEY_CURRENCY = "ر.س"

# KPI cards: (key, title, badge, accent). Keys in MONEY_KEYS are rendered as
# formatted money (2 decimals + currency); the rest are plain integer counts.
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
    """Single responsive RTL bar row rendered with ordinary Qt widgets."""

    clicked = Signal(str, list)

    def __init__(
        self,
        label: str,
        source_labels: list[str],
        value: float,
        max_value: float,
        total_value: float,
        bar_color: str,
        money: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._label = label.strip() or UNSPECIFIED
        self._source_labels = source_labels or [self._label]
        self._money = money
        self._value = max(0.0, float(value))
        self._max_value = max(1.0, float(max_value))
        self._total_value = max(1.0, float(total_value))
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(42)

        row = QHBoxLayout(self)
        row.setDirection(QBoxLayout.RightToLeft)
        row.setContentsMargins(0, 3, 0, 3)
        row.setSpacing(10)

        label_widget = QLabel(self._label)
        label_widget.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        label_widget.setWordWrap(True)
        label_widget.setFixedWidth(180)
        label_widget.setToolTip(self._label)
        label_widget.setStyleSheet(f"color:{TEXT_DARK}; font-size:12px; font-weight:800;")

        track = QFrame()
        track.setFixedHeight(14)
        track.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        track.setStyleSheet("background:#E2E8F0; border:none; border-radius:7px;")
        track_layout = QHBoxLayout(track)
        track_layout.setContentsMargins(0, 0, 0, 0)
        track_layout.setSpacing(0)
        fill = QFrame()
        fill.setStyleSheet(f"background:{bar_color}; border:none; border-radius:7px;")
        # Bar width uses a fixed 1000-unit resolution so float money values (and
        # plain counts) both map to integer Qt stretch factors.
        fill_units = max(0, min(1000, int(round(self._value / self._max_value * 1000))))
        track_layout.addWidget(fill, fill_units)
        track_layout.addStretch(1000 - fill_units)

        value_text = _format_money(self._value) if self._money else f"{int(self._value):,}"
        value_widget = QLabel(value_text)
        value_widget.setAlignment(Qt.AlignCenter)
        value_widget.setMinimumWidth(58 if not self._money else 130)
        value_widget.setStyleSheet(f"color:{TEXT_DARK}; font-size:12px; font-weight:900;")

        row.addWidget(label_widget)
        row.addWidget(track, 1)
        row.addWidget(value_widget)

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._label, self._source_labels)
        super().mousePressEvent(event)


class DashboardBarChart(QFrame):
    """Reusable home dashboard chart panel with RTL horizontal bars."""

    slice_clicked = Signal(str, str, list)

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
        self._value_key = "count"
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
        self._body_layout.setSpacing(7)
        box.addWidget(self._title_label)
        box.addWidget(self._summary)
        box.addWidget(self._body, 1)

    def set_data(
        self,
        title: str | None,
        data: list[dict[str, Any]] | None,
        label_key: str | None = None,
        value_key: str = "count",
    ) -> None:
        if title:
            self._title = title
            self._title_label.setText(title)
        if label_key:
            self._label_key = label_key
        self._value_key = value_key
        self._rows = self._normalise_rows(data or [])
        self.build_rows()

    def build_rows(self) -> None:
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
            item = DashboardBarRow(
                str(row["label"]),
                list(row.get("source_labels", [row["label"]])),
                float(row["count"]),
                max_value,
                total,
                self._bar_color,
                money=self._money,
            )
            item.clicked.connect(self._emit_slice_clicked)
            self._body_layout.addWidget(item)
        self._body_layout.addStretch(1)

    def _normalise_rows(self, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # Money charts keep fractional totals; count charts stay integer-valued.
        cast = float if self._money else int
        totals: dict[str, Any] = {}
        source_labels: dict[str, list[str]] = {}
        for item in data:
            try:
                value = cast(item.get(self._value_key, 0))
            except (TypeError, ValueError):
                value = cast(0)
            if value <= 0:
                continue
            label = str(item.get(self._label_key, "") or UNSPECIFIED).strip() or UNSPECIFIED
            totals[label] = totals.get(label, cast(0)) + value
            source_labels.setdefault(label, [])
            if label not in source_labels[label]:
                source_labels[label].append(label)
        self._source_category_count = len(totals)
        rows = [
            {"label": label, "count": count, "source_labels": source_labels[label]}
            for label, count in sorted(totals.items(), key=lambda row: (-row[1], row[0]))
        ]
        if self._max_bars is not None and len(rows) > self._max_bars:
            visible_count = max(1, self._max_bars - 1)
            head = rows[:visible_count]
            tail = rows[visible_count:]
            head.append(
                {
                    "label": "أخرى",
                    "count": sum(cast(row["count"]) for row in tail),
                    "source_labels": [
                        label for row in tail for label in row.get("source_labels", [row["label"]])
                    ],
                }
            )
            rows = head
        return rows

    def _emit_slice_clicked(self, label: str, source_labels: list[str]) -> None:
        self.slice_clicked.emit(self._label_key, label, source_labels)

    def _clear(self) -> None:
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()


class DashboardPage(QWidget):
    """Existing compact dashboard shown as the main window home page."""

    def __init__(self, service: DashboardService | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service or DashboardService()
        self.setLayoutDirection(Qt.RightToLeft)
        self.setStyleSheet(f"DashboardPage {{ background:{BG}; }}")
        self._card_value_labels: dict[str, QLabel] = {}
        self._build_ui()
        self.refresh_dashboard()

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
        layout.setContentsMargins(24, 22, 24, 24)
        layout.setSpacing(18)
        layout.addWidget(self._header())
        layout.addWidget(self._cards())
        layout.addWidget(self._charts(), 1)
        scroll.setWidget(content)

    def _header(self) -> QWidget:
        header = QWidget()
        box = QVBoxLayout(header)
        box.setContentsMargins(0, 0, 0, 0)
        title = QLabel("لوحة التحكم")
        title.setAlignment(Qt.AlignRight)
        title.setStyleSheet(f"color:{GREEN_DARK}; font-size:24px; font-weight:900;")
        subtitle = QLabel("نظرة عامة على بيانات إدارة الفواتير الإلكترونية")
        subtitle.setAlignment(Qt.AlignRight)
        subtitle.setStyleSheet(f"color:{TEXT_MUTED}; font-size:13px; font-weight:700;")
        box.addWidget(title)
        box.addWidget(subtitle)
        return header

    def _cards(self) -> QWidget:
        section = QWidget()
        grid = QGridLayout(section)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(14)
        for index, (key, title, badge, accent) in enumerate(KPI_CARDS):
            grid.addWidget(self._card(key, title, badge, accent), 0, index)
            grid.setColumnStretch(index, 1)
        return section

    def _card(self, key: str, title: str, badge: str, accent: str) -> QFrame:
        card = QFrame()
        card.setMinimumHeight(110)
        card.setStyleSheet(
            f"background:{CARD_BG}; border:1px solid {BORDER}; border-right:5px solid {accent}; border-radius:12px;"
        )
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 14, 16, 14)
        top = QHBoxLayout()
        top.setDirection(QBoxLayout.RightToLeft)
        label = QLabel(title)
        label.setStyleSheet(f"color:{TEXT_MUTED}; font-size:13px; font-weight:800; border:none;")
        badge_label = QLabel(badge)
        badge_label.setAlignment(Qt.AlignCenter)
        badge_label.setFixedSize(28, 28)
        badge_label.setStyleSheet(f"color:{accent}; background:#F8FAFC; border:1px solid #E2E8F0; border-radius:8px;")
        is_money = key in MONEY_KEYS
        value = QLabel(_format_money(0) if is_money else "0")
        value.setAlignment(Qt.AlignRight)
        value.setWordWrap(False)
        # Money values are longer, so use a slightly smaller value font.
        value_font_size = 22 if is_money else 32
        value.setStyleSheet(
            f"color:{TEXT_DARK}; font-size:{value_font_size}px; font-weight:900; border:none;"
        )
        top.addWidget(label)
        top.addStretch(1)
        top.addWidget(badge_label)
        box.addLayout(top)
        box.addWidget(value)
        self._card_value_labels[key] = value
        return card

    def _charts(self) -> QWidget:
        section = QWidget()
        grid = QGridLayout(section)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(14)
        # Total sales per company (few companies -> no "أخرى" bucket) and total
        # sales per customer (capped at the top 12, rest grouped into "أخرى").
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

    def refresh_dashboard(self) -> None:
        self.update_cards(self.service.get_dashboard_counts())
        self.update_charts(
            self.service.get_sales_by_company(),
            self.service.get_sales_by_customer(),
        )

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
