"""C8: add the `usage_metadata` block to model-reply cache entries written before it existed.

Provider, model, key alias and token counts were always stored (`call_usage`), so they are copied. The call time was
not stored and the early ledgers were overwritten, so `timestamp` is UNKNOWN; the time of the git commit that first
added the entry is recorded separately as `timestamp_upper_bound` (the call happened no later than that).
Idempotent; never touches `response`, so `--from-cache` replays are unchanged. OCR entries are not model calls.

Run:  python code/analysis/backfill_usage_metadata.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))
from agent.llm_client import usage_metadata  # noqa: E402


def first_commit_time(path: Path) -> str | None:
    out = subprocess.run(["git", "log", "--diff-filter=A", "--follow", "--format=%cI", "--", str(path)],
                         capture_output=True, text=True, cwd=ROOT).stdout.split()
    return out[-1] if out else None


def main() -> int:
    changed = unknown = 0
    for p in sorted((ROOT / "code" / "cache").rglob("*.json")):
        if p.parent.name == "ocr":
            continue
        e = json.loads(p.read_text(encoding="utf-8"))
        if "usage_metadata" in e:
            continue
        meta = usage_metadata(e)
        if meta["timestamp"] == "UNKNOWN":
            unknown += 1
            meta["timestamp_upper_bound"] = first_commit_time(p) or "UNKNOWN (not committed yet)"
        e["usage_metadata"] = meta
        p.write_text(json.dumps(e, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8",
                     newline="\n")
        changed += 1
    print(f"backfilled {changed} entries; timestamp UNKNOWN in {unknown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
