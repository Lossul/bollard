"""Image preprocessing shared by the batch pipeline and the webapp.

Two concerns:
- EXIF inspection/stripping (stdlib-only, JPEG marker-level -- no pixel
  decode needed). A model should never have access to ground-truth
  coordinates embedded in the file it's being asked to guess from.
- Bottom-crop (needs Pillow -- removing pixels isn't a metadata operation).
  Some Mapillary images are dash-mounted and show a vehicle dashboard/hood
  at the bottom of frame; a manual spot-check found this in a Toronto
  sub-region capture session but nowhere else sampled across 6 continents.
  Cropped uniformly for every image rather than detected per-image, since
  detection would need its own model call. 25% was reached empirically --
  an initial 20% still left a sliver of hood visible on the worst sampled
  case; verified visually rather than trusted from an eyeballed estimate.
"""

from __future__ import annotations

import io
import struct

from PIL import Image

SOI = b"\xff\xd8"
APP1_MARKER = 0xE1
EXIF_PREFIX = b"Exif\x00\x00"

# Covers the worst dashboard case found in the spot-check, at the cost of
# trimming this much off every image regardless of whether it needs it.
BOTTOM_CROP_FRACTION = 0.25


def iter_jpeg_segments(image_bytes: bytes) -> list[tuple[int, int, bytes]]:
    """Return (marker, offset, data) for each marker segment before the scan data."""
    if image_bytes[0:2] != SOI:
        raise ValueError("not a JPEG (missing SOI)")

    segments = []
    pos = 2
    while pos < len(image_bytes) - 1:
        if image_bytes[pos] != 0xFF:
            break
        marker = image_bytes[pos + 1]
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7 or marker == 0x01:
            pos += 2
            continue
        if marker == 0xDA:  # start of scan -- entropy-coded data follows
            break
        seg_len = struct.unpack(">H", image_bytes[pos + 2 : pos + 4])[0]
        data = image_bytes[pos + 4 : pos + 2 + seg_len]
        segments.append((marker, pos, data))
        pos += 2 + seg_len
    return segments


def _parse_ifd(tiff: bytes, offset: int, endian: str) -> dict[int, tuple[int, int, bytes]]:
    (count,) = struct.unpack_from(endian + "H", tiff, offset)
    entries = {}
    for i in range(count):
        entry_off = offset + 2 + i * 12
        tag, typ, cnt = struct.unpack_from(endian + "HHI", tiff, entry_off)
        entries[tag] = (typ, cnt, tiff[entry_off + 8 : entry_off + 12])
    return entries


def has_exif_gps(image_bytes: bytes) -> bool:
    """True if any EXIF APP1 segment's IFD0 has a GPSInfo tag (0x8825)."""
    for marker, _, data in iter_jpeg_segments(image_bytes):
        if marker != APP1_MARKER or not data.startswith(EXIF_PREFIX):
            continue
        tiff = data[len(EXIF_PREFIX) :]
        if len(tiff) < 8:
            continue
        endian = "<" if tiff[0:2] == b"II" else ">"
        (ifd0_offset,) = struct.unpack_from(endian + "I", tiff, 4)
        try:
            ifd0 = _parse_ifd(tiff, ifd0_offset, endian)
        except struct.error:
            continue
        if 0x8825 in ifd0:
            return True
    return False


def strip_exif(image_bytes: bytes) -> bytes:
    """Return the JPEG with every EXIF APP1 segment removed; other bytes untouched."""
    segments = iter_jpeg_segments(image_bytes)
    exif_spans = [
        (offset, offset + 4 + len(data))
        for marker, offset, data in segments
        if marker == APP1_MARKER and data.startswith(EXIF_PREFIX)
    ]
    if not exif_spans:
        return image_bytes

    out = bytearray()
    cursor = 0
    for start, end in exif_spans:
        out += image_bytes[cursor:start]
        cursor = end
    out += image_bytes[cursor:]
    return bytes(out)


def crop_bottom(image_bytes: bytes, fraction: float = BOTTOM_CROP_FRACTION) -> bytes:
    """Crop the bottom `fraction` of the image off, re-encoding in its original format."""
    with Image.open(io.BytesIO(image_bytes)) as img:
        width, height = img.size
        keep_height = round(height * (1 - fraction))
        cropped = img.crop((0, 0, width, keep_height))

        out = io.BytesIO()
        cropped.save(out, format=img.format or "JPEG")
        return out.getvalue()
