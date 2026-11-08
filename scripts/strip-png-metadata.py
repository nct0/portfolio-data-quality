"""Remove metadata chunks from PNG files in place, keeping only what is needed to draw the image.

Usage: python scripts/strip-png-metadata.py FILE.png [FILE.png ...]

Text, time, EXIF, color-profile and physical-size chunks can carry tool names, user names or dates; the image does
not need them.
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

SIGNATURE = b"\x89PNG\r\n\x1a\n"
KEEP = {b"IHDR", b"PLTE", b"tRNS", b"IDAT", b"IEND"}


def chunks(data: bytes):
    if not data.startswith(SIGNATURE):
        raise ValueError("not a PNG file")
    pos = len(SIGNATURE)
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        (crc,) = struct.unpack(">I", data[pos + 8 + length:pos + 12 + length])
        if zlib.crc32(kind + body) != crc:
            raise ValueError(f"bad CRC in {kind!r} chunk")
        yield kind, data[pos:pos + 12 + length]
        pos += 12 + length


def strip(path: Path) -> list[str]:
    data = path.read_bytes()
    kept, removed = [SIGNATURE], []
    for kind, raw in chunks(data):
        if kind in KEEP:
            kept.append(raw)
        else:
            removed.append(kind.decode("ascii"))
    path.write_bytes(b"".join(kept))
    return removed


def main(paths: list[str]) -> int:
    for name in paths:
        path = Path(name)
        removed = strip(path)
        remaining = sorted({kind.decode("ascii") for kind, _ in chunks(path.read_bytes())})
        print(f"  {path.name}: removed {', '.join(removed) or 'nothing'}; chunks now {', '.join(remaining)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
