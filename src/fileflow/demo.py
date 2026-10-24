"""Original deterministic fixtures. Never seed into an existing directory."""

import struct
import zipfile
import zlib
from pathlib import Path


def create_demo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=False)
    (root / "meeting_notes.txt").write_text(
        "FileFlow demo\nAgenda: review the preview before applying.\n", encoding="utf-8"
    )
    (root / "meeting_notes_copy.txt").write_bytes((root / "meeting_notes.txt").read_bytes())
    (root / "expenses.csv").write_text(
        "category,amount\nBooks,24.50\nCoffee,3.20\n", encoding="utf-8"
    )
    (root / "draft.md").write_text("# Project notes\nSynthetic sample content.\n", encoding="utf-8")
    (root / "helper.py").write_text('print("A synthetic demo file")\n', encoding="utf-8")

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    width, height = 48, 32
    pixels = b"".join(b"\0" + bytes([40, 160, 160]) * width for _ in range(height))
    (root / "screenshot.png").write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(pixels))
        + chunk(b"IEND", b"")
    )
    with zipfile.ZipFile(root / "assets.zip", "w") as archive:
        archive.writestr("readme.txt", "Original synthetic FileFlow archive.\n")
    (root / "download.crdownload").write_text("Incomplete download; leave this file unchanged.\n")
    return root
