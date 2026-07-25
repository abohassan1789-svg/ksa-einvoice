"""Small PostgreSQL CRUD service for CRM review screens."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.sql import SQL, Identifier

from app.config.database import build_database_url, runtime_connect_kwargs
from app.config.settings import get_settings


@dataclass(frozen=True)
class FieldSpec:
    name: str
    label: str
    data_type: str = "text"
    required: bool = False
    readonly: bool = False
    hidden_on_form: bool = False
    # Virtual fields are shown on the form/list for convenience (e.g. the linked
    # customer's name/phone) but are NOT columns on the screen's own table, so
    # they are skipped on SELECT/INSERT/UPDATE.
    virtual: bool = False
    # Optional group box label; fields sharing a group render together.
    group: str = ""
    # Combo data source: "employees" | "case_statuses" | "customers_phone" | "customers_name".
    combo_source: str = ""


@dataclass(frozen=True)
class TableSpec:
    key: str
    table_name: str
    title: str
    icon_text: str
    primary_key: str
    search_columns: tuple[str, ...]
    list_columns: tuple[str, ...]
    fields: tuple[FieldSpec, ...]
    # Optional explicit column set for the F1 lookup dialog. When empty the
    # dialog falls back to all visible form fields.
    lookup_columns: tuple[str, ...] = ()


TABLE_SPECS: dict[str, TableSpec] = {
    "customers": TableSpec(
        key="customers",
        table_name="customers",
        title="إدارة العملاء",
        icon_text="C",
        primary_key="customer_id",
        search_columns=("customer_name", "phone_number", "area_number", "unit_number", "vat_number"),
        list_columns=("customer_id", "customer_name", "phone_number", "area_number", "unit_number"),
        fields=(
            FieldSpec("customer_id", "كود العميل", "int", readonly=True),
            FieldSpec("customer_name", "اسم العميل"),
            FieldSpec("phone_number", "الهاتف"),
            FieldSpec("place_area_feddan", "كود المنطقة", "int"),
            FieldSpec("area_number", "رقم المنطقة"),
            FieldSpec("building", "المبنى"),
            FieldSpec("unit_number", "الوحدة"),
            FieldSpec("floor_number", "الدور"),
            # الرقم الضريبي / السجل التجاري / العنوان.
            FieldSpec("vat_number", "الرقم الضريبي"),
            FieldSpec("cr", "السجل التجاري"),
            FieldSpec("address", "العنوان"),
            FieldSpec("installment_duration_years", "مدة الأقساط", "int", hidden_on_form=True),
            FieldSpec("remaining_installments", "الأقساط المتبقية", "int", hidden_on_form=True),
            FieldSpec("installment_amount", "قيمة القسط", "int", hidden_on_form=True),
            FieldSpec("legacy_area_number_2", "رقم منطقة إضافي", hidden_on_form=True),
        ),
    ),
    "products": TableSpec(
        key="products",
        table_name="products",
        title="إدارة الأصناف",
        icon_text="ص",
        primary_key="item_code",
        search_columns=("item_name", "item_code"),
        list_columns=("item_code", "item_name", "quantity", "price", "total"),
        fields=(
            # كود الصنف is auto-assigned from the products sequence (starts 1001),
            # exactly like customer_id — shown read-only, never written by the app.
            FieldSpec("item_code", "كود الصنف", "int", readonly=True),
            FieldSpec("item_name", "اسم الصنف", required=True),
            FieldSpec("quantity", "الكمية", "float"),
            FieldSpec("price", "سعر الوحدة", "float"),
            # الإجمالي is a database generated column (quantity × price). It is
            # virtual here so it is never sent on INSERT/UPDATE; the screen shows
            # a live preview and the grid shows the stored value.
            FieldSpec("total", "الإجمالي", "float", readonly=True, virtual=True),
        ),
    ),
    "companies": TableSpec(
        key="companies",
        table_name="companies",
        title="إدارة الشركات",
        icon_text="ش",
        primary_key="id",
        # Searchable by company name (Arabic/English), commercial registration, or
        # VAT number — a partial (ILIKE) match on any of these.
        search_columns=("name_ar", "name_en", "commercial_registration", "vat_number"),
        # The search grid shows only these three columns (in this order). The row's
        # primary key (id) is still fetched and attached for selection even though
        # it is not a visible column.
        list_columns=("name_ar", "vat_number", "commercial_registration"),
        fields=(
            # id is auto-assigned by PostgreSQL IDENTITY — shown read-only, never
            # written by the app (exactly like item_code / customer_id).
            FieldSpec("id", "كود الشركة", "int", readonly=True),
            FieldSpec("name_ar", "اسم الشركة (عربي)", required=True),
            FieldSpec("name_en", "اسم الشركة (إنجليزي)"),
            # Registration / VAT numbers are STRINGS (never numeric) so leading
            # zeros are preserved; each is UNIQUE at the database level.
            FieldSpec("commercial_registration", "السجل التجاري", required=True),
            FieldSpec("vat_number", "الرقم الضريبي", required=True),
            FieldSpec("address_ar", "العنوان (عربي)"),
            FieldSpec("address_en", "العنوان (إنجليزي)"),
        ),
    ),
    "employees": TableSpec(
        key="employees",
        table_name="employees",
        title="إدارة الموظفين",
        icon_text="E",
        primary_key="employee_id",
        search_columns=("employee_name", "phone_number", "governorate", "district_center", "village"),
        list_columns=("employee_id", "employee_name", "phone_number", "governorate"),
        fields=(
            FieldSpec("employee_id", "كود الموظف", "int", readonly=True),
            FieldSpec("employee_name", "اسم الموظف"),
            FieldSpec("phone_number", "الهاتف", required=True),
            FieldSpec("governorate", "المحافظة"),
            FieldSpec("district_center", "المركز"),
            FieldSpec("village", "القرية"),
        ),
    ),
    "daily_followups": TableSpec(
        key="daily_followups",
        table_name="daily_followups",
        title="إدارة المتابعة اليومية",
        icon_text="D",
        primary_key="daily_followup_id",
        search_columns=("customer_name", "customer_phone", "notes"),
        list_columns=(
            "daily_followup_id",
            "customer_name",
            "customer_phone",
            "follow_up_date",
            "case_status_name",
            "notes",
        ),
        lookup_columns=(
            "daily_followup_id",
            "follow_up_date",
            "customer_id",
            "customer_name",
            "customer_phone",
            "place_area_feddan",
            "area_number",
            "building",
            "unit_number",
            "floor_number",
            "employee_name",
            "case_status_name",
            "installment_duration_years",
            "remaining_installments",
            "installment_amount",
            "installment_type",
            "unit_sale_amount",
            "seller_commission_amount",
            "buyer_commission_amount",
            "buyer_name",
            "buyer_phone_number",
            "notes",
        ),
        fields=(
            # --- بيانات العميل (تُجلب من العميل المختار) ---
            FieldSpec("customer_phone", "رقم التليفون", virtual=True,
                      group="بيانات العميل", combo_source="customers_phone"),
            FieldSpec("customer_name", "إسم العميل", virtual=True,
                      group="بيانات العميل", combo_source="customers_name"),
            FieldSpec("customer_id", "كود العميل", "int", readonly=True, group="بيانات العميل"),
            FieldSpec("place_area_feddan", "المنطقة بالفدان", "int", readonly=True,
                      virtual=True, group="بيانات العميل"),
            FieldSpec("area_number", "رقم المنطقة", readonly=True, virtual=True, group="بيانات العميل"),
            FieldSpec("building", "عمارة", readonly=True, virtual=True, group="بيانات العميل"),
            FieldSpec("unit_number", "وحدة", readonly=True, virtual=True, group="بيانات العميل"),
            FieldSpec("floor_number", "الطابق", readonly=True, virtual=True, group="بيانات العميل"),
            # --- بيانات المتابعة والحالة والمتبقي ---
            FieldSpec("daily_followup_id", "كود المتابعة", "int", readonly=True,
                      group="بيانات المتابعة والحالة"),
            FieldSpec("follow_up_date", "تاريخ المتابعة", "date", group="بيانات المتابعة والحالة"),
            FieldSpec("employee_id", "اسم الموظف", "int",
                      group="بيانات المتابعة والحالة", combo_source="employees"),
            FieldSpec("case_status_id", "الحالة", "int",
                      group="بيانات المتابعة والحالة", combo_source="case_statuses"),
            FieldSpec("installment_duration_years", "مدة الأقساط بالسنين", "float",
                      group="بيانات المتابعة والحالة"),
            FieldSpec("remaining_installments", "متبقي الأقساط", "float",
                      group="بيانات المتابعة والحالة"),
            FieldSpec("installment_amount", "قيمة القسط", "float", group="بيانات المتابعة والحالة"),
            FieldSpec("installment_type", "نوع القسط", group="بيانات المتابعة والحالة"),
            FieldSpec("unit_sale_amount", "قيمة بيع الوحدة", "float", group="بيانات المتابعة والحالة"),
            FieldSpec("seller_commission_amount", "عمولة البائع", "float",
                      group="بيانات المتابعة والحالة"),
            FieldSpec("buyer_commission_amount", "عمولة المشتري", "float",
                      group="بيانات المتابعة والحالة"),
            FieldSpec("buyer_name", "اسم المشتري", group="بيانات المتابعة والحالة"),
            FieldSpec("buyer_phone_number", "تليفون المشتري", group="بيانات المتابعة والحالة"),
            FieldSpec("notes", "ملاحظات", group="بيانات المتابعة والحالة"),
            FieldSpec("contact_count", "عدد مرات الاتصال", "int", hidden_on_form=True),
        ),
    ),
    "places": TableSpec(
        key="places",
        table_name="places",
        title="إدارة المناطق",
        icon_text="P",
        primary_key="place_id",
        search_columns=("place_number",),
        list_columns=("place_id", "place_number"),
        fields=(
            FieldSpec("place_id", "كود المنطقة", "int", readonly=True),
            FieldSpec("place_number", "رقم / اسم المنطقة", required=True),
        ),
    ),
    "case_statuses": TableSpec(
        key="case_statuses",
        table_name="case_statuses",
        title="إدارة الحالات",
        icon_text="S",
        primary_key="case_status_id",
        search_columns=("case_status_name",),
        list_columns=("case_status_id", "case_status_name"),
        fields=(
            FieldSpec("case_status_id", "كود الحالة", "int", readonly=True),
            FieldSpec("case_status_name", "اسم الحالة", required=True),
        ),
    ),
    "receipt_vouchers": TableSpec(
        key="receipt_vouchers",
        table_name="receipt_vouchers",
        title="إدارة سندات القبض",
        icon_text="س",
        primary_key="id",
        # Searchable by voucher number, customer name (via the customers JOIN in
        # _list_receipt_vouchers), or voucher date — partial (ILIKE) match.
        search_columns=("voucher_number", "customer_name", "voucher_date"),
        # The search grid shows only these four columns (in this order); the
        # row's primary key (id) is still fetched and attached for selection.
        list_columns=("voucher_number", "customer_name", "voucher_date", "amount"),
        lookup_columns=("id", "voucher_number", "voucher_date", "customer_name", "amount"),
        fields=(
            # id is auto-assigned by PostgreSQL IDENTITY — shown read-only, never
            # written by the app (exactly like the companies screen).
            FieldSpec("id", "كود السند", "int", readonly=True),
            # Voucher number: leave empty to auto-number from the PostgreSQL
            # sequence (PA-001, PA-002, ...) — the DB DEFAULT assigns it
            # atomically; an authorised user may type a manual number instead.
            # NOT required here precisely so the empty/automatic case passes.
            FieldSpec("voucher_number", "رقم السند"),
            FieldSpec("voucher_date", "التاريخ", "date", required=True),
            # Customer / company are chosen from dropdowns; the stored value is
            # the foreign-key id, never the display name.
            FieldSpec("customer_id", "العميل", "int", required=True),
            FieldSpec("company_id", "الشركة", "int", required=True),
            # Stored codes: cash / bank_transfer (Arabic labels in the UI; a DB
            # CHECK constraint is the final guard).
            FieldSpec("payment_type", "نوع الدفع", required=True),
            FieldSpec("amount", "المبلغ", "float", required=True),
            FieldSpec("description", "البيان"),
            # Grid-only column from the customers JOIN (label for the list
            # header); never an editor and never written to the table.
            FieldSpec("customer_name", "اسم العميل", virtual=True, hidden_on_form=True),
        ),
    ),
}


class ReviewDataService:
    """CRUD operations for the review UI."""

    CUSTOMER_LOOKUP_LIMIT = 100

    def __init__(self) -> None:
        self.settings = get_settings()
        self.database_url = build_database_url(self.settings).replace("postgresql+psycopg://", "postgresql://")
        # A single long-lived connection is reused for every query. Opening a
        # fresh PostgreSQL connection per query (TCP + auth handshake) was the
        # main cause of the lag when a screen was built for the first time.
        self._connection: psycopg.Connection | None = None

    def _ensure_connection(self) -> psycopg.Connection:
        if self._connection is None or self._connection.closed:
            self._connection = psycopg.connect(
                self.database_url,
                row_factory=dict_row,
                autocommit=True,
                **runtime_connect_kwargs(),
            )
        return self._connection

    @contextmanager
    def connect(self):
        """Yield the shared connection.

        Kept as a context manager so existing ``with self.connect() as conn:``
        call sites are unchanged, but it reuses one persistent (autocommit)
        connection instead of opening/closing one per call. Exiting the block
        does NOT close the connection. If the connection has gone stale it is
        transparently reopened on the next call.
        """
        try:
            yield self._ensure_connection()
        except psycopg.OperationalError:
            # Drop the dead connection so the next call reconnects.
            self._connection = None
            raise

    def list_records(self, spec: TableSpec, keyword: str = "", limit: int = 500) -> list[dict[str, Any]]:
        columns = [spec.primary_key, *[c for c in spec.list_columns if c != spec.primary_key]]
        return self.list_records_with_columns(spec, columns, keyword, limit)

    def list_records_with_columns(
        self,
        spec: TableSpec,
        columns: list[str] | tuple[str, ...],
        keyword: str = "",
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        if spec.key == "daily_followups":
            return self._list_daily_followups(columns, keyword, limit)
        if spec.key == "receipt_vouchers":
            return self._list_receipt_vouchers(columns, keyword, limit)
        columns = list(dict.fromkeys([spec.primary_key, *[c for c in columns if c != spec.primary_key]]))
        query = SQL("SELECT {cols} FROM {table}").format(
            cols=SQL(", ").join(Identifier(c) for c in columns),
            table=Identifier(spec.table_name),
        )
        params: list[Any] = []
        if keyword.strip():
            like_parts = []
            for column in spec.search_columns:
                like_parts.append(SQL("CAST({col} AS text) ILIKE %s").format(col=Identifier(column)))
                params.append(f"%{keyword.strip()}%")
            query += SQL(" WHERE ") + SQL(" OR ").join(like_parts)
        query += SQL(" ORDER BY {pk} ASC LIMIT %s").format(pk=Identifier(spec.primary_key))
        params.append(limit)
        with self.connect() as conn:
            return list(conn.execute(query, params))

    def list_places_for_selection(self) -> list[dict[str, Any]]:
        query = SQL(
            "SELECT place_id, place_number FROM {table} ORDER BY place_id ASC"
        ).format(table=Identifier("places"))
        with self.connect() as conn:
            return list(conn.execute(query))

    # Every selectable column on the daily-followup screen, mapped to its SQL
    # expression. Used both for the SELECT list and for the all-columns search.
    _DAILY_FOLLOWUP_SELECT = (
        ("daily_followup_id", "d.daily_followup_id"),
        ("follow_up_date", "d.follow_up_date"),
        ("customer_id", "d.customer_id"),
        ("customer_name", "c.customer_name"),
        ("customer_phone", "c.phone_number"),
        ("place_area_feddan", "c.place_area_feddan"),
        ("area_number", "c.area_number"),
        ("building", "c.building"),
        ("unit_number", "c.unit_number"),
        ("floor_number", "c.floor_number"),
        ("employee_name", "e.employee_name"),
        ("case_status_name", "cs.case_status_name"),
        ("installment_duration_years", "d.installment_duration_years"),
        ("remaining_installments", "d.remaining_installments"),
        ("installment_amount", "d.installment_amount"),
        ("installment_type", "d.installment_type"),
        ("unit_sale_amount", "d.unit_sale_amount"),
        ("seller_commission_amount", "d.seller_commission_amount"),
        ("buyer_commission_amount", "d.buyer_commission_amount"),
        ("buyer_name", "d.buyer_name"),
        ("buyer_phone_number", "d.buyer_phone_number"),
        ("notes", "d.notes"),
    )

    _DAILY_FOLLOWUP_SEARCH = (
        ("daily_followup_id", "d.daily_followup_id"),
        ("customer_id", "d.customer_id"),
        ("customer_name", "c.customer_name"),
        ("customer_phone", "c.phone_number"),
        ("follow_up_date", "d.follow_up_date"),
        ("case_status_name", "cs.case_status_name"),
        ("notes", "d.notes"),
    )

    def _list_daily_followups(
        self,
        columns: list[str] | tuple[str, ...],
        keyword: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        select_map = dict(self._DAILY_FOLLOWUP_SELECT)
        requested = list(dict.fromkeys(columns))
        selected = [(alias, select_map[alias]) for alias in requested if alias in select_map]
        if not selected:
            selected = [("daily_followup_id", select_map["daily_followup_id"])]
        select_list = SQL(", ").join(
            SQL("{expr} AS {alias}").format(expr=SQL(expr), alias=Identifier(alias))
            for alias, expr in selected
        )
        query = (
            SQL("SELECT ")
            + select_list
            + SQL(
                " FROM daily_followups d "
                "LEFT JOIN customers c ON c.customer_id = d.customer_id "
                "LEFT JOIN case_statuses cs ON cs.case_status_id = d.case_status_id "
                "LEFT JOIN employees e ON e.employee_id = d.employee_id"
            )
        )
        params: list[Any] = []
        if keyword.strip():
            like = f"%{keyword.strip()}%"
            conditions = SQL(" OR ").join(
                SQL("CAST({expr} AS text) ILIKE %s").format(expr=SQL(expr))
                for _alias, expr in self._DAILY_FOLLOWUP_SEARCH
            )
            query += SQL(" WHERE ") + conditions
            params.extend([like] * len(self._DAILY_FOLLOWUP_SEARCH))
        query += SQL(" ORDER BY d.daily_followup_id ASC LIMIT %s")
        params.append(limit)
        with self.connect() as conn:
            return list(conn.execute(query, params))

    # Every selectable column on the receipt-vouchers screen, mapped to its SQL
    # expression (customer_name comes from the customers JOIN). Used both for
    # the SELECT list and for the search below — same pattern as daily_followups.
    _RECEIPT_VOUCHER_SELECT = (
        ("id", "rv.id"),
        ("voucher_number", "rv.voucher_number"),
        ("voucher_date", "rv.voucher_date"),
        ("customer_id", "rv.customer_id"),
        ("customer_name", "c.customer_name"),
        ("company_id", "rv.company_id"),
        ("payment_type", "rv.payment_type"),
        ("amount", "rv.amount"),
        ("description", "rv.description"),
    )

    # The requested search entries: voucher number, customer name, or date.
    _RECEIPT_VOUCHER_SEARCH = (
        ("voucher_number", "rv.voucher_number"),
        ("customer_name", "c.customer_name"),
        ("voucher_date", "rv.voucher_date"),
    )

    def _list_receipt_vouchers(
        self,
        columns: list[str] | tuple[str, ...],
        keyword: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        select_map = dict(self._RECEIPT_VOUCHER_SELECT)
        requested = list(dict.fromkeys(columns))
        selected = [(alias, select_map[alias]) for alias in requested if alias in select_map]
        if not selected:
            selected = [("id", select_map["id"])]
        select_list = SQL(", ").join(
            SQL("{expr} AS {alias}").format(expr=SQL(expr), alias=Identifier(alias))
            for alias, expr in selected
        )
        query = (
            SQL("SELECT ")
            + select_list
            + SQL(
                " FROM receipt_vouchers rv "
                "LEFT JOIN customers c ON c.customer_id = rv.customer_id"
            )
        )
        params: list[Any] = []
        if keyword.strip():
            like = f"%{keyword.strip()}%"
            conditions = SQL(" OR ").join(
                SQL("CAST({expr} AS text) ILIKE %s").format(expr=SQL(expr))
                for _alias, expr in self._RECEIPT_VOUCHER_SEARCH
            )
            query += SQL(" WHERE ") + conditions
            params.extend([like] * len(self._RECEIPT_VOUCHER_SEARCH))
        query += SQL(" ORDER BY rv.id ASC LIMIT %s")
        params.append(limit)
        with self.connect() as conn:
            return list(conn.execute(query, params))

    def list_companies_for_selection(self) -> list[dict[str, Any]]:
        query = SQL("SELECT id, name_ar FROM companies ORDER BY name_ar ASC")
        with self.connect() as conn:
            return list(conn.execute(query))

    def get_company_details(self, company_id: Any) -> dict[str, Any] | None:
        """Full seller-header fields for a company (name / CR / VAT / address).

        Used by the receipt-voucher printout so the ``سند قبض`` header carries
        the same company identity (name, commercial registration, VAT number,
        address, and embedded logo) as the Saudi tax invoice.
        """
        if company_id in (None, ""):
            return None
        query = SQL(
            "SELECT id, name_ar, name_en, commercial_registration, vat_number, "
            "address_ar, address_en, logo, logo_mime FROM companies WHERE id = %s"
        )
        with self.connect() as conn:
            return conn.execute(query, [company_id]).fetchone()

    def get_company_logo(self, company_id: Any) -> dict[str, Any] | None:
        """Return ``{"logo": bytes, "logo_mime": str}`` for a company, or None.

        Fetched on its own (not via :meth:`get_record`) so the potentially large
        image blob is only ever loaded when a document is actually printed or the
        companies screen shows the logo preview.
        """
        if company_id in (None, ""):
            return None
        query = SQL("SELECT logo, logo_mime FROM companies WHERE id = %s")
        with self.connect() as conn:
            return conn.execute(query, [company_id]).fetchone()

    def set_company_logo(
        self, company_id: Any, data: bytes | None, mime: str | None
    ) -> None:
        """Store (or clear, when ``data`` is None) a company's embedded logo."""
        if company_id in (None, ""):
            return
        query = SQL("UPDATE companies SET logo = %s, logo_mime = %s WHERE id = %s")
        with self.connect() as conn:
            conn.execute(query, [data, (mime if data else None), company_id])

    def find_customer_by_phone(self, phone: Any) -> dict[str, Any] | None:
        text = str(phone).strip()
        if not text:
            return None
        query = SQL(
            "SELECT customer_id, customer_name, phone_number FROM customers "
            "WHERE CAST(phone_number AS text) = %s ORDER BY customer_id ASC LIMIT 1"
        )
        with self.connect() as conn:
            return conn.execute(query, [text]).fetchone()

    def find_customer_by_name(self, name: Any) -> dict[str, Any] | None:
        text = str(name).strip()
        if not text:
            return None
        query = SQL(
            "SELECT customer_id, customer_name, phone_number FROM customers "
            "WHERE customer_name ILIKE %s ORDER BY customer_id ASC LIMIT 1"
        )
        with self.connect() as conn:
            return conn.execute(query, [text]).fetchone()

    def list_employees_for_selection(self) -> list[dict[str, Any]]:
        query = SQL(
            "SELECT employee_id, employee_name FROM employees ORDER BY employee_name ASC"
        )
        with self.connect() as conn:
            return list(conn.execute(query))

    def list_case_statuses_for_selection(self) -> list[dict[str, Any]]:
        query = SQL(
            "SELECT case_status_id, case_status_name FROM case_statuses ORDER BY case_status_name ASC"
        )
        with self.connect() as conn:
            return list(conn.execute(query))

    def find_case_status_by_name(self, name: Any) -> dict[str, Any] | None:
        """Resolve a case-status row by its (case-insensitive) name.

        Used by the Excel importer to turn the visible ``الحالة`` text back into
        a ``case_status_id`` foreign key.
        """
        text = str(name).strip()
        if not text:
            return None
        query = SQL(
            "SELECT case_status_id, case_status_name FROM case_statuses "
            "WHERE case_status_name ILIKE %s ORDER BY case_status_id ASC LIMIT 1"
        )
        with self.connect() as conn:
            return conn.execute(query, [text]).fetchone()

    def fetch_column_values(
        self, table_name: str, columns: list[str] | tuple[str, ...]
    ) -> list[dict[str, Any]]:
        """Return every row of ``table_name`` projected to ``columns``.

        Used by the Excel importer to load the existing values that make up a
        screen's duplicate signature (e.g. all customer phone numbers), so it can
        reject rows that would clash with data already in the database.
        """
        cols = list(dict.fromkeys(columns))
        query = SQL("SELECT {cols} FROM {table}").format(
            cols=SQL(", ").join(Identifier(c) for c in cols),
            table=Identifier(table_name),
        )
        with self.connect() as conn:
            return list(conn.execute(query))

    def list_customers_for_selection(self) -> list[dict[str, Any]]:
        query = SQL(
            "SELECT customer_id, customer_name, phone_number FROM customers ORDER BY customer_id ASC"
        )
        with self.connect() as conn:
            return list(conn.execute(query))

    def search_customers(self, keyword: Any, limit: int | None = 100) -> list[dict[str, Any]]:
        """Search customers by code (customer_id), name, or phone.

        Partial (ILIKE) search, case-insensitive; Arabic and English both work.
        Results are ordered with an exact customer_id (code) match first, then
        by customer_id ascending. Limited to ``limit`` rows for performance.

        Notes on the current schema (001_create_core_schema.sql):
        * The customers table has no separate "mobile" column; only
          ``phone_number``. A future mobile column would be added here.
        * There is no active/inactive flag, so all customers are returned.
          If such a flag is added later, an ``is_active`` filter should be
          added to the WHERE clause here.
        """
        text = "" if keyword is None else str(keyword).strip()
        columns = ["customer_id", "phone_number", "customer_name"]
        base_query = SQL("SELECT {cols} FROM customers").format(
            cols=SQL(", ").join(Identifier(column) for column in columns)
        )
        if not text:
            query = base_query + SQL(" ORDER BY customer_id ASC")
            params: list[Any] = []
            if limit is not None:
                query += SQL(" LIMIT %s")
                params.append(limit)
            with self.connect() as conn:
                return list(conn.execute(query, params))
        like = f"%{text}%"
        query = (
            base_query
            + SQL(
                " WHERE CAST(customer_id AS text) ILIKE %s "
                "OR COALESCE(customer_name, '') ILIKE %s "
                "OR COALESCE(CAST(phone_number AS text), '') ILIKE %s "
                "ORDER BY (CAST(customer_id AS text) = %s) DESC, customer_id ASC"
            )
        )
        params = [like, like, like, text]
        if limit is not None:
            query += SQL(" LIMIT %s")
            params.append(limit)
        with self.connect() as conn:
            return list(conn.execute(query, params))

    def lookup_customers(self, keyword: Any, limit: int | None = 100) -> list[dict[str, Any]]:
        """Lightweight customer lookup for remote dropdown searches.

        Returns only the fields needed by dropdowns and caps the result size at
        100 so callers cannot accidentally hydrate the full customers table.
        """
        text = "" if keyword is None else str(keyword).strip()
        capped_limit = self._customer_lookup_limit(limit)
        columns = ["customer_id", "customer_name", "phone_number"]
        base_query = SQL("SELECT {cols} FROM customers").format(
            cols=SQL(", ").join(Identifier(column) for column in columns)
        )
        params: list[Any] = []
        if text:
            like = f"%{text}%"
            query = (
                base_query
                + SQL(
                    " WHERE CAST(customer_id AS text) ILIKE %s "
                    "OR COALESCE(customer_name, '') ILIKE %s "
                    "OR COALESCE(CAST(phone_number AS text), '') ILIKE %s "
                    "ORDER BY (CAST(customer_id AS text) = %s) DESC, "
                    "(COALESCE(customer_name, '') ILIKE %s) DESC, "
                    "(COALESCE(customer_name, '') ILIKE %s) DESC, "
                    "customer_name NULLS LAST, customer_id ASC LIMIT %s"
                )
            )
            params = [like, like, like, text, text, f"{text}%", capped_limit]
        else:
            query = base_query + SQL(
                " ORDER BY customer_name NULLS LAST, customer_id ASC LIMIT %s"
            )
            params = [capped_limit]
        with self.connect() as conn:
            return list(conn.execute(query, params))

    @classmethod
    def _customer_lookup_limit(cls, limit: int | None) -> int:
        try:
            requested = int(limit) if limit is not None else cls.CUSTOMER_LOOKUP_LIMIT
        except (TypeError, ValueError):
            requested = cls.CUSTOMER_LOOKUP_LIMIT
        return max(1, min(cls.CUSTOMER_LOOKUP_LIMIT, requested))

    def value_exists(
        self,
        spec: TableSpec,
        column: str,
        value: Any,
        exclude_id: Any | None = None,
    ) -> bool:
        """True if ``value`` already exists in ``column`` (optionally ignoring one row).

        Used by screens with user-entered UNIQUE columns (e.g. a company's
        commercial registration / VAT number) to show a clear Arabic message
        before hitting the database's UNIQUE index, which remains the final guard.
        Comparison is whitespace-insensitive to match the stored, trimmed value.
        """
        text = "" if value is None else str(value).strip()
        if text == "":
            return False
        query = SQL(
            "SELECT 1 FROM {table} WHERE btrim(CAST({col} AS text)) = %s"
        ).format(table=Identifier(spec.table_name), col=Identifier(column))
        params: list[Any] = [text]
        if exclude_id is not None:
            query += SQL(" AND {pk} <> %s").format(pk=Identifier(spec.primary_key))
            params.append(exclude_id)
        query += SQL(" LIMIT 1")
        with self.connect() as conn:
            return conn.execute(query, params).fetchone() is not None

    def next_id(self, spec: TableSpec) -> int:
        with self.connect() as conn:
            return self._next_id(conn, spec)

    def get_record(self, spec: TableSpec, record_id: Any) -> dict[str, Any] | None:
        columns = [field.name for field in spec.fields if not field.virtual]
        query = SQL("SELECT {cols} FROM {table} WHERE {pk} = %s").format(
            cols=SQL(", ").join(Identifier(c) for c in columns),
            table=Identifier(spec.table_name),
            pk=Identifier(spec.primary_key),
        )
        with self.connect() as conn:
            record = conn.execute(query, [record_id]).fetchone()
            if record and spec.key == "daily_followups" and record.get("customer_id"):
                customer = conn.execute(
                    SQL(
                        "SELECT customer_name, phone_number FROM customers WHERE customer_id = %s"
                    ),
                    [record["customer_id"]],
                ).fetchone()
                if customer:
                    record["customer_name"] = customer.get("customer_name")
                    record["customer_phone"] = customer.get("phone_number")
            return record

    def save_record(self, spec: TableSpec, payload: dict[str, Any], record_id: Any | None) -> Any:
        clean_payload = {
            field.name: self._coerce_value(field, payload.get(field.name))
            for field in spec.fields
            if field.name in payload
            and not field.virtual
            and not (field.readonly and field.name == spec.primary_key)
        }
        for field in spec.fields:
            if field.required and not clean_payload.get(field.name):
                raise ValueError(f"الحقل مطلوب: {field.label}")

        if spec.key == "daily_followups":
            follow_up_date = clean_payload.get("follow_up_date")
            if follow_up_date is not None:
                clean_payload["follow_up_month"] = follow_up_date.month
                clean_payload["follow_up_year"] = follow_up_date.year

        if spec.key == "receipt_vouchers" and clean_payload.get("voucher_number") is None:
            # Empty voucher number means "automatic": drop the column so the
            # database DEFAULT assigns the next PA-<n> from the sequence
            # atomically (concurrency-safe; never MAX+1). On update this simply
            # leaves the stored number unchanged.
            clean_payload.pop("voucher_number", None)

        with self.connect() as conn:
            if record_id is None:
                # Concurrency-safe id allocation: do NOT set the primary key from
                # MAX(pk)+1. Let PostgreSQL assign it atomically from the table's
                # sequence (migration 008 wires a DEFAULT nextval on every core
                # table) and read the assigned value back with RETURNING. Two
                # devices clicking "New/Save" at the same instant therefore each
                # receive their own unique number with no locking and no
                # duplicate-key failures. NEVER reintroduce MAX(pk)+1 here.
                columns = list(clean_payload.keys())  # never contains the PK
                if columns:
                    query = SQL(
                        "INSERT INTO {table} ({cols}) VALUES ({values}) RETURNING {pk}"
                    ).format(
                        table=Identifier(spec.table_name),
                        cols=SQL(", ").join(Identifier(c) for c in columns),
                        values=SQL(", ").join(SQL("%s") for _ in columns),
                        pk=Identifier(spec.primary_key),
                    )
                    params = [clean_payload[c] for c in columns]
                else:
                    query = SQL(
                        "INSERT INTO {table} DEFAULT VALUES RETURNING {pk}"
                    ).format(
                        table=Identifier(spec.table_name),
                        pk=Identifier(spec.primary_key),
                    )
                    params = []
                return conn.execute(query, params).fetchone()[spec.primary_key]

            columns = list(clean_payload.keys())
            assignments = SQL(", ").join(
                SQL("{col} = %s").format(col=Identifier(column)) for column in columns
            )
            query = SQL("UPDATE {table} SET {assignments} WHERE {pk} = %s").format(
                table=Identifier(spec.table_name),
                assignments=assignments,
                pk=Identifier(spec.primary_key),
            )
            conn.execute(query, [clean_payload[c] for c in columns] + [record_id])
            return record_id

    # Child tables that reference each master table through an ON DELETE RESTRICT
    # foreign key (see migration 001_create_core_schema.sql). This is the single
    # source of truth for relationship safety, shared by single-record delete and
    # bulk "delete all" so both refuse to orphan linked rows.
    _CHILD_RELATIONSHIPS: dict[str, tuple[tuple[str, str, str], ...]] = {
        "customers": (("daily_followups", "customer_id", "يوجد تعاملات يومية مرتبطة بالعميل"),),
        "employees": (("daily_followups", "employee_id", "يوجد تعاملات يومية مرتبطة بالموظف"),),
        "case_statuses": (("daily_followups", "case_status_id", "يوجد تعاملات يومية مرتبطة بالحالة"),),
    }

    def delete_record(self, spec: TableSpec, record_id: Any) -> None:
        blockers = self.delete_blockers(spec, record_id)
        if blockers:
            raise ValueError("\n".join(blockers))
        query = SQL("DELETE FROM {table} WHERE {pk} = %s").format(
            table=Identifier(spec.table_name),
            pk=Identifier(spec.primary_key),
        )
        with self.connect() as conn:
            conn.execute(query, [record_id])

    def delete_blockers(self, spec: TableSpec, record_id: Any) -> list[str]:
        blockers: list[str] = []
        with self.connect() as conn:
            for table_name, column_name, message in self._CHILD_RELATIONSHIPS.get(spec.key, ()):
                count = conn.execute(
                    SQL("SELECT COUNT(*) AS c FROM {table} WHERE {column} = %s").format(
                        table=Identifier(table_name),
                        column=Identifier(column_name),
                    ),
                    [record_id],
                ).fetchone()["c"]
                if count:
                    blockers.append(f"{message}: {count}")
        return blockers

    def delete_all_records(self, spec: TableSpec) -> int:
        """Delete every row of this screen's own table only.

        Refuses the operation (raising ``ValueError``) when linked child rows
        would be orphaned, so a full-table wipe can never violate the existing
        ON DELETE RESTRICT relationships. Returns the number of rows deleted.
        """
        blockers = self.delete_all_blockers(spec)
        if blockers:
            raise ValueError("\n".join(blockers))
        query = SQL("DELETE FROM {table}").format(table=Identifier(spec.table_name))
        with self.connect() as conn:
            cursor = conn.execute(query)
            deleted = getattr(cursor, "rowcount", 0)
            return deleted if isinstance(deleted, int) and deleted >= 0 else 0

    def delete_all_blockers(self, spec: TableSpec) -> list[str]:
        """Reasons a full-table delete is blocked, mirroring :meth:`delete_blockers`.

        A bulk delete is blocked whenever *any* child row still references this
        table (an ``IS NOT NULL`` foreign-key value), since removing all parents
        would break those links.
        """
        blockers: list[str] = []
        with self.connect() as conn:
            for table_name, column_name, message in self._CHILD_RELATIONSHIPS.get(spec.key, ()):
                count = conn.execute(
                    SQL("SELECT COUNT(*) AS c FROM {table} WHERE {column} IS NOT NULL").format(
                        table=Identifier(table_name),
                        column=Identifier(column_name),
                    ),
                ).fetchone()["c"]
                if count:
                    blockers.append(f"{message}: {count}")
        return blockers

    def _next_id(self, conn, spec: TableSpec) -> int:
        """Preview-only hint for the id a new record will *likely* get.

        Shown in the read-only code field when the user clicks "New". It is NOT
        used to allocate the real primary key — allocation happens atomically in
        :meth:`save_record` via the table's PostgreSQL sequence (RETURNING). This
        MAX(pk)+1 value is display-only and safe because it never becomes the
        stored id; under concurrency the sequence, not this number, decides.
        """
        query = SQL("SELECT COALESCE(MAX({pk}), 0) + 1 AS next_id FROM {table}").format(
            pk=Identifier(spec.primary_key),
            table=Identifier(spec.table_name),
        )
        next_id = int(conn.execute(query).fetchone()["next_id"])
        # customers and products both start their user-facing code at 1001.
        if spec.key in {"customers", "products"}:
            return max(1001, next_id)
        return next_id

    def _coerce_value(self, field: FieldSpec, value: Any) -> Any:
        if value is None:
            return None
        text = str(value).strip()
        if text == "":
            return None
        if field.data_type == "int":
            try:
                return int(text)
            except ValueError as exc:
                raise ValueError(f"قيمة رقمية غير صحيحة في {field.label}: {text}") from exc
        if field.data_type == "float":
            try:
                return float(text)
            except ValueError as exc:
                raise ValueError(f"قيمة رقمية غير صحيحة في {field.label}: {text}") from exc
        if field.data_type == "date":
            from datetime import datetime

            try:
                return datetime.strptime(text[:10], "%Y-%m-%d").date()
            except ValueError as exc:
                raise ValueError(
                    f"تاريخ غير صحيح في {field.label} (الصيغة YYYY-MM-DD): {text}"
                ) from exc
        return text
