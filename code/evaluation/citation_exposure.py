"""Round 6 R3: citation exposure on eval by rule-C path (audit only; changes nothing).

Every cited step in output.csv (FINDING steps, and every step of a COMPLETE row) is classified by the path rule C took
for its resource: (i) the latest admissible file mentioning the resource_id, or (ii) the fallback, the latest admissible
full snapshot of the system/table (absence by omission). For path (ii), each citation is checked: the cited file is
a full snapshot of that system captured <= audit_at; and whether a STRONGER proof exists: another admissible capture
that mentions the resource_id, or a later admissible full snapshot of the same system. Writes
code/evaluation/citation_exposure.json.
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "code"))
from agent import pipeline  # noqa: E402
from agent.loaders import parse_ts  # noqa: E402


def main() -> int:
    rr = pipeline.run(REPO / "dataset", from_cache=True)
    eval_ids = {r["case_id"] for r in rr.dataset.requests}
    reqs = {r["case_id"]: r for r in rr.dataset.requests}
    out = {r["case_id"]: r for r in csv.DictReader(open(REPO / "output.csv", encoding="utf-8"))}
    paths, fallback = Counter(), []
    for cid in sorted(eval_ids, key=lambda c: int(c.rsplit("_", 1)[1])):
        status = out[cid]["deletion_status"]
        res = {r["resource_id"]: r for r in rr.outcomes[cid].trace.get("resources", [])}
        audit = parse_ts(reqs[cid]["audit_at"])
        for s in json.loads(out[cid]["steps_json"]):
            if not s["evidence_id"]:
                continue
            r = res.get(s["resource_id"])
            basis = r["state"]["proof_basis"] if r else "?"
            if s["evidence_id"] not in rr.index.files:
                paths["activity_or_audit_id"] += 1
                continue
            path = {"MENTION": "i_mention", "FULL_SNAPSHOT": "ii_full_snapshot"}.get(basis, basis)
            paths[path] += 1
            if path != "ii_full_snapshot":
                continue
            ef = rr.index.files[s["evidence_id"]]
            snaps = [x for x in rr.index.snapshots_by_system.get(s["system_id"], []) if parse_ts(x.captured_at) <= audit
                     and parse_ts(rr.index.files[x.file_id].captured_at) <= audit]
            cited = [x for x in snaps if x.file_id == s["evidence_id"]]
            mentions = sorted({o.file_id for o in rr.index.by_resource.get(s["resource_id"], [])
                               if parse_ts(o.file_captured_at) <= audit and parse_ts(o.time) <= audit})
            later = sorted({x.file_id for x in snaps if cited and parse_ts(x.captured_at) > max(parse_ts(c.captured_at) for c in cited)})
            fallback.append({"case_id": cid, "status": status, "resource_id": s["resource_id"], "system_id": s["system_id"],
                             "operation": s["operation"], "cited": s["evidence_id"], "cited_captured_at": ef.captured_at,
                             "audit_at": reqs[cid]["audit_at"],
                             "cited_is_full_snapshot_of_system": bool(cited),
                             "cited_tables": sorted({c.container for c in cited}),
                             "stronger_mentioning_capture": mentions, "later_full_snapshot_of_system": later})
    report = {"cited_steps_by_path": dict(paths), "fallback_citations": fallback,
              "fallback_cases": sorted({f["case_id"] for f in fallback}, key=lambda c: int(c.rsplit("_", 1)[1])),
              "fallback_with_a_stronger_proof": [f for f in fallback if f["stronger_mentioning_capture"]
                                                 or f["later_full_snapshot_of_system"] or not f["cited_is_full_snapshot_of_system"]]}
    (REPO / "code" / "evaluation" / "citation_exposure.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print("cited steps by path:", dict(paths))
    print("fallback citations:", len(fallback), "in", len(report["fallback_cases"]), "cases")
    print("fallback with a stronger proof or not a snapshot of the system:", len(report["fallback_with_a_stronger_proof"]))
    for f in fallback[:8]:
        print(" ", f)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
