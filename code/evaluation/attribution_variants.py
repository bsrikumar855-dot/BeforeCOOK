"""Round 5: measure the attribution variants (baseline, +A1, +A1+A2, +A1+A2+A3, +ordering) end to end.

Each variant runs `code/main.py --from-cache` in a child process with ORCHESTRATE_ATTRIBUTION=<variant> into a
temporary folder. Sample numbers are generated mechanically from the diff of sample_predictions against
dataset/sample_output.csv (never hand-assembled); eval numbers are UNVERIFIED (no labels) and come with label-free
self-consistency checks and the spec audit.

  python code/evaluation/attribution_variants.py        (writes code/evaluation/attribution_variants.json)
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CODE = REPO / "code"
VARIANTS = ["baseline", "a1", "a1a2", "a1a2a3", "a1a2a3o", "a1o"]
FIELDS = ["phase", "system_id", "resource_id", "operation", "state", "evidence_id"]
ASSIGNED = ("INFERRED", "INFERRED_CLAIM", "INFERRED_OFFSET", "INFERRED_ORDER")
CLAIM_RESULTS = {"COMPLETE": {"SUCCESS"}, "NOT_STARTED": {"FAILED"}, "PARTIALLY_COMPLETE": {"PARTIAL", "SUCCESS"}}

sys.path.insert(0, str(CODE / "evaluation"))
import spec_audit  # noqa: E402


def rows(path: Path) -> dict[str, tuple[str, list]]:
    return {r["case_id"]: (r["deletion_status"], json.loads(r["steps_json"]))
            for r in csv.DictReader(open(path, encoding="utf-8"))}


def key(s: dict) -> tuple:
    return tuple(s[f] for f in FIELDS)


def run_variant(v: str, out: Path) -> dict:
    env = {k: val for k, val in os.environ.items() if "API_KEY" not in k}
    env.update({"ORCHESTRATE_ATTRIBUTION": v, "PYTHONHASHSEED": "1"})
    proc = subprocess.run([sys.executable, str(CODE / "main.py"), "--from-cache", "--output", str(out / "output.csv"),
                           "--sample-output", str(out / "samples.csv"), "--traces", str(out / "traces"),
                           "--ledger", str(out / "ledger.json"), "--allow-degraded"],
                          capture_output=True, text=True, env=env, cwd=str(REPO))
    path = out / "output.csv" if (out / "output.csv").exists() else out / "output.degraded.csv"
    traces = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in (out / "traces").glob("case_*.json")}
    summary = json.loads((out / "traces" / "_run_summary.json").read_text(encoding="utf-8"))
    return {"exit": proc.returncode, "eval": rows(path), "samples": rows(out / "samples.csv"), "traces": traces,
            "summary": summary, "out": out}


def sample_metrics(pred: dict, ref: dict) -> dict:
    m = Counter()
    field_ok, field_den = Counter(), 0
    for cid, (es, esteps) in ref.items():
        ps, psteps = pred.get(cid, ("", []))
        m["status"] += es == ps
        ek, pk = Counter(map(key, esteps)), Counter(map(key, psteps))
        m["matched"] += sum((ek & pk).values())
        m["expected"] += len(esteps)
        m["predicted"] += len(psteps)
        m["sequence"] += [key(s) for s in esteps] == [key(s) for s in psteps] and es == ps
        n = max(len(esteps), len(psteps))
        field_den += n
        for i in range(n):
            for f in FIELDS:
                field_ok[f] += i < len(esteps) and i < len(psteps) and esteps[i][f] == psteps[i][f]
    return {"status": f"{m['status']}/{len(ref)}", "precision": f"{m['matched']}/{m['predicted']}",
            "recall": f"{m['matched']}/{m['expected']}", "sequence": f"{m['sequence']}/{len(ref)}",
            "fields": {f: f"{field_ok[f]}/{field_den}" for f in FIELDS}, "_m": dict(m)}


def label_of(traces: dict, cid: str, rid: str) -> str | None:
    for a in traces.get(cid, {}).get("attribution", []):
        if a["resource_id"] == rid:
            return a["label"]
    return None


def timing_pairs(traces: dict, cids) -> list[dict]:
    out = []
    for cid in cids:
        for a in traces.get(cid, {}).get("attribution", []):
            if any(e["kind"] == "TIMING" for e in a.get("edges", [])):
                out.append({"case_id": cid, **a})
    return out


def denominators(traces: dict, cids) -> dict:
    tp = timing_pairs(traces, cids)
    amb = [p for p in tp if p["label"] == "AMBIGUOUS"]
    eliminated = sum(1 for cid in cids for e in traces.get(cid, {}).get("eliminated", []) if e["reason"].startswith(("A1", "A2")))
    return {"timing_candidate_edges": len(tp) + eliminated, "filter_removed_edges": eliminated,
            "ambiguous_pairs": len(amb), "ambiguous_resources": len({p["resource_id"] for p in amb}),
            "ambiguous_cases": len({p["case_id"] for p in amb}),
            "assignments": dict(Counter(p["label"] for p in tp if p["label"] in ASSIGNED)),
            "assignments_total": sum(1 for p in tp if p["label"] in ASSIGNED)}


def label_free_checks(run: dict, dataset_activity: dict, requests: dict) -> dict:
    traces = run["traces"]
    owners = Counter()
    bad_claim, bad_band = [], []
    bands = {g: b for g, b in run["summary"]["attribution"]["bands"].items() if b["used"]}
    for cid, t in traces.items():
        for a in t.get("attribution", []):
            if a["label"] not in ASSIGNED and a["label"] != "CONFIRMED":
                continue
            timing = [e for e in a.get("edges", []) if e["kind"] == "TIMING"]
            if not timing:
                continue
            act = dataset_activity[timing[0]["source_id"]]
            if act["event_type"] == "DELETE_RECORD" and act["target_system"] == "account_service":
                owners[a["resource_id"]] += 1
                if a["label"] in ("INFERRED_CLAIM", "INFERRED_OFFSET") and \
                        act["reported_result"] not in CLAIM_RESULTS.get(requests[cid]["reported_status"], {act["reported_result"]}):
                    bad_claim.append((cid, a["resource_id"]))
            p = a.get("provenance") or {}
            g = "|".join((act["event_type"], act["target_system"], act["reported_result"]))
            if a["label"] == "INFERRED_OFFSET" and g in bands and not bands[g]["low_s"] <= p.get("offset_s", 0) <= bands[g]["high_s"]:
                bad_band.append((cid, a["resource_id"]))
    header, out_rows = spec_audit.read(run["out"] / ("output.csv" if (run["out"] / "output.csv").exists() else "output.degraded.csv"))
    ctx = spec_audit.load_context(REPO / "dataset", run["out"] / "traces")
    violations = spec_audit.audit(header, out_rows, ctx)
    return {"one_to_one_resources_assigned_twice": sorted(r for r, n in owners.items() if n > 1),
            "assigned_accounts_inconsistent_with_case": bad_claim, "assigned_followups_outside_band": bad_band,
            "spec_audit_violations": len(violations), "exit": run["exit"]}


def a1_falsification(base: dict, a1: dict, activity: dict, requests: dict) -> dict:
    """Deletion cases with 0 claim-consistent account candidates, before and after elimination; candidates removed
    by A1 versus cases whose account is established independently by hard links; A1 on those hard links."""
    from datetime import datetime, timedelta
    ts = lambda v: datetime.fromisoformat(v.replace("Z", "+00:00"))
    accounts = [a for a in activity.values() if a["source_system"] == "deletion_worker"
                and a["event_type"] == "DELETE_RECORD" and a["target_system"] == "account_service"]
    deletion = [c for c, t in base["traces"].items() if (t.get("intent") or {}).get("is_deletion")]
    owned, hard_account = set(), {}
    for cid, t in base["traces"].items():
        for at in t.get("attribution", []):
            if at["label"] in ("CONFIRMED", "CONFLICT"):
                owned.add(at["resource_id"])
            if at["label"] == "CONFIRMED" and at["in_scope"] and at.get("system_id") == "account_service" \
                    and any(e["kind"] == "HARD" for e in at["edges"]):
                hard_account.setdefault(cid, set()).add(at["resource_id"])
    out = {"deletion_cases": len(deletion), "cases_with_hard_linked_account": len(hard_account)}
    for name, lo_h, hi_h in (("window_-3h_+3h", 3, 3), ("window_-3h_+1h", 3, 1)):
        dist_b, dist_a = {}, {}
        zero_before, zero_after = [], []
        for cid in deletion:
            r = requests[cid]
            rep, cut = ts(r["reported_at"]), ts(r["audit_at"])
            win = [a for a in accounts if rep - timedelta(hours=lo_h) <= ts(a["event_time"]) <= rep + timedelta(hours=hi_h)
                   and ts(a["event_time"]) <= cut]
            ok = CLAIM_RESULTS.get(r["reported_status"])
            cons_all = [a for a in win if ok is None or a["reported_result"] in ok]
            if win and not cons_all:
                zero_before.append(cid)
            if cid in hard_account:
                continue
            free = [a for a in win if a["resource_id"] not in owned]
            cons = [a for a in free if ok is None or a["reported_result"] in ok]
            if free and not cons:
                zero_after.append(cid)
            dist_b[min(len(free), 5)] = dist_b.get(min(len(free), 5), 0) + 1
            dist_a[min(len(cons), 5)] = dist_a.get(min(len(cons), 5), 0) + 1
        out[name] = {"zero_consistent_before_elimination": zero_before,
                     "zero_consistent_after_elimination": zero_after,
                     "free_candidates_per_case_before_A1": dict(sorted(dist_b.items())),
                     "consistent_candidates_per_case_after_A1": dict(sorted(dist_a.items()))}
    out["candidate_edges_removed_by_A1"] = sum(1 for t in a1["traces"].values() for e in t.get("eliminated", [])
                                               if e["reason"].startswith("A1"))
    table, bad = {}, []
    for cid, rids in hard_account.items():
        r = requests[cid]
        rep = ts(r["reported_at"])
        for a in accounts:
            if a["resource_id"] in rids and abs((ts(a["event_time"]) - rep).total_seconds()) <= 3 * 3600:
                k = f"{r['reported_status']}->{a['reported_result']}"
                table[k] = table.get(k, 0) + 1
                if a["reported_result"] not in CLAIM_RESULTS[r["reported_status"]]:
                    bad.append((cid, a["resource_id"]))
    out["A1_on_hard_linked_accounts"] = {"status_result_counts": table, "inconsistent": bad}
    out["claim_conflict_cases"] = a1["summary"]["attribution"]["claim_conflicts"]
    return out


def main() -> int:
    ref = rows(REPO / "dataset" / "sample_output.csv")
    sample_ids = set(ref)
    activity = {r["activity_id"]: r for r in csv.DictReader(open(REPO / "dataset" / "system_activity.csv", encoding="utf-8"))}
    requests = {r["case_id"]: r for f in ("deletion_requests.csv", "sample_deletion_requests.csv")
                for r in csv.DictReader(open(REPO / "dataset" / f, encoding="utf-8"))}
    expected_res = {cid: {s["resource_id"] for s in steps} for cid, (_, steps) in ref.items()}
    expected_sys: dict[str, dict[str, set[str]]] = {}
    for cid, (_, steps) in ref.items():
        for st in steps:
            expected_sys.setdefault(cid, {}).setdefault(st["system_id"], set()).add(st["resource_id"])
    owner_of = {}
    for cid, rs in expected_res.items():
        for r in rs:
            owner_of.setdefault(r, set()).add(cid)
    report: dict = {"variants": {}}
    with tempfile.TemporaryDirectory() as tmp:
        runs = {}
        for v in VARIANTS:
            (Path(tmp) / v).mkdir()
            runs[v] = run_variant(v, Path(tmp) / v)
            print(f"ran {v}: exit {runs[v]['exit']}")
        base = runs["baseline"]
        base_m = sample_metrics(base["samples"], ref)
        timing_only = [(cid, r) for cid in sorted(ref) for r in sorted(expected_res[cid])
                       if label_of(base["traces"], cid, r) in ("AMBIGUOUS", "CONFLICT")]
        base_correct_steps = {(cid, key(s)) for cid, (_, steps) in ref.items() for s in steps
                              if key(s) in {key(x) for x in base["samples"].get(cid, ("", []))[1]}}
        base_status_ok = {cid for cid in ref if base["samples"].get(cid, ("",))[0] == ref[cid][0]}
        for v in VARIANTS:
            r = runs[v]
            m = sample_metrics(r["samples"], ref)
            resolved = [(c, x, label_of(r["traces"], c, x)) for c, x in timing_only if (label_of(r["traces"], c, x) or "") in ASSIGNED]
            # every assignment in a sample case that is new vs baseline, judged against the reference
            new_assign, incorrect, correct, unverifiable = [], [], [], []
            for p in timing_pairs(r["traces"], sample_ids):
                if p["label"] not in ASSIGNED or label_of(base["traces"], p["case_id"], p["resource_id"]) == p["label"]:
                    continue
                item = (p["case_id"], p["resource_id"], p["label"])
                new_assign.append(item)
                ref_accounts = expected_sys[p["case_id"]].get("account_service", set())
                if p["resource_id"] in expected_res[p["case_id"]]:
                    correct.append(item)
                elif owner_of.get(p["resource_id"], set()) - {p["case_id"]}:
                    incorrect.append(item + (f"reference gives it to {sorted(owner_of[p['resource_id']])}",))
                elif p.get("system_id") == "account_service" and ref_accounts:
                    incorrect.append(item + (f"reference account of {p['case_id']} is {sorted(ref_accounts)}",))
                else:
                    unverifiable.append(item)
            broken = sorted((cid, k[2], k[3]) for cid, k in base_correct_steps
                            if k not in {key(x) for x in r["samples"].get(cid, ("", []))[1]})
            status_changes = sorted((cid, base["samples"][cid][0], r["samples"][cid][0], ref[cid][0]) for cid in ref
                                    if r["samples"][cid][0] != base["samples"][cid][0])
            eval_changed = sorted(c for c in base["eval"] if base["eval"][c] != r["eval"][c])
            flips = []
            for cid in eval_changed:
                bs, rs_ = base["eval"][cid][0], r["eval"][cid][0]
                if bs != rs_:
                    added = [s for s in r["eval"][cid][1] if key(s) not in {key(x) for x in base["eval"][cid][1]}]
                    flips.append({"case_id": cid, "from": bs, "to": rs_,
                                  "new_steps": [(s["operation"], s["resource_id"], s["evidence_id"]) for s in added][:6]})
            report["variants"][v] = {
                "exit": r["exit"], "samples": {k: val for k, val in m.items() if k != "_m"},
                "timing_only_pairs": {"total": len(timing_only), "resolved": len(resolved),
                                      "correct": sum(1 for c, x, _ in resolved if x in expected_res[c]),
                                      "resolved_pairs": resolved},
                "new_sample_assignments": len(new_assign), "correct": correct, "incorrect": incorrect,
                "unverifiable": unverifiable, "previously_correct_steps_broken": broken,
                "sample_status_changes": status_changes,
                "previously_correct_statuses_changed": [s for s in status_changes if s[0] in base_status_ok],
                "eval": {"denominators": denominators(r["traces"], [c for c in r["eval"]]),
                         "claim_conflicts": len(r["summary"]["attribution"]["claim_conflicts"]),
                         "rows_changed": len(eval_changed), "status": dict(Counter(s for s, _ in r["eval"].values())),
                         "status_flips": flips},
                "samples_denominators": denominators(r["traces"], sample_ids),
                "label_free": label_free_checks(r, activity, requests),
                "joint": r["summary"]["attribution"]["joint"],
            }
            if v == "a1a2":
                report["bands"] = r["summary"]["attribution"]["bands"]
            vv = report["variants"][v]
            print(f"{v}: samples {vv['samples']['status']} P {vv['samples']['precision']} R {vv['samples']['recall']} "
                  f"seq {vv['samples']['sequence']} | timing-only resolved {vv['timing_only_pairs']['resolved']}/"
                  f"{len(timing_only)} correct {vv['timing_only_pairs']['correct']} | new sample assignments "
                  f"{len(new_assign)}: correct {len(correct)} incorrect {len(incorrect)} unverifiable {len(unverifiable)} | "
                  f"broken {len(broken)} status changes {len(status_changes)} | eval rows changed {len(eval_changed)} "
                  f"flips {len(flips)} | checks {vv['label_free']}")
        report["a1_falsification"] = a1_falsification(base, runs["a1"], activity, requests)
        print("A1 falsification:", json.dumps(report["a1_falsification"])[:700])
        report["timing_only_pairs"] = timing_only
        report["baseline_samples"] = {k: val for k, val in base_m.items() if k != "_m"}
    (CODE / "evaluation" / "attribution_variants.json").write_text(json.dumps(report, indent=1, default=str) + "\n",
                                                                  encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
