"""Round 6 R1: conditional-rule branch audit (skills: orchestrate-test-fix edge-case-testing, input-tracing).

For every deletion_rules.csv rule whose condition is not "Always", each attributed eval and sample resource of that
category is classified into the condition's TRUE / FALSE / UNKNOWN branch from its raw observation fields at audit_at
(classifiers below, one per rule, stated in the report), next to the decision the pipeline took. For branches with eval
instances but no sample coverage, up to 3 eval instances are listed with the raw evidence lines that mention the
resource, for a hand check. Writes code/evaluation/rule_branch_audit.json.
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "code"))
from agent import pipeline  # noqa: E402
from agent.identity import EMAIL_RE  # noqa: E402
from agent.loaders import parse_ts  # noqa: E402

CONDITIONAL = {"SUPPORT_MESSAGE": "rule_05", "SUPPORT_ATTACHMENT": "rule_06", "ANALYTICS_PROFILE": "rule_07",
               "PRODUCT_TELEMETRY": "rule_08", "EMAIL_DELIVERY_LOG": "rule_11", "SECURITY_AUDIT_LOG": "rule_12",
               "LEGAL_HOLD_RECORD": "rule_13", "BACKUP_ARCHIVE": "rule_14", "ACCOUNT_TOMBSTONE": "rule_15"}
CLASSIFIER = {
    "rule_05": "TRUE if the message text names an order/agent/other context (\"order\", \"agent\", \"ticket\") or is "
               "already [REDACTED] in a shared thread; FALSE if text exists without such context; UNKNOWN if no text",
    "rule_06": "TRUE if every person identifier in the attachment record (uploaded_by, requester_*) belongs to the subject; "
               "FALSE if one belongs to someone else; UNKNOWN if none observed",
    "rule_07": "TRUE if the state's exposure is DIRECT_IDENTIFIERS or STABLE_REFERENCE_ONLY; FALSE if NO_IDENTIFIERS; "
               "UNKNOWN otherwise (incl. ABSENT records)",
    "rule_08": "as rule_07", "rule_11": "TRUE if the record exposes an email; FALSE if observed without one",
    "rule_12": "TRUE if the latest admissible retention_until is after audit_at; FALSE if at or before; UNKNOWN if absent",
    "rule_13": "TRUE if retained_until is after audit_at (hold active); FALSE if at or before; UNKNOWN if absent",
    "rule_14": "TRUE unless evidence says restore access cannot be blocked (no such field exists in the data); "
               "restore state reported separately",
    "rule_15": "TRUE if the tombstone's account_status is DELETED; FALSE if another status; UNKNOWN if absent",
}


def classify(rule: str, cid: str, res: dict, obs: list, req: dict, subject_ids: set[str]) -> str:
    audit = parse_ts(req["audit_at"])
    adm = [o for o in obs if parse_ts(o.file_captured_at) <= audit]
    f = {}
    for o in sorted(adm, key=lambda o: (o.time, o.obs_id)):
        f.update({k: v for k, v in o.fields.items() if v not in (None, "")})
    exposure = res["state"].get("exposure")
    if rule == "rule_05":
        text = str(f.get("message_text") or f.get("message_excerpt") or "")
        if not text:
            return "UNKNOWN"
        return "TRUE" if any(w in text.lower() for w in ("order", "agent", "ticket")) or text.strip() == "[REDACTED]" else "FALSE"
    if rule == "rule_06":
        ids = {str(f[k]).lower() for k in ("uploaded_by", "requester_email") if f.get(k) and EMAIL_RE.fullmatch(str(f[k]))}
        if not ids:
            return "UNKNOWN"
        return "TRUE" if ids <= subject_ids else "FALSE"
    if rule in ("rule_07", "rule_08"):
        return {"DIRECT_IDENTIFIERS": "TRUE", "STABLE_REFERENCE_ONLY": "TRUE", "NO_IDENTIFIERS": "FALSE"}.get(exposure, "UNKNOWN")
    if rule == "rule_11":
        return "TRUE" if exposure == "DIRECT_IDENTIFIERS" else ("FALSE" if exposure in ("NO_IDENTIFIERS", "REDACTED") else "UNKNOWN")
    if rule in ("rule_12", "rule_13"):
        v = f.get("retention_until") or f.get("retained_until")
        if not v or parse_ts(str(v)) is None:
            return "UNKNOWN"
        return "TRUE" if parse_ts(str(v)) > audit else "FALSE"
    if rule == "rule_14":
        return "TRUE"
    if rule == "rule_15":
        s = f.get("account_status")
        return "UNKNOWN" if not s else ("TRUE" if str(s).upper() == "DELETED" else "FALSE")
    return "UNKNOWN"


def evidence_lines(ds_root: Path, manifest: dict, file_id: str, rid: str, limit: int = 2) -> list[str]:
    p = ds_root / manifest[file_id]
    if p.suffix.lower() in (".png", ".pdf"):
        return [f"({p.suffix} evidence; see the screenshot/PDF extraction for {rid})"]
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if rid in line:
            out.append(line.strip()[:400])
            if len(out) >= limit:
                break
    return out


def main() -> int:
    rr = pipeline.run(REPO / "dataset", from_cache=True)
    ref_ops: dict[tuple, list] = defaultdict(list)
    for r in csv.DictReader(open(REPO / "dataset" / "sample_output.csv", encoding="utf-8")):
        for st in json.loads(r["steps_json"]):
            ref_ops[(r["case_id"], st["resource_id"])].append(st["operation"])
    agree: dict[tuple, Counter] = defaultdict(Counter)
    eval_ids = {r["case_id"] for r in rr.dataset.requests}
    reqs = {r["case_id"]: r for r in rr.dataset.all_requests}
    manifest = {r["file_id"]: r["file_path"] for r in rr.dataset.manifest}
    counts: dict[tuple, Counter] = defaultdict(Counter)
    decisions: dict[tuple, Counter] = defaultdict(Counter)
    examples: dict[tuple, list] = defaultdict(list)
    for cid, o in sorted(rr.outcomes.items(), key=lambda kv: int(kv[0].rsplit("_", 1)[1])):
        subject_ids = {v.lower() for v in (o.trace.get("subject", {}).get("identifiers", {}).get("email", {}) or {})}
        for res in o.trace.get("resources", []):
            rule = CONDITIONAL.get(res["category"])
            if not rule:
                continue
            branch = classify(rule, cid, res, rr.index.by_resource.get(res["resource_id"], []), reqs[cid], subject_ids)
            split = "eval" if cid in eval_ids else "sample"
            counts[(rule, res["category"], branch)][split] += 1
            if split == "sample":
                mine = sorted(x for x in res["operations"] if x)
                theirs = sorted(ref_ops.get((cid, res["resource_id"]), []))
                agree[(rule, res["category"], branch)]["agree" if mine == theirs else
                                                       ("not_in_reference" if not theirs else "differs")] += 1
            ops = [x for x in res["operations"] if x] or [f"no step ({res.get('note') or 'excluded'})"]
            decisions[(rule, res["category"], branch)][" + ".join(ops)] += 1
            if split == "eval" and len(examples[(rule, res["category"], branch)]) < 3:
                pf = res.get("proof_file_id")
                examples[(rule, res["category"], branch)].append(
                    {"case_id": cid, "resource_id": res["resource_id"], "operations": ops, "proof": pf,
                     "state": {k: res["state"].get(k) for k in ("presence", "exposure", "restore_state", "proof_basis")},
                     "evidence": evidence_lines(REPO / "dataset", manifest, pf, res["resource_id"]) if pf else []})
    rows = []
    for (rule, cat, branch), c in sorted(counts.items()):
        rows.append({"rule": rule, "category": cat, "branch": branch, "eval": c["eval"], "sample": c["sample"],
                     "sample_vs_reference": dict(agree[(rule, cat, branch)]),
                     "decisions": dict(decisions[(rule, cat, branch)]),
                     "spot_check_candidates": examples[(rule, cat, branch)] if c["eval"] and not c["sample"] else []})
    present = {r["rule"] for r in rows}
    for cat, rule in CONDITIONAL.items():
        if rule not in present:
            rows.append({"rule": rule, "category": cat, "branch": "-", "eval": 0, "sample": 0, "decisions": {},
                         "spot_check_candidates": []})
    out = {"classifiers": CLASSIFIER, "rows": sorted(rows, key=lambda r: (r["rule"], r["branch"]))}
    (REPO / "code" / "evaluation" / "rule_branch_audit.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    for r in out["rows"]:
        print(f"{r['rule']} {r['category']:20} {r['branch']:8} eval={r['eval']:4} sample={r['sample']:3} {r['decisions']}"
              f" sample_vs_ref={r.get('sample_vs_reference')}{'  SPOT-CHECK' if r['spot_check_candidates'] else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
