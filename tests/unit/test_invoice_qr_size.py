"""The printed QR size is pinned per template.

The templates were each measured off their own reference PDF and carried five
different QR sizes (110 / 111 / 121 / 168). At the user's request (2026-07-16)
they were unified on النموذج الأول's 168px and the surrounding geometry was
re-seated around it. That is easy to undo by accident while re-measuring one
template against its reference, so it is pinned here: every builder must emit a
QR at exactly its pinned constant, and none may hard-code its own number.

2026-07-18 — the QR did not survive an A4 print: 168px put a 101-across Phase-2
code at 0.440mm per module, which the print path shrank to roughly 0.40mm. Every
template moved to a **vector SVG at 216px** (57.15mm, 0.615mm per module).

The standing rule, and why there is only ONE size constant: "أي تعديل أو تحديث
عملته على الـ QR code في النموذج الأولاني يكون متحدث على جميع النماذج" — النموذج
الأول's QR *is* every template's QR. ``QR_PRINT_PX_V1`` is kept only as an alias
of ``QR_PRINT_PX``; a test below pins that they are equal, so the first template
cannot be changed alone.

Growing the code broke three layouts that had been measured around the old 168px
square, and each was re-seated rather than left to overflow:

* v2 — the QR cell was a fixed 180px content-box; now sized from QR_PRINT_PX.
* v5 — the meta boxes rode ~22px up inside the code; ``_BELOW_SHIFT`` is now
  derived from the QR's bottom edge plus a clearance.
* v6 — a 216px code cannot fit the design's 213px panel at all; the panel height
  is derived from the QR and the totals rows are scaled by the same factor so the
  shared top/bottom borders still coincide.
* v7 — ``_HEAD_GROWTH`` was a literal tuned for 168px; now derived.

Vector-specific guarantees live in ``test_invoice_qr_vector.py``.
"""

from __future__ import annotations

import datetime
import re
from decimal import Decimal

import pytest

from app.ui.screens.saudi_invoice_print import (
    QR_PRINT_PX,
    QR_PRINT_PX_V1,
    build_invoice_html,
)
from app.ui.screens.saudi_invoice_print_v2 import build_invoice_html_v2
from app.ui.screens.saudi_invoice_print_v3 import build_invoice_html_v3
from app.ui.screens.saudi_invoice_print_v4 import build_invoice_html_v4
from app.ui.screens.saudi_invoice_print_v5 import build_invoice_html_v5
from app.ui.screens.saudi_invoice_print_v6 import build_invoice_html_v6
from app.ui.screens.saudi_invoice_print_v7 import build_invoice_html_v7
from app.ui.screens.saudi_invoice_print_v8 import build_invoice_html_v8

BUILDERS = [
    ("النموذج الأول", build_invoice_html),
    ("النموذج الثاني", build_invoice_html_v2),
    ("النموذج الثالث", build_invoice_html_v3),
    ("النموذج الرابع", build_invoice_html_v4),
    ("النموذج الخامس", build_invoice_html_v5),
    ("النموذج السادس", build_invoice_html_v6),
    ("النموذج السابع", build_invoice_html_v7),
    ("النموذج الثامن", build_invoice_html_v8),
]

# Kept as a separate list only so a future per-template divergence has an obvious
# place to go; today it is BUILDERS minus النموذج الأول and every entry is the
# same size. See the module docstring.
SHARED_SIZE_BUILDERS = [
    ("النموذج الثاني", build_invoice_html_v2),
    ("النموذج الثالث", build_invoice_html_v3),
    ("النموذج الرابع", build_invoice_html_v4),
    ("النموذج الخامس", build_invoice_html_v5),
    ("النموذج السادس", build_invoice_html_v6),
    ("النموذج السابع", build_invoice_html_v7),
    ("النموذج الثامن", build_invoice_html_v8),
]


def _data() -> dict:
    return {
        "invoice_number": "6285",
        "issue_datetime": datetime.datetime(2026, 6, 25, 10, 30),
        "payment_label": "نقدي",
        "seller": {"name": "شركة ريوف المواسم", "address": "الرياض", "vat": "312389238400003", "cr": "1010101010"},
        "customer": {"name": "المصنع روف الحال", "address": "سكاكا", "vat": "310045693200003",
                     "cr": "7070707070", "phone": "0500000000"},
        "lines": [
            {"code": "R3962", "name": "شكوالته بلجيكي", "qty": Decimal("65"), "price": Decimal("80"),
             "unit": "PCE", "before": Decimal("5200"), "vat_amount": Decimal("780"),
             "vat_rate": Decimal("15"), "total": Decimal("5980")},
        ],
        "totals": {"subtotal": Decimal("5200"), "vat": Decimal("780"), "total": Decimal("5980"),
                   "discount": Decimal("0"), "taxable": Decimal("5200")},
        "qr_payload": "AR1234TEST",
        "printed_by": "مدير النظام",
        "printed_at": "2026-06-25 10:30",
        "vat_rate": "15",
    }


def _qr_img_tag(html: str) -> str:
    """The <img> whose src is the QR data URI (the logo is a data URI too)."""
    tags = re.findall(r"<img[^>]*>", html)
    qr_tags = [t for t in tags if re.search(r'alt="(?:QR|qr)"', t)]
    assert len(qr_tags) == 1, f"expected exactly one QR <img>, found {len(qr_tags)}"
    return qr_tags[0]


def _wh(tag: str) -> tuple[float, float]:
    width = re.search(r"width:\s*([\d.]+)px", tag)
    height = re.search(r"height:\s*([\d.]+)px", tag)
    assert width and height, "QR <img> has no explicit width/height"
    return float(width.group(1)), float(height.group(1))


@pytest.mark.parametrize("label, builder", SHARED_SIZE_BUILDERS,
                         ids=[b[0] for b in SHARED_SIZE_BUILDERS])
def test_other_templates_print_the_qr_at_the_shared_size(label, builder):
    width, height = _wh(_qr_img_tag(builder(_data())))
    assert width == float(QR_PRINT_PX), f"{label}: QR width is not QR_PRINT_PX"
    assert height == float(QR_PRINT_PX), f"{label}: QR height is not QR_PRINT_PX"


def test_template_one_prints_the_qr_at_the_shared_size():
    width, height = _wh(_qr_img_tag(build_invoice_html(_data())))
    assert width == float(QR_PRINT_PX_V1)
    assert height == float(QR_PRINT_PX_V1)


def test_there_is_only_one_qr_size_for_all_templates():
    """The standing rule, enforced structurally.

    "أي تعديل عملته على الـ QR في النموذج الأولاني يكون متحدث على جميع النماذج" —
    so QR_PRINT_PX_V1 must stay an alias of QR_PRINT_PX. If someone reintroduces a
    per-template size by giving V1 its own value, this fails immediately.
    """
    assert QR_PRINT_PX_V1 == QR_PRINT_PX


def test_all_eight_templates_agree_on_the_qr_size():
    sizes = {label: _wh(_qr_img_tag(builder(_data()))) for label, builder in BUILDERS}
    distinct = set(sizes.values())
    assert len(distinct) == 1, f"templates disagree on QR size: {sizes}"
    assert distinct.pop() == (float(QR_PRINT_PX), float(QR_PRINT_PX))


@pytest.mark.parametrize("label, builder", BUILDERS, ids=[b[0] for b in BUILDERS])
def test_every_template_keeps_the_qr_square(label, builder):
    width, height = _wh(_qr_img_tag(builder(_data())))
    assert width == height, f"{label}: QR is not square ({width}x{height})"


@pytest.mark.parametrize("label, builder", BUILDERS, ids=[b[0] for b in BUILDERS])
def test_every_template_uses_the_vector_qr(label, builder):
    """No template may fall back to the raster PNG.

    ``image-rendering: pixelated`` used to be required here, back when the QR was
    a downscaled bitmap. On an SVG it does the opposite of what it says — it makes
    Chromium rasterise the vector at layout size — so its ABSENCE is now the thing
    worth pinning.
    """
    tag = _qr_img_tag(builder(_data()))
    assert "data:image/svg+xml;base64," in tag, f"{label}: QR is not the vector one"
    assert "data:image/png" not in tag, f"{label}: QR fell back to a raster PNG"
    assert "image-rendering" not in tag, (
        f"{label}: image-rendering on an SVG forces rasterisation"
    )
