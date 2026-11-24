"""Write the favicon as SVG and pixel-exact PNGs (stdlib only)."""

import struct
import zlib
from pathlib import Path

PALETTE = {
    "B": (0x4B, 0x55, 0x63, 255),  # bezel
    "S": (0x0F, 0x17, 0x2A, 255),  # screen
    "G": (0x4A, 0xDE, 0x80, 255),  # heartbeat trace
    "r": (0xD9, 0x77, 0x57, 255),  # status LEDs, one per agent
    "b": (0x3B, 0x82, 0xF6, 255),
    "p": (0xA7, 0x8B, 0xFA, 255),
}
CLEAR = (0, 0, 0, 0)
# A monitor showing a heartbeat, three agent LEDs on the bezel.
ROWS = [
    "................",
    ".BBBBBBBBBBBBBB.",
    "BSSSSSGSSSSSSSSB",
    "BSSSSSGSSSSSSSSB",
    "BSSSSGSGSSSSSSSB",
    "BSSSSGSGSSSSSSSB",
    "BGGGGSSGSSSGGGGB",
    "BSSSSSSSGSGSSSSB",
    "BSSSSSSSGSGSSSSB",
    "BSSSSSSSSGSSSSSB",
    "BBrBbBpBBBBBBBBB",
    ".BBBBBBBBBBBBBB.",
    "......BBBB......",
    "......BBBB......",
    "....BBBBBBBB....",
    "................",
]
GRID = 16


def cell(x, y):
    return PALETTE.get(ROWS[y][x], CLEAR)


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


out = Path("agent_monitor/static")
(out / "favicon.svg").write_text(svg())
for size, name in [(32, "favicon-32.png"), (180, "apple-touch-icon.png")]:
    (out / name).write_bytes(png(size))
print("wrote", sorted(p.name for p in out.glob("*fav*")) + ["apple-touch-icon.png"])
