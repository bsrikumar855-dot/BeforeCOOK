"""Output validators. Every row is checked before it is written; the evaluator reuses them.

Catches: malformed JSON, wrong keys, unknown enums, phase/operation/state mismatches,
non-consecutive step_no, out-of-order phases or operations, duplicate steps, invalid
evidence references, evidence captured after audit_at, evidence_id where the contract
requires "" (and vice versa), and NOT_APPLICABLE with steps.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from .loaders import parse_ts
from .schema import OPERATION_PHASE, OPERATION_STATE, STATUSES, STEP_KEYS
from .steps import sort_key

HEADER = ["case_id", "deletion_status", "steps_json"]


class EvidenceCatalog:
    """Every citable id with its capture/event time: manifest file_ids, activity_ids, audit_ids."""

    def __init__(self, manifest: list[dict], activity: list, audit_log: list[dict]):
        self.time = {r["file_id"]: r["captured_at"] for r in manifest}
        self.time.update({a.activity_id: a.event_time for a in activity})
        self.time.update({a["audit_id"]: a["event_time"] for a in audit_log})


def validate_row(status: str, steps_json: str, audit_at: str, catalog: EvidenceCatalog) -> list[str]:
    errors: list[str] = []
    if status not in STATUSES:
        return [f"unknown deletion_status {status!r}"]
    try:
        steps = json.loads(steps_json)
    except json.JSONDecodeError as exc:
        return [f"malformed JSON: {exc}"]
    if not isinstance(steps, list):
        return ["steps_json is not a JSON array"]
    if status == "NOT_APPLICABLE":
        return [] if steps == [] else ["NOT_APPLICABLE must have []"]
    seen = set()
    for i, s in enumerate(steps, 1):
        if not isinstance(s, dict) or set(s) != set(STEP_KEYS):
            errors.append(f"step {i}: keys {sorted(s) if isinstance(s, dict) else type(s).__name__}")
            continue
        if s["step_no"] != i:
            errors.append(f"step {i}: step_no {s['step_no']} not consecutive")
        op = s["operation"]
        if op not in OPERATION_PHASE:
            errors.append(f"step {i}: unknown operation {op}")
            continue
        if s["phase"] != OPERATION_PHASE[op]:
            errors.append(f"step {i}: operation {op} not allowed in phase {s['phase']}")
        if s["state"] != OPERATION_STATE[op]:
            errors.append(f"step {i}: state {s['state']} does not match {op}")
        key = (s["phase"], s["system_id"], s["resource_id"], op)
        if key in seen:
            errors.append(f"step {i}: duplicate step {key}")
        seen.add(key)
        ev = s["evidence_id"]
        must_cite = s["phase"] == "FINDING" or status == "COMPLETE"
        if must_cite and not ev:
            errors.append(f"step {i}: evidence_id required")
        if not must_cite and ev:
            errors.append(f"step {i}: evidence_id must be empty for future work")
        if ev:
            if ev not in catalog.time:
                errors.append(f"step {i}: unknown evidence id {ev}")
            elif parse_ts(catalog.time[ev]) > parse_ts(audit_at):
                errors.append(f"step {i}: evidence {ev} captured after audit_at")
        if status == "COMPLETE" and s["phase"] != "VERIFICATION":
            errors.append(f"step {i}: COMPLETE allows only VERIFICATION steps")
    valid = [s for s in steps if isinstance(s, dict) and set(s) == set(STEP_KEYS) and s["operation"] in OPERATION_PHASE]
    if valid and [sort_key(s) for s in valid] != sorted(sort_key(s) for s in valid):
        errors.append("steps out of order (phase, operation, system_id, resource_id)")
    if status == "INCOMPLETE" and steps and not any(s.get("phase") == "FINDING" for s in valid):
        errors.append("INCOMPLETE with steps but no FINDING")
    return errors


def validate_output_file(path: Path, expected_case_ids: list[str]) -> list[str]:
    errors: list[str] = []
    with open(path, encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        rows = list(reader)
    if header != HEADER:
        errors.append(f"header {header} != {HEADER}")
    ids = [r[0] for r in rows]
    if len(rows) != len(expected_case_ids):
        errors.append(f"{len(rows)} rows, expected {len(expected_case_ids)}")
    if ids != sorted(ids, key=case_sort_key):
        errors.append("rows not sorted by case_id")
    if set(ids) != set(expected_case_ids):
        errors.append("case_id set differs from deletion_requests.csv")
    if len(set(ids)) != len(ids):
        errors.append("duplicate case_id rows")
    return errors


def case_sort_key(case_id: str) -> tuple:
    """case_33 < case_100: sort by the numeric suffix, then the raw string."""
    head, _, tail = case_id.rpartition("_")
    return (head, int(tail) if tail.isdigit() else float("inf"), case_id)
