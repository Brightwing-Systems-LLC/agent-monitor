"""Write the mascot favicon as SVG and pixel-exact PNGs (stdlib only)."""

import struct
import zlib
from pathlib import Path

CORAL, EYE = (0xD9, 0x77, 0x57, 255), (0x11, 0x11, 0x11, 255)
CLEAR = (0, 0, 0, 0)
# 13x8 sprite, padded to 15x15 (1 col each side, rows centred).
ROWS = [
    "..XXXXXXXXX..",
    "..XEXXXXXEX..",
    "XXXXXXXXXXXXX",
    "XXXXXXXXXXXXX",
    "..XXXXXXXXX..",
    "..XXXXXXXXX..",
    "...X.X.X.X...",
    "...X.X.X.X...",
]
GRID = 15
OFF_X, OFF_Y = 1, 3


def cell(x, y):
    gx, gy = x - OFF_X, y - OFF_Y
    if 0 <= gy < len(ROWS) and 0 <= gx < len(ROWS[0]):
        ch = ROWS[gy][gx]
        return CORAL if ch == "X" else EYE if ch == "E" else CLEAR
    return CLEAR


def png(size: int) -> bytes:
    scale = size / GRID
    raw = b""
    for py in range(size):
        raw += b"\0"
        for px in range(size):
            raw += bytes(cell(int(px / scale), int(py / scale)))

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def svg() -> str:
    rects = []
    for y in range(GRID):
        for x in range(GRID):
            c = cell(x, y)
            if c != CLEAR:
                rects.append(
                    f'<rect x="{x}" y="{y}" width="1" height="1" fill="#{c[0]:02x}{c[1]:02x}{c[2]:02x}"/>'
                )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {GRID} {GRID}" '
        f'shape-rendering="crispEdges">{"".join(rects)}</svg>\n'
    )


out = Path("claude_monitor/static")
(out / "favicon.svg").write_text(svg())
for size, name in [(32, "favicon-32.png"), (180, "apple-touch-icon.png")]:
    (out / name).write_bytes(png(size))
print("wrote", sorted(p.name for p in out.glob("*fav*")) + ["apple-touch-icon.png"])
