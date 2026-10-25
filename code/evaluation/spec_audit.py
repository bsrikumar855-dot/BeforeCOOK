"""G5: literal spec audit of output.csv (skill: orchestrate-submit/references/spec-auditor.md).

Every constant below is TYPED from problem_statement.md ("Required Output", "Allowed Operations", "Rules"); nothing
is imported from code/agent, so this is an independent witness, not the code agreeing with itself. Inputs are the
participant files in dataset/ and the run's traces (data, read only for the NO_ATTRIBUTABLE_RESOURCE reason code).

  python code/evaluation/spec_audit.py              audit output.csv, then run the negative control
  exit 0 = no violation and every injected defect caught; 1 otherwise
"""
from __future__ import annotations

import copy
import csv
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# ---- typed from problem_statement.md --------------------------------------------------------------------------
HEADER = ["case_id", "deletion_status", "steps_json"]                       # "Required Output"
ROW_COUNT = 518                                                              # "exactly 518 data rows"
STATUSES = {"COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"}
STEP_KEYS = {"step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"}
PHASE_ORDER = ["FINDING", "REMEDIATION", "VERIFICATION"]                    # "Order steps by phase"
TABLE = [                                                                     # "Allowed Operations", in table order
    ("FINDING", "REPORT_REMAINING_RECORD", "PRESENT"), ("FINDING", "REPORT_EXPOSED_PII", "PRESENT"),
    ("FINDING", "REPORT_ACTIVE_DATA_FLOW", "ACTIVE"), ("FINDING", "REPORT_RESTORE_RISK", "ACTIVE"),
    ("REMEDIATION", "DISABLE_DATA_FLOW", "DISABLED"), ("REMEDIATION", "BLOCK_RESTORE", "BLOCKED"),
    ("REMEDIATION", "DELETE_RECORD", "ABSENT"), ("REMEDIATION", "REDACT_RECORD", "REDACTED"),
    ("REMEDIATION", "ANONYMIZE_RECORD", "ANONYMIZED"),
    ("VERIFICATION", "VERIFY_RECORD_ABSENT", "ABSENT"), ("VERIFICATION", "VERIFY_RECORD_PRESENT", "PRESENT"),
    ("VERIFICATION", "VERIFY_RECORD_REDACTED", "REDACTED"), ("VERIFICATION", "VERIFY_RECORD_ANONYMIZED", "ANONYMIZED"),
    ("VERIFICATION", "VERIFY_DATA_FLOW_DISABLED", "DISABLED"), ("VERIFICATION", "VERIFY_RESTORE_BLOCKED", "BLOCKED"),
]
TRIPLES = set(TABLE)
OP_RANK = {op: i for i, (_, op, _) in enumerate(TABLE)}
EMPTY_ALLOWED_REASON = "NO_ATTRIBUTABLE_RESOURCE"      # our documented policy (DESIGN.md C5), not spec text


def case_number(case_id: str) -> tuple:
    """ "sorted by case_id" read as the numeric suffix (case_99 < case_100), the order deletion_requests.csv itself
    uses (DESIGN.md D-ROWSORT). Typed here, not imported from agent.validation."""
    head, _, tail = case_id.rpartition("_")
    return (head, int(tail)) if tail.isdigit() else (case_id, -1)


def ts(v: str) -> datetime:
    return datetime.fromisoformat(v.strip().replace("Z", "+00:00"))


def load_context(dataset: Path, traces: Path) -> dict:
    req = list(csv.DictReader(open(dataset / "deletion_requests.csv", encoding="utf-8")))
    times = {r["file_id"]: r["captured_at"] for r in csv.DictReader(open(dataset / "evidence_files.csv", encoding="utf-8"))}
    times.update({r["activity_id"]: r["event_time"] for r in csv.DictReader(open(dataset / "system_activity.csv", encoding="utf-8"))})
    times.update({r["audit_id"]: r["event_time"] for r in csv.DictReader(open(dataset / "user_audit_log.csv", encoding="utf-8"))})
    reasons = {}
    for p in traces.glob("case_*.json"):
        reasons[p.stem] = json.loads(p.read_text(encoding="utf-8")).get("reason_code")
    return {"ids": [r["case_id"] for r in req], "audit_at": {r["case_id"]: r["audit_at"] for r in req},
            "times": times, "reasons": reasons}


def audit(header: list[str], rows: list[list[str]], ctx: dict) -> list[tuple[str, str, str]]:
    """Return (defect_class, case_id, detail) for every violation."""
    v: list[tuple[str, str, str]] = []
    if header != HEADER:
        v.append(("header", "-", f"{header}"))
    ids = [r[0] if r else "" for r in rows]
    if len(rows) != ROW_COUNT:
        v.append(("row_count", "-", f"{len(rows)} rows"))
    if set(ids) != set(ctx["ids"]) or len(set(ids)) != len(ids):
        v.append(("id_set", "-", f"missing {sorted(set(ctx['ids']) - set(ids))[:5]} extra {sorted(set(ids) - set(ctx['ids']))[:5]}"))
    elif ids != ctx["ids"]:
        v.append(("row_order", "-", "rows are not in deletion_requests.csv (case_id) order"))
    if ids != sorted(ids, key=case_number):
        v.append(("case_id_sort", "-", "rows are not sorted by case_id's numeric suffix"))
    for r in rows:
        if len(r) != 3:
            v.append(("row_shape", r[0] if r else "?", f"{len(r)} fields"))
            continue
        cid, status, raw = r
        if status not in STATUSES:
            v.append(("status", cid, status))
            continue
        try:
            steps = json.loads(raw)
        except json.JSONDecodeError as exc:
            v.append(("json", cid, str(exc)))
            continue
        if not isinstance(steps, list):
            v.append(("json", cid, "not an array"))
            continue
        if status == "NOT_APPLICABLE":
            if steps != []:
                v.append(("not_applicable_steps", cid, f"{len(steps)} steps"))
            continue
        if status == "INCOMPLETE" and not steps and ctx["reasons"].get(cid) != EMPTY_ALLOWED_REASON:
            v.append(("incomplete_empty", cid, f"reason_code={ctx['reasons'].get(cid)}"))
        if status == "COMPLETE" and not steps:
            v.append(("complete_empty", cid, "COMPLETE with no verification step"))
        audit_at = ts(ctx["audit_at"].get(cid, "1970-01-01T00:00:00Z"))
        keys_ok = []
        for i, s in enumerate(steps, 1):
            if not isinstance(s, dict) or set(s) != STEP_KEYS:
                v.append(("step_keys", cid, f"step {i}: {sorted(s) if isinstance(s, dict) else s}"))
                continue
            keys_ok.append(s)
            if s["step_no"] != i:
                v.append(("step_no", cid, f"position {i} has step_no {s['step_no']}"))
            if (s["phase"], s["operation"], s["state"]) not in TRIPLES:
                v.append(("triple", cid, f"step {i}: {(s['phase'], s['operation'], s['state'])}"))
                continue
            ev = s["evidence_id"]
            if status == "COMPLETE" and s["phase"] != "VERIFICATION":
                v.append(("complete_phase", cid, f"step {i}: {s['phase']}"))
            cite_required = s["phase"] == "FINDING" or status == "COMPLETE"
            if cite_required and not ev:
                v.append(("citation_missing", cid, f"step {i}"))
            if status == "INCOMPLETE" and s["phase"] in ("REMEDIATION", "VERIFICATION") and ev != "":
                v.append(("future_step_cited", cid, f"step {i}: {ev}"))
            if ev:
                if ev not in ctx["times"]:
                    v.append(("citation_unknown", cid, f"step {i}: {ev}"))
                elif ts(ctx["times"][ev]) > audit_at:
                    v.append(("citation_after_audit", cid, f"step {i}: {ev} at {ctx['times'][ev]}"))
        good = [s for s in keys_ok if (s["phase"], s["operation"], s["state"]) in TRIPLES]
        order = [(PHASE_ORDER.index(s["phase"]), OP_RANK[s["operation"]], s["system_id"], s["resource_id"]) for s in good]
        if order != sorted(order):
            v.append(("ordering", cid, "not phase → operation table → system_id → resource_id"))
    return v


def read(path: Path) -> tuple[list[str], list[list[str]]]:
    with open(path, encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    return rows[0], rows[1:]


def negative_control(header: list[str], rows: list[list[str]], ctx: dict) -> dict[str, bool]:
    """Inject one defect of each class into a copy; each must be reported under its class."""
    def first(status: str, pred=lambda s: True) -> int:
        return next(i for i, r in enumerate(rows) if r[1] == status and pred(json.loads(r[2])))

    def mutate_steps(i: int, fn) -> list[list[str]]:
        rs = copy.deepcopy(rows)
        steps = json.loads(rs[i][2])
        fn(steps)
        rs[i][2] = json.dumps(steps)
        return rs

    inc = first("INCOMPLETE", lambda s: len(s) >= 3)
    com = first("COMPLETE", lambda s: len(s) >= 2)
    na = first("NOT_APPLICABLE")
    files = sorted((t, e) for e, t in ctx["times"].items() if e.startswith("evidence_"))
    late_case = next(i for i, r in enumerate(rows) if r[1] == "COMPLETE" and ts(files[-1][0]) > ts(ctx["audit_at"][r[0]]))
    late_ev = files[-1][1]                      # the newest evidence file, captured after that case's audit_at
    empty_case = next(cid for cid in ctx["ids"] if ctx["reasons"].get(cid) != EMPTY_ALLOWED_REASON
                      and rows[ctx["ids"].index(cid)][1] == "INCOMPLETE")
    cases = {
        "header": (["case_id", "status", "steps_json"], rows),
        "row_count": (header, rows[:-1]),
        "id_set": (header, rows[:-1] + [["case_99999", "NOT_APPLICABLE", "[]"]]),
        "row_order": (header, [rows[1], rows[0]] + rows[2:]),
        "case_id_sort": (header, sorted(rows, key=lambda r: r[0])),   # plain string order: case_100 before case_33
        "status": (header, [r if j else [r[0], "DONE", r[2]] for j, r in enumerate(rows)]),
        "json": (header, [r if j != inc else [r[0], r[1], r[2][:-1]] for j, r in enumerate(rows)]),
        "not_applicable_steps": (header, [r if j != na else [r[0], r[1], rows[inc][2]] for j, r in enumerate(rows)]),
        "incomplete_empty": (header, [r if r[0] != empty_case else [r[0], "INCOMPLETE", "[]"] for r in rows]),
        "step_keys": (header, mutate_steps(inc, lambda s: s[0].update(extra=1))),
        "step_no": (header, mutate_steps(inc, lambda s: s[1].update(step_no=7))),
        "triple": (header, mutate_steps(inc, lambda s: s[0].update(state="ACTIVE") if s[0]["state"] != "ACTIVE"
                                        else s[0].update(state="PRESENT"))),
        "complete_phase": (header, mutate_steps(com, lambda s: s[0].update(phase="FINDING", operation="REPORT_REMAINING_RECORD",
                                                                          state="PRESENT"))),
        "citation_missing": (header, mutate_steps(com, lambda s: s[0].update(evidence_id=""))),
        "future_step_cited": (header, mutate_steps(inc, lambda s: s[-1].update(evidence_id="evidence_01"))),
        "citation_unknown": (header, mutate_steps(com, lambda s: s[0].update(evidence_id="evidence_9999"))),
        "citation_after_audit": (header, mutate_steps(late_case, lambda s: s[0].update(evidence_id=late_ev))),
        "ordering": (header, mutate_steps(com, lambda s: (s.reverse(), [x.update(step_no=k) for k, x in enumerate(s, 1)]))),
    }
    caught = {}
    for cls, (h, rs) in cases.items():
        found = {c for c, _, _ in audit(h, rs, ctx)}
        caught[cls] = cls in found
    return caught


def main() -> int:
    ctx = load_context(REPO / "dataset", REPO / "code" / "traces")
    header, rows = read(REPO / "output.csv")
    violations = audit(header, rows, ctx)
    counts = Counter(r[1] for r in rows)
    steps = sum(len(json.loads(r[2])) for r in rows)
    print(f"output.csv: {len(rows)} rows {dict(sorted(counts.items()))}, {steps} steps; violations: {len(violations)}")
    for c, cid, d in violations[:20]:
        print(f"  {c} {cid}: {d}")
    empties = [r[0] for r in rows if r[1] == "INCOMPLETE" and r[2] == "[]"]
    print(f"INCOMPLETE [] rows (allowed only with reason {EMPTY_ALLOWED_REASON}): {empties}")
    ids = [r[0] for r in rows]
    print(f"note: rows follow deletion_requests.csv order (numeric case_id); plain string order would differ: {ids != sorted(ids)}")
    caught = negative_control(header, rows, ctx)
    print(f"negative control: {sum(caught.values())}/{len(caught)} injected defect classes caught")
    for cls, ok in caught.items():
        if not ok:
            print(f"  NOT CAUGHT: {cls}")
    result = {"violations": violations, "status_counts": counts, "steps": steps, "incomplete_empty": empties,
              "negative_control": caught}
    (REPO / "code" / "evaluation" / "spec_audit.json").write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    return 0 if not violations and all(caught.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
