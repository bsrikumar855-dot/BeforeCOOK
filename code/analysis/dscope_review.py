"""rc6 Priority 1: D-SCOPE review of the single-request subjects (read-only; no model calls).

For every eval case whose subject has no other request in the dataset and that excludes a record as OUT_OF_SCOPE,
list every source that mentions the excluded record (evidence files from the parsed index, the OCR text of every
screenshot pass, system_activity rows), place each one against the case's [requested_at, audit_at], and check whether
any of the subject's identifiers (every email, phone, hash, user id and name the identity step resolved) occur in the
same source, and in the same line for line-oriented text. The output is the evidence table behind DESIGN.md rc6-P1.

  python code/analysis/dscope_review.py      (writes code/evaluation/dscope_review.json)
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))

from agent import pipeline  # noqa: E402
from agent.loaders import DEFAULT_DATASET_DIR, parse_ts  # noqa: E402


def side(t, lo, hi) -> str:
    return "before_requested_at" if t < lo else "after_audit_at" if t > hi else "in_window"


def main() -> int:
    rr = pipeline.run(DEFAULT_DATASET_DIR, from_cache=True)
    ds, index = rr.dataset, rr.index
    sample_ids = {r["case_id"] for r in ds.sample_requests}
    traces = {c: o.trace for c, o in rr.outcomes.items()}
    subjects = defaultdict(set)
    for cid, t in traces.items():
        for u in (t.get("subject") or {}).get("user_ids") or []:
            subjects[u].add(cid)
    ocr = defaultdict(str)
    for p in sorted((ROOT / "code" / "cache" / "ocr").glob("*.json")):
        e = json.loads(p.read_text(encoding="utf-8"))
        ocr[e["file_id"]] += "\n" + e["text"]
    activity = defaultdict(list)
    for a in ds.activity:
        activity[a.resource_id].append(a)
    rows = []
    for cid in sorted(traces, key=lambda c: int(c.rsplit("_", 1)[1])):
        t = traces[cid]
        if cid in sample_ids or not t.get("request"):
            continue
        users = (t.get("subject") or {}).get("user_ids") or []
        others = sorted({o for u in users for o in subjects[u]} - {cid})
        oos = [a for a in t.get("attribution", []) if not a.get("in_scope") and a.get("scope_reason")]
        if others or not oos:
            continue
        r = t["request"]
        lo, hi = parse_ts(r["requested_at"]), parse_ts(r["audit_at"])
        idents = sorted({v for kind, vals in (t["subject"].get("identifiers") or {}).items() for v in vals})
        burst = (parse_ts(r["reported_at"]), r["reported_at"])
        for a in oos:
            rid = a["resource_id"]
            sources = []
            for fid in sorted(index.mentions.get(rid, set()) | {f for f, txt in ocr.items() if rid in txt}):
                f = index.files.get(fid)
                cap = parse_ts(f.captured_at) if f and f.captured_at else None
                text = (index.text.get(fid) or "") + ocr.get(fid, "")
                same_line = sorted({i for ln in text.splitlines() if rid in ln for i in idents if i and i in ln})
                in_file = sorted({i for i in idents if i and i in text})
                sources.append({"source": fid, "kind": "file", "path": f.path if f else None,
                                "captured_at": f.captured_at if f else None,
                                "side": side(cap, lo, hi) if cap else "unknown",
                                "subject_identifiers_same_line": same_line, "subject_identifiers_in_file": in_file})
            for act in sorted(activity.get(rid, []), key=lambda x: x.event_time):
                at = parse_ts(act.event_time)
                sources.append({"source": act.activity_id, "kind": "activity", "event_time": act.event_time,
                                "side": side(at, lo, hi), "event_type": act.event_type, "source_system": act.source_system,
                                "reported_result": act.reported_result,
                                "hours_from_reported_at": round((at - burst[0]).total_seconds() / 3600, 1),
                                "subject_identifiers_in_details": sorted(i for i in idents if i and i in act.details)})
            edges = [{"source": e["source_id"], "identifier": e["identifier"].split(":", 1)[0], "time": e["time"],
                      "side": side(parse_ts(e["time"]), lo, hi)} for e in a.get("edges", [])]
            admissible_links = [s for s in sources if s["kind"] == "file" and s["side"] != "after_audit_at"
                                and s["subject_identifiers_same_line"]]
            rows.append({"case_id": cid, "status": t["status"], "requested_at": r["requested_at"], "audit_at": r["audit_at"],
                         "reported_at": r["reported_at"], "reported_status": r["reported_status"],
                         "resource_id": rid, "system_id": a.get("system_id"), "label": a["label"],
                         "identity_edges": edges, "sources": sources,
                         "admissible_identity_link_in_window": [s["source"] for s in admissible_links if s["side"] == "in_window"],
                         "admissible_identity_link_before_request": [s["source"] for s in admissible_links
                                                                     if s["side"] == "before_requested_at"]})
    out = {"cases": sorted({r["case_id"] for r in rows}, key=lambda c: int(c.rsplit("_", 1)[1])),
           "records": len(rows), "rows": rows}
    (ROOT / "code" / "evaluation" / "dscope_review.json").write_text(json.dumps(out, indent=1, sort_keys=True) + "\n",
                                                                     encoding="utf-8")
    for r in rows:
        print(r["case_id"], r["resource_id"], "edges:", [(e["source"], e["side"]) for e in r["identity_edges"]],
              "| in-window admissible link:", r["admissible_identity_link_in_window"],
              "| pre-request link:", r["admissible_identity_link_before_request"],
              "| activity:", [(s["source"], s["side"], s["reported_result"]) for s in r["sources"] if s["kind"] == "activity"],
              "| files:", [(s["source"], s["side"]) for s in r["sources"] if s["kind"] == "file"])
    print(f"{len(out['cases'])} cases, {len(rows)} excluded records")
    return 0


if __name__ == "__main__":
    sys.exit(main())
