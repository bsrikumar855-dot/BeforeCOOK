"""REJECTED experiment rc8-P1 (kept for reproducibility, not imported by the pipeline): a capacity constraint.

Hypothesis under test: each case owns at most one resource per system. Here a case "owns" a resource that
has steps in the reference answer. If so, an AMBIGUOUS timing row would be forced onto a candidate case
whenever every other candidate already owns a resource in that system.

Evidence that motivated it: no sample reference has two resources of one system in one case.

Result: the constraint forces no AMBIGUOUS row, so it gains nothing, and no ownership rule is built on it.
Reads the traces written by `python3 code/main.py --from-cache`.

    python3 code/analysis/capacity_probe.py
"""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OWNED = {"CONFIRMED", "INFERRED", "INFERRED_CLAIM"}


def sample_cardinality() -> Counter[int]:
    """Number of resources per (case, system) in the sample reference: {resources: pairs}."""
    per: Counter[int] = Counter()
    with open(ROOT / "dataset" / "sample_output.csv", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            pairs = {(s["system_id"], s["resource_id"]) for s in json.loads(row["steps_json"])}
            per.update(Counter(Counter(system for system, _ in pairs).values()))
    return per


def forced_rows(traces: list[dict]) -> tuple[int, dict[str, str]]:
    """Unit propagation under the capacity rule. Returns (AMBIGUOUS rows seen, {resource: forced case})."""
    occupied: defaultdict[tuple[str, str], set[str]] = defaultdict(set)
    candidates: defaultdict[str, set[str]] = defaultdict(set)
    system_of: dict[str, str] = {}
    for t in traces:
        for a in t["attribution"]:
            system_of[a["resource_id"]] = a["system_id"]
            if a["label"] in OWNED and a["in_scope"]:
                occupied[(t["case_id"], a["system_id"])].add(a["resource_id"])
            elif a["label"] == "AMBIGUOUS":
                candidates[a["resource_id"]].add(t["case_id"])
    forced: dict[str, str] = {}
    progress = True
    while progress:
        progress = False
        for rid, cases in sorted(candidates.items()):
            if rid in forced:
                continue
            free = sorted(c for c in cases if not occupied[(c, system_of[rid])])
            if len(free) == 1:
                forced[rid] = free[0]
                occupied[(free[0], system_of[rid])].add(rid)
                progress = True
    return len(candidates), forced


def main() -> None:
    traces = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((ROOT / "code" / "traces").glob("case_*.json"))]
    per = sample_cardinality()
    print("sample (case, system) pairs by resource count:", dict(sorted(per.items())))
    rows, forced = forced_rows(traces)
    print(f"AMBIGUOUS rows: {rows}; forced by the capacity rule: {len(forced)}")
    for rid, case in sorted(forced.items()):
        print(f"  {rid} -> {case}")


if __name__ == "__main__":
    main()
