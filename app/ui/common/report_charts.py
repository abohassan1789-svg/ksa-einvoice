"""Reusable compact chart widgets for report screens.

These bar/donut widgets were originally defined inside the CRM dashboard screen.
They are shared here so report screens (e.g. the status analysis report) can use
them independently of the sales dashboard, which now renders its own simpler
money-aware bars.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.ui.common.theme import BORDER, GREEN

CARD_BG = "#FFFFFF"
TEXT_DARK = "#0F172A"
TEXT_MUTED = "#64748B"
NO_DATA_MSG = "لا توجد بيانات كافية لعرض الرسم البياني"
UNSPECIFIED = "غير محدد"

DONUT_COLORS = ("#16A34A", "#2563EB", "#F59E0B", "#DC2626", "#7C3AED", "#0891B2", "#475569")


class DashboardBarRow(QWidget):
    """Single RTL-friendly horizontal bar row."""

    clicked = Signal(str, list)

    def __init__(
        self,
        label: str,
        source_labels: list[str],
        value: int,
        max_value: int,
        total_value: int,
        bar_color: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._label = label.strip() or UNSPECIFIED
        self._source_labels = source_labels or [self._label]
        self._value = max(0, int(value))
        self._max_value = max(1, int(max_value))
        self._total_value = max(1, int(total_value))
        self._share = (self._value / self._total_value) * 100
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(40)

        row = QHBoxLayout(self)
        row.setDirection(QBoxLayout.RightToLeft)
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(10)

        label_widget = QLabel(self._label)
        label_widget.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        label_widget.setWordWrap(True)
        label_widget.setFixedWidth(150)
        label_widget.setToolTip(self._label)
        label_widget.setStyleSheet(f"color:{TEXT_DARK}; font-size:12px; font-weight:800;")

        track = QFrame()
        track.setFixedHeight(12)
        track.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        track.setStyleSheet("background:#E2E8F0; border:none; border-radius:6px;")
        track_layout = QHBoxLayout(track)
        track_layout.setContentsMargins(0, 0, 0, 0)
        track_layout.setSpacing(0)
        fill = QFrame()
        fill.setStyleSheet(f"background:{bar_color}; border:none; border-radius:6px;")
        track_layout.addWidget(fill, self._value)
        track_layout.addStretch(max(0, self._max_value - self._value))

        value_widget = QLabel(f"{self._value:,}")
        value_widget.setAlignment(Qt.AlignCenter)
        value_widget.setMinimumWidth(54)
        value_widget.setStyleSheet(f"color:{TEXT_DARK}; font-size:12px; font-weight:900;")

        row.addWidget(label_widget)
        row.addWidget(track, 1)
        row.addWidget(value_widget)

    def mousePressEvent(self, event) -> None:  # noqa: ANN001 - Qt event
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._label, self._source_labels)
        super().mousePressEvent(event)


class DashboardBarChart(QFrame):
    """Reusable compact horizontal bar panel."""

    slice_clicked = Signal(str, str, list)

    def __init__(
        self,
        title: str,
        label_key: str,
        bar_color: str = GREEN,
        max_bars: int | None = None,
        min_height: int = 260,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._title = title
        self._label_key = label_key
        self._value_key = "count"
        self._bar_color = bar_color
        self._max_bars = max_bars
        self._rows: list[dict[str, Any]] = []
        self._source_category_count = 0
        self._build_shell(min_height)

    def _build_shell(self, min_height: int) -> None:
        self.setObjectName("chartPanel")
        self.setMinimumHeight(min_height)
        self.setLayoutDirection(Qt.RightToLeft)
        self.setStyleSheet(
            f"QFrame#chartPanel {{ background:{CARD_BG}; border:1px solid {BORDER}; border-radius:10px; }}"
        )
        box = QVBoxLayout(self)
        box.setContentsMargins(14, 12, 14, 12)
        box.setSpacing(10)
        self._title_label = QLabel(self._title)
        self._title_label.setAlignment(Qt.AlignRight)
        self._title_label.setStyleSheet(f"color:{TEXT_DARK}; font-size:15px; font-weight:900;")
        box.addWidget(self._title_label)
        self._summary = QLabel("")
        self._summary.setAlignment(Qt.AlignRight)
        self._summary.setStyleSheet(f"color:{TEXT_MUTED}; font-size:12px; font-weight:700;")
        box.addWidget(self._summary)
        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(6)
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
            self._body_layout.addWidget(self._no_data(), 1)
            return
        total = sum(int(row["count"]) for row in self._rows)
        max_value = max(int(row["count"]) for row in self._rows)
        self._summary.setText(f"الإجمالي: {total:,} | التصنيفات: {self._source_category_count:,}")
        for row in self._rows:
            widget = DashboardBarRow(
                str(row["label"]),
                list(row.get("source_labels", [row["label"]])),
                int(row["count"]),
                max_value,
                total,
                self._bar_color,
            )
            widget.clicked.connect(self._emit_slice_clicked)
            self._body_layout.addWidget(widget)
        self._body_layout.addStretch(1)

    def _normalise_rows(self, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        totals: dict[str, int] = {}
        source_labels: dict[str, list[str]] = {}
        for item in data:
            try:
                value = int(item.get(self._value_key, 0))
            except (TypeError, ValueError):
                value = 0
            if value <= 0:
                continue
            label = str(item.get(self._label_key, "") or UNSPECIFIED).strip() or UNSPECIFIED
            totals[label] = totals.get(label, 0) + value
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
                    "count": sum(int(row["count"]) for row in tail),
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

    def _no_data(self) -> QLabel:
        label = QLabel(NO_DATA_MSG)
        label.setAlignment(Qt.AlignCenter)
        label.setWordWrap(True)
        label.setStyleSheet(f"color:{TEXT_MUTED}; font-size:13px; font-weight:700;")
        return label


class DonutCanvas(QWidget):
    """Small donut renderer for status distribution."""

    segment_clicked = Signal(str, list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[dict[str, Any]] = []
        self.setMinimumSize(142, 142)
        self.setMaximumSize(168, 168)

    def set_rows(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: ANN001 - Qt event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(17, 17, min(self.width(), self.height()) - 34, min(self.width(), self.height()) - 34)
        total = sum(int(row["count"]) for row in self._rows)
        if total <= 0:
            painter.setPen(QPen(QColor("#CBD5E1"), 16))
            painter.drawEllipse(rect)
            return
        start = 90 * 16
        for index, row in enumerate(self._rows):
            span = int(-360 * 16 * int(row["count"]) / total)
            painter.setPen(QPen(QColor(DONUT_COLORS[index % len(DONUT_COLORS)]), 18, Qt.SolidLine, Qt.RoundCap))
            painter.drawArc(rect, start, span)
            start += span
        painter.setPen(QColor(TEXT_DARK))
        painter.setFont(self.font())
        painter.drawText(rect, Qt.AlignCenter, f"{total:,}")

    def mousePressEvent(self, event) -> None:  # noqa: ANN001 - Qt event
        if self._rows and event.button() == Qt.LeftButton:
            row = self._rows[0]
            self.segment_clicked.emit(str(row["label"]), list(row.get("source_labels", [row["label"]])))
        super().mousePressEvent(event)


class DashboardDonutChart(DashboardBarChart):
    """Status panel with a donut and a compact legend."""

    def _build_shell(self, min_height: int) -> None:
        self.setObjectName("chartPanel")
        self.setMinimumHeight(min_height)
        self.setLayoutDirection(Qt.RightToLeft)
        self.setStyleSheet(
            f"QFrame#chartPanel {{ background:{CARD_BG}; border:1px solid {BORDER}; border-radius:10px; }}"
        )
        box = QVBoxLayout(self)
        box.setContentsMargins(14, 12, 14, 12)
        box.setSpacing(8)
        self._title_label = QLabel(self._title)
        self._title_label.setAlignment(Qt.AlignRight)
        self._title_label.setStyleSheet(f"color:{TEXT_DARK}; font-size:15px; font-weight:900;")
        box.addWidget(self._title_label)
        self._summary = QLabel("")
        self._summary.setAlignment(Qt.AlignRight)
        self._summary.setStyleSheet(f"color:{TEXT_MUTED}; font-size:12px; font-weight:700;")
        box.addWidget(self._summary)
        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setDirection(QBoxLayout.RightToLeft)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(12)
        self._donut = DonutCanvas()
        self._donut.segment_clicked.connect(self._emit_slice_clicked)
        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(5)
        body_layout.addWidget(self._donut)
        body_layout.addWidget(self._body, 1)
        box.addWidget(body, 1)

    def build_rows(self) -> None:
        self._clear()
        self._donut.set_rows(self._rows)
        if not self._rows:
            self._summary.setText("الإجمالي: 0")
            self._body_layout.addWidget(self._no_data(), 1)
            return
        total = sum(int(row["count"]) for row in self._rows)
        self._summary.setText(f"الإجمالي: {total:,} | أحدث حالة لكل عميل")
        for index, row in enumerate(self._rows):
            label = QLabel(f"■ {row['label']}    {int(row['count']):,}")
            label.setAlignment(Qt.AlignRight)
            label.setStyleSheet(
                f"color:{DONUT_COLORS[index % len(DONUT_COLORS)]}; font-size:12px; font-weight:900;"
            )
            label.setCursor(Qt.PointingHandCursor)
            label.mousePressEvent = lambda event, r=row: self._emit_slice_clicked(  # type: ignore[method-assign]
                str(r["label"]), list(r.get("source_labels", [r["label"]]))
            )
            self._body_layout.addWidget(label)
        self._body_layout.addStretch(1)
