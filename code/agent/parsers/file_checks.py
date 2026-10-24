"""Evidence-file admission checks (G8). A file that fails one is rejected before parsing: its observations are
dropped, the event is counted as DEGRADE, and the run exits 4 (DESIGN.md "Failure semantics").

  REJECTED_PATH  manifest path is absolute or escapes the dataset directory
  MISSING        manifest names a file that does not exist
  WRONG_TYPE     content signature (magic bytes) contradicts the extension
  CORRUPT        PNG chunk CRC / IEND or PDF %%EOF check fails
  TRUNCATED      SQL snapshot opens a transaction that it never commits
"""
from __future__ import annotations

import re
import struct
import zlib
from pathlib import Path, PurePosixPath, PureWindowsPath

PNG_SIG = b"\x89PNG\r\n\x1a\n"
SIGNATURES = {"png": PNG_SIG, "pdf": b"%PDF"}
TEXT_FORMATS = {"json", "log", "sql", "txt", "csv"}
DEGRADE_STATUSES = {"REJECTED_PATH", "MISSING", "WRONG_TYPE", "CORRUPT", "TRUNCATED", "PARSE_ERROR"}


def resolve_inside(root: Path, rel: str) -> Path | None:
    """The file's path if it stays inside root; None for absolute paths, drive letters or '..' escapes."""
    if not rel or PurePosixPath(rel).is_absolute() or PureWindowsPath(rel).is_absolute() or PureWindowsPath(rel).drive:
        return None
    base = root.resolve()
    path = (base / rel).resolve()
    return path if path == base or base in path.parents else None


def _png_ok(raw: bytes) -> str | None:
    pos = len(PNG_SIG)
    seen_iend = False
    while pos + 8 <= len(raw):
        length, ctype = struct.unpack(">I4s", raw[pos:pos + 8])
        end = pos + 8 + length + 4
        if end > len(raw):
            return f"chunk {ctype!r} runs past the end of the file"
        data = raw[pos + 8:pos + 8 + length]
        crc = struct.unpack(">I", raw[pos + 8 + length:end])[0]
        if zlib.crc32(ctype + data) & 0xFFFFFFFF != crc:
            return f"CRC mismatch in chunk {ctype!r}"
        pos = end
        if ctype == b"IEND":
            seen_iend = True
            break
    return None if seen_iend else "no IEND chunk"


def check(raw: bytes, fmt: str) -> tuple[str, str] | None:
    """(status, reason) when the content must be rejected, else None."""
    for other, sig in SIGNATURES.items():
        if other != fmt and raw.startswith(sig):
            return "WRONG_TYPE", f"content is {other.upper()} but the extension is .{fmt}"
    if fmt in SIGNATURES and not raw.startswith(SIGNATURES[fmt]):
        return "WRONG_TYPE", f"missing the .{fmt} signature"
    if fmt == "png":
        bad = _png_ok(raw)
        if bad:
            return "CORRUPT", bad
    if fmt == "pdf" and b"%%EOF" not in raw[-1024:]:
        return "CORRUPT", "no %%EOF trailer"
    if fmt in TEXT_FORMATS and b"\x00" in raw[:4096]:
        return "WRONG_TYPE", "binary content in a text format"
    if fmt == "sql":
        text = raw.decode("utf-8", errors="replace")
        if re.search(r"^\s*BEGIN(\s+TRANSACTION)?\s*;", text, re.M | re.I):
            tail = re.sub(r"(--[^\n]*\n?|\s)+$", "", text)
            if not re.search(r"COMMIT\s*;$", tail, re.I):
                return "TRUNCATED", "transaction opened but never committed (truncated export)"
    return None
