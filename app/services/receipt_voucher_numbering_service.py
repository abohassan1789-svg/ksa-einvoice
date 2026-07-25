"""Voucher-number generation for the Receipt Vouchers module (سندات القبض).

Turns the raw, concurrency-safe PostgreSQL sequence
(``receipt_voucher_number_seq``, owned by ``receipt_vouchers.voucher_number``)
into formatted document numbers ``PA-001``, ``PA-002``, ``PA-003``, ... and
keeps automatic numbers from ever colliding with manually-entered ones.

Design
------
* Formatting is a pure function: sequence value ``n`` -> ``PA-<n zero-padded to
  3>`` (grows past 999 -> ``PA-1000``). This matches the SQL column DEFAULT
  ``'PA-' || to_char(nextval(...), 'FM000')`` exactly, so a number produced in
  Python and one produced by the database DEFAULT are identical.
* ``next_number`` consumes the sequence atomically (safe under concurrency).
* ``peek_next_number`` shows what the next automatic number *would* be, without
  consuming it (advisory, e.g. to pre-fill a form).
* When an authorised user stores a manual ``PA-<n>``, ``register_manual_number``
  advances the sequence past ``<n>`` so a future automatic number cannot repeat
  it. Manual numbers that are not in the ``PA-<n>`` shape (a free-form string)
  are accepted as-is and simply don't move the sequence; the UNIQUE index is the
  final duplicate guard in every case.

No SQL is written here — all sequence access goes through
``ReceiptVoucherRepository``.
"""

from __future__ import annotations

import re

from app.models.receipt_voucher import (
    VOUCHER_NUMBER_PAD,
    VOUCHER_NUMBER_PREFIX,
)
from app.repositories.receipt_voucher_repository import ReceiptVoucherRepository

# Recognises an automatic-style number: PA- followed by digits only.
_AUTO_NUMBER_RE = re.compile(rf"^{re.escape(VOUCHER_NUMBER_PREFIX)}(\d+)$")


def format_number(sequence_value: int) -> str:
    """Render a raw sequence value as a ``PA-<n>`` document number.

    ``format_number(1) == 'PA-001'``; padding is a minimum, not a cap, so
    ``format_number(1000) == 'PA-1000'``.
    """
    return f"{VOUCHER_NUMBER_PREFIX}{int(sequence_value):0{VOUCHER_NUMBER_PAD}d}"


def parse_sequence_value(voucher_number: str) -> int | None:
    """Return the integer ``n`` from a ``PA-<n>`` number, or None if it is not
    in that automatic shape."""
    if voucher_number is None:
        return None
    match = _AUTO_NUMBER_RE.match(voucher_number.strip())
    return int(match.group(1)) if match else None


class ReceiptVoucherNumberingService:
    def __init__(self, repository: ReceiptVoucherRepository | None = None) -> None:
        self.repository = repository or ReceiptVoucherRepository()

    def next_number(self) -> str:
        """Consume the sequence and return the next automatic number (atomic)."""
        return format_number(self.repository.next_sequence_value())

    def peek_next_number(self) -> str:
        """Advisory next automatic number for pre-filling a form (not a reservation)."""
        return format_number(self.repository.peek_next_sequence_value())

    def register_manual_number(self, voucher_number: str) -> None:
        """Advance the sequence past a manual ``PA-<n>`` so future automatic
        numbers cannot collide with it. A no-op for non ``PA-<n>`` numbers.
        """
        value = parse_sequence_value(voucher_number)
        if value is not None:
            self.repository.reserve_number_ceiling(value)
