"""Round 6 R4: sample vs eval distribution sanity (skill: orchestrate-submit/references/evaluator.md). Audit only.

Compares predicted sample rows (code/evaluation/sample_predictions.csv), reference sample rows (dataset/sample_output.csv)
and eval rows (output.csv): steps per case, operations per case, phases present per status, and the share of cases with
VERIFY_RECORD_PRESENT, tombstone steps, restore steps and flow steps. Lists eval outliers: top 10 by step count, every
eval case using an operation never seen in the sample reference, and every INCOMPLETE case whose findings lack the
remediation their type requires. Writes code/evaluation/distribution_sanity.json.
"""
from __future__ import annotations

import csv
import json
import statistics
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REMEDIATION_FOR = {"REPORT_REMAINING_RECORD": {"DELETE_RECORD"}, "REPORT_EXPOSED_PII": {"REDACT_RECORD", "ANONYMIZE_RECORD"},
                   "REPORT_ACTIVE_DATA_FLOW": {"DISABLE_DATA_FLOW"}, "REPORT_RESTORE_RISK": {"BLOCK_RESTORE"}}
VERIFY_FOR = {"DELETE_RECORD": "VERIFY_RECORD_ABSENT", "REDACT_RECORD": "VERIFY_RECORD_REDACTED",
              "ANONYMIZE_RECORD": "VERIFY_RECORD_ANONYMIZED", "DISABLE_DATA_FLOW": "VERIFY_DATA_FLOW_DISABLED",
              "BLOCK_RESTORE": "VERIFY_RESTORE_BLOCKED"}


def rows(p: Path) -> dict:
    return {r["case_id"]: (r["deletion_status"], json.loads(r["steps_json"])) for r in csv.DictReader(open(p, encoding="utf-8"))}


def profile(rs: dict) -> dict:
    deletion = {c: v for c, v in rs.items() if v[0] != "NOT_APPLICABLE"}
    n = [len(s) for _, s in deletion.values()]
    ops = Counter(op for _, s in deletion.values() for op in {x["operation"] for x in s})
    phases = {}
    for st in ("COMPLETE", "INCOMPLETE"):
        cs = [s for status, s in deletion.values() if status == st]
        phases[st] = {"cases": len(cs), **{p: sum(1 for s in cs if any(x["phase"] == p for x in s)) for p in
                                             ("FINDING", "REMEDIATION", "VERIFICATION")}}
    share = lambda pred: round(sum(1 for _, s in deletion.values() if any(pred(x) for x in s)) / max(1, len(deletion)), 3)
    return {"cases": len(rs), "deletion_cases": len(deletion),
            "status": dict(Counter(v[0] for v in rs.values())),
            "steps_per_case": {"mean": round(statistics.mean(n), 2) if n else 0, "median": statistics.median(n) if n else 0,
                               "min": min(n) if n else 0, "max": max(n) if n else 0},
            "distinct_operations_per_case_mean": round(statistics.mean([len({x["operation"] for x in s}) for _, s in
                                                                         deletion.values()]), 2) if deletion else 0,
            "share_of_cases_with_operation": {op: round(c / max(1, len(deletion)), 3) for op, c in sorted(ops.items())},
            "phases_present_per_status": phases,
            "share_verify_present": share(lambda x: x["operation"] == "VERIFY_RECORD_PRESENT"),
            "share_tombstone_steps": share(lambda x: x["system_id"] == "identity_service"),
            "share_restore_steps": share(lambda x: x["operation"] in ("REPORT_RESTORE_RISK", "BLOCK_RESTORE", "VERIFY_RESTORE_BLOCKED")),
            "share_flow_steps": share(lambda x: x["operation"] in ("REPORT_ACTIVE_DATA_FLOW", "DISABLE_DATA_FLOW", "VERIFY_DATA_FLOW_DISABLED"))}


def main() -> int:
    ref = rows(REPO / "dataset" / "sample_output.csv")
    pred = rows(REPO / "code" / "evaluation" / "sample_predictions.csv")
    ev = rows(REPO / "output.csv")
    ref_ops = {x["operation"] for _, s in ref.values() for x in s}
    top = sorted(ev.items(), key=lambda kv: (-len(kv[1][1]), int(kv[0].rsplit("_", 1)[1])))[:10]
    novel = sorted((c, sorted({x["operation"] for x in s} - ref_ops)) for c, (_, s) in ev.items()
                   if {x["operation"] for x in s} - ref_ops)
    gaps = []
    for c, (status, s) in ev.items():
        if status != "INCOMPLETE":
            continue
        for x in s:
            if x["phase"] != "FINDING":
                continue
            rem = {y["operation"] for y in s if y["resource_id"] == x["resource_id"] and y["phase"] == "REMEDIATION"}
            ver = {y["operation"] for y in s if y["resource_id"] == x["resource_id"] and y["phase"] == "VERIFICATION"}
            need = REMEDIATION_FOR[x["operation"]]
            if not rem & need or not any(VERIFY_FOR[r] in ver for r in rem & need):
                gaps.append({"case_id": c, "resource_id": x["resource_id"], "finding": x["operation"],
                             "remediation": sorted(rem), "verification": sorted(ver)})
    out = {"reference_samples": profile(ref), "predicted_samples": profile(pred), "eval": profile(ev),
           "eval_top10_by_steps": [(c, st, len(s)) for c, (st, s) in top],
           "eval_cases_with_operations_never_in_sample_reference": novel,
           "incomplete_findings_without_required_remediation_or_check": gaps}
    (REPO / "code" / "evaluation" / "distribution_sanity.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    for k in ("reference_samples", "predicted_samples", "eval"):
        p = out[k]
        print(k, p["status"], "steps", p["steps_per_case"], "ops/case", p["distinct_operations_per_case_mean"],
              "VERIFY_PRESENT", p["share_verify_present"], "tombstone", p["share_tombstone_steps"], "restore",
              p["share_restore_steps"], "flow", p["share_flow_steps"], "phases", p["phases_present_per_status"])
    print("top10", out["eval_top10_by_steps"])
    print("novel ops", novel[:20], len(novel))
    print("finding gaps", len(gaps), gaps[:5])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
