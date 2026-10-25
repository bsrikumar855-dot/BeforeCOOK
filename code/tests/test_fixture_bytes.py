"""The committed custom-case fixtures are exactly what build_fixtures.py writes, on any platform and from either a
LF or a CRLF copy of dataset/deletion_rules.csv (an evaluation run must not rewrite committed files)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "code" / "evaluation" / "custom_cases"
sys.path.insert(0, str(FIXTURES))
import build_fixtures  # noqa: E402


def _files(d: Path) -> dict[str, bytes]:
    return {p.relative_to(d).as_posix(): p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()}


def test_fresh_build_equals_committed_fixtures(tmp_path):
    build_fixtures.build(tmp_path)
    for name in build_fixtures.CASES:
        assert _files(tmp_path / name) == _files(FIXTURES / name), name


def test_crlf_rules_source_gives_the_same_bytes(tmp_path, monkeypatch):
    crlf = tmp_path / "deletion_rules.csv"
    crlf.write_bytes(build_fixtures.RULES_SRC.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    monkeypatch.setattr(build_fixtures, "RULES_SRC", crlf)
    out = tmp_path / "out"
    build_fixtures.build(out)
    for name in build_fixtures.CASES:
        rules = (out / name / "deletion_rules.csv").read_bytes()
        assert b"\r" not in rules and rules == (FIXTURES / name / "deletion_rules.csv").read_bytes(), name
