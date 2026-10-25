"""F2: measure, per email rule, how many request / identity / evidence email values each rule would change.

Rules: case-insensitive matching (values with upper case), whitespace trimming, Gmail dot removal (NOT applied:
collisions it would create), plus-address collapsing (NOT applied: collisions), malformed-address rejection
(consecutive dots, leading/trailing dot in the local part, empty label), and non-ASCII handling (values with
non-ASCII characters; values whose .lower() turns into ASCII, i.e. lookalikes that lower() would equate).
Writes code/evaluation/email_edge_cases.json. No model calls.
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))
from agent.identity import EMAIL_RE, valid_email  # noqa: E402


def main() -> int:
    D = ROOT / "dataset"
    reqs = [r for f in ("deletion_requests.csv", "sample_deletion_requests.csv") for r in csv.DictReader(open(D / f, encoding="utf-8"))]
    ident = [r["email"] for r in csv.DictReader(open(D / "user_identities.csv", encoding="utf-8"))]
    ident += [r[s] for r in csv.DictReader(open(D / "user_audit_log.csv", encoding="utf-8")) if r["field_name"] == "email"
              for s in ("old_value", "new_value") if r[s]]
    evidence = []
    for p in sorted((D / "evidence").rglob("*")):
        if p.suffix.lower() in (".csv", ".json", ".log", ".sql", ".txt"):
            evidence += EMAIL_RE.findall(p.read_text(encoding="utf-8", errors="replace"))   # what the pipeline extracts
    groups = {"request_email": [r["email"] for r in reqs], "identity_tables": ident, "evidence_tokens": evidence}
    known = {e.strip().lower() for e in ident}

    def gmail_dots(e):
        local, _, dom = e.lower().partition("@")
        return local.replace(".", "") + "@" + dom if dom in ("gmail.com", "googlemail.com") else e.lower()

    def plus(e):
        local, _, dom = e.lower().partition("@")
        return local.split("+", 1)[0] + "@" + dom

    out = {}
    for name, vals in groups.items():
        c = Counter()
        for v in vals:
            c["values"] += 1
            c["has_upper_case"] += v != v.lower()
            c["has_surrounding_whitespace"] += v != v.strip()
            c["malformed"] += not valid_email(v.strip())
            c["non_ascii"] += not v.isascii()
            c["lower_folds_non_ascii_to_ascii"] += (not v.isascii()) and v.lower().isascii()
            c["has_plus"] += "+" in v.partition("@")[0]
            g = gmail_dots(v.strip())
            c["gmail_dot_removal_would_collide"] += g != v.strip().lower() and g in {gmail_dots(k) for k in known if k != v.strip().lower()}
            pl = plus(v.strip())
            c["plus_collapse_would_collide"] += pl != v.strip().lower() and pl in known
        out[name] = dict(c)
    # distinct identities that Gmail dot removal or plus collapsing would merge
    merged = Counter(gmail_dots(k) for k in known)
    out["identity_emails_merged_by_gmail_dot_removal"] = sum(1 for v in merged.values() if v > 1)
    (ROOT / "code" / "evaluation" / "email_edge_cases.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
