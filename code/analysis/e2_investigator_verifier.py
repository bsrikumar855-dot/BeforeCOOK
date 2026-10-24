"""NOT SHIPPED experiment E2 (kept for reproducibility; not invoked by code/main.py).

Deterministic verifier for investigator proposals (experiment E2). The model proposes; this code decides.

A proposal {case_id, resource_id, decision, cited_tool_results, rationale} is ACCEPTED only if all hold:
  1. decision is ASSIGN and (case_id, resource_id) is an unresolved pair of this batch;
  2. every cited id exists in this batch's tool trace, and at least one is cited;
  3. a cited result shows an observation of resource_id carrying an identifier owned by the case's subject
     (shared identifier or user_id link);
  4. the resource is not hard-linked to another person, and the acceptance does not assign one resource to two
     cases or a second account delete to one case.
Ordering-only support is rejected: experiment E1 measured anchored ordering at 4 correct / 1 incorrect on the
samples, so an ordering argument cannot meet the zero-incorrect bar.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Decision:
    case_id: str
    resource_id: str
    accepted: bool
    reason: str


def _supports(entry: dict, resource_id: str, subject_users: set[str]) -> bool:
    rows = entry.get("result")
    if not isinstance(rows, list):
        return False
    for row in rows:
        if not isinstance(row, dict) or row.get("resource_id", resource_id) != resource_id:
            continue
        if set(row.get("owner_user_ids", [])) & subject_users:
            return True
        if any(set(i.get("owner_user_ids", [])) & subject_users for i in row.get("identifiers", [])):
            return True
    return False


def verify(proposals: list[dict], batch: dict, trace: list[dict], subject_users: dict[str, set[str]],
           resource_owners: dict[str, list[str]], account_resources: set[str],
           cases_with_account: set[str]) -> list[Decision]:
    by_id = {t["id"]: t for t in trace}
    open_pairs = {(c, r) for c, r in batch["pairs"]}
    decisions: list[Decision] = []
    taken: dict[str, str] = {}
    accounts_taken: set[str] = set(cases_with_account)
    for p in proposals:
        cid, rid = p.get("case_id"), p.get("resource_id")
        if p.get("decision") != "ASSIGN":
            decisions.append(Decision(cid, rid, False, "model answered AMBIGUOUS"))
            continue
        if (cid, rid) not in open_pairs:
            decisions.append(Decision(cid, rid, False, "not an unresolved pair of this batch"))
            continue
        cited = p.get("cited_tool_results") or []
        if not cited or any(c not in by_id for c in cited):
            decisions.append(Decision(cid, rid, False, "cites nothing, or a tool result that does not exist"))
            continue
        owners = set(resource_owners.get(rid, []))
        if owners and not owners & subject_users.get(cid, set()):
            decisions.append(Decision(cid, rid, False, f"contradicts hard link to {sorted(owners)}"))
            continue
        if rid in taken and taken[rid] != cid:
            decisions.append(Decision(cid, rid, False, f"double assignment (already {taken[rid]})"))
            continue
        if rid in account_resources and cid in accounts_taken:
            decisions.append(Decision(cid, rid, False, "case already has an account delete (one-to-one)"))
            continue
        if not any(_supports(by_id[c], rid, subject_users.get(cid, set())) for c in cited):
            decisions.append(Decision(cid, rid, False, "no cited result shows a shared identifier or user_id link"))
            continue
        taken[rid] = cid
        if rid in account_resources:
            accounts_taken.add(cid)
        decisions.append(Decision(cid, rid, True, "identity link in cited tool results"))
    return decisions
