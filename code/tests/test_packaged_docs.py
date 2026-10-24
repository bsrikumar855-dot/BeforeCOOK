"""The documents that ship inside code.zip are byte-identical to the repository copies."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_shipped_docs_match_repo_docs():
    for name in ("DESIGN.md", "CONSTANTS.md"):
        assert (ROOT / "code" / name).read_bytes() == (ROOT / name).read_bytes(), f"code/{name} is stale: copy {name}"
    for name in ("evaluation_report.md", "usage_report.md"):
        assert (ROOT / "evaluation" / name).read_bytes() == (ROOT / "code" / "evaluation" / name).read_bytes(), name
