"""النموذج الأول prints a vector QR that survives an A4 laser print.

Why this file exists (2026-07-18): the invoice scanned fine as a PDF on screen
but not once printed on A4. The measured cause was the printed MODULE PITCH.

Two different numbers get called "modules" and conflating them caused real
confusion here, so this file is explicit throughout. A QR *symbol* of version V
is ``21 + 4*(V-1)`` modules on a side; the 4-module quiet zone sits outside the
symbol and is never part of that formula. The printed box contains symbol +
quiet zone, so the pitch divides by the TOTAL:

    payload (base64)   version   symbol modules   total across
    612 chars (pre-fix)   19           93             101
    500 chars (current)   17           85              93

The old 168px box put 101 across at 44.45mm = 0.440mm per module, which the print
path shrank further to roughly 0.40mm. That is below what an office laser holds
once toner spread is added, while the PDF kept the full-resolution artwork and so
still read on screen.

Deliberately NOT the cause (each was measured before changing anything):
resampling — the box_size=16 source is an exact integer multiple of the module
grid, so nearest-neighbour downscaling cost 0.00% module damage at 300/600dpi;
the quiet zone was already 4 modules; contrast was already pure #000/#fff; and
the PNG was embedded into the PDF losslessly and uncompressed-by-scaling.

So the fix is size first (QR_PRINT_PX_V1 = 192px = 50.8mm = 0.503mm/module) and
vector second — SVG has no source resolution to lose, so the printer rasterises
the modules itself at 600-1200dpi and the code survives the Letter->A4
fit-to-page shrink losslessly instead of being resampled to fit.

These tests pin the properties that make it scan, and — most importantly — that
the encoded ZATCA TLV bytes are identical to what the raster path encodes. The
carrier changed; the payload must not.
"""

from __future__ import annotations

import base64
import re

import pytest

from app.ui.screens.saudi_invoice_print import (
    QR_MIN_MODULE_MM,
    QR_PRINT_PX_V1,
    _qr_svg_markup,
    build_invoice_html,
    qr_data_uri,
    qr_vector_data_uri,
)

from .test_invoice_qr_size import _data, _qr_img_tag

qrcode = pytest.importorskip("qrcode", reason="QR rendering is an optional dependency")

QUIET_ZONE_MODULES = 4


def _realistic_phase2_payload() -> str:
    """A signed Phase-2 TLV — the densest QR the app actually prints.

    Built through the real generator rather than a literal, because the module
    count is what the size assertions below hinge on and it is sensitive to the
    encoding mode. A synthetic ``"A" * 604`` would be packed in QR *alphanumeric*
    mode and come out several versions smaller, quietly making the module-pitch
    assertion pass on a code far sparser than any real invoice carries.
    """
    import base64 as b64
    import datetime

    from app.services import saudi_zatca_generator as zg

    return zg.build_qr(
        seller_name="شركة ريوف المواسم للتجارة",
        vat_number="312389238400003",
        timestamp=datetime.datetime(2026, 6, 25, 10, 30),
        total_with_vat="5980.00",
        vat_total="780.00",
        invoice_hash=b64.b64encode(b"h" * 32).decode(),   # SHA-256
        signature=b64.b64encode(b"s" * 64).decode(),      # ECDSA P-256
        public_key=b64.b64encode(b"p" * 88).decode(),
        stamp=b64.b64encode(b"t" * 64).decode(),
    )


LONG_PAYLOAD = _realistic_phase2_payload()


def _svg_from_uri(uri: str) -> str:
    assert uri.startswith("data:image/svg+xml;base64,"), uri[:60]
    return base64.b64decode(uri.split(",", 1)[1]).decode("utf-8")


def _viewbox(svg: str) -> tuple[float, float, float, float]:
    match = re.search(r'viewBox="([-\d.]+) ([-\d.]+) ([-\d.]+) ([-\d.]+)"', svg)
    assert match, "SVG has no viewBox"
    return tuple(float(g) for g in match.groups())  # type: ignore[return-value]


def _matrix(payload: str):
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=1,
        border=QUIET_ZONE_MODULES,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    return qr.get_matrix()


# ----------------------------------------------------------------------
# Encoded data integrity — the requirement that outranks every other one
# ----------------------------------------------------------------------
@pytest.mark.parametrize("payload", ["AR1234TEST", LONG_PAYLOAD])
def test_vector_qr_encodes_exactly_the_same_bytes_as_the_raster_qr(payload):
    """Switching carrier must not touch a single bit of the ZATCA TLV."""
    svg = _svg_from_uri(qr_vector_data_uri(payload))
    expected = _matrix(payload)
    # Rebuild the module grid from the SVG path and compare cell by cell.
    span = int(_viewbox(svg)[2])
    grid = [[False] * span for _ in range(span)]
    for x, y, run in re.findall(r"M(\d+) (\d+)h(\d+)v1h-\3z", svg):
        for i in range(int(run)):
            grid[int(y)][int(x) + i] = True

    assert span == len(expected), "module count differs from the canonical QR"
    assert grid == expected, "vector QR modules differ from the raster QR modules"


def test_vector_qr_decodes_back_to_the_original_payload():
    """End-to-end: the drawn modules really carry the payload we put in."""
    zbar = pytest.importorskip(
        "pyzbar.pyzbar", reason="pyzbar not installed; module-grid equality still covers this"
    )
    from PIL import Image

    payload = LONG_PAYLOAD
    matrix = _matrix(payload)
    scale = 8
    span = len(matrix)
    img = Image.new("L", (span * scale, span * scale), 255)
    px = img.load()
    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            if dark:
                for dy in range(scale):
                    for dx in range(scale):
                        px[x * scale + dx, y * scale + dy] = 0
    decoded = zbar.decode(img)
    assert decoded, "rendered QR did not decode"
    assert decoded[0].data.decode() == payload


# ----------------------------------------------------------------------
# Geometry: square, quiet zone, physical size
# ----------------------------------------------------------------------
def test_viewbox_is_square_and_starts_at_origin():
    svg = _svg_from_uri(qr_vector_data_uri(LONG_PAYLOAD))
    min_x, min_y, width, height = _viewbox(svg)
    assert (min_x, min_y) == (0.0, 0.0)
    assert width == height, f"viewBox is not square: {width}x{height}"


def test_aspect_ratio_is_locked_so_the_code_can_never_stretch():
    svg = _svg_from_uri(qr_vector_data_uri(LONG_PAYLOAD))
    assert 'preserveAspectRatio="xMidYMid meet"' in svg


def test_quiet_zone_is_four_clear_modules_on_all_four_sides():
    svg = _svg_from_uri(qr_vector_data_uri(LONG_PAYLOAD))
    span = int(_viewbox(svg)[2])
    grid = [[False] * span for _ in range(span)]
    for x, y, run in re.findall(r"M(\d+) (\d+)h(\d+)v1h-\3z", svg):
        for i in range(int(run)):
            grid[int(y)][int(x) + i] = True

    z = QUIET_ZONE_MODULES
    for i in range(z):
        assert not any(grid[i]), f"top quiet-zone row {i} has dark modules"
        assert not any(grid[span - 1 - i]), f"bottom quiet-zone row {i} has dark modules"
        assert not any(row[i] for row in grid), f"left quiet-zone column {i} has dark modules"
        assert not any(row[span - 1 - i] for row in grid), f"right quiet-zone column {i} is dark"

    # And the zone is exactly 4, not more: row/col 4 must carry the finder pattern.
    assert grid[z][z], "module at the quiet-zone edge is not the finder pattern"


def test_the_reference_payload_really_is_a_dense_phase2_code():
    """Guards the premise of the size test below.

    ``span`` is the TOTAL across (symbol + 2*4 quiet zone), not the symbol size.

    The bound is a floor, not an equality: it exists so the size assertions can't
    pass on a trivially sparse code. It was 101 across (version 19, 93 symbol
    modules) until the TLV encoding fix (2026-07-18) put raw DER bytes in tags
    7-9 instead of their base64 text, cutting ~80 bytes and dropping the symbol to
    version 17 (85 modules, 93 across). A *smaller* count is the fix working; a
    much smaller one would mean the crypto tags went missing, which this catches.
    """
    span = int(_viewbox(_svg_from_uri(qr_vector_data_uri(LONG_PAYLOAD)))[2])
    assert len(LONG_PAYLOAD) > 450, "reference payload is not a signed Phase-2 TLV"
    assert span >= 93, f"expected >=93 modules across, got {span}"
    # The symbol version implied by that span must satisfy 21 + 4*(V-1).
    symbol = span - 8
    assert (symbol - 21) % 4 == 0, f"{symbol} is not a valid QR symbol size"
    version = (symbol - 21) // 4 + 1
    assert version >= 17, f"expected symbol version >=17, got {version}"


def test_symbol_version_and_module_count_obey_the_standard_formula():
    """modules = 21 + 4*(version-1), applied to the SYMBOL only.

    Pinned because the quiet zone was repeatedly folded into "modules" in earlier
    write-ups, which makes a version-19 code look like a version-21 one.
    """
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M,
                       box_size=1, border=QUIET_ZONE_MODULES)
    qr.add_data(LONG_PAYLOAD)
    qr.make(fit=True)

    symbol = qr.modules_count
    assert symbol == 21 + 4 * (qr.version - 1)

    total = len(qr.get_matrix())
    assert total == symbol + 2 * QUIET_ZONE_MODULES
    # ...and the SVG viewBox is the total, not the symbol.
    assert int(_viewbox(_svg_from_uri(qr_vector_data_uri(LONG_PAYLOAD)))[2]) == total


def test_payload_stays_within_the_700_character_qr_limit():
    """Security Features Implementation Standards v1.2 §4.1: up to 700 chars."""
    from app.services.saudi_zatca_generator import QR_MAX_BASE64_CHARS

    assert QR_MAX_BASE64_CHARS == 700
    assert len(LONG_PAYLOAD) <= QR_MAX_BASE64_CHARS


def test_printed_module_size_clears_the_laser_print_floor():
    """The number that actually decides whether a printed code scans."""
    svg = _svg_from_uri(qr_vector_data_uri(LONG_PAYLOAD))
    span = int(_viewbox(svg)[2])
    mm = QR_PRINT_PX_V1 / 96 * 25.4  # CSS px -> mm at the 96dpi print baseline
    per_module_mm = mm / span

    assert mm >= 35.0, f"printed QR is {mm:.1f}mm, under the 35mm floor"
    assert per_module_mm >= QR_MIN_MODULE_MM, (
        f"modules are {per_module_mm:.3f}mm ({span} across at {mm:.1f}mm); "
        f"under {QR_MIN_MODULE_MM}mm office lasers stop scanning reliably"
    )
    # 300dpi headroom: a module must be at least ~6 device dots at 300dpi.
    assert per_module_mm / 25.4 * 300 >= 5.0


# The page is scaled on its way to paper: Windows print dialogs default to "fit to
# printable area" (~0.95) and this artwork is Letter printed on A4 (~0.973).
WORST_CASE_PRINT_SHRINK = 0.92


def test_module_size_survives_the_worst_case_print_shrink():
    """The regression that the first attempt at this fix missed.

    Sizing so the *nominal* pitch just clears the floor is not enough — it left
    0.003mm of headroom and still failed on paper. The printed pitch has to hold
    up after the print path's scaling too.
    """
    span = int(_viewbox(_svg_from_uri(qr_vector_data_uri(LONG_PAYLOAD)))[2])
    nominal = QR_PRINT_PX_V1 / 96 * 25.4 / span
    printed = nominal * WORST_CASE_PRINT_SHRINK
    assert printed >= QR_MIN_MODULE_MM, (
        f"modules are {nominal:.3f}mm nominal but only {printed:.3f}mm after a "
        f"{WORST_CASE_PRINT_SHRINK:g}x print shrink — below the {QR_MIN_MODULE_MM}mm floor"
    )


def test_headroom_is_reported_so_payload_growth_is_visible():
    """A longer payload => more modules => smaller pitch. Fail before paper does.

    ZATCA payload length is not under this app's control (seller name length,
    signature/key sizes). If a future payload pushes the code past this many
    modules, the printed pitch stops clearing the floor and this test says so
    rather than letting it be discovered on a printed invoice.
    """
    max_modules = int(QR_PRINT_PX_V1 / 96 * 25.4 * WORST_CASE_PRINT_SHRINK / QR_MIN_MODULE_MM)
    span = int(_viewbox(_svg_from_uri(qr_vector_data_uri(LONG_PAYLOAD)))[2])
    assert span <= max_modules, (
        f"payload now needs {span} modules; at {QR_PRINT_PX_V1}px only "
        f"{max_modules} fit while holding {QR_MIN_MODULE_MM}mm after print shrink. "
        "Increase QR_PRINT_PX_V1 (the header grid has slack) or shorten the payload."
    )


# ----------------------------------------------------------------------
# Contrast / rendering
# ----------------------------------------------------------------------
def test_modules_are_pure_black_on_a_pure_white_ground():
    svg = _svg_from_uri(qr_vector_data_uri(LONG_PAYLOAD))
    assert 'fill="#ffffff"' in svg, "no opaque white background rect"
    assert 'fill="#000000"' in svg, "modules are not pure black"
    # No greys, no alpha, nothing that a printer would half-tone.
    fills = set(re.findall(r'fill="([^"]+)"', svg))
    assert fills == {"#ffffff", "#000000"}, f"unexpected fills: {fills}"
    assert "opacity" not in svg


def test_module_edges_are_not_antialiased():
    svg = _svg_from_uri(qr_vector_data_uri(LONG_PAYLOAD))
    assert 'shape-rendering="crispEdges"' in svg


def test_white_ground_covers_the_whole_viewbox():
    svg = _svg_from_uri(qr_vector_data_uri(LONG_PAYLOAD))
    span = int(_viewbox(svg)[2])
    assert f'<rect x="0" y="0" width="{span}" height="{span}" fill="#ffffff"/>' in svg


# ----------------------------------------------------------------------
# How the template embeds it
# ----------------------------------------------------------------------
def test_template_one_embeds_the_vector_qr_not_a_png():
    tag = _qr_img_tag(build_invoice_html(_data()))
    assert "data:image/svg+xml;base64," in tag, "النموذج الأول is not using the vector QR"
    assert "data:image/png" not in tag


def test_template_one_does_not_pixelate_the_vector_qr():
    """`pixelated` would make Chromium rasterise the SVG and undo the whole fix."""
    tag = _qr_img_tag(build_invoice_html(_data()))
    assert "image-rendering" not in tag


def test_template_one_sizes_the_qr_square_at_the_v1_constant():
    tag = _qr_img_tag(build_invoice_html(_data()))
    assert f"width: {QR_PRINT_PX_V1}px" in tag
    assert f"height: {QR_PRINT_PX_V1}px" in tag


# ----------------------------------------------------------------------
# Fallbacks
# ----------------------------------------------------------------------
@pytest.mark.parametrize("payload", [None, ""])
def test_missing_payload_falls_back_to_the_placeholder_not_an_empty_qr(payload):
    """An unsigned/draft invoice must show the placeholder, never a QR of "".

    ``_qr_svg_markup("")`` would happily encode the empty string into a valid
    version-1 code, so the guard has to live in the caller — pinning it here
    because a scannable QR carrying nothing is worse than an obvious placeholder.
    """
    assert qr_vector_data_uri(payload) == qr_data_uri(payload)
    assert "svg" not in qr_vector_data_uri(payload)


def test_the_svg_builder_never_emits_a_non_square_grid():
    svg = _qr_svg_markup(LONG_PAYLOAD)
    min_x, min_y, width, height = _viewbox(svg)
    assert width == height


def test_a_long_payload_still_produces_one_svg_under_a_sane_size():
    uri = qr_vector_data_uri(LONG_PAYLOAD)
    # Path-merged runs keep this far below the megabyte range a per-rect SVG hits.
    assert len(uri) < 200_000, f"vector QR data URI is {len(uri)} bytes"
