"""Multi-page support shared by the printable invoice templates.

النماذج الثالث → الثامن each draw on a **fixed** 816 × 1056 px sheet with
``overflow:hidden``, because every one of them was measured off a reference PDF
(or a design bundle) whose blocks are positioned absolutely. That works
perfectly right up to the moment the items table grows past the page — and then
Chromium simply stops painting at 1056px, with no error and no warning. What
disappears is whatever the design put last: on النماذج الثالث/الرابع/السادس/الثامن
that is the **QR and the totals**, which is how a nine-item invoice printed with
its QR sliced in half (user report, 2026-07-23); on النموذج السابع it is the
surplus item rows.

The rule the user set for all of them (2026-07-23):

    «ما يجيش مقصوص كده — رحّل الباركود والإجماليات لصفحة ثانية»

so this module supplies the two pieces every template needs to obey it:

* :func:`paginate` — split the item lines into pages, given how much room a page
  has for rows and how much is left once the totals/QR band is on it. The band
  always lands on the **last** page, whole;
* :func:`row_height` / :func:`wrapped_lines` — an estimate of how tall a row will
  actually render, because an item name that wraps to three lines makes its row
  three lines tall and a page budget counted in design-height rows would then
  overflow anyway (see [[invoice-long-item-names]] — the name wraps and grows the
  row, by house rule).

Why estimates and not a real measurement: the templates are pure string builders
with no browser attached (that is what keeps them unit-testable and what lets the
PDF exporter run them off-screen). The estimator is deliberately biased to
*over*-count — a page that takes one row fewer than it could is invisible, while
one that takes one row too many is the bug this module exists to remove.

None of this compresses anything. Rows keep their design height on every page:
squeezing 20 rows into a page sized for 5 is what the templates used to do and it
produced unreadable 18px rows *and* still clipped. Pages are cheap.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Sequence

# ---------------------------------------------------------------------------
# Text metrics
# ---------------------------------------------------------------------------
# Per-character advance widths in ems, for the bold sans faces every template
# uses (Arial / Tahoma / Calibri are close enough at this precision). The ASCII
# figures come from Arial Bold's own metrics; the Arabic one is an average taken
# off النموذج السابع's reference values, rounded **up** so the estimate errs
# towards predicting a wrap that does not happen.
_EM_DIGIT = 0.556
_EM_THIN = 0.278       # , and .
_EM_SPACE = 0.278
_EM_ASCII = 0.60       # any other latin character, rounded up
_EM_ARABIC = 0.50


def text_em(text: str) -> float:
    """Estimated width of ``text`` in ems."""
    total = 0.0
    for ch in text:
        if ch.isdigit():
            total += _EM_DIGIT
        elif ch in ",.":
            total += _EM_THIN
        elif ch == " ":
            total += _EM_SPACE
        elif "؀" <= ch <= "ۿ" or "ﭐ" <= ch <= "﻿":
            total += _EM_ARABIC
        else:
            total += _EM_ASCII
    return total


def wrapped_lines(text: str, width_px: float, font_px: float) -> int:
    """How many lines ``text`` needs inside ``width_px`` at ``font_px``.

    A greedy word-wrap simulation rather than ``ceil(total / width)``: the latter
    under-counts badly for a column narrow enough that a single long word is
    pushed to a line of its own, which is exactly the case that overflows.

    A word longer than the whole line breaks mid-token — every template sets
    ``overflow-wrap:anywhere`` (or ``word-wrap:break-word``) on the name column
    for precisely that reason.
    """
    if width_px <= 0 or font_px <= 0:
        return 1
    limit = width_px / font_px          # the line's width, in ems
    if limit <= 0:
        return 1
    lines = 1
    used = 0.0
    for word in str(text).split():
        width = text_em(word)
        gap = _EM_SPACE if used else 0.0
        if used and used + gap + width > limit:
            lines += 1
            used = 0.0
            gap = 0.0
        if width > limit:
            # Breaks mid-token: fill this line, then whole lines, then a remainder.
            width -= limit - used
            whole = math.floor(width / limit)
            lines += 1 + whole
            used = width - whole * limit
        else:
            used += gap + width
    return max(1, lines)


def row_height(
    text: str,
    *,
    base: float,
    width_px: float,
    font_px: float,
    line_height: float,
    chrome: float = 0.0,
) -> float:
    """The height a row will really occupy once its name column has wrapped.

    ``base`` is the design's own row height, ``chrome`` the row's vertical
    padding and borders. A single-line name always yields ``base`` (so an
    ordinary invoice paginates exactly as its design geometry says it should);
    a name that wraps grows the row and is charged for it.
    """
    lines = wrapped_lines(text, width_px, font_px)
    return max(base, chrome + lines * font_px * line_height)


# ---------------------------------------------------------------------------
# Page splitting
# ---------------------------------------------------------------------------
def paginate(
    items: Sequence[Any],
    height_of: Callable[[Any], float],
    *,
    budget: float,
    budget_last: float,
    foot: float = 0.0,
) -> list[list[Any]]:
    """Split ``items`` into pages. The last page is the one carrying the band.

    ``budget`` is the room a page has for rows; ``budget_last`` the smaller room
    left on the page that also prints the totals and the QR. The returned list
    always holds at least one page (possibly empty — an invoice with no lines
    still prints its totals), and the **last** entry is always the page the
    caller must put the band on.

    ``foot`` is height a page needs *only* when the invoice runs to more than one
    — the «صفحة x من y» strip. It cannot simply be subtracted up front: the strip
    exists because the invoice paginated, and the invoice must not paginate
    because of the strip. So it is charged in a second pass, which is stable
    because reserving more room can only ever raise the page count.

    Pages are filled front to back, as a printed invoice is read. When the rows
    fill the final page so completely that the band no longer fits, the minimum
    tail — one row — moves onto a page of its own rather than the band being
    exiled to an empty sheet: «الصفحة الأخيرة فيها صنف والإجماليات» reads as a
    continuation, an empty page with a lone QR reads as a fault. The band does
    get a page to itself in the one case where nothing else will do: a single row
    too tall to share a page with it.
    """
    items = list(items)
    heights = [max(0.0, float(height_of(item))) for item in items]

    # The ordinary invoice: everything fits on one sheet, band included.
    if sum(heights) <= budget_last:
        return [items]
    if foot:
        budget -= foot
        budget_last -= foot

    pages: list[list[Any]] = []
    current: list[Any] = []
    used = 0.0
    for item, height in zip(items, heights):
        if current and used + height > budget:
            pages.append(current)
            current, used = [], 0.0
        current.append(item)
        used += height
    pages.append(current)

    tail = pages[-1]
    tail_h = sum(heights[len(items) - len(tail):])
    if tail_h > budget_last:
        if heights[-1] > budget_last:
            # One row that cannot share a page with the band at all: it keeps its
            # page whole and the band takes the next. Nothing is cut either way.
            pages.append([])
        else:
            moved = tail.pop()
            if not tail:                      # never leave a blank page behind
                pages.pop()
            pages.append([moved])
    return pages


def page_label(page: int, count: int) -> str:
    """«صفحة ٢ من ٣», in Latin digits — empty for a single-page invoice."""
    if count <= 1:
        return ""
    return f"صفحة {page} من {count}"


# The strip's own height (14px of 12px/1.15 text) plus 16px — 4.2mm — of clearance
# below it. The clearance is not decoration: an office laser cannot print within
# ~4mm of the paper edge, so a strip seated any lower prints with its descenders
# shaved off, which is the same «مقصوص» complaint in miniature. Every template
# charges FOOT_H to its page budgets (only when the invoice really paginates —
# see `paginate`), which is what keeps the rows off it.
FOOT_H = 30
_FOOT_TOP = 26      # the strip's top, measured up from the sheet's bottom edge


def page_foot(page: int, count: int, *, sheet_h: int, sheet_w: int = 816,
              colour: str = "#4a4a4a", size: float = 12) -> str:
    """The «صفحة x من y» strip for one sheet — empty on a single-page invoice.

    Absolutely positioned, so the sheet must be ``position:relative`` (all the
    paginating templates are) and the strip costs the flow nothing.
    """
    label = page_label(page, count)
    if not label:
        return ""
    return (
        f'<div dir="rtl" lang="ar" style="position:absolute; left:0; '
        f'top:{sheet_h - _FOOT_TOP}px; width:{sheet_w}px; text-align:center; '
        f'font-size:{size}px; font-weight:400; line-height:1.15; '
        f'color:{colour};">{label}</div>'
    )


# ---------------------------------------------------------------------------
# Sheet chrome
# ---------------------------------------------------------------------------
# Every sheet but the last must start a new printed page. Both spellings are
# emitted: `page-break-after` is the one Chromium's print path has honoured
# since forever, `break-after` its modern replacement.
SHEET_BREAK = "page-break-after:always; break-after:page;"

# A sheet must never be split down the middle by the printer's own pagination:
# the sheets are exactly one page tall, so a stray half-pixel would otherwise
# push a hairline of every sheet onto a page of its own.
SHEET_KEEP = "page-break-inside:avoid; break-inside:avoid;"


def sheet_style(*, last: bool) -> str:
    """The pagination declarations a paginated sheet's own style must carry."""
    return SHEET_KEEP if last else f"{SHEET_KEEP} {SHEET_BREAK}"


__all__ = [
    "FOOT_H",
    "paginate",
    "page_foot",
    "page_label",
    "row_height",
    "sheet_style",
    "text_em",
    "wrapped_lines",
    "SHEET_BREAK",
    "SHEET_KEEP",
]
