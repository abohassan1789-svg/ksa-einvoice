"""Pure data helpers for the F1 lookup dialog.

These are deliberately free of any Qt or database dependency so they can be
unit tested in isolation (see tests/unit/test_customer_lookup_filter.py).
"""

from __future__ import annotations

from typing import Any


def filter_lookup_rows(
    rows: list[dict[str, Any]],
    keyword: str,
    visible_columns: list[str] | tuple[str, ...],
) -> list[dict[str, Any]]:
    text = keyword.strip().casefold()
    if not text:
        return rows
    return [
        row
        for row in rows
        if any(text in str(row.get(column, "")).casefold() for column in visible_columns)
    ]


def project_visible_columns(
    row: dict[str, Any], visible_columns: list[str] | tuple[str, ...]
) -> list[str]:
    return ["" if row.get(column) is None else str(row.get(column)) for column in visible_columns]
