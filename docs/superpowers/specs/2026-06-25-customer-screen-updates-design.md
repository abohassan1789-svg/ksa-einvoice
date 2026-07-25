# Customer Screen Updates Design

## Goal

Update the PySide6 CRM review customer screen so it matches the requested workflow: hide unused installment fields, select customer area from `places`, improve RTL layout density, add an F1 customer lookup dialog, and make customer IDs start at 1001.

## Scope

- Modify the active review UI in `app/ui/review_window.py`.
- Modify metadata and CRUD behavior in `app/services/review_data_service.py`.
- Add a PostgreSQL migration script that renumbers existing customers from 1001 and updates `daily_followups.customer_id`.
- Add focused tests for customer ID generation and metadata behavior.

## Design

The customer table keeps all existing database columns. The four unused fields are hidden from the customer form only: `installment_duration_years`, `remaining_installments`, `installment_amount`, and `legacy_area_number_2`.

The customer area code remains stored in `customers.place_area_feddan`, but the UI presents it as a single-selection area control populated from `places`. Selecting an area stores `places.place_id` and displays the corresponding `places.place_number`.

The customer page layout uses a wider list panel and slightly shorter input controls. Labels and editors keep explicit RTL alignment.

Pressing F1 on the customer page opens a customer lookup dialog. The dialog searches across the visible columns, lets the user show or hide columns, and double-clicking a row loads that customer into the underlying customer form for review or editing.

New customer IDs use `MAX(customer_id) + 1`, with a minimum starting value of 1001 for customers only. A migration script renumbers existing customers and updates dependent daily followups in the same transaction.

## Verification

- Unit tests cover hidden customer fields, customer minimum ID generation, and lookup filtering helpers.
- Run the full pytest suite.
- Run a Python compile check for the modified app files.

