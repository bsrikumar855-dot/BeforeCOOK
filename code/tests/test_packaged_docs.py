"""The documents that ship inside code.zip are byte-identical to the repository copies."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_shipped_docs_match_repo_docs():
    for name in ("DESIGN.md", "CONSTANTS.md"):
        assert (ROOT / "code" / name).read_bytes() == (ROOT / name).read_bytes(), f"code/{name} is stale: copy {name}"
    for name in ("evaluation_report.md", "usage_report.md"):
        assert (ROOT / "evaluation" / name).read_bytes() == (ROOT / "code" / "evaluation" / name).read_bytes(), name


def test_manifest_records_the_shipped_copies():
    import hashlib
    import json
    recorded = json.loads((ROOT / "code" / "docs_sync.json").read_text(encoding="utf-8"))
    for name in ("DESIGN.md", "CONSTANTS.md"):
        assert recorded[f"code/{name}"] == hashlib.sha256((ROOT / "code" / name).read_bytes()).hexdigest(), name


def _repo(tmp_path, original: bytes, copy: bytes) -> Path:
    (tmp_path / "code").mkdir(parents=True)
    for src, dst in (("DESIGN.md", "code/DESIGN.md"), ("CONSTANTS.md", "code/CONSTANTS.md")):
        (tmp_path / src).write_bytes(original)
        (tmp_path / dst).write_bytes(copy)
    return tmp_path


def test_sync_overwrites_a_copy_untouched_since_the_last_sync(tmp_path):
    import sync_docs
    repo = _repo(tmp_path, b"v1\n", b"v1\n")
    assert sync_docs.sync(repo) == ([], [])                      # records v1
    for src in ("DESIGN.md", "CONSTANTS.md"):
        (repo / src).write_bytes(b"v2\n")
    assert sync_docs.diverged(repo) == ["code/DESIGN.md", "code/CONSTANTS.md"]
    assert sync_docs.sync(repo) == (["code/DESIGN.md", "code/CONSTANTS.md"], [])
    (repo / "DESIGN.md").write_bytes(b"v3\n")                   # a second sync before any commit is fine
    assert sync_docs.sync(repo) == (["code/DESIGN.md"], [])
    assert sync_docs.diverged(repo) == []


def test_sync_refuses_a_copy_edited_directly(tmp_path):
    import sync_docs
    repo = _repo(tmp_path, b"v1\n", b"v1\n")
    sync_docs.sync(repo)
    (repo / "code" / "DESIGN.md").write_bytes(b"edited in code/\n")
    assert sync_docs.sync(repo) == ([], ["code/DESIGN.md"])
    assert (repo / "code" / "DESIGN.md").read_bytes() == b"edited in code/\n"
    other = _repo(tmp_path / "x", b"root\n", b"unknown\n")    # no manifest: a differing copy is never overwritten
    assert sync_docs.sync(other) == ([], ["code/DESIGN.md", "code/CONSTANTS.md"])
