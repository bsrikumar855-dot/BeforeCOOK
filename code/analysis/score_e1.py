#!/usr/bin/env python3
"""Score experiment E1 (strategies A and B) against the sample answers. Reference answers score, never build.

    python3 code/analysis/score_e1.py
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import e1_order_inference as e1  # noqa: E402
from agent import pipeline  # noqa: E402
from agent.loaders import DEFAULT_DATASET_DIR, REPO_ROOT  # noqa: E402

_attribute = pipeline.attribute


def run(dataset_dir, from_cache, order_strategy=None):
    """Production pipeline, with the reverted strategy patched in after attribution for scoring only."""
    def patched(contexts, index, activity, universe, **kwargs):      # rc9: pass on page_ids (added in rc6)
        result = _attribute(contexts, index, activity, universe, **kwargs)
        if order_strategy:
            strategy = {"A": e1.strategy_a, "B": e1.strategy_b}[order_strategy]
            e1.apply(result, strategy(contexts, result, activity))
        return result
    pipeline.attribute = patched
    try:
        return pipeline.run(dataset_dir, from_cache=from_cache)
    finally:
        pipeline.attribute = _attribute
from agent.steps import serialize  # noqa: E402


def key(s: dict) -> tuple:
    return tuple(s[k] for k in ("phase", "system_id", "resource_id", "operation", "state", "evidence_id"))


def sample_steps(rr) -> dict[str, list[dict]]:
    return {r["case_id"]: json.loads(serialize(rr.outcomes[r["case_id"]].steps)) for r in rr.dataset.sample_requests}


def main() -> int:
    with open(REPO_ROOT / "dataset" / "sample_output.csv", encoding="utf-8") as fh:
        expected = {r["case_id"]: r for r in csv.DictReader(fh)}
    exp_pairs = {(c, s["resource_id"]) for c, r in expected.items() for s in json.loads(r["steps_json"])}
    owner = {}
    for c, rid in exp_pairs:
        owner.setdefault(rid, set()).add(c)
    base = run(DEFAULT_DATASET_DIR, from_cache=True)
    dropped = sorted((c, a.resource_id) for c in expected for a in base.attribution.per_case[c]
                     if a.label == "AMBIGUOUS" and (c, a.resource_id) in exp_pairs)
    base_steps = sample_steps(base)
    report = {"timing_only_ambiguous_sample_pairs": len(dropped)}
    for name in ("A", "B"):
        rr = run(DEFAULT_DATASET_DIR, from_cache=True, order_strategy=name)
        res = Counter()
        wrong = []
        sample_ids = set(expected)
        for cid, attrs in rr.attribution.per_case.items():
            for a in attrs:
                if a.label != "INFERRED_ORDER":
                    continue
                res["resolved_total"] += 1
                if cid in sample_ids:
                    res["resolved_in_sample_cases"] += 1
                else:
                    res["resolved_in_eval_cases_UNVERIFIED"] += 1
                if a.resource_id in owner:
                    ok = cid in owner[a.resource_id]
                    res["evaluable_correct" if ok else "evaluable_incorrect"] += 1
                    if not ok:
                        wrong.append((cid, a.resource_id))
                elif cid in sample_ids:
                    res["sample_case_resource_not_in_answers"] += 1
        labels = {(c, a.resource_id): a.label for c in expected for a in rr.attribution.per_case[c]}
        res["of_19_resolved_correctly"] = sum(1 for p in dropped if labels.get(p) == "INFERRED_ORDER")
        res["of_19_still_ambiguous"] = sum(1 for p in dropped if labels.get(p) == "AMBIGUOUS")
        steps = sample_steps(rr)
        broken = added = removed = 0
        for cid, e in expected.items():
            ek = Counter(key(s) for s in json.loads(e["steps_json"]))
            b = Counter(key(s) for s in base_steps[cid])
            n = Counter(key(s) for s in steps[cid])
            broken += sum(((b & ek) - (n & ek)).values())
            added += sum((n - b).values())
            removed += sum((b - n).values())
        status_changes = sum(1 for c in expected if rr.outcomes[c].status != base.outcomes[c].status)
        matched = sum(sum((Counter(key(s) for s in json.loads(expected[c]["steps_json"])) &
                           Counter(key(s) for s in steps[c])).values()) for c in expected)
        report[name] = {**res, "incorrect_pairs": wrong, "previously_correct_steps_broken": broken,
                        "sample_steps_added": added, "sample_steps_removed": removed,
                        "sample_status_changes": status_changes, "sample_steps_matched_after": matched,
                        "sample_steps_predicted_after": sum(len(v) for v in steps.values()),
                        "eval_rows_changed_UNVERIFIED": sum(
                            1 for r in rr.dataset.requests
                            if serialize(rr.outcomes[r["case_id"]].steps) != serialize(base.outcomes[r["case_id"]].steps)
                            or rr.outcomes[r["case_id"]].status != base.outcomes[r["case_id"]].status)}
    print(json.dumps(report, indent=1))
    (REPO_ROOT / "code" / "evaluation" / "e1_results.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
