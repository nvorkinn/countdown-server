"""A screen's frame as a browser can show it: a PNG.

The frame is in the screen's format (device_config.format). mono-1bpp-packed is the e-paper's own buffer (see
routes/frames.py): the panel's landscape picture at the screen's size (800x480 for the 7.5" screens, 1872x1404 for the
HD ones), rows top to bottom, 8 pixels a byte with the leftmost in the high bit, and a set bit is black. png is already
a PNG of the panel's landscape picture. A portrait screen gets a portrait picture that the renderer turned a quarter
anticlockwise to fit its landscape panel: a mono frame is turned back here, as it's encoded anyway; a png frame is
served as it is and turned back by the page (the `frame-portrait` style in base.html).
"""

import struct
import zlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from broker.models import FrameFormat

BLACK, WHITE = b"\x00", b"\xff"

# Each byte of the buffer as its 8 pixels, one 8-bit grey byte each.
_PIXELS = [b"".join(BLACK if byte & (0x80 >> bit) else WHITE for bit in range(8)) for byte in range(256)]


def native_size(width: int, height: int) -> tuple[int, int]:
    """Return the panel's own (landscape) picture size for a screen of `width` by `height`, either way round."""
    return max(width, height), min(width, height)


def preview(frame: bytes, frame_format: FrameFormat, size: tuple[int, int], *, portrait: bool) -> bytes:
    """Return the frame, in `frame_format`, as a PNG: a mono one turned back to portrait for a `portrait` screen.

    `size` is the screen's width and height as it registered, either way round. Raises ValueError for a mono frame
    that isn't that size's buffer.
    """
    if frame_format == "png":
        return frame
    return to_png(frame, native_size(*size), portrait=portrait)


def to_png(frame: bytes, size: tuple[int, int], *, portrait: bool) -> bytes:
    """Return a mono-1bpp-packed frame as an 8-bit greyscale PNG, turned back to portrait for a `portrait` screen.

    `size` is the panel's landscape picture, (width, height). Raises ValueError for a frame that isn't its buffer:
    read at the wrong width, one would only be noise.
    """
    panel_w, panel_h = size
    if len(frame) * 8 != panel_w * panel_h:
        msg = f"a {panel_w}x{panel_h} frame is {panel_w * panel_h // 8} bytes, not {len(frame)}"
        raise ValueError(msg)
    pixels = b"".join(_PIXELS[byte] for byte in frame)
    if portrait:
        # The renderer put portrait pixel (x, y) at landscape (y, panel_h - 1 - x), so portrait row y is landscape
        # column y read bottom to top.
        rows = [pixels[y::panel_w][::-1] for y in range(panel_w)]
        width, height = panel_h, panel_w
    else:
        rows = [pixels[y * panel_w : (y + 1) * panel_w] for y in range(panel_h)]
        width, height = panel_w, panel_h
    # Each row starts with its filter type: 0, none.
    data = zlib.compress(b"".join(b"\x00" + row for row in rows), 9)
    return b"".join(
        [
            b"\x89PNG\r\n\x1a\n",
            _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)),
            _chunk(b"IDAT", data),
            _chunk(b"IEND", b""),
        ]
    )


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
