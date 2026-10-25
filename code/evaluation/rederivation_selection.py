"""Round 6 R2: choose 24 eval cases for independent re-derivation, by strata, BEFORE any independent answer exists.

Strata are detected mechanically from the run's traces and the dataset; within a stratum the lowest case number wins,
then status quotas (8 COMPLETE, 12 INCOMPLETE, 2 NOT_APPLICABLE, plus case_55 and case_63) are filled in case order.
Writes code/evaluation/independent_rederivation/selection.json.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "code" / "evaluation" / "independent_rederivation"


def num(cid: str) -> int:
    return int(cid.rsplit("_", 1)[1])


def main() -> int:
    eval_ids = [r["case_id"] for r in csv.DictReader(open(REPO / "dataset" / "deletion_requests.csv", encoding="utf-8"))]
    out_rows = {r["case_id"]: r for r in csv.DictReader(open(REPO / "output.csv", encoding="utf-8"))}
    traces = {c: json.loads((REPO / "code" / "traces" / f"{c}.json").read_text(encoding="utf-8")) for c in eval_ids}
    manifest = {r["file_id"]: r["file_path"] for r in csv.DictReader(open(REPO / "dataset" / "evidence_files.csv", encoding="utf-8"))}
    images = {f for f, p in manifest.items() if p.endswith(".png")}
    activity = list(csv.DictReader(open(REPO / "dataset" / "system_activity.csv", encoding="utf-8")))
    sync_like = {}
    for a in activity:
        if a["event_type"] in ("SYNC_RECORD", "RESTORE_BACKUP", "INDEX_RECORD"):
            sync_like.setdefault(a["resource_id"], []).append(a["event_time"])

    def status(c):
        return out_rows[c]["deletion_status"]

    strata = {
        "replica_drift": lambda c, t: any(len({o for o in r["state"].get("observation_ids", [])}) > 1 and "replica" in json.dumps(r).lower()
                                          and not r["compliant"] for r in t.get("resources", [])),
        "post_delete_sync_or_restore": lambda c, t: any(
            r["resource_id"] in sync_like and any(ts > t["request"]["reported_at"] and ts <= t["request"]["audit_at"]
                                                  for ts in sync_like[r["resource_id"]]) for r in t.get("resources", [])),
        "historical_identifier_only": lambda c, t: bool(t.get("subject", {}).get("resolution")) and all(
            "user_audit_log" in x for x in t["subject"]["resolution"]),
        "screenshot_dependent": lambda c, t: any(s["evidence_id"] in images for s in json.loads(out_rows[c]["steps_json"])),
        "tombstone": lambda c, t: any(r["category"] == "ACCOUNT_TOMBSTONE" for r in t.get("resources", [])),
        "legal_hold_or_retention": lambda c, t: any(r["category"] == "LEGAL_HOLD_RECORD" for r in t.get("resources", [])),
        "a1_inferred_account": lambda c, t: any(a["label"] == "INFERRED_CLAIM" for a in t.get("attribution", [])),
    }
    chosen: dict[str, str] = {"case_55": "fixed: undeterminable restore control (C5)", "case_63": "fixed: NO_ATTRIBUTABLE_RESOURCE"}
    for name, pred in strata.items():
        for c in sorted(eval_ids, key=num):
            if c not in chosen and status(c) != "NOT_APPLICABLE" and pred(c, traces[c]):
                chosen[c] = f"stratum: {name}"
                break
        else:
            chosen[f"_missing_{name}"] = "no eval case matches"
    quota = {"COMPLETE": 8, "INCOMPLETE": 12 + 2, "NOT_APPLICABLE": 2}       # +2 = case_55, case_63 (INCOMPLETE)
    have = {s: sum(1 for c in chosen if not c.startswith("_") and status(c) == s) for s in quota}
    for c in sorted(eval_ids, key=num):
        s = status(c)
        if c not in chosen and have[s] < quota[s]:
            chosen[c] = "status fill"
            have[s] += 1
    picked = {c: why for c, why in chosen.items() if not c.startswith("_")}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "selection.json").write_text(json.dumps({
        "selected_before_any_independent_answer": True,
        "cases": dict(sorted(picked.items(), key=lambda kv: num(kv[0]))),
        "status_counts": {s: sum(1 for c in picked if status(c) == s) for s in quota},
        "strata_without_match": [k for k in chosen if k.startswith("_")]}, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(picked, indent=1), {s: sum(1 for c in picked if status(c) == s) for s in quota})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
