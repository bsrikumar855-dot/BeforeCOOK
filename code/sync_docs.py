"""Keep the shipped copies of DESIGN.md and CONSTANTS.md identical to the repository-root originals.

The root files are authoritative (README.md links them and every change record edits them); `code/DESIGN.md` and
`code/CONSTANTS.md` exist only so that code.zip, which ships `code/` but not the repository root, carries them.

    python3 code/sync_docs.py            copy root -> code/ (refuses to overwrite a code/ copy that was edited directly)
    python3 code/sync_docs.py --check    exit 1 if any copy differs from its original, change nothing

`code/docs_sync.json` records the sha256 of the content last written to each copy. A copy is overwritten only when
it still has that hash, so an edit made to the copy instead of the original is reported (exit 2) rather than
silently lost. A copy with no recorded hash is overwritten only if it already equals its original.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PAIRS = [("DESIGN.md", "code/DESIGN.md"), ("CONSTANTS.md", "code/CONSTANTS.md")]
MANIFEST = "code/docs_sync.json"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def diverged(repo: Path = REPO) -> list[str]:
    return [copy for src, copy in PAIRS if (repo / src).read_bytes() != (repo / copy).read_bytes()]


def sync(repo: Path = REPO) -> tuple[list[str], list[str]]:
    """Return (copies written, copies refused); the manifest is rewritten for every copy left equal to its original."""
    mpath = repo / MANIFEST
    recorded = json.loads(mpath.read_text(encoding="utf-8")) if mpath.exists() else {}
    written, refused, manifest = [], [], {}
    for src, copy in PAIRS:
        original, current = (repo / src).read_bytes(), (repo / copy).read_bytes()
        if original != current:
            if recorded.get(copy) != sha(current):
                refused.append(copy)
                if copy in recorded:
                    manifest[copy] = recorded[copy]
                continue
            (repo / copy).write_bytes(original)
            written.append(copy)
        manifest[copy] = sha(original)
    text = json.dumps(manifest, indent=1, sort_keys=True) + "\n"
    if not mpath.exists() or mpath.read_text(encoding="utf-8") != text:
        mpath.write_text(text, encoding="utf-8", newline="\n")
    return written, refused


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sync code/DESIGN.md and code/CONSTANTS.md from the repository root")
    ap.add_argument("--check", action="store_true", help="report divergence only; exit 1 if any copy differs")
    args = ap.parse_args(argv)
    if args.check:
        bad = diverged()
        print("in sync" if not bad else f"diverged: {', '.join(bad)} (run python3 code/sync_docs.py)")
        return 1 if bad else 0
    written, refused = sync()
    for c in written:
        print(f"wrote {c}")
    for c in refused:
        print(f"REFUSED {c}: it differs from the last synced content; move that edit into the root original first")
    if not written and not refused:
        print("in sync")
    return 2 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
