"""C6: resources per data category (eval run vs sample answers), and every resource whose category is UNMAPPED.

Reads the traces of the last `code/main.py` run and `dataset/sample_output.csv` joined to the sample traces.
Writes code/evaluation/category_coverage.json. No model calls.
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    rules = [r["data_category"] for r in csv.DictReader(open(ROOT / "dataset" / "deletion_rules.csv", encoding="utf-8"))]
    eval_ids = {r["case_id"] for r in csv.DictReader(open(ROOT / "dataset" / "deletion_requests.csv", encoding="utf-8"))}
    traces = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "code" / "traces").glob("case_*.json")}
    in_eval, emitted, unmapped = Counter(), Counter(), []
    sample_cat: dict[str, str] = {}
    for cid, t in traces.items():
        for r in t.get("resources", []):
            if cid in eval_ids:
                in_eval[r["category"]] += 1
                if r.get("operations"):
                    emitted[r["category"]] += 1
                if r["category"] == "UNMAPPED":
                    unmapped.append({"case_id": cid, "resource_id": r["resource_id"], "system_id": r["system_id"]})
            else:
                sample_cat[(cid, r["resource_id"])] = r["category"]
    in_samples = Counter()
    for row in csv.DictReader(open(ROOT / "dataset" / "sample_output.csv", encoding="utf-8")):
        for s in {(s["resource_id"]) for s in json.loads(row["steps_json"])}:
            in_samples[sample_cat.get((row["case_id"], s), "NOT_IN_PREDICTION")] += 1
    cats = rules + ["DATA_FLOW", "UNMAPPED"]
    table = {c: {"eval_resources": in_eval.get(c, 0), "eval_with_steps": emitted.get(c, 0),
                 "sample_answer_resources": in_samples.get(c, 0)} for c in cats}
    out = {"categories": table, "sample_answer_resources_not_predicted": in_samples.get("NOT_IN_PREDICTION", 0),
           "unmapped": unmapped,
           "no_sample_coverage": [c for c in rules if table[c]["sample_answer_resources"] == 0]}
    (ROOT / "code" / "evaluation" / "category_coverage.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    for c, v in table.items():
        print(f"{c:22} eval {v['eval_resources']:5} with_steps {v['eval_with_steps']:5} samples {v['sample_answer_resources']:4}")
    print("no sample coverage:", out["no_sample_coverage"], "| unmapped:", len(unmapped))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
