"""Round 6 R2: compare the independent re-derivation with output.csv, mechanically.

Reads code/evaluation/independent_rederivation/<case>.json (written by an independent agent that did not read code/,
traces, output.csv or our docs) and output.csv. Per case: status match, exact step match on (phase, system_id,
resource_id, operation, state, evidence_id), and the missing / extra steps. Triage is added by hand in triage.json
(OUR_BUG / SUBAGENT_ERROR / AMBIGUOUS_SPEC with quoted raw evidence). Writes independent_rederivation/comparison.json.
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DIR = REPO / "code" / "evaluation" / "independent_rederivation"
FIELDS = ("phase", "system_id", "resource_id", "operation", "state", "evidence_id")


def key(s: dict) -> tuple:
    return tuple(s.get(f, "") for f in FIELDS)


def main() -> int:
    selection = json.loads((DIR / "selection.json").read_text(encoding="utf-8"))["cases"]
    ours = {r["case_id"]: r for r in csv.DictReader(open(REPO / "output.csv", encoding="utf-8"))}
    per_case, totals = {}, Counter()
    for cid in selection:
        p = DIR / f"{cid}.json"
        if not p.exists():
            per_case[cid] = {"missing_file": True}
            totals["missing_files"] += 1
            continue
        theirs = json.loads(p.read_text(encoding="utf-8"))
        ts, tsteps = theirs["deletion_status"], theirs.get("steps", [])
        os_, osteps = ours[cid]["deletion_status"], json.loads(ours[cid]["steps_json"])
        tk, ok = Counter(map(key, tsteps)), Counter(map(key, osteps))
        match = sum((tk & ok).values())
        why = {key(s): s.get("justification", "") for s in tsteps}
        per_case[cid] = {"status_ours": os_, "status_independent": ts, "status_match": os_ == ts,
                         "steps_ours": len(osteps), "steps_independent": len(tsteps), "steps_matched": match,
                         "sequence_exact": [key(s) for s in osteps] == [key(s) for s in tsteps] and os_ == ts,
                         "only_ours": [list(k) for k in (ok - tk).elements()],
                         "only_independent": [list(k) + [why.get(k, "")] for k in (tk - ok).elements()],
                         "independent_uncertain": theirs.get("uncertain", [])}
        totals["cases"] += 1
        totals["status_match"] += os_ == ts
        totals["sequence_exact"] += per_case[cid]["sequence_exact"]
        totals["steps_ours"] += len(osteps)
        totals["steps_independent"] += len(tsteps)
        totals["steps_matched"] += match
    out = {"totals": dict(totals), "cases": per_case}
    (DIR / "comparison.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    t = totals
    print(f"cases {t['cases']} (missing files {t['missing_files']}); status {t['status_match']}/{t['cases']}; "
          f"steps matched {t['steps_matched']} of ours {t['steps_ours']} / of theirs {t['steps_independent']}; "
          f"sequence exact {t['sequence_exact']}/{t['cases']}")
    for cid, c in per_case.items():
        if c.get("missing_file") or (c["status_match"] and not c["only_ours"] and not c["only_independent"]):
            continue
        print(f"\n{cid}: ours {c['status_ours']} ({c['steps_ours']}) vs independent {c['status_independent']} "
              f"({c['steps_independent']}), matched {c['steps_matched']}")
        for s in c["only_ours"]:
            print("   only ours :", s)
        for s in c["only_independent"]:
            print("   only indep:", s[:6], "|", s[6][:140])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
