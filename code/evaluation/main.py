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


REPO_PATH = REPO_ROOT

def redact(text: str, *dirs) -> str:
    """Captured subprocess output without local paths (temp dirs, the repo, the home directory)."""
    for d in sorted({str(x) for x in dirs} | {str(REPO_PATH), str(Path.home())}, key=len, reverse=True):
        text = text.replace(d, "<" + ("repo" if d == str(REPO_PATH) else "home" if d == str(Path.home()) else "tmp") + ">")
        text = text.replace(d.replace("\\", "\\\\"), "<path>")
    return text


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
                tags.append({"resource_id": rid, "tag": "NOT_DERIVABLE" if not trace.get("_resource_in_text", {}).get(rid)
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
        link = "direct" if kinds & {"HARD", "CATALOG"} else ("inferred" if a and a["label"].startswith("INFERRED")
                                                               and kinds & {"SOURCE_REFERENCE"} else
                                                               ("timing-only" if "TIMING" in kinds else "none"))
        label = (a["label"] if a else "NOT_ATTRIBUTED") + ("" if not a or a["in_scope"] else "/OUT_OF_SCOPE")
        correct = bool(a and (a["label"] == "CONFIRMED" or a["label"].startswith("INFERRED")) and a["in_scope"])
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
            if not a["label"].startswith("INFERRED") or not a["in_scope"]:
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
    sample_non_amb = sum(v for k, v in label_counts(sample_ids).items() if k == "CONFIRMED" or k.startswith("INFERRED")
                         and "/" not in k)
    eval_pairs = sum(label_counts(eval_ids).values())
    eval_non_amb = sum(v for k, v in label_counts(eval_ids).items() if k == "CONFIRMED" or k.startswith("INFERRED")
                       and "/" not in k)
    return {
        "labelled_pairs": len(rows), "correct": sum(r["correct"] for r in rows), "rows": rows,
        "by_link": {k: dict(v) for k, v in sorted(by_link.items())},
        "labels_samples": label_counts(sample_ids), "labels_eval": label_counts(eval_ids),
        "coverage_samples": (sample_non_amb, sample_pairs), "coverage_eval": (eval_non_amb, eval_pairs),
        "inferred_evaluable": inferred_eval, "inferred_unevaluable": inferred_unevaluable,
        "inferred_recall": (sum(1 for r in no_direct if r["label"].startswith("INFERRED") and r["correct"]), len(no_direct)),
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
            out[name] = {"returncode": proc.returncode, "pythonhashseed": opt["seed"], "stdout": redact(proc.stdout.strip(), tmp)[-300:],
                         "stderr": redact(proc.stderr.strip(), tmp)[-300:], "model_calls": len(ledger.get("calls", [])),
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
    """Usage of the run that wrote output.csv, built from the cache entries that run used (C8).

    The run's ledger lists every cache entry it read (task, key) and every live call. Each entry carries the provider's
    usage metadata of the call that produced it; a batched call is counted once per call_id."""
    import yaml
    cfg = yaml.safe_load((CODE_DIR / "config" / "models.yaml").read_text(encoding="utf-8"))
    prices = cfg.get("paid_list_price_usd_per_million_tokens", {})
    ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else {}
    calls: dict[str, dict] = {}
    for c in ledger.get("calls", []):
        calls[c["call_id"]] = {**c, "origin": "live"}
    used_entries: set[Path] = set()
    entries_by_model = Counter()
    for h in ledger.get("cache_hits", []):
        p = CODE_DIR / "cache" / h["task"] / f"{h['key']}.json"
        if not p.exists():
            continue
        used_entries.add(p)
        meta = json.loads(p.read_text(encoding="utf-8")).get("usage_metadata") or {}
        entries_by_model[(h["task"], meta.get("model"))] += 1
        if meta.get("call_id") and meta["call_id"] not in calls:
            calls[meta["call_id"]] = {**meta, "task": h["task"], "origin": "cache"}
    tok = ("input_tokens", "output_tokens", "thinking_tokens", "total_tokens")

    def add(counter: Counter, c: dict) -> None:
        counter["calls"] += 1
        counter["live" if c["origin"] == "live" else "cached"] += 1
        for k in tok:
            counter[k] += int(c.get(k) or 0)

    per_model, per_slot = defaultdict(Counter), defaultdict(Counter)
    timestamps = Counter()
    for c in calls.values():
        add(per_model[(c.get("provider"), c.get("model"), c.get("task", "?"))], c)
        add(per_slot[(c.get("provider"), c.get("key_slot"))], c)
        timestamps["UNKNOWN" if (c.get("timestamp") or "UNKNOWN") == "UNKNOWN" else "recorded"] += 1
    rows = []
    for (prov, model, task), m in sorted(per_model.items(), key=lambda kv: str(kv[0])):
        price = prices.get(model)
        est = None if price is None else (m["input_tokens"] * price["input"] +
                                          (m["output_tokens"] + m["thinking_tokens"]) * price["output"]) / 1e6
        rows.append({"provider": prov, "model": model, "task": task, **m,
                     "entries_used": entries_by_model.get((task, model), 0), "estimated_paid_usd": est})
    total = Counter()
    for r in rows:
        for k in ("calls", "live", "cached") + tok:
            total[k] += r.get(k, 0)
    priced = [r["estimated_paid_usd"] for r in rows if r["estimated_paid_usd"] is not None]
    # Experiment calls: model-reply cache entries this run did not read (fixtures, model comparison, E2, re-extraction).
    experiments: dict[tuple, Counter] = defaultdict(Counter)
    seen_exp: set[str] = set()
    for p in sorted((CODE_DIR / "cache").rglob("*.json")):
        if p.parent.name == "ocr" or p in used_entries:
            continue
        meta = json.loads(p.read_text(encoding="utf-8")).get("usage_metadata") or {}
        cid = meta.get("call_id")
        if not cid or cid in calls or cid in seen_exp:
            continue
        seen_exp.add(cid)
        e = experiments[(p.parent.name, meta.get("model"))]
        e["calls"] += 1
        e["tokens"] += int(meta.get("total_tokens") or 0)
    ocr_root = CODE_DIR / "cache" / "ocr"
    ocr = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(ocr_root.glob("*.json"))] if ocr_root.exists() else []
    return {"rows": rows, "total": dict(total), "requests": n_requests,
            "estimated_paid_usd": sum(priced), "unpriced_models": sorted({r["model"] for r in rows if r["estimated_paid_usd"] is None}),
            "per_slot": {f"{p}/{s}": dict(c) for (p, s), c in sorted(per_slot.items(), key=lambda kv: str(kv[0]))},
            "timestamps": dict(timestamps), "cache_entries_used": len(used_entries),
            "live_calls_this_run": len(ledger.get("calls", [])), "cache_hits_this_run": len(ledger.get("cache_hits", [])),
            "events": ledger.get("events", []),
            "ocr": {"engines": sorted({o["engine"] for o in ocr}), "images": len({o["image_sha256"] for o in ocr}),
                    "passes": len({o.get("pass") for o in ocr})},
            "experiments": {f"{t}/{m}": dict(c) for (t, m), c in sorted(experiments.items(), key=lambda kv: str(kv[0]))}}


# ----------------------------------------------------------------------------------- report
def write_reports(m: dict, att: dict, custom: list[dict], det: dict | None, usage: dict, summary: dict,
                  appendix: list[str], traces: dict | None = None, ds_sample_rows: list | None = None) -> None:
    traces, ds_sample_rows = traces or {}, ds_sample_rows or []
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
    L += ["### Residual sample mismatches, step by step (generated from the reference-vs-output diff)", "",
          "| Case | Missing (expected) / extra (predicted) step | Tag | Why |", "|---|---|---|---|"]
    residual_tags = Counter()
    for cid, pc in m["per_case"].items():
        tag_of = {t["resource_id"]: t for t in pc["root_causes"]}
        for kind, steps in (("missing", pc.get("missing", [])), ("extra", pc.get("extra", []))):
            for st in steps:
                t = tag_of.get(st["resource_id"]) or tag_of.get("*") or {"tag": "UNTAGGED", "why": ""}
                residual_tags[t["tag"]] += 1
                ev = st["evidence_id"] or '""'
                L.append(f"| {cid} | {kind}: {st['phase']} {st['operation']} {st['system_id']}/{st['resource_id']} "
                         f"evidence={ev} | {t['tag']} | {t['why'][:110]} |")
    L += ["", "Residual steps by tag: " + ", ".join(f"{k} {v}" for k, v in sorted(residual_tags.items())), ""]
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
    L += ["### Screenshot id grounding (C9, multi-pass since C10; local OCR, no model calls)", "",
          f"Engine: {', '.join(og.get('engines', [])) or 'UNKNOWN'}. A resource id read by the vision model is kept only when the",
          "id appears in the united OCR text of four passes (grayscale base, 2x, 2x psm 11, 3x autocontrast psm 6; CONSTANTS.md).",
          "Letters and digits match exactly; an underscore may be read as a space, a period or nothing. OCR-only ids are",
          "listed, never added (Tesseract also reads dates and mangled digits as ids).", "",
          "| Image | VLM ids | OCR ids | Grounded | Ungrounded (dropped) | OCR-only (not added) |", "|---|---|---|---|---|---|"]
    for fid, g in sorted(og.get("per_image", {}).items()):
        L.append(f"| {fid} | {len(g['vlm_ids'])} | {len(g['ocr_ids'])} | {len(g['grounded'])} | "
                 f"{', '.join(g['ungrounded']) or '—'} | {', '.join(g['ocr_only']) or '—'} |")
    L += ["", f"Ungrounded ids dropped: {len(og.get('ungrounded', []))}. History: single-pass C9 dropped 5 visible ids "
          "(audit_record_002, search_1874, data_flow_9846, export_1258, export_1261): base resolution missed small text and the "
          "matcher required a literal underscore. An independent re-check found it; C10 fixed both. Neither version changed an "
          "output row or a sample step.", "",
          "NOT_DERIVABLE_FROM_INPUTS (missing sample steps no parsed source names; not pursued): `account_004`, "
          "`audit_record_6645`, `audit_record_5569`.", "",
          "Rows emitted as INCOMPLETE `[]` with `reason_code=NO_ATTRIBUTABLE_RESOURCE` (DESIGN.md C5/M2(b)): "
          + (", ".join(summary["eval"].get("no_attributable_resource", [])) or "none") + " (eval); "
          + (", ".join(summary.get("samples", {}).get("no_attributable_resource", [])) or "none") + " (samples).", ""]
    cov = _category_coverage()
    if cov:
        L += ["### Data-category coverage (C6)", "",
              "Resources per category in the eval traces, and in the sample answers (joined to the sample traces).",
              "Categories with no sample coverage are exercised by fixtures X6–X13 (section 4).", "",
              "| Category | Eval resources | Eval resources with steps | Sample-answer resources |", "|---|---|---|---|"]
        L += [f"| {c} | {v['eval_resources']} | {v['eval_with_steps']} | {v['sample_answer_resources']} |"
              for c, v in cov["categories"].items()]
        L += ["", f"UNMAPPED eval resources: {len(cov['unmapped'])}. No sample coverage: {', '.join(cov['no_sample_coverage'])}.", ""]
    L += [f"## 4. Custom evaluation cases (X1–X{len(custom)}, fixtures in `code/evaluation/custom_cases/`)", "",
          "| Case | Failure mode tested | Expected | Predicted | Steps exact | Result |", "|---|---|---|---|---|---|"]
    for c in custom:
        L.append(f"| {c['case']} | {c['failure_mode']} | {c['expected_status']} | {c['predicted_status']} | "
                 f"{c['steps_ok']} | {'PASS' if c['pass'] else 'FAIL'}{(' — ' + c['error'][:120]) if c['error'] else ''} |")
    L += ["", "Why each case exists (expected output, why naive logic fails, spec rule) is documented in DESIGN.md §10 and in "
          "`code/evaluation/custom_cases/build_fixtures.py`.", ""]
    adv = [c for c in custom if c["failure_mode"].startswith("ADVERSARIAL")]
    L += ["### Safety (C7)", "",
          "Adversarial fixtures (expected outputs written from the spec, not from the code). The model's only outputs",
          "are an intent label from a closed enum and enum-validated screenshot fields; status and steps are computed by",
          "code, so an instruction in a request or in evidence has no field it could write to (threat model: DESIGN.md §7).", "",
          "| Case | Attack | Expected | Predicted | Intent path | Result |", "|---|---|---|---|---|---|"]
    L += [f"| {c['case']} | {c['failure_mode'].split(': ', 1)[1]} | {c['expected_status']} | {c['predicted_status']} | "
          f"{c['intent_path']} | {'PASS' if c['pass'] else 'FAIL'} |" for c in adv]
    L += ["", "Robustness bug found by X17/X18 and fixed (C7a): a request_text over 5,000 characters was an input issue, so a",
          "deletion request became INCOMPLETE `[]` and the self-contradiction gate stopped the whole run (exit 2); an empty",
          "text was sent to the model. Now an empty text is NOT_APPLICABLE without a call, and a long text is classified on",
          "its first 5,000 characters (`truncated_to` in the trace). 0 eval rows changed.", ""]
    cmp_path = EVAL_DIR / "intent_model_comparison.json"
    if cmp_path.exists():
        cmp = json.loads(cmp_path.read_text(encoding="utf-8"))
        L += ["## 4b. Intent model comparison (C4)", "",
              f"{cmp['texts']} texts: the 32 sample requests (all DELETION per sample_output.csv) and the 10 eval texts the",
              "pipeline labels NON_DELETION (corroborated by CLASSIFY_REQUEST rows; that truth is an inference, not a label).", "",
              "| Model | Accuracy | DELETION | NON_DELETION | Calls | Tokens |", "|---|---|---|---|---|---|"]
        L += [f"| {m} | {r['accuracy']} | {r['by_truth']['DELETION']} | {r['by_truth']['NON_DELETION']} | {r['calls']} | {r['tokens']} |"
              for m, r in cmp["models"].items()]
        L += ["", f"Agreement {cmp['agreement']}. Kept `gemini-3.5-flash-lite`: equal accuracy at fewer tokens. Key probes "
              "(aliases only) are in `code/evaluation/models_verified.json`.", ""]
    av = EVAL_DIR / "attribution_variants.json"
    if av.exists():
        R = json.loads(av.read_text(encoding="utf-8"))
        names = {"baseline": "baseline", "a1": "+A1 claim consistency", "a1a2": "+A1+A2 offset bands",
                 "a1a2a3": "+A1+A2+A3 joint", "a1a2a3o": "+A1+A2+A3+ordering", "a1o": "+A1+ordering (extra)"}
        L += ["## 4e. Attribution variants (Round 5, `code/evaluation/attribution_variants.py`)", "",
              "Each variant runs end to end (`ORCHESTRATE_ATTRIBUTION=<variant>`); sample columns are generated from the",
              f"reference-vs-output diff. Timing-only sample pairs (AMBIGUOUS at baseline): {len(R['timing_only_pairs'])}.", "",
              "| Variant | Status | Precision | Recall | Sequence | Timing-only resolved / correct | New sample assignments: correct / incorrect | Prev.-correct steps broken | Prev.-correct statuses changed | Eval rows changed | Eval status flips | Eval AMBIGUOUS pairs / resources / cases | Eval assignments | Label-free checks |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for v, x in R["variants"].items():
            d, lf = x["eval"]["denominators"], x["label_free"]
            ok = not (lf["one_to_one_resources_assigned_twice"] or lf["assigned_accounts_inconsistent_with_case"]
                      or lf["assigned_followups_outside_band"] or lf["spec_audit_violations"] or lf["exit"])
            L.append(f"| {names.get(v, v)} | {x['samples']['status']} | {x['samples']['precision']} | {x['samples']['recall']} | "
                     f"{x['samples']['sequence']} | {x['timing_only_pairs']['resolved']} / {x['timing_only_pairs']['correct']} | "
                     f"{len(x['correct'])} / {len(x['incorrect'])} | {len(x['previously_correct_steps_broken'])} | "
                     f"{len(x['previously_correct_statuses_changed'])} | {x['eval']['rows_changed']} | {len(x['eval']['status_flips'])} | "
                     f"{d['ambiguous_pairs']} / {d['ambiguous_resources']} / {d['ambiguous_cases']} | {d['assignments_total']} "
                     f"{d['assignments']} | {'pass' if ok else lf} |")
        L += ["", "Incorrect resolutions and breakages, verbatim from the diff: " + "; ".join(
            f"{names.get(v, v)}: incorrect {x['incorrect']}, broken {x['previously_correct_steps_broken']}, statuses "
            f"{x['previously_correct_statuses_changed']}" for v, x in R["variants"].items()
            if x["incorrect"] or x["previously_correct_steps_broken"]) + ".", "",
              "Shipped: **+A1** (the only variant with 0 incorrect resolutions, 0 broken steps, 0 status changes). It "
              "made 0 COMPLETE↔INCOMPLETE flips on eval, so no status flip needs state evidence.", ""]
        f = R.get("a1_falsification", {})
        if f:
            L += ["A1 falsification report (label-free):", "",
                  f"- Deletion cases: {f['deletion_cases']}; with an account hard-linked independently: {f['cases_with_hard_linked_account']}.",
                  f"- A1 on hard-linked accounts: {f['A1_on_hard_linked_accounts']['status_result_counts']}, inconsistent: "
                  f"{len(f['A1_on_hard_linked_accounts']['inconsistent'])}.",
                  f"- Candidate edges removed by A1 (eval + samples): {f['candidate_edges_removed_by_A1']}; CLAIM_CONFLICT cases: "
                  f"{len(f['claim_conflict_cases'])}."]
            for w in ("window_-3h_+3h", "window_-3h_+1h"):
                x = f[w]
                L.append(f"- {w}: deletion cases with 0 consistent account candidates before elimination "
                         f"{len(x['zero_consistent_before_elimination'])}, after elimination "
                         f"{len(x['zero_consistent_after_elimination'])}; free candidates per case (5 = 5+, cases without a "
                         f"hard-linked account) before A1 {x['free_candidates_per_case_before_A1']}, after A1 "
                         f"{x['consistent_candidates_per_case_after_A1']}.")
            L.append("")
        bands = R.get("bands", {})
        if bands:
            L += ["A2 offset bands (from the +A1+A2 run; CONFIRMED pairs only):", "",
                  "| Group | n | Median (min) | MAD (s) | Min / max (min) | Band (min) | CONFIRMED outside own band | Multimodal | Used |",
                  "|---|---|---|---|---|---|---|---|---|"]
            for g, b in sorted(bands.items(), key=lambda kv: -kv[1]["n"]):
                L.append(f"| {g} | {b['n']} | {b['median_s'] / 60:.1f} | {b['mad_s']:.0f} | {b['min_s'] / 60:.1f} / "
                         f"{b['max_s'] / 60:.1f} | [{b['low_s'] / 60:.1f}, {b['high_s'] / 60:.1f}] | {b['outside']} | "
                         f"{b['multimodal']} | {'yes' if b['used'] else 'no: ' + b['bandless_reason']} |")
            L.append("")
    amb_accounts = []
    for cid, t in traces.items():
        if not (t.get("intent") or {}).get("is_deletion") or cid in {r["case_id"] for r in ds_sample_rows}:
            continue
        accts = [a for a in t.get("attribution", []) if a.get("system_id") == "account_service" and a["in_scope"]]
        if accts and not any(a["label"] == "CONFIRMED" or a["label"].startswith("INFERRED") for a in accts) \
                and any(a["label"] == "AMBIGUOUS" for a in accts):
            amb_accounts.append(cid)
    L += [f"Eval deletion cases whose primary account is still AMBIGUOUS (no account step): {len(amb_accounts)}.", ""]
    rb, ce, dsn = (EVAL_DIR / "rule_branch_audit.json", EVAL_DIR / "citation_exposure.json",
                   EVAL_DIR / "distribution_sanity.json")
    if rb.exists():
        R6 = json.loads(rb.read_text(encoding="utf-8"))
        L += ["## 4f. Round 6 eval-side audits", "",
              "Conditional-rule branches (`code/evaluation/rule_branch_audit.py`; branch from raw fields at audit_at; "
              "sample columns compare our operations with the reference for the same resource):", "",
              "| Rule | Category | Branch | Eval | Sample | Sample vs reference | Our decisions |", "|---|---|---|---|---|---|---|"]
        for r in R6["rows"]:
            L.append(f"| {r['rule']} | {r['category']} | {r['branch']} | {r['eval']} | {r['sample']} | "
                     f"{r.get('sample_vs_reference') or '-'} | {r['decisions'] or '-'} |")
        L.append("")
    if ce.exists():
        C6 = json.loads(ce.read_text(encoding="utf-8"))
        L += [f"Citation exposure (`code/evaluation/citation_exposure.py`): cited eval steps by rule-C path "
              f"{C6['cited_steps_by_path']}; fallback (full-snapshot) citations in {len(C6['fallback_cases'])} cases; "
              f"with a stronger admissible capture: {len(C6['fallback_with_a_stronger_proof'])}.", ""]
    if dsn.exists():
        D6 = json.loads(dsn.read_text(encoding="utf-8"))
        ref_p, ev_p = D6["reference_samples"], D6["eval"]
        L += [f"Distribution sanity (`code/evaluation/distribution_sanity.py`): steps per case reference "
              f"{ref_p['steps_per_case']} vs eval {ev_p['steps_per_case']}; VERIFY_RECORD_PRESENT share "
              f"{ref_p['share_verify_present']} vs {ev_p['share_verify_present']}; restore {ref_p['share_restore_steps']} vs "
              f"{ev_p['share_restore_steps']}; flow {ref_p['share_flow_steps']} vs {ev_p['share_flow_steps']}; eval cases with "
              f"operations unseen in the reference: {len(D6['eval_cases_with_operations_never_in_sample_reference'])}; INCOMPLETE "
              f"findings without their remediation and check: {len(D6['incomplete_findings_without_required_remediation_or_check'])}.", ""]
    rd = EVAL_DIR / "independent_rederivation"
    if (rd / "comparison.json").exists():
        CMP = json.loads((rd / "comparison.json").read_text(encoding="utf-8"))
        TRI = json.loads((rd / "triage.json").read_text(encoding="utf-8")) if (rd / "triage.json").exists() else {}
        t = CMP["totals"]
        L += ["Independent re-derivation (24 stratified eval cases chosen before any answer existed; an agent that read only "
              "the problem statement and dataset/):", "",
              f"- Status agreement {t['status_match']}/{t['cases']}; steps of ours matched exactly {t['steps_matched']}/"
              f"{t['steps_ours']}; steps of the independent answer matched {t['steps_matched']}/{t['steps_independent']}; rows "
              f"identical {t['sequence_exact']}/{t['cases']}.",
              f"- Triage of every disagreement: {TRI.get('counts', {})} (details with quoted raw evidence in "
              "`code/evaluation/independent_rederivation/triage.json`)."]
        for it in TRI.get("items", []):
            L.append(f"  - {it['case_id']} {it['triage']}: {'; '.join(it['steps'])}")
        L.append("")
    cp = EVAL_DIR / "coupling_probes.json"
    if cp.exists():
        pr = json.loads(cp.read_text(encoding="utf-8"))
        L += ["## 4c. Dataset-coupling probes (G1, `code/evaluation/coupling_probes.py`)", "",
              "Perturbed copies of dataset/ and code/cache/ in a temp dir, each run with --from-cache in a child process;",
              "outputs compared after mapping ids back and canonicalising step order with the spec ordering rule.", "",
              "| Probe | Result | Eval rows changed | Sample rows changed | Fixture |", "|---|---|---|---|---|"]
        for k in ("a_id_bijection", "b_row_order", "c_timestamp_format", "d_email_case", "e_drop_evidence_file"):
            r = pr.get(k, {})
            fx = {kk: vv for kk, vv in r.get("fixture", {}).items() if kk not in ("prefixes", "unrenamed_examples")}
            extra = (f"; dependent cases {r['dependent_cases']}, changed outside them {r['changed_outside_dependents']}"
                     if k.startswith("e_") else "")
            L.append(f"| {k} | {'PASS' if r.get('pass') else 'FAIL'} | {r.get('eval_rows_changed')} | "
                     f"{r.get('sample_rows_changed')} | {fx}{extra} |")
        mr = pr.get("media_and_rules", {})
        L += ["", f"Cases depending on PNG/PDF content (renamed through the cached extraction, OCR text and a PDF text hook): "
              f"{mr.get('cases_depending_on_png_or_pdf')}/{mr.get('of_cases')}; changed under (a): {mr.get('png_pdf_cases_changed_under_a')}.",
              f"Corpus literals in executable code: {len([h for h in pr.get('literals', {}).get('hits', []) if h['kind'] == 'code'])}. "
              f"Rules firing on exactly one case (reviewed: general mechanisms, rare in this data, none copied wording): "
              f"{len(mr.get('single_case_rules', []))}.", "",
              "Defects found: (1) the probe's own renamer missed ids joined to another token in a model reply "
              "(`user_11_account_5762`); fixed in the probe. (2) Traceability: an elimination entry named the other person but "
              "not the evidence that linked them, so an INFERRED pick's dependency on that evidence was invisible (dropping "
              "evidence_08 changed case_78 and case_102 outside the trace-derived dependents). Eliminations now record their "
              "`sources`; 0 output rows changed.", ""]
    sa = EVAL_DIR / "spec_audit.json"
    if sa.exists():
        a = json.loads(sa.read_text(encoding="utf-8"))
        nc = a["negative_control"]
        L += ["## 4d. Literal spec audit (G5, `code/evaluation/spec_audit.py`)", "",
              "Constants typed from problem_statement.md, nothing imported from the code. Checks header, 518 rows and ids,",
              "statuses, NOT_APPLICABLE = [], the 7 step keys, phase/operation/state triples, consecutive step_no, ordering,",
              "COMPLETE = verification only and cited, INCOMPLETE future steps uncited, citations exist and are <= audit_at,",
              "INCOMPLETE [] only with NO_ATTRIBUTABLE_RESOURCE.", "",
              f"- output.csv: {sum(a['status_counts'].values())} rows {a['status_counts']}, {a['steps']} steps; violations: {len(a['violations'])}",
              f"- INCOMPLETE [] rows: {a['incomplete_empty']}",
              f"- Negative control: {sum(nc.values())}/{len(nc)} injected defect classes caught ({', '.join(nc)})", ""]
    sec = EVAL_DIR / "security_audit.json"
    if sec.exists():
        sa = json.loads(sec.read_text(encoding="utf-8"))
        L += ["### Security pass (G8, `code/evaluation/security_audit.py`)", "",
              f"Every regex in code/main.py and code/agent/** run on ten 1 MB adversarial lines in a child process, bound "
              f"{sa['bound_seconds']} s per input:", "",
              "| Where | Pattern | Worst time (s) | Slowest input | Result |", "|---|---|---|---|---|"]
        for r in sa["regex"]:
            pat = (r.get("pattern") or "").replace("|", "\\|")
            L.append(f"| {r['where']} | `{pat}` | {r.get('max_seconds')} | {r.get('slowest_input')} | {r['result'][:40]} |")
        L += ["", "Seven patterns were quadratic before G8 (each > 30 s on a 1 MB line): the email pattern, the log key=value "
              "pattern, both screenshot id token patterns, and three lazy PDF block patterns. Fixed by RFC 5321 bounds, "
              "word-start lookbehinds, bounded id prefixes and a linear block scan; output.csv and every trace unchanged.", "",
              "Hostile evidence fixtures (temporary dataset copies, run end to end):", "",
              "| Fixture | Rejected | Exit | Rows | Cases failed closed | Checks |", "|---|---|---|---|---|---|"]
        for name, f in sa["fixtures"].items():
            L.append(f"| {name} | {f['rejected']} | {f['exit']} | {f['rows']} | {f['cases_failed_closed']} | "
                     f"{'all pass' if f['pass'] else f['checks']} |")
        L.append("")
    L += ["## 5. Determinism", ""]
    if det:
        for name, r in det.items():
            L.append(f"- {name}: PYTHONHASHSEED={r.get('pythonhashseed')}, exit {r['returncode']}, live model calls {r['model_calls']}, files compared {r['files_compared']}"
                     + (f", identical to run_1: {r['identical_to_run_1']}, differences: {r['differences']}" if "identical_to_run_1" in r else ""))
    else:
        L.append("- Not run in this invocation (use `--determinism`).")
    L += ["", "## 6. Failures found and improvements made", ""] + NARRATIVE
    L += [""] + appendix
    for target in (EVAL_DIR, REPO_ROOT / "evaluation"):     # code/evaluation and the zip-root evaluation/ the spec names
        target.mkdir(exist_ok=True)
        (target / "evaluation_report.md").write_text("\n".join(L) + "\n", encoding="utf-8", newline="\n")

    t = usage["total"]
    n = max(1, usage["requests"])
    usd = usage["estimated_paid_usd"]
    unpriced = usage["unpriced_models"]
    U = ["# Usage report", "",
         "Model usage behind the final full-dataset run that produced `output.csv`, built by `code/evaluation/main.py`",
         "from the cache entries that run read (listed in its ledger, `code/evaluation/usage_ledger.json`) plus its live",
         "calls. Every cache entry stores the provider's usage metadata (`usage_metadata`: provider, model, served model",
         "version, key alias, input/output/thinking tokens, timestamp) of the call that produced it. A batched intent call",
         "produced up to 40 entries; it is counted once (by call_id).", "",
         f"- Requests in the eval run: {usage['requests']} (the pipeline also classifies the 32 sample requests in the same batches)",
         f"- Cache entries read by this run: {usage['cache_entries_used']}; live calls in this run: {usage['live_calls_this_run']}",
         f"- Distinct provider calls behind the run: {t.get('calls', 0)} ({t.get('cached', 0)} replayed from cache, "
         f"{t.get('live', 0)} live)", "",
         "## Per model", "",
         "| Provider | Model | Task | Calls | Cache entries used | Input tokens | Output tokens | Thinking tokens | Total tokens | Est. paid-list cost (USD) |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for r in usage["rows"]:
        cost = "UNKNOWN (no published price)" if r["estimated_paid_usd"] is None else f"{r['estimated_paid_usd']:.4f}"
        U.append(f"| {r['provider']} | {r['model']} | {r['task']} | {r['calls']} | {r['entries_used']} | {r['input_tokens']} | "
                 f"{r['output_tokens']} | {r['thinking_tokens']} | {r['total_tokens']} | {cost} |")
    U += [f"| **all** | | | {t.get('calls', 0)} | {usage['cache_entries_used']} | {t.get('input_tokens', 0)} | "
          f"{t.get('output_tokens', 0)} | {t.get('thinking_tokens', 0)} | {t.get('total_tokens', 0)} | {usd:.4f}"
          f"{' + UNKNOWN' if unpriced else ''} |",
          f"| local OCR | {', '.join(usage['ocr']['engines']) or 'not used'} | screenshot id grounding | {usage['ocr']['images']} images x {usage['ocr']['passes']} passes | "
          f"| 0 | 0 | 0 | 0 | 0.0000 (local, no API) |", "",
          "## Per key alias", "",
          "Aliases only (`primary`, `fallback_1` …); key values never appear in any file. Keys of one Google Cloud project",
          "share a quota pool and the API does not reveal the project, so pools are UNKNOWN.", "",
          "| Provider / alias | Calls | Live | From cache | Total tokens |", "|---|---|---|---|---|"]
    U += [f"| {k} | {v.get('calls', 0)} | {v.get('live', 0)} | {v.get('cached', 0)} | {v.get('total_tokens', 0)} |"
          for k, v in usage["per_slot"].items()]
    U += ["", "## Totals and cost", "",
          f"- Total tokens: {t.get('total_tokens', 0)}; average per eval request: {t.get('total_tokens', 0) / n:.1f} "
          f"(input {t.get('input_tokens', 0) / n:.1f}, output {t.get('output_tokens', 0) / n:.1f}); calls per request {t.get('calls', 0) / n:.3f}",
          "- Actual cost: $0. Every call used the Gemini API free tier; the OCR engine runs locally.",
          f"- Estimated cost at paid list price: ${usd:.4f} total, ${usd / n:.6f} per request"
          + (f" (excluding {', '.join(unpriced)}: no published price, UNKNOWN)" if unpriced else "") + ".",
          f"- Price source: {PRICE_SOURCE}",
          f"- Fallback / quota / validation events in this run: {dict(Counter(e.get('event') for e in usage['events'])) or 'none'}",
          f"- Call timestamps: {usage['timestamps']}. Calls made before C8 did not store their time; their entries carry",
          "  `timestamp: UNKNOWN` and `timestamp_upper_bound` (the commit that first added the entry). Calls made after C8",
          "  store the provider-call time.", "",
          "## Disclosures", "",
          "- **Lost early ledger.** The first live run (2026-10-24) failed 12 intent batches (`gemini-2.5-flash-lite` HTTP 404 on",
          "  every key, `gemini-2.5-flash` 20 requests/day cap) and two vision replies failed the output validator and were",
          "  retried. Those failed or rejected calls produced no cache entry, and their ledger was overwritten by the rerun, so",
          "  their tokens are not in this report (UNKNOWN; at most 12 intent and 2 vision calls). Intent was switched to",
          "  `gemini-3.5-flash-lite` (DESIGN.md D-MODEL-IDS).",
          "- The provider may serve a different version than the requested id: the served `model_version` is kept in every",
          "  entry (for example, requests to `gemini-3.5-flash` reported `gemini-3.6-flash`).",
          "- The ledger of this run lists cache entries, not new calls: a `--from-cache` run makes no network call.", "",
          "## Experiment calls (not part of the dataset run)", "",
          "Model-reply cache entries the final run did not read, one row per cache folder and model, counted once per call:",
          "custom and adversarial fixtures, the C4 intent-model comparison, the E2 investigator turns and screenshot",
          "re-extractions. Groq was never called (no GROQ_API_KEY).", "",
          "| Cache folder / model | Calls | Total tokens |", "|---|---|---|"]
    U += [f"| {k} | {v.get('calls', 0)} | {v.get('tokens', 0)} |" for k, v in usage["experiments"].items()]
    for target in (EVAL_DIR, REPO_ROOT / "evaluation"):     # code/evaluation and the zip-root evaluation/ the spec names
        target.mkdir(exist_ok=True)
        (target / "usage_report.md").write_text("\n".join(U) + "\n", encoding="utf-8", newline="\n")


def _category_coverage() -> dict:
    sys.path.insert(0, str(CODE_DIR / "analysis"))
    import category_coverage  # noqa: E402
    category_coverage.main()
    return json.loads((EVAL_DIR / "category_coverage.json").read_text(encoding="utf-8"))


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
                  ambiguity_appendix(traces, [r["case_id"] for r in ds.requests]), traces, ds.sample_requests)
    results = {"samples": {k: v for k, v in m.items() if k not in ("per_case",)}, "attribution": att, "custom": custom,
               "determinism": det, "usage": usage}
    (EVAL_DIR / "evaluation_results.json").write_text(json.dumps(results, indent=1, default=str) + "\n", encoding="utf-8")
    print(f"status {pct(m['status_correct'], m['cases'])}; steps recall {pct(m['steps_matched'], m['steps_expected'])}; "
          f"precision {pct(m['steps_matched'], m['steps_predicted'])}; sequence {pct(m['sequence_exact'], m['cases'])}; "
          f"custom {sum(c['pass'] for c in custom)}/{len(custom)} pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
