"""G8: evidence admission checks and linear-time regexes (the 1 MB battery and the end-to-end hostile fixtures run
in code/evaluation/security_audit.py)."""
import struct
import time
import zlib
from pathlib import Path

from agent.parsers.file_checks import check, resolve_inside

ROOT = Path(__file__).resolve().parents[2]


def _png(extra: bytes = b"") -> bytes:
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) + chunk(b"IEND", b"") + extra


def test_path_traversal_and_absolute_paths_are_rejected(tmp_path):
    (tmp_path / "evidence").mkdir()
    (tmp_path / "evidence" / "a.log").write_text("x")
    assert resolve_inside(tmp_path, "evidence/a.log") is not None
    for bad in ("../../etc/passwd", "evidence/../../x", "/etc/passwd", "C:/Windows/win.ini", "C:\\Windows\\win.ini", ""):
        assert resolve_inside(tmp_path, bad) is None, bad


def test_wrong_type_corrupt_and_truncated_files_are_rejected():
    assert check(_png(), "png") is None
    assert check(_png(), "json")[0] == "WRONG_TYPE"                     # PNG named .json
    assert check(b'{"a": 1}', "png")[0] == "WRONG_TYPE"                 # JSON named .png
    bad = bytearray(_png())
    bad[20] ^= 0xFF
    assert check(bytes(bad), "png")[0] == "CORRUPT"
    assert check(_png()[:-12], "png")[0] == "CORRUPT"                   # no IEND
    assert check(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n", "pdf")[0] == "CORRUPT"
    assert check(b"%PDF-1.4\n%%EOF\n", "pdf") is None
    assert check(b"BEGIN TRANSACTION;\nCREATE TABLE t(a);\nINSERT INTO t VALUES(1);\n", "sql")[0] == "TRUNCATED"
    assert check(b"BEGIN TRANSACTION;\nCREATE TABLE t(a);\nCOMMIT;\n-- done\n", "sql") is None


def test_dataset_files_all_pass_the_admission_checks():
    for p in sorted((ROOT / "dataset" / "evidence").rglob("*")):
        if p.is_file():
            assert check(p.read_bytes(), p.suffix.lstrip(".").lower()) is None, p.name


def _timed(fn, arg) -> float:
    t = time.perf_counter()
    fn(arg)
    return time.perf_counter() - t


def test_previously_quadratic_regexes_are_linear():
    from agent.identity import EMAIL_RE
    from agent.parsers import ocr_grounding, pdf_text
    from agent.parsers.log_lines import KV_RE
    from agent.parsers.png_vlm import TOKEN_RE
    n = 200_000                      # quadratic versions took > 30 s at 1 MB and seconds at this size
    cases = [(EMAIL_RE.findall, "a" * n), (EMAIL_RE.findall, "a.b@c." * (n // 6)), (KV_RE.findall, "a" * n),
             (ocr_grounding.TOKEN_RE.findall, "ab " * (n // 3)), (TOKEN_RE.findall, "ab_cd_" * (n // 6)),
             (pdf_text._objects, b"1" * n), (pdf_text._objects, b"1 0 obj " * (n // 8)),
             (pdf_text._cmap, b"beginbfchar " * (n // 12))]
    for fn, arg in cases:
        assert _timed(fn, arg) < 1.0, (fn, arg[:20])


def test_bounded_email_still_matches_real_addresses_only():
    from agent.identity import EMAIL_RE
    assert EMAIL_RE.findall("contact s.yoon04@gmail.com now") == ["s.yoon04@gmail.com"]
    assert EMAIL_RE.findall("x" * 70 + "@example.com") == []           # over RFC 5321's 64-char local part
