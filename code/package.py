"""Build code.zip: the stdlib equivalent of the README's packaging command, for machines without `zip`.

    zip -r code.zip README.md requirements.txt code evaluation -x "*/__pycache__/*" "*/.venv/*" "*/venv/*" "*/node_modules/*" "*/.git/*" "*/.DS_Store"

Same inputs (README.md, requirements.txt, code/, evaluation/) and the same exclusions, plus the release rules: no dataset/, no .env, no
nested archives, no temporary or degraded outputs. It refuses to build while code/DESIGN.md or code/CONSTANTS.md differ
from the root originals (code/sync_docs.py). Entries are written in sorted order with a fixed timestamp, so the
archive is byte-identical for identical inputs. Prints the archive's sha256.

    python3 code/package.py [--out code.zip]
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
INCLUDE = ["README.md", "requirements.txt", "code", "evaluation"]
EXCLUDE_DIRS = {"__pycache__", ".venv", "venv", "node_modules", ".git", ".pytest_cache", "dataset"}
EXCLUDE_NAMES = {".DS_Store", ".env"}
EXCLUDE_SUFFIXES = {".zip", ".pyc", ".tmp"}
FIXED_TIME = (2026, 1, 1, 0, 0, 0)


def members() -> list[Path]:
    out = []
    for item in INCLUDE:
        p = REPO / item
        paths = [p] if p.is_file() else sorted(x for x in p.rglob("*") if x.is_file())
        for f in paths:
            rel = f.relative_to(REPO)
            if set(rel.parts) & EXCLUDE_DIRS or f.name in EXCLUDE_NAMES or f.suffix in EXCLUDE_SUFFIXES \
                    or f.name.endswith((".degraded.csv", ".csv.tmp")):
                continue
            out.append(f)
    return sorted(out, key=lambda f: f.relative_to(REPO).as_posix())


def build(out: Path) -> str:
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in members():
            info = zipfile.ZipInfo(f.relative_to(REPO).as_posix(), FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, f.read_bytes())
    return hashlib.sha256(out.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description="Build code.zip (README packaging command, stdlib only)")
    ap.add_argument("--out", type=Path, default=REPO / "code.zip")
    args = ap.parse_args()
    sys.path.insert(0, str(REPO / "code"))
    from sync_docs import diverged
    stale = diverged()
    if stale:
        print(f"refusing to package: {', '.join(stale)} differ from the root originals (run python3 code/sync_docs.py)")
        return 1
    digest = build(args.out)
    print(f"{args.out} {len(members())} files sha256={digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
