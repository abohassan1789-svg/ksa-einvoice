"""Read-only service for the Daily Follow-up Smart Report."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from app.services.review_data_service import TABLE_SPECS
from app.ui.common.theme import EXTRA_COLUMN_LABELS


@dataclass(frozen=True)
class ReportColumn:
    key: str
    label: str
    sql_expr: str
    data_type: str = "text"
    filter_kind: str = "text"


@dataclass(frozen=True)
class SmartFilterCondition:
    column: str
    operator: str
    value: Any = None
    value_to: Any = None


@dataclass(frozen=True)
class DailyFollowupReportRequest:
    selected_columns: list[str] = field(default_factory=list)
    date_from: Any = None
    date_to: Any = None
    filters: list[SmartFilterCondition] = field(default_factory=list)
    join_mode: str = "AND"
    quick_search: str = ""
    limit: int | None = None


@dataclass(frozen=True)
class DailyFollowupReportResult:
    columns: list[ReportColumn]
    rows: list[dict[str, Any]]


class DailyFollowupReportSettingsService:
    """Persist report UI settings to a local JSON file."""

    def __init__(self, path: str | Path = "daily_followup_report_settings.json") -> None:
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def save(self, settings: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(settings, ensure_ascii=False, indent=2)
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp_path.write_text(payload, encoding="utf-8")
        tmp_path.replace(self.path)


class DailyFollowupReportService:
    """Build and run safe, parameterized Daily Follow-up report queries."""

    CUSTOMER_LOOKUP_LIMIT = 100

    DEFAULT_COLUMN_KEYS = [
        "follow_up_date",
        "customer_name",
        "customer_phone",
        "case_status_name",
        "employee_name",
        "area_number",
        "notes",
    ]

    _COLUMN_SQL: tuple[tuple[str, str, str, str], ...] = (
        ("daily_followup_id", "d.daily_followup_id", "number", "number"),
        ("follow_up_date", "d.follow_up_date", "date", "date"),
        ("customer_id", "d.customer_id", "number", "customer"),
        ("customer_name", "c.customer_name", "text", "customer"),
        ("customer_phone", "c.phone_number", "text", "customer"),
        ("place_area_feddan", "c.place_area_feddan", "number", "area"),
        ("area_number", "c.area_number", "text", "area"),
        ("building", "c.building", "text", "text"),
        ("unit_number", "c.unit_number", "text", "text"),
        ("floor_number", "c.floor_number", "text", "text"),
        ("employee_name", "e.employee_name", "text", "employee"),
        ("case_status_name", "cs.case_status_name", "text", "status"),
        ("installment_duration_years", "d.installment_duration_years", "number", "number"),
        ("remaining_installments", "d.remaining_installments", "number", "number"),
        ("installment_amount", "d.installment_amount", "number", "number"),
        ("installment_type", "d.installment_type", "text", "text"),
        ("unit_sale_amount", "d.unit_sale_amount", "number", "number"),
        ("seller_commission_amount", "d.seller_commission_amount", "number", "number"),
        ("buyer_commission_amount", "d.buyer_commission_amount", "number", "number"),
        ("buyer_name", "d.buyer_name", "text", "text"),
        ("buyer_phone_number", "d.buyer_phone_number", "text", "text"),
        ("notes", "d.notes", "text", "text"),
    )

    def __init__(self, review_service: Any | None = None) -> None:
        self.review_service = review_service
        self.columns = self._build_columns()
        self._column_by_key = {column.key: column for column in self.columns}

    def _build_columns(self) -> list[ReportColumn]:
        spec = TABLE_SPECS["daily_followups"]
        labels = {field.name: field.label for field in spec.fields}
        labels.update(EXTRA_COLUMN_LABELS)
        return [
            ReportColumn(
                key=key,
                label=labels.get(key, key),
                sql_expr=sql_expr,
                data_type=data_type,
                filter_kind=filter_kind,
            )
            for key, sql_expr, data_type, filter_kind in self._COLUMN_SQL
        ]

    def validate_columns(self, selected_columns: list[str] | tuple[str, ...]) -> list[ReportColumn]:
        keys = list(selected_columns) if selected_columns else list(self.DEFAULT_COLUMN_KEYS)
        columns: list[ReportColumn] = []
        for key in dict.fromkeys(keys):
            column = self._column_by_key.get(key)
            if column is None:
                raise ValueError(f"Unsupported report column: {key}")
            columns.append(column)
        if not columns:
            raise ValueError("At least one report column must be selected.")
        return columns

    def build_query(self, request: DailyFollowupReportRequest) -> tuple[str, list[Any]]:
        selected_columns = self.validate_columns(request.selected_columns)
        select_list = ", ".join(
            f'{column.sql_expr} AS "{column.key}"' for column in selected_columns
        )
        query = (
            f"SELECT {select_list} "
            "FROM daily_followups d "
            "LEFT JOIN customers c ON c.customer_id = d.customer_id "
            "LEFT JOIN case_statuses cs ON cs.case_status_id = d.case_status_id "
            "LEFT JOIN employees e ON e.employee_id = d.employee_id"
        )
        where_parts: list[str] = []
        params: list[Any] = []

        if self._clean(request.date_from):
            where_parts.append("d.follow_up_date >= %s")
            params.append(request.date_from)
        if self._clean(request.date_to):
            where_parts.append("d.follow_up_date < (%s::date + INTERVAL '1 day')")
            params.append(request.date_to)

        filter_parts: list[str] = []
        for condition in request.filters:
            condition_sql, condition_params = self._build_filter_condition(condition)
            if condition_sql:
                filter_parts.append(condition_sql)
                params.extend(condition_params)
        if filter_parts:
            joiner = " OR " if str(request.join_mode).upper() == "OR" else " AND "
            where_parts.append("(" + joiner.join(filter_parts) + ")")

        if self._clean(request.quick_search):
            search_parts = [
                f"CAST({column.sql_expr} AS text) ILIKE %s"
                for column in self.columns
            ]
            where_parts.append("(" + " OR ".join(search_parts) + ")")
            params.extend([f"%{str(request.quick_search).strip()}%"] * len(search_parts))

        if where_parts:
            query += " WHERE " + " AND ".join(where_parts)
        query += " ORDER BY d.daily_followup_id ASC"
        if request.limit is not None:
            query += " LIMIT %s"
            params.append(max(1, int(request.limit)))
        return query, params

    def fetch_report(self, request: DailyFollowupReportRequest) -> DailyFollowupReportResult:
        query, params = self.build_query(request)
        selected_columns = self.validate_columns(request.selected_columns)
        service = self._review_service()
        with service.connect() as conn:
            rows = [
                {column.key: row.get(column.key) for column in selected_columns}
                for row in conn.execute(query, params)
            ]
        return DailyFollowupReportResult(columns=selected_columns, rows=rows)

    def load_filter_options(self) -> dict[str, list[dict[str, Any]]]:
        service = self._review_service()
        return {
            "statuses": [
                {"id": row.get("case_status_id"), "label": row.get("case_status_name") or ""}
                for row in service.list_case_statuses_for_selection()
            ],
            "employees": [
                {"id": row.get("employee_id"), "label": row.get("employee_name") or ""}
                for row in service.list_employees_for_selection()
            ],
            "areas": [
                {"id": row.get("place_id"), "label": row.get("place_number") or ""}
                for row in service.list_places_for_selection()
            ],
            "customers": [
                option for option in self.lookup_customers("", limit=self.CUSTOMER_LOOKUP_LIMIT)
            ],
        }

    def search_customers(self, keyword: Any, limit: int = 100) -> list[dict[str, Any]]:
        return list(self._review_service().search_customers(keyword, limit=limit))

    def lookup_customers(self, keyword: Any, limit: int = 100) -> list[dict[str, Any]]:
        text = "" if keyword is None else str(keyword).strip()
        capped_limit = self._customer_lookup_limit(limit)
        service = self._review_service()
        lookup = getattr(service, "lookup_customers", None)
        if callable(lookup):
            rows = lookup(text, limit=capped_limit)
        else:
            rows = service.search_customers(text, limit=capped_limit)
        return [
            {
                "id": row.get("customer_id"),
                "label": self._customer_option_label(row),
                "name": row.get("customer_name") or "",
                "code": row.get("customer_code") or row.get("customer_id"),
                "phone": row.get("phone_number") or "",
            }
            for row in list(rows)[:capped_limit]
        ]

    def _build_filter_condition(self, condition: SmartFilterCondition) -> tuple[str, list[Any]]:
        column = self._column_by_key.get(condition.column)
        if column is None:
            raise ValueError(f"Unsupported report column: {condition.column}")
        operator = self._normalize_operator(condition.operator)
        value = condition.value
        if operator == "between":
            if not self._clean(value) or not self._clean(condition.value_to):
                return "", []
            return f"{column.sql_expr} BETWEEN %s AND %s", [value, condition.value_to]
        if not self._clean(value):
            return "", []
        if condition.column == "customer_name" and isinstance(value, int):
            if operator == "not_equals":
                return "d.customer_id <> %s", [value]
            return "d.customer_id = %s", [value]
        if operator == "contains":
            return f"{self._text_expr(column)} ILIKE %s", [f"%{value}%"]
        if operator == "equals":
            return f"{self._comparison_expr(column)} = %s", [str(value) if column.data_type == "text" else value]
        if operator == "not_equals":
            return f"{self._comparison_expr(column)} <> %s", [str(value) if column.data_type == "text" else value]
        if operator == "starts_with":
            return f"{self._text_expr(column)} ILIKE %s", [f"{value}%"]
        if operator == "ends_with":
            return f"{self._text_expr(column)} ILIKE %s", [f"%{value}"]
        if operator == "greater_than":
            return f"{column.sql_expr} > %s", [value]
        if operator == "less_than":
            return f"{column.sql_expr} < %s", [value]
        raise ValueError(f"Unsupported report operator: {condition.operator}")

    @staticmethod
    def _normalize_operator(operator: str) -> str:
        return str(operator).strip().lower().replace(" ", "_")

    def _review_service(self) -> Any:
        if self.review_service is None:
            from app.services.review_data_service import ReviewDataService

            self.review_service = ReviewDataService()
        return self.review_service

    @staticmethod
    def _customer_option_label(row: dict[str, Any]) -> str:
        name = row.get("customer_name") or ""
        code = row.get("customer_code") or row.get("customer_id")
        phone = row.get("phone_number") or ""
        if name and code:
            return f"{name} - {code}"
        if name and phone:
            return f"{name} - {phone}"
        return name or str(code or "") or phone

    @classmethod
    def _customer_lookup_limit(cls, limit: Any) -> int:
        try:
            requested = int(limit)
        except (TypeError, ValueError):
            requested = cls.CUSTOMER_LOOKUP_LIMIT
        return max(1, min(cls.CUSTOMER_LOOKUP_LIMIT, requested))

    @staticmethod
    def _text_expr(column: ReportColumn) -> str:
        if column.data_type == "text":
            return column.sql_expr
        return f"CAST({column.sql_expr} AS text)"

    @staticmethod
    def _comparison_expr(column: ReportColumn) -> str:
        if column.data_type == "text":
            return f"CAST({column.sql_expr} AS text)"
        return column.sql_expr

    @staticmethod
    def _clean(value: Any) -> bool:
        return value is not None and str(value).strip() != ""
