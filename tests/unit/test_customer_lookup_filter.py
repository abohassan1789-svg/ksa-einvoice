from app.ui.review_window import filter_lookup_rows, project_visible_columns


ROWS = [
    {
        "customer_id": 1001,
        "customer_name": "مصطفى محمد",
        "phone_number": "01155590109",
        "area_number": "9",
        "unit_number": "20",
    },
    {
        "customer_id": 1002,
        "customer_name": "محمد عبد الله",
        "phone_number": "01228660466",
        "area_number": "0",
        "unit_number": "21",
    },
]


def test_filter_lookup_rows_searches_any_visible_column():
    visible_columns = ["customer_id", "customer_name", "phone_number", "area_number"]

    result = filter_lookup_rows(ROWS, "60466", visible_columns)

    assert result == [ROWS[1]]


def test_filter_lookup_rows_ignores_hidden_columns():
    visible_columns = ["customer_id", "customer_name"]

    result = filter_lookup_rows(ROWS, "60466", visible_columns)

    assert result == []


def test_project_visible_columns_keeps_order():
    result = project_visible_columns(ROWS[0], ["customer_name", "customer_id"])

    assert result == ["مصطفى محمد", "1001"]

