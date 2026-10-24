"""Deterministic step generation, ordering and serialization (DESIGN.md G1-G6, D-SORT).

INCOMPLETE: a FINDING (cites its proof) and a REMEDIATION ("") per non-compliant record,
            plus a VERIFICATION ("") for every emitted record.
COMPLETE:   VERIFICATION steps only, each citing the evidence that proves the state.
Order: phase, then the operation order of the problem statement's table, then system_id,
then resource_id as a string (untested assumption: no sample has a tie inside one
phase/operation/system).
"""
from __future__ import annotations

import json

from .schema import OPERATION_ORDER, OPERATION_PHASE, OPERATION_STATE, PHASES, Verdict


def build_steps(verdicts: list[Verdict]) -> tuple[str, list[dict], list[Verdict]]:
    """Returns (status, steps, verdicts dropped because a COMPLETE step would need a proof they lack)."""
    emitted = [v for v in verdicts if v.compliant is not None]
    incomplete = any(v.compliant is False for v in emitted)
    dropped = [] if incomplete else [v for v in emitted if not v.provable]
    emitted = emitted if incomplete else [v for v in emitted if v.provable]
    raw: list[dict] = []
    for v in emitted:
        if v.compliant is False:
            raw.append(_step(v, v.finding_op, v.proof_file_id or ""))
            raw.append(_step(v, v.remediation_op, ""))
        raw.append(_step(v, v.verification_op, "" if incomplete else (v.proof_file_id or "")))
    raw.sort(key=sort_key)
    for i, s in enumerate(raw, 1):
        s["step_no"] = i
    return ("INCOMPLETE" if incomplete else "COMPLETE"), raw, dropped


def _step(v: Verdict, operation: str, evidence_id: str) -> dict:
    return {"step_no": 0, "phase": OPERATION_PHASE[operation], "system_id": v.system_id,
            "resource_id": v.resource_id, "operation": operation, "state": OPERATION_STATE[operation],
            "evidence_id": evidence_id}


def sort_key(step: dict) -> tuple:
    return (PHASES.index(step["phase"]), OPERATION_ORDER.index(step["operation"]), step["system_id"],
            step["resource_id"])


def serialize(steps: list[dict]) -> str:
    ordered = [{k: s[k] for k in ("step_no", "phase", "system_id", "resource_id", "operation", "state",
                                  "evidence_id")} for s in steps]
    return json.dumps(ordered, ensure_ascii=False, separators=(",", ":"))
