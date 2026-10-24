#!/usr/bin/env python3
"""Read-only measurements M1 (status-flip exposure of AMBIGUOUS pairs) and M2 (self-contradicting rows).

Runs the pipeline from cache in memory and never writes output.csv. Attribution uncertainty (who owns a
record) and state uncertainty (what the record looked like at audit_at) are reported separately.

    python3 code/analysis/measure_ambiguity.py
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.loaders import DEFAULT_DATASET_DIR, REPO_ROOT  # noqa: E402
from agent.pipeline import run  # noqa: E402
from agent.rules import Policy  # noqa: E402
from agent.state import reconstruct  # noqa: E402
from agent.validation import self_contradictions  # noqa: E402


def m1(rr) -> dict:
    policy = Policy(rr.dataset.rules)
    worker = {}
    for a in sorted(rr.dataset.activity, key=lambda a: (a.event_time, a.activity_id)):
        if a.source_system == "deletion_worker":
            worker.setdefault(a.resource_id, []).append(a.event_type)
    rows, totals = [], Counter()
    for r in rr.dataset.requests:
        cid = r["case_id"]
        attrs = [a for a in rr.attribution.per_case.get(cid, []) if a.label == "AMBIGUOUS" and a.in_scope]
        if not attrs:
            continue
        present, compliant, unknown = [], [], []
        for a in attrs:
            system = a.system_id or rr.index.system_of(a.resource_id)
            state = reconstruct(a.resource_id, system, r["audit_at"], rr.index)
            cat, basis = policy.category(a.resource_id, system, rr.index, worker.get(a.resource_id, []))
            v = policy.verdict(cat, basis, state)
            # "proven PRESENT" = an admissible observation (not absence by omission) shows a non-compliant record
            if v.compliant is False and state.proof_basis == "MENTION":
                present.append(a.resource_id)
            elif v.compliant is True:
                compliant.append(a.resource_id)
            else:
                unknown.append(a.resource_id)
        status = rr.outcomes[cid].status
        flips = status == "COMPLETE" and bool(present)
        rows.append({"case_id": cid, "status": status, "ambiguous_pairs": len(attrs), "proven_present": present,
                     "compliant_if_owned": len(compliant), "state_unknown": unknown, "would_flip": flips})
        totals["cases"] += 1
        totals["pairs"] += len(attrs)
        totals["proven_present_resources"] += len(present)
        totals["cases_with_proven_present"] += bool(present)
        totals["would_flip_complete_to_incomplete"] += flips
        totals["cases_with_unknown_state"] += bool(unknown)
        totals["state_unknown_resources"] += len(unknown)
    return {"totals": dict(totals), "rows": rows}


def main() -> int:
    rr = run(DEFAULT_DATASET_DIR, from_cache=True)
    res = m1(rr)
    print("M1 status-flip exposure (eval, AMBIGUOUS pairs; ownership never assumed):")
    for k, v in res["totals"].items():
        print(f"  {k}: {v}")
    flips = [x for x in res["rows"] if x["would_flip"]]
    print(f"  cases that would flip: {[x['case_id'] for x in flips]}")
    with open(REPO_ROOT / "output.csv", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    bad = self_contradictions(rows)
    sample = REPO_ROOT / "code" / "evaluation" / "sample_predictions.csv"
    with open(sample, encoding="utf-8", newline="") as fh:
        bad_samples = self_contradictions(list(csv.DictReader(fh)))
    print("M2 self-contradicting rows: output.csv", bad, "| samples", bad_samples)
    for cid in [b[0] for b in bad + bad_samples]:
        t = rr.outcomes[cid].trace
        print(f"  {cid}: degraded={t.get('degraded')!r}; excluded={[(e['resource_id'], e['reason'][:70]) for e in t.get('excluded', [])]}")
    out = REPO_ROOT / "code" / "evaluation" / "measurements.json"
    out.write_text(json.dumps({"m1": res, "m2": {"output": bad, "samples": bad_samples}}, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
