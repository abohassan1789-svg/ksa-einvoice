from app.services.review_data_service import ReviewDataService, TABLE_SPECS


class _FakeCursor:
    def __init__(self, next_id):
        self.next_id = next_id

    def fetchone(self):
        return {"next_id": self.next_id}


class _FakeConnection:
    def __init__(self, next_id):
        self.next_id = next_id

    def execute(self, query, params=None):
        return _FakeCursor(self.next_id)


class _RecordedException:
    def __init__(self, query, params):
        self.query = query
        self.params = params

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class _ListConnection:
    """Fake connection that records execute() and returns preset rows."""

    def __init__(self, rows_by_call):
        # rows_by_call: list of list[dict], one entry per execute() call.
        self.rows_by_call = list(rows_by_call)
        self.calls: list[tuple] = []
        self.closed = False

    def execute(self, query, params=None):
        self.calls.append((query, params))
        cur = _RecordedException(query, params)
        cur._rows = self.rows_by_call.pop(0) if self.rows_by_call else []
        return cur


def test_search_customers_empty_keyword_returns_all_ordered():
    service = ReviewDataService()
    rows = [{"customer_id": 1, "customer_name": "A", "phone_number": "010"}]
    service._connection = _ListConnection([rows])

    result = service.search_customers("")

    assert result == rows
    query, params = service._connection.calls[0]
    # No keyword -> single param (the limit).
    assert params == [100]


def test_search_customers_selects_lightweight_lookup_fields():
    service = ReviewDataService()
    rows = [{"customer_id": 1, "customer_name": "A", "phone_number": "010", "building": "B"}]
    service._connection = _ListConnection([rows])

    result = service.search_customers("", limit=None)

    assert result == rows
    query, params = service._connection.calls[0]
    rendered = str(query)
    assert "customer_name" in rendered
    assert "phone_number" in rendered
    assert "area_number" not in rendered
    assert "unit_number" not in rendered
    assert "installment_amount" not in rendered
    assert params == []


def test_search_customers_with_keyword_orders_exact_code_first():
    service = ReviewDataService()
    rows = [
        {"customer_id": 1005, "customer_name": "Ahmed", "phone_number": "1005"},
        {"customer_id": 1002, "customer_name": "Other", "phone_number": "999"},
    ]
    service._connection = _ListConnection([rows])

    result = service.search_customers("1005")

    assert result == rows
    _query, params = service._connection.calls[0]
    # Three ILIKE terms + exact-match sort key + limit.
    assert params == ["%1005%", "%1005%", "%1005%", "1005", 100]


def test_lookup_customers_empty_query_returns_first_100_by_name_then_code():
    service = ReviewDataService()
    rows = [{"customer_id": 500, "customer_name": "Customer 500", "phone_number": "010500"}]
    service._connection = _ListConnection([rows])

    result = service.lookup_customers("   ", limit=500)

    assert result == rows
    query, params = service._connection.calls[0]
    rendered = str(query)
    assert "SELECT *" not in rendered
    assert "customer_id" in rendered
    assert "customer_name" in rendered
    assert "phone_number" in rendered
    assert "ORDER BY customer_name" in rendered
    assert params == [100]


def test_lookup_customers_searches_name_code_and_phone_with_trimmed_partial_match():
    service = ReviewDataService()
    rows = [{"customer_id": 500, "customer_name": "محمد 500", "phone_number": "010500"}]
    service._connection = _ListConnection([rows])

    result = service.lookup_customers("  محمد  ", limit=1000)

    assert result == rows
    query, params = service._connection.calls[0]
    rendered = str(query)
    assert "CAST(customer_id AS text) ILIKE %s" in rendered
    assert "COALESCE(customer_name, '') ILIKE %s" in rendered
    assert "COALESCE(CAST(phone_number AS text), '') ILIKE %s" in rendered
    assert "COALESCE(customer_name, '') ILIKE %s) DESC" in rendered
    assert params == [
        "%محمد%",
        "%محمد%",
        "%محمد%",
        "محمد",
        "محمد",
        "محمد%",
        100,
    ]


def test_lookup_customers_never_uses_limit_above_100():
    service = ReviewDataService()
    service._connection = _ListConnection([[]])

    service.lookup_customers("500", limit=12000)

    _query, params = service._connection.calls[0]
    assert params[-1] == 100


def test_customer_installment_fields_are_hidden_from_form():
    customer_spec = TABLE_SPECS["customers"]

    hidden_fields = {field.name for field in customer_spec.fields if field.hidden_on_form}

    assert hidden_fields == {
        "installment_duration_years",
        "remaining_installments",
        "installment_amount",
        "legacy_area_number_2",
    }


def test_customer_next_id_starts_at_1001_when_table_is_empty():
    service = ReviewDataService()

    next_id = service._next_id(_FakeConnection(1), TABLE_SPECS["customers"])

    assert next_id == 1001


def test_non_customer_next_id_keeps_max_plus_one_behavior():
    service = ReviewDataService()

    next_id = service._next_id(_FakeConnection(1), TABLE_SPECS["places"])

    assert next_id == 1


def test_daily_followup_list_query_selects_only_requested_columns():
    service = ReviewDataService()
    rows = [{"daily_followup_id": 1, "customer_name": "Ali"}]
    service._connection = _ListConnection([rows])

    result = service.list_records(TABLE_SPECS["daily_followups"], "")

    assert result == rows
    query, _params = service._connection.calls[0]
    rendered = str(query)
    assert "buyer_name" not in rendered
    assert "buyer_commission_amount" not in rendered
    assert "daily_followup_id" in rendered
    assert "customer_name" in rendered


def test_daily_followup_search_uses_lightweight_columns():
    service = ReviewDataService()
    service._connection = _ListConnection([[{"daily_followup_id": 1}]])

    service.list_records(TABLE_SPECS["daily_followups"], "90")

    query, params = service._connection.calls[0]
    rendered = str(query)
    assert "buyer_commission_amount" not in rendered
    assert "seller_commission_amount" not in rendered
    assert "installment_duration_years" not in rendered
    assert "customer_name" in rendered
    assert "phone_number" in rendered
    assert params == ["%90%"] * 7 + [500]
