"""Evaluation: sample metrics, attribution metrics, root causes, custom cases X1-X5, determinism, usage.

    python3 code/evaluation/main.py                  # evaluate the last main.py run (+ custom cases)
    python3 code/evaluation/main.py --from-cache     # custom cases without network
    python3 code/evaluation/main.py --determinism    # also: two repeat runs + one --from-cache run without keys

Sample metrics and custom-case results are reported separately; there is no combined headline.
Writes code/evaluation/evaluation_report.md, code/evaluation/usage_report.md and evaluation_results.json.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
CODE_DIR = EVAL_DIR.parent
REPO_ROOT = CODE_DIR.parent
sys.path.insert(0, str(CODE_DIR))

from agent.loaders import load_dataset  # noqa: E402
from agent.parsers import build_evidence_index  # noqa: E402
from agent.identity import IdentityUniverse  # noqa: E402
from agent.schema import STEP_KEYS  # noqa: E402
from agent.steps import serialize  # noqa: E402
from agent.validation import EvidenceCatalog, validate_row  # noqa: E402

FIELDS = list(STEP_KEYS)
TREATMENT_FAMILY = {
    "REPORT_REMAINING_RECORD": "DELETE", "DELETE_RECORD": "DELETE", "VERIFY_RECORD_ABSENT": "DELETE",
    "REPORT_EXPOSED_PII": "MASK", "REDACT_RECORD": "REDACT", "VERIFY_RECORD_REDACTED": "REDACT",
    "ANONYMIZE_RECORD": "ANONYMIZE", "VERIFY_RECORD_ANONYMIZED": "ANONYMIZE", "VERIFY_RECORD_PRESENT": "RETAIN",
    "REPORT_ACTIVE_DATA_FLOW": "FLOW", "DISABLE_DATA_FLOW": "FLOW", "VERIFY_DATA_FLOW_DISABLED": "FLOW",
    "REPORT_RESTORE_RISK": "BACKUP", "BLOCK_RESTORE": "BACKUP", "VERIFY_RESTORE_BLOCKED": "BACKUP",
}


def read_rows(path: Path) -> dict[str, dict]:
    with open(path, encoding="utf-8", newline="") as fh:
        return {r["case_id"]: r for r in csv.DictReader(fh)}


def full_key(s: dict) -> tuple:
    return tuple(s[k] for k in FIELDS if k != "step_no")


def pct(n: int, d: int) -> str:
    return f"{n}/{d} ({100.0 * n / d:.1f}%)" if d else f"{n}/0"


# ----------------------------------------------------------------------------------- samples
def sample_metrics(expected: dict[str, dict], predicted: dict[str, dict], ds, traces: dict[str, dict]) -> dict:
    catalog = EvidenceCatalog(ds.manifest, ds.activity, ds.audit_log)
    audits = {r["case_id"]: r["audit_at"] for r in ds.sample_requests}
    status_ok = 0
    seq_ok = 0
    exact_with_no = 0
    matched = exp_total = pred_total = 0
    field_correct = Counter()
    field_denominator = 0
    keyed_correct = Counter()
    keyed_total = 0
    missing, extra, invalid = [], [], []
    per_case = {}
    for cid in sorted(expected, key=lambda c: int(c.split("_")[1])):
        e = json.loads(expected[cid]["steps_json"])
        p = json.loads(predicted[cid]["steps_json"]) if cid in predicted else []
        es, ps = expected[cid]["deletion_status"], predicted.get(cid, {}).get("deletion_status")
        status_ok += es == ps
        seq_ok += e == p
        n = max(len(e), len(p))
        field_denominator += n
        for i in range(n):
            for f in FIELDS:
                if i < len(e) and i < len(p) and e[i][f] == p[i][f]:
                    field_correct[f] += 1
        ek = Counter(full_key(s) for s in e)
        pk = Counter(full_key(s) for s in p)
        m = sum((ek & pk).values())
        matched += m
        exp_total += len(e)
        pred_total += len(p)
        exact_with_no += sum(1 for s in p if s in e)
        by_rk_e = {(s["phase"], s["system_id"], s["resource_id"]): s for s in e}
        by_rk_p = {(s["phase"], s["system_id"], s["resource_id"]): s for s in p}
        for k in set(by_rk_e) & set(by_rk_p):
            keyed_total += 1
            for f in ("operation", "state", "evidence_id"):
                keyed_correct[f] += by_rk_e[k][f] == by_rk_p[k][f]
        miss = [s for s in e if full_key(s) not in pk]
        ext = [s for s in p if full_key(s) not in ek]
        missing += [(cid, s) for s in miss]
        extra += [(cid, s) for s in ext]
        errs = validate_row(ps or "", predicted[cid]["steps_json"], audits[cid], catalog) if cid in predicted else ["row missing"]
        invalid += [(cid, x) for x in errs]
        per_case[cid] = {"expected_status": es, "predicted_status": ps, "sequence_exact": e == p,
                         "missing": miss, "extra": ext,
                         "root_causes": root_causes(cid, es, ps, miss, ext, traces.get(cid, {}))}
    return {"cases": len(expected), "status_correct": status_ok, "sequence_exact": seq_ok,
            "steps_expected": exp_total, "steps_predicted": pred_total, "steps_matched": matched,
            "steps_exact_including_step_no": exact_with_no,
            "field_correct": dict(field_correct), "field_denominator": field_denominator,
            "keyed_correct": dict(keyed_correct), "keyed_total": keyed_total,
            "missing": missing, "extra": extra, "invalid": invalid, "per_case": per_case}


def root_causes(cid: str, es: str, ps: str | None, missing: list[dict], extra: list[dict], trace: dict) -> list[dict]:
    """One tag per mismatched resource: RULE, MISREAD, ATTRIBUTION, CITATION, STATE, INTENT, SERIALIZATION."""
    tags = []
    if ps is None:
        return [{"resource_id": "*", "tag": "SERIALIZATION", "why": "row missing from predictions"}]
    if (es == "NOT_APPLICABLE") != (ps == "NOT_APPLICABLE"):
        return [{"resource_id": "*", "tag": "INTENT", "why": f"expected {es}, predicted {ps}"}]
    if trace.get("validation_errors"):
        tags.append({"resource_id": "*", "tag": "SERIALIZATION", "why": "; ".join(trace["validation_errors"])[:200]})
    attr = {a["resource_id"]: a for a in trace.get("attribution", [])}
    excluded = {x["resource_id"]: x for x in trace.get("excluded", [])}
    resources = {r["resource_id"]: r for r in trace.get("resources", [])}
    miss_by = defaultdict(list)
    for s in missing:
        miss_by[s["resource_id"]].append(s)
    extra_by = defaultdict(list)
    for s in extra:
        extra_by[s["resource_id"]].append(s)
    for rid in sorted(set(miss_by) | set(extra_by)):
        ms, xs = miss_by.get(rid, []), extra_by.get(rid, [])
        if ms and xs:
            fam_m = {TREATMENT_FAMILY[s["operation"]] for s in ms}
            fam_x = {TREATMENT_FAMILY[s["operation"]] for s in xs}
            same_ops = sorted(s["operation"] for s in ms) == sorted(s["operation"] for s in xs)
            if same_ops:
                tags.append({"resource_id": rid, "tag": "CITATION", "why": f"evidence {[s['evidence_id'] for s in xs]} "
                                                                         f"vs expected {[s['evidence_id'] for s in ms]}"})
            elif fam_m - {"MASK"} != fam_x - {"MASK"}:
                tags.append({"resource_id": rid, "tag": "RULE", "why": f"treatment {sorted(fam_x)} vs {sorted(fam_m)}"})
            else:
                tags.append({"resource_id": rid, "tag": "STATE", "why": "compliance differs: "
                             f"{[s['operation'] for s in xs]} vs {[s['operation'] for s in ms]}"})
        elif ms:
            a = attr.get(rid)
            if a and a["label"] in ("AMBIGUOUS", "CONFLICT"):
                tags.append({"resource_id": rid, "tag": "ATTRIBUTION", "why": f"{a['label']} -> no steps by policy"})
            elif a and not a["in_scope"]:
                tags.append({"resource_id": rid, "tag": "ATTRIBUTION", "why": f"out of scope: {a['scope_reason']}"})
            elif rid in excluded:
                tags.append({"resource_id": rid, "tag": "STATE", "why": excluded[rid]["reason"][:160]})
            elif rid in resources:
                tags.append({"resource_id": rid, "tag": "STATE", "why": f"verdict {resources[rid]['operations']}"})
            else:
                tags.append({"resource_id": rid, "tag": "MISREAD" if not trace.get("_resource_in_text", {}).get(rid)
                             else "ATTRIBUTION",
                             "why": "resource absent from every parsed text source (image content not extracted or misread)"
                             if not trace.get("_resource_in_text", {}).get(rid) else "present in evidence but never linked"})
        else:
            a = attr.get(rid)
            tags.append({"resource_id": rid, "tag": "ATTRIBUTION",
                         "why": f"extra resource ({a['label'] if a else 'unattributed'}): {a['rationale'][:120] if a else ''}"})
    return tags


# ----------------------------------------------------------------------------------- attribution
def attribution_metrics(expected: dict[str, dict], traces: dict[str, dict], sample_ids: list[str],
                        eval_ids: list[str]) -> dict:
    labelled = sorted({(cid, s["resource_id"]) for cid in expected for s in json.loads(expected[cid]["steps_json"])})
    owner_case = defaultdict(set)
    for cid, rid in labelled:
        owner_case[rid].add(cid)
    rows = []
    for cid, rid in labelled:
        a = next((x for x in traces.get(cid, {}).get("attribution", []) if x["resource_id"] == rid), None)
        kinds = {e["kind"] for e in a["edges"]} if a else set()
        link = "direct" if kinds & {"HARD", "CATALOG"} else ("inferred" if a and a["label"] == "INFERRED"
                                                               and kinds & {"SOURCE_REFERENCE"} else
                                                               ("timing-only" if "TIMING" in kinds else "none"))
        label = (a["label"] if a else "NOT_ATTRIBUTED") + ("" if not a or a["in_scope"] else "/OUT_OF_SCOPE")
        correct = bool(a and a["label"] in ("CONFIRMED", "INFERRED") and a["in_scope"])
        rows.append({"case_id": cid, "resource_id": rid, "label": label, "link": link, "correct": correct})
    by_link = defaultdict(Counter)
    for r in rows:
        by_link[r["link"]]["total"] += 1
        by_link[r["link"]]["correct"] += r["correct"]
        by_link[r["link"]][r["label"]] += 1

    def label_counts(ids):
        c = Counter()
        for cid in ids:
            for a in traces.get(cid, {}).get("attribution", []):
                c[a["label"] + ("" if a["in_scope"] else "/OUT_OF_SCOPE")] += 1
        return dict(sorted(c.items()))

    inferred_eval, inferred_unevaluable = [], []
    for cid in sample_ids + eval_ids:
        for a in traces.get(cid, {}).get("attribution", []):
            if a["label"] != "INFERRED" or not a["in_scope"]:
                continue
            if a["resource_id"] in owner_case:
                inferred_eval.append((cid, a["resource_id"], cid in owner_case[a["resource_id"]]))
            else:
                inferred_unevaluable.append((cid, a["resource_id"]))
    no_direct = [r for r in rows if r["link"] != "direct"]
    confirmed_without_direct = []
    confirmed_model_only = []
    for cid in sample_ids + eval_ids:
        for a in traces.get(cid, {}).get("attribution", []):
            if a["label"] != "CONFIRMED":
                continue
            if not any(e["kind"] in ("HARD", "CATALOG") for e in a["edges"]):
                confirmed_without_direct.append((cid, a["resource_id"]))
            elif "MODEL_EXTRACTION" in a["rationale"] and "SAME_RECORD" not in a["rationale"] \
                    and "TRACE_JOIN" not in a["rationale"] and "CATALOG" not in a["rationale"] \
                    and "DOCUMENT" not in a["rationale"]:
                confirmed_model_only.append((cid, a["resource_id"]))
    sample_pairs = sum(label_counts(sample_ids).values())
    sample_non_amb = sum(v for k, v in label_counts(sample_ids).items() if k in ("CONFIRMED", "INFERRED"))
    eval_pairs = sum(label_counts(eval_ids).values())
    eval_non_amb = sum(v for k, v in label_counts(eval_ids).items() if k in ("CONFIRMED", "INFERRED"))
    return {
        "labelled_pairs": len(rows), "correct": sum(r["correct"] for r in rows), "rows": rows,
        "by_link": {k: dict(v) for k, v in sorted(by_link.items())},
        "labels_samples": label_counts(sample_ids), "labels_eval": label_counts(eval_ids),
        "coverage_samples": (sample_non_amb, sample_pairs), "coverage_eval": (eval_non_amb, eval_pairs),
        "inferred_evaluable": inferred_eval, "inferred_unevaluable": inferred_unevaluable,
        "inferred_recall": (sum(1 for r in no_direct if r["label"] == "INFERRED" and r["correct"]), len(no_direct)),
        "dropped_by_policy": [r for r in rows if r["label"].split("/")[0] in ("AMBIGUOUS", "CONFLICT")],
        "confirmed_without_direct_link": confirmed_without_direct,
        "confirmed_only_via_model_extraction": confirmed_model_only,
    }


# ----------------------------------------------------------------------------------- custom cases
def run_custom_cases(from_cache: bool) -> list[dict]:
    sys.path.insert(0, str(EVAL_DIR / "custom_cases"))
    from build_fixtures import CASES, build  # noqa: E402
    from agent.pipeline import run  # noqa: E402
    build()
    results = []
    for name, case in CASES.items():
        d = EVAL_DIR / "custom_cases" / name
        exp = list(csv.DictReader(open(d / "expected_output.csv", encoding="utf-8")))[0]
        try:
            rr = run(d, from_cache=from_cache, include_samples=False)
            (cid, o), = rr.outcomes.items()
            got_status, got_steps = o.status, json.loads(serialize(o.steps))
            path = (o.trace.get("intent") or {}).get("path")
            error = ""
        except Exception as exc:          # report, never crash the evaluator
            got_status, got_steps, path, error = "ERROR", [], None, f"{type(exc).__name__}: {exc}"
        want = json.loads(exp["steps_json"])
        results.append({"case": name, "failure_mode": case["failure_mode"], "expected_status": exp["deletion_status"],
                        "predicted_status": got_status, "status_ok": got_status == exp["deletion_status"],
                        "steps_ok": got_steps == want, "pass": got_status == exp["deletion_status"] and got_steps == want,
                        "expected_steps": want, "predicted_steps": got_steps, "intent_path": path, "error": error})
    return results


# ----------------------------------------------------------------------------------- determinism
def _digest_dir(d: Path) -> dict[str, str]:
    return {p.relative_to(d).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.rglob("*")) if p.is_file()}


def determinism_check() -> dict:
    out = {}
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        # Separate processes with different hash seeds; --allow-degraded so the rows are compared even when the
        # file gate (exit 2) keeps them out of output.csv.
        runs = {"run_1": {"seed": "1"}, "run_2": {"seed": "2"},
                "from_cache_no_keys": {"from_cache": True, "strip_keys": True, "seed": "3"}}
        for name, opt in runs.items():
            d = tmp / name
            cmd = [sys.executable, str(CODE_DIR / "main.py"), "--output", str(d / "output.csv"),
                   "--sample-output", str(d / "sample_predictions.csv"), "--traces", str(d / "traces"),
                   "--ledger", str(tmp / f"{name}_ledger.json"), "--allow-degraded"]
            env = dict(os.environ)
            env["PYTHONHASHSEED"] = opt["seed"]
            if opt.get("from_cache"):
                cmd.append("--from-cache")
            if opt.get("strip_keys"):
                for var in [k for k in env if k.startswith("GEMINI_API_KEY")]:
                    env.pop(var)                                  # --from-cache never reads .env either
            proc = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(REPO_ROOT))
            ledger = json.loads((tmp / f"{name}_ledger.json").read_text()) if (tmp / f"{name}_ledger.json").exists() else {}
            out[name] = {"returncode": proc.returncode, "pythonhashseed": opt["seed"], "stdout": proc.stdout.strip()[-300:],
                         "stderr": proc.stderr.strip()[-300:], "model_calls": len(ledger.get("calls", [])),
                         "digests": _digest_dir(d) if d.exists() else {}}
        base = out["run_1"]["digests"]
        for name in ("run_2", "from_cache_no_keys"):
            other = out[name]["digests"]
            out[name]["identical_to_run_1"] = base == other and bool(base)
            out[name]["differences"] = sorted(k for k in set(base) | set(other) if base.get(k) != other.get(k))[:20]
    for v in out.values():
        v["files_compared"] = len(v.pop("digests"))
    return out


# ----------------------------------------------------------------------------------- usage
def usage_summary(ledger_path: Path, n_requests: int) -> dict:
    import yaml
    cfg = yaml.safe_load((CODE_DIR / "config" / "models.yaml").read_text(encoding="utf-8"))
    prices = cfg.get("paid_list_price_usd_per_million_tokens", {})
    ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else {}
    calls: dict[str, dict] = {}
    for c in ledger.get("calls", []):
        calls[c["call_id"]] = {**c, "origin": "this run"}
    hits_by_model = Counter()
    for h in ledger.get("cache_hits", []):
        hits_by_model[h.get("model")] += 1
        entry = None
        p = CODE_DIR / "cache" / h["task"] / f"{h['key']}.json"
        if p.exists():
            entry = json.loads(p.read_text(encoding="utf-8"))
        if entry and entry.get("call_id") and entry["call_id"] not in calls:
            calls[entry["call_id"]] = {"call_id": entry["call_id"], "provider": entry.get("provider"),
                                       "model": entry.get("model"), "key_slot": entry.get("key_slot"),
                                       **entry.get("call_usage", {}), "origin": "earlier run (cached)"}
    per_model = defaultdict(lambda: Counter())
    for c in calls.values():
        m = per_model[(c.get("provider"), c.get("model"))]
        m["calls"] += 1
        for k in ("input_tokens", "output_tokens", "thinking_tokens", "total_tokens"):
            m[k] += int(c.get(k, 0) or 0)
    rows = []
    for (prov, model), m in sorted(per_model.items(), key=lambda kv: str(kv[0])):
        price = prices.get(model, {})
        est = (m["input_tokens"] * price.get("input", 0) + (m["output_tokens"] + m["thinking_tokens"]) *
               price.get("output", 0)) / 1e6
        rows.append({"provider": prov, "model": model, **m, "cache_hits_this_run": hits_by_model.get(model, 0),
                     "estimated_paid_usd": est})
    total = Counter()
    for r in rows:
        for k in ("calls", "input_tokens", "output_tokens", "thinking_tokens", "total_tokens"):
            total[k] += r[k]
    total_est = sum(r["estimated_paid_usd"] for r in rows)
    # Experiment calls (not part of the dataset run): every cached investigator turn and every vision reply
    # produced after the first full run (re-extraction under the current prompt).
    experiments = Counter()
    for task in ("investigator", "vision"):
        root = CODE_DIR / "cache" / task
        for p in sorted(root.glob("*.json")) if root.exists() else []:
            e = json.loads(p.read_text(encoding="utf-8"))
            if task == "vision" and e.get("call_id") in {c["call_id"] for c in calls.values()}:
                continue
            u = e.get("call_usage", {})
            k = (task, e.get("model"))
            experiments[(k, "calls")] += 1
            experiments[(k, "tokens")] += int(u.get("total_tokens", 0) or 0)
    return {"rows": rows, "total": dict(total), "estimated_paid_usd": total_est, "requests": n_requests,
            "live_calls_this_run": len(ledger.get("calls", [])), "cache_hits_this_run": len(ledger.get("cache_hits", [])),
            "events": ledger.get("events", []), "models_verified": ledger.get("models_verified", {}),
            "experiments": {f"{t}/{m}": {"calls": experiments[((t, m), "calls")], "tokens": experiments[((t, m), "tokens")]}
                            for (t, m), kind in experiments if kind == "calls"}}


# ----------------------------------------------------------------------------------- report
def write_reports(m: dict, att: dict, custom: list[dict], det: dict | None, usage: dict, summary: dict,
                  appendix: list[str]) -> None:
    L = []
    L += ["# Evaluation report", "",
          "Generated by `python3 code/evaluation/main.py` from the last `python3 code/main.py` run. Sample metrics and",
          "custom-case results are reported separately; there is no combined headline. Numbers are measured, not estimated.", ""]
    L += ["## 1. Sample set (32 public cases, compared with `dataset/sample_output.csv`)", "",
          "| Metric | Result |", "|---|---|",
          f"| Case status accuracy | {pct(m['status_correct'], m['cases'])} |",
          f"| Step-level exact match: recall (expected steps reproduced exactly, all fields except step_no) | {pct(m['steps_matched'], m['steps_expected'])} |",
          f"| Step-level exact match: precision (predicted steps that are exactly right) | {pct(m['steps_matched'], m['steps_predicted'])} |",
          f"| Steps identical including step_no | {pct(m['steps_exact_including_step_no'], m['steps_expected'])} |",
          f"| Sequence accuracy (whole steps_json array identical) | {pct(m['sequence_exact'], m['cases'])} |",
          f"| Missing steps / extra steps | {len(m['missing'])} / {len(m['extra'])} |",
          f"| Invalid rows or evidence references (validator) | {len(m['invalid'])} |", ""]
    L += ["### Per-field accuracy (positional)", "",
          f"Steps are aligned by position (step_no). Denominator = sum over cases of max(len(expected), len(predicted)) = "
          f"{m['field_denominator']}; a missing or extra position counts as wrong for every field.", "",
          "| Field | Correct |", "|---|---|"]
    L += [f"| {f} | {pct(m['field_correct'].get(f, 0), m['field_denominator'])} |" for f in FIELDS]
    L += ["", f"Key-aligned view (steps matched on phase + system_id + resource_id, {m['keyed_total']} pairs): " +
          ", ".join(f"{f} {pct(m['keyed_correct'].get(f, 0), m['keyed_total'])}" for f in ("operation", "state", "evidence_id")), ""]
    L += ["### Every wrong sample row with its root cause", "",
          "| Case | Status (expected → predicted) | Root causes |", "|---|---|---|"]
    tag_count = Counter()
    for cid, pc in m["per_case"].items():
        if pc["sequence_exact"] and pc["expected_status"] == pc["predicted_status"]:
            continue
        causes = "; ".join(f"{t['tag']} `{t['resource_id']}`: {t['why']}" for t in pc["root_causes"])
        for t in pc["root_causes"]:
            tag_count[t["tag"]] += 1
        L.append(f"| {cid} | {pc['expected_status']} → {pc['predicted_status']} | {causes} |")
    L += ["", "Root-cause totals (one tag per mismatched resource): " + ", ".join(f"{k} {v}" for k, v in sorted(tag_count.items())), ""]
    L += ["## 2. Attribution", "",
          f"- Labelled (case, resource) pairs from the sample answers: {att['labelled_pairs']}; correctly attributed "
          f"(CONFIRMED or INFERRED, in scope): {pct(att['correct'], att['labelled_pairs'])}.",
          f"- Coverage (non-ambiguous share of candidate pairs): samples {pct(*att['coverage_samples'])}; eval {pct(*att['coverage_eval'])}.",
          f"- INFERRED precision (pairs whose resource appears in some sample answer): "
          f"{pct(sum(1 for x in att['inferred_evaluable'] if x[2]), len(att['inferred_evaluable']))}; "
          f"UNEVALUABLE INFERRED pairs (resource in no sample answer): {len(att['inferred_unevaluable'])}.",
          f"- INFERRED recall over labelled pairs without a direct identity link: {pct(*att['inferred_recall'])}.",
          f"- Expected sample pairs dropped by the AMBIGUOUS/CONFLICT output policy: {len(att['dropped_by_policy'])}.",
          f"- CONFIRMED attributions without a direct identity edge (gate: must be 0): {len(att['confirmed_without_direct_link'])}.",
          f"- CONFIRMED attributions whose only identity edge was read from a screenshot by the model (validated against "
          f"known identifiers): {len(att['confirmed_only_via_model_extraction'])} {att['confirmed_only_via_model_extraction'][:10]}.",
          f"- Hard-link contradictions (resources linked to >1 known person): {len(summary.get('conflicts', {}))} "
          f"{summary.get('conflicts', {})}.",
          f"- Content contradictions (record's own identifiers name someone else while a catalog claim or lookup key "
          f"links it to the subject): {len(summary.get('content_conflicts', {}))} {summary.get('content_conflicts', {})}.", "",
          "| Label | Samples | Eval (518) |", "|---|---|---|"]
    keys = sorted(set(att["labels_samples"]) | set(att["labels_eval"]))
    L += [f"| {k} | {att['labels_samples'].get(k, 0)} | {att['labels_eval'].get(k, 0)} |" for k in keys]
    L += ["", "Labelled pairs grouped by the kind of link available:", "", "| Link | Pairs | Correct | Labels |", "|---|---|---|---|"]
    for link, c in att["by_link"].items():
        labels = ", ".join(f"{k} {v}" for k, v in sorted(c.items()) if k not in ("total", "correct"))
        L.append(f"| {link} | {c['total']} | {c['correct']} | {labels} |")
    L += ["", "Expected pairs dropped by the policy: " + (", ".join(f"{r['case_id']}/{r['resource_id']}" for r in att["dropped_by_policy"]) or "none"), ""]
    L += ["## 3. Eval run (518 cases)", "",
          f"- Status distribution: {summary['eval']['status']}",
          f"- Intent paths: {summary['eval']['intent_paths']}",
          f"- Excluded resource reasons: {summary['eval']['excluded_reasons']}",
          f"- Degraded cases (INCOMPLETE,[]): {len(summary['eval']['degraded'])} {summary['eval']['degraded'][:20]}",
          f"- Evidence files: {summary['evidence_files']}; image rows accepted {summary['image_extraction']['accepted_rows']}, "
          f"dropped {len(summary['image_extraction']['dropped_rows'])}, failed files {len(summary['image_extraction']['failed_files'])}", ""]
    og = summary.get("ocr_grounding") or {}
    L += ["### Screenshot id grounding (C9, local OCR, no model calls)", "",
          f"Engine: {', '.join(og.get('engines', [])) or 'UNKNOWN'}. A resource id read by the vision model is kept only when the",
          "exact token appears in the image's OCR text; OCR-only ids are listed, never added (Tesseract also reads dates and",
          "mangled digits as ids).", "",
          "| Image | VLM ids | OCR ids | Grounded | Ungrounded (dropped) | OCR-only (not added) |", "|---|---|---|---|---|---|"]
    for fid, g in sorted(og.get("per_image", {}).items()):
        L.append(f"| {fid} | {len(g['vlm_ids'])} | {len(g['ocr_ids'])} | {len(g['grounded'])} | "
                 f"{', '.join(g['ungrounded']) or '—'} | {', '.join(g['ocr_only']) or '—'} |")
    L += ["", f"Ungrounded ids dropped: {len(og.get('ungrounded', []))}. Effect on the eval output: 0 rows changed; on the samples: "
          "0 steps changed (none of the dropped ids is in a sample answer).", "",
          "NOT_DERIVABLE_FROM_INPUTS (missing sample steps no parsed source names; not pursued): `account_004`, "
          "`audit_record_6645`, `audit_record_5569`.", "",
          "Rows emitted as INCOMPLETE `[]` with `reason_code=NO_ATTRIBUTABLE_RESOURCE` (DESIGN.md C5/M2(b)): "
          + (", ".join(summary["eval"].get("no_attributable_resource", [])) or "none") + " (eval); "
          + (", ".join(summary.get("samples", {}).get("no_attributable_resource", [])) or "none") + " (samples).", ""]
    L += ["## 4. Custom evaluation cases (X1–X5, fixtures in `code/evaluation/custom_cases/`)", "",
          "| Case | Failure mode tested | Expected | Predicted | Steps exact | Result |", "|---|---|---|---|---|---|"]
    for c in custom:
        L.append(f"| {c['case']} | {c['failure_mode']} | {c['expected_status']} | {c['predicted_status']} | "
                 f"{c['steps_ok']} | {'PASS' if c['pass'] else 'FAIL'}{(' — ' + c['error'][:120]) if c['error'] else ''} |")
    L += ["", "Why each case exists (expected output, why naive logic fails, spec rule) is documented in DESIGN.md §10 and in "
          "`code/evaluation/custom_cases/build_fixtures.py`.", ""]
    L += ["## 5. Determinism", ""]
    if det:
        for name, r in det.items():
            L.append(f"- {name}: PYTHONHASHSEED={r.get('pythonhashseed')}, exit {r['returncode']}, live model calls {r['model_calls']}, files compared {r['files_compared']}"
                     + (f", identical to run_1: {r['identical_to_run_1']}, differences: {r['differences']}" if "identical_to_run_1" in r else ""))
    else:
        L.append("- Not run in this invocation (use `--determinism`).")
    L += ["", "## 6. Failures found and improvements made", ""] + NARRATIVE
    L += [""] + appendix
    (EVAL_DIR / "evaluation_report.md").write_text("\n".join(L) + "\n", encoding="utf-8")

    U = ["# Usage report", "",
         "Summarises the model usage behind the final full-dataset run that produced `output.csv`. Tokens come from the",
         "provider's `usageMetadata` recorded on every call (live calls of this run plus the calls that produced the cache",
         "entries this run reused). Provider: Google Gemini API, free tier (actual cost $0).", "",
         f"- Requests in the eval run: {usage['requests']}",
         f"- Live model calls in this run: {usage['live_calls_this_run']}; cache hits in this run: {usage['cache_hits_this_run']}", "",
         "| Provider | Model | Calls | Cache hits (this run) | Input tokens | Output tokens | Thinking tokens | Total tokens | Est. paid-list cost (USD) |",
         "|---|---|---|---|---|---|---|---|---|"]
    for r in usage["rows"]:
        U.append(f"| {r['provider']} | {r['model']} | {r['calls']} | {r['cache_hits_this_run']} | {r['input_tokens']} | "
                 f"{r['output_tokens']} | {r['thinking_tokens']} | {r['total_tokens']} | {r['estimated_paid_usd']:.4f} |")
    t = usage["total"]
    n = max(1, usage["requests"])
    U += [f"| **all** | | {t.get('calls', 0)} | {usage['cache_hits_this_run']} | {t.get('input_tokens', 0)} | "
          f"{t.get('output_tokens', 0)} | {t.get('thinking_tokens', 0)} | {t.get('total_tokens', 0)} | {usage['estimated_paid_usd']:.4f} |", "",
          f"- Average tokens per eval request: {t.get('total_tokens', 0) / n:.1f}",
          f"- Actual cost: $0 (free tier). Estimated cost at paid list price: ${usage['estimated_paid_usd']:.4f} total, "
          f"${usage['estimated_paid_usd'] / n:.6f} per request.",
          f"- Price source: {PRICE_SOURCE}", "",
          f"- Fallback / quota / validation events in this run: {dict(Counter(e.get('event') for e in usage['events']))}",
          f"- Model ids listed by the provider for each key (`python3 code/main.py --check-models`): "
          f"{json.dumps(_models_verified().get('by_slot', 'not checked'))}", "",
          "## Notes on the counts", "",
          "- Listed is not the same as callable. Probes on 2026-10-24 returned HTTP 404 \"no longer available to new users\" for",
          "  `gemini-2.5-flash-lite` on every key and for `gemini-2.5-flash` on one key, and HTTP 429",
          "  `GenerateRequestsPerDayPerProjectPerModel-FreeTier` (20 requests/day per project) for `gemini-2.5-flash` on two",
          "  keys. The first live run therefore failed 12 intent batches; intent was switched to `gemini-3.5-flash-lite`",
          "  (DESIGN.md D-MODEL-IDS) and the run repeated, reusing every cached reply.",
          "- The table counts the calls whose replies are in the cache and were used by this run. Two earlier vision calls",
          "  were rejected by the output validator and retried; their token usage was recorded only in the first run's",
          "  ledger, which the rerun overwrote, so they are not included (UNKNOWN, at most two calls).",
          "- The intent replies also cover the 32 sample requests (the pipeline runs samples and eval jointly for global",
          "  attribution); averages divide by the 518 eval requests.",
          "- Calls made while evaluating the custom fixtures (X1–X5) are not part of the dataset run and are not counted.", "",
          "## Experiment calls (not part of the dataset run)", "",
          "Counted from the cached replies: E2 investigator turns, and screenshot replies produced after the dataset run",
          "(the evidence_42 re-extraction under the tri-state prompt). Groq was never called: no GROQ_API_KEY is set,",
          "so every investigator turn fell back to Gemini (one `fallback_used` event per turn).", "",
          "| Task / model | Calls | Total tokens |", "|---|---|---|"] + [
          f"| {k} | {v['calls']} | {v['tokens']} |" for k, v in sorted(usage.get("experiments", {}).items())]
    (EVAL_DIR / "usage_report.md").write_text("\n".join(U) + "\n", encoding="utf-8")


def _models_verified() -> dict:
    p = EVAL_DIR / "models_verified.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def ambiguity_appendix(traces: dict[str, dict], eval_ids: list[str]) -> list[str]:
    """Every AMBIGUOUS / CONFLICT pair on the eval set, per case, with its effect on the output."""
    rows, systems, resources = [], Counter(), set()
    for cid in eval_ids:
        t = traces.get(cid, {})
        pairs = [a for a in t.get("attribution", []) if a["label"] in ("AMBIGUOUS", "CONFLICT") and a["in_scope"]]
        if not pairs:
            continue
        for a in pairs:
            systems[a.get("system_id")] += 1
            resources.add(a["resource_id"])
        rows.append(f"| {cid} | {t.get('status')} | {len(pairs)} | " +
                    ", ".join(f"{a['resource_id']}{' (CONFLICT)' if a['label'] == 'CONFLICT' else ''}" for a in pairs) + " |")
    head = ["## Appendix A. AMBIGUOUS / CONFLICT pairs on the eval set", "",
            f"Effect on output: none of these pairs produces a step (output policy D-ATTR). {len(rows)} eval cases carry at "
            f"least one such pair; {len(resources)} distinct resources; pairs by system: {dict(sorted(systems.items(), key=str))}. "
            "Per-pair rationale (rival cases, batch) is in `code/traces/<case_id>.json` under `attribution` and `excluded`.", "",
            "| Case | Output status | Pairs | Resources withheld |", "|---|---|---|---|"]
    return head + rows


PRICE_SOURCE = ("Gemini Developer API pricing page, paid tier (https://ai.google.dev/gemini-api/docs/pricing; page last "
                "updated 2026-10-22 UTC, retrieved 2026-10-24). Rates are copied into code/config/models.yaml.")
NARRATIVE = [
    "- **Citation rule.** Rule A (latest file mentioning the id) matched 71/75 cited sample steps; rule C (A, else the latest",
    "  full snapshot of the system) matched 74/75. C was adopted; the single miss (one step) was not chased (DESIGN.md rejection log).",
    "- **PDF text layer.** Reading the PDFs with a stdlib text-layer decoder instead of a model recovered `support_001`'s citation.",
    "- **Retained records.** The first build dropped security audit records that were only visible in files captured after",
    "  audit_at. A retained record's required final state is PRESENT by policy, so it is now emitted as a check in INCOMPLETE",
    "  cases and dropped (and logged) in COMPLETE cases, where a step must cite proof.",
    "- **Scope.** Three users filed two requests each; resources from the other request leaked in until scope was limited to",
    "  identity evidence inside [requested_at, audit_at] or the case's own deletion burst.",
    "- **Model availability (found in the first live run).** 358 of 550 cases fell back to NOT_APPLICABLE through",
    "  `model_failure`: `gemini-2.5-flash-lite` answered HTTP 404 (\"no longer available to new users\") on every key and",
    "  `gemini-2.5-flash` hit its 20-requests/day free-tier cap. Diagnosed from the provider's error bodies; intent moved",
    "  to `gemini-3.5-flash-lite` (which rejects `thinkingBudget`, so the field is no longer sent), and a 404 now retires",
    "  that (model, key) for the run. After the rerun: 508 DELETION, 10 NON_DELETION, 0 model failures.",
    "- **Screenshot rows.** The model joined two visible ids once (`user_11_account_5762`) and showed two records under",
    "  their data-catalog aliases (a file name; a customer key). Joined ids are salvaged only when exactly one token has a",
    "  known resource prefix; aliases resolve only on an exact catalog match. Both are logged per row.",
    "- **Content contradiction.** A catalog row linked `marketing_001` to one subject while the record itself shows another",
    "  person absent from the identity tables. It is now CONFLICT (no steps) instead of a CONFIRMED attribution.",
    "- **Trace determinism.** Traces carried a `cache_hit` flag, so a live run and a cached run differed in 358 traces; the",
    "  flag now lives only in the usage ledger and traces are byte-identical across live, repeat and keyless runs.",
    "",
    "### Change records (checkpoint 807cc74 onward; one local commit per shipped change)",
    "",
    "| Id | Change | Rows it should fix | Rows it could break | Measured | Verdict |",
    "|---|---|---|---|---|---|",
    "| C0 | Model failures counted in `degraded`; exit 4 never replaces output.csv; `--allow-degraded` writes "
    "output.degraded.csv | none (guard) | none | 0 rows changed; 4 new tests; a real VLM failure exited 4 with "
    "output.csv untouched | SHIPPED (807cc74) |",
    "| C1 | File validator rejects INCOMPLETE `[]` and COMPLETE without VERIFICATION | none (gate) | none | 0 rows "
    "changed; case_55 and case_63 now keep every run at exit 2, so output.csv stays at ef0cc990 until the M2 "
    "policy is approved | SHIPPED (e9681aa) |",
    "| E1-A | Anchored monotone matching of account deletes (INFERRED_ORDER) | 19 timing-only AMBIGUOUS sample "
    "pairs | any pair it resolves wrongly | 4 correct, 1 incorrect, 14 still AMBIGUOUS; 0 steps broken; 25 eval "
    "rows would change (UNVERIFIED) | REVERTED (1 incorrect) |",
    "| E1-B | Burst segmentation, gap > 25 min (unlabelled histogram trough) | same | same | 1 correct, 1 "
    "incorrect, 17 still AMBIGUOUS; 0 steps broken; 15 eval rows would change (UNVERIFIED) | REVERTED (1 "
    "incorrect) |",
    "| C2 | Tri-state `direct_identifiers_visible` (+ reason, null rejected, legacy null -> UNKNOWN); screenshot "
    "category claims must fit the system; vision fallback gemini-3.5-flash | tombstone_17 semantics | a "
    "screenshot-derived treatment | evidence_42 re-extracted: tombstone_17 unchanged (exposure UNKNOWN, no "
    "step); without the category check the new reply would have turned the tombstone into a DELETE (caught, "
    "rejected); 0 rows changed | SHIPPED (ef0f478) |",
    "| E2 | Tool-calling investigator + deterministic verifier (INFERRED_AGENT) | remaining AMBIGUOUS/CONFLICT "
    "pairs | any accepted wrong proposal | 11 sample batches, 28 model calls, 17 tool calls, 41,780 tokens; 126 "
    "proposals, all AMBIGUOUS; 0 accepted. Verifier negative control: 3/3 wrong proposals rejected (unit "
    "test) | NOT SHIPPED (no metric moves; eval run skipped) |",
    "",
    "- **Known remaining failure class.** 19 of 22 missing sample steps are AMBIGUOUS attributions (interleaved deletion",
    "  bursts), which by policy produce no steps; 3 are records found in no parsed source. Individual wrong rows are not",
    "  fixed until reviewed.",
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-cache", action="store_true")
    ap.add_argument("--determinism", action="store_true")
    args = ap.parse_args(argv)
    ds = load_dataset()
    expected = read_rows(REPO_ROOT / "dataset" / "sample_output.csv")
    predicted = read_rows(EVAL_DIR / "sample_predictions.csv")
    traces = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in (CODE_DIR / "traces").glob("case_*.json")}
    universe = IdentityUniverse(ds.users, ds.audit_log)
    index = build_evidence_index(ds, universe)
    activity_ids = {a.resource_id for a in ds.activity}
    for cid in expected:
        t = traces.setdefault(cid, {})
        t["_resource_in_text"] = {s["resource_id"]: (s["resource_id"] in index.mentions or s["resource_id"] in activity_ids)
                                  for s in json.loads(expected[cid]["steps_json"])}
    m = sample_metrics(expected, predicted, ds, traces)
    att = attribution_metrics(expected, traces, [r["case_id"] for r in ds.sample_requests], [r["case_id"] for r in ds.requests])
    summary = json.loads((CODE_DIR / "traces" / "_run_summary.json").read_text(encoding="utf-8"))
    custom = run_custom_cases(args.from_cache)
    det = determinism_check() if args.determinism else None
    usage = usage_summary(EVAL_DIR / "usage_ledger.json", len(ds.requests))
    write_reports(m, att, custom, det, usage, summary,
                  ambiguity_appendix(traces, [r["case_id"] for r in ds.requests]))
    results = {"samples": {k: v for k, v in m.items() if k not in ("per_case",)}, "attribution": att, "custom": custom,
               "determinism": det, "usage": usage}
    (EVAL_DIR / "evaluation_results.json").write_text(json.dumps(results, indent=1, default=str) + "\n", encoding="utf-8")
    print(f"status {pct(m['status_correct'], m['cases'])}; steps recall {pct(m['steps_matched'], m['steps_expected'])}; "
          f"precision {pct(m['steps_matched'], m['steps_predicted'])}; sequence {pct(m['sequence_exact'], m['cases'])}; "
          f"custom {sum(c['pass'] for c in custom)}/{len(custom)} pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
