#!/usr/bin/env python3
"""Score experiment E2 (investigator loop + deterministic verifier) on the sample batches first.

    python3 code/analysis/score_e2.py [--from-cache] [--eval]

Only batches that contain a sample case are investigated unless --eval is given. Reference answers score the
accepted proposals; they are never shown to the model or the verifier.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent.attribution import resource_owners  # noqa: E402
from agent.identity import IdentityUniverse  # noqa: E402
from e2_investigator import Investigator, build_batches, verify_batch  # noqa: E402
from agent.llm_client import LLMClient, ResponseCache, UsageLedger  # noqa: E402
from agent.loaders import DEFAULT_DATASET_DIR, REPO_ROOT  # noqa: E402
from agent.pipeline import run  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-cache", action="store_true")
    ap.add_argument("--eval", action="store_true", help="also investigate batches without sample cases")
    args = ap.parse_args()
    rr = run(DEFAULT_DATASET_DIR, from_cache=True)          # pipeline replay: zero calls
    ds = rr.dataset
    universe = IdentityUniverse(ds.users, ds.audit_log)
    from agent.attribution import CaseContext
    cases = {r["case_id"]: CaseContext(r["case_id"], r, universe.resolve(r["case_id"], r["email"]), True)
             for r in ds.all_requests}
    with open(REPO_ROOT / "dataset" / "sample_output.csv", encoding="utf-8") as fh:
        expected = {r["case_id"]: r for r in csv.DictReader(fh)}
    exp_pairs = {(c, s["resource_id"]) for c, r in expected.items() for s in json.loads(r["steps_json"])}
    owner = {}
    for c, rid in exp_pairs:
        owner.setdefault(rid, set()).add(c)
    batches = build_batches(rr.attribution, cases, None if args.eval else set(expected))
    ledger = UsageLedger()
    client = LLMClient(from_cache=args.from_cache, ledger=ledger)
    inv = Investigator(client, ResponseCache(), rr.index, ds.activity, universe, cases)
    owners = {rid: sorted({e.user_id for e in edges}) for rid, edges in resource_owners(rr.index, ds.activity, universe).items()}
    accounts = {a.resource_id for a in ds.activity if a.event_type == "DELETE_RECORD" and a.target_system == "account_service"}
    stats, per_batch = Counter(), []
    sample_pairs = {(c, r) for b in batches.values() for c, r in b["pairs"] if c in expected}
    for bid, b in sorted(batches.items()):
        br = inv.run_batch(b, batches)
        decisions = verify_batch(br, rr.attribution, owners, accounts, cases)
        stats["batches"] += 1
        stats["model_calls"] += br.model_calls
        stats["tool_calls"] += br.tool_calls
        stats["tokens"] += br.tokens
        stats["batches_failed"] += bool(br.error)
        for p in br.proposals:
            stats["proposed_ASSIGN" if p.get("decision") == "ASSIGN" else "proposed_AMBIGUOUS"] += 1
        for d in decisions:
            if not d.accepted:
                stats["rejected_or_ambiguous"] += 1
                continue
            stats["accepted"] += 1
            if d.resource_id in owner:
                stats["accepted_correct" if d.case_id in owner[d.resource_id] else "accepted_incorrect"] += 1
            else:
                stats["accepted_UNVERIFIED"] += 1
        per_batch.append({"batch": bid, "pairs": len(b["pairs"]), "model_calls": br.model_calls,
                          "tool_calls": br.tool_calls, "tokens": br.tokens, "error": br.error,
                          "decisions": [(d.case_id, d.resource_id, d.accepted, d.reason) for d in decisions]})
    report = {"sample_timing_only_pairs_in_scope": len(sample_pairs & exp_pairs), **stats,
              "events": dict(Counter(e["event"] for e in ledger.events)),
              "models": dict(Counter((c["provider"], c["model"]) for c in ledger.calls).most_common()) and
              {f"{p}/{m}": n for (p, m), n in Counter((c["provider"], c["model"]) for c in ledger.calls).items()},
              "per_batch": per_batch}
    print(json.dumps({k: v for k, v in report.items() if k != "per_batch"}, indent=1))
    out = REPO_ROOT / "code" / "evaluation" / ("e2_results_eval.json" if args.eval else "e2_results.json")
    out.write_text(json.dumps(report, indent=1, default=str) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
