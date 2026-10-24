"""REVERTED experiment E1 (kept for reproducibility, not imported by the pipeline): resolve timing-only AMBIGUOUS pairs from event ordering (label INFERRED_ORDER).

Hypothesis under test, not an assumed fact: a case's deletion-worker rows can be tied to it through an
ordering relationship with resources that are independently anchored to the case by user_id.
Hard links are never overridden; a pair stays AMBIGUOUS whenever the rule is not unique.

Strategy A (anchored monotone matching, account deletes only, the one justified one-to-one structure):
  anchor(case) = earliest deletion-worker row, inside the case's burst window, on a resource CONFIRMED to
  the case by customer_reference / subject_reference == user_id. Within one ambiguous batch, if every
  unassigned case has a distinct anchor and there are as many rows as cases, sort cases by anchor and rows
  by time and assign the k-th row to the k-th case, provided each row precedes its case's anchor and lies
  in that case's window. Anything else leaves the whole batch AMBIGUOUS.
Strategy B (burst segmentation):
  sort deletion-worker rows by time and start a new burst after a gap of more than BURST_GAP. A burst whose
  rows include CONFIRMED resources of exactly one deletion case assigns its AMBIGUOUS rows to that case
  (at most one account delete per case).
"""
from __future__ import annotations

from datetime import timedelta

from agent.attribution import AttributionResult, CaseContext
from agent.loaders import parse_ts
from agent.schema import ActivityClaim, Attribution, Edge

# First trough of the unlabelled histogram of gaps between consecutive deletion-worker rows
# (24 gaps in 25-30 min vs 66 in 20-25 min and 48 in 30-40 min). Fixed before scoring.
BURST_GAP = timedelta(minutes=25)
WORKER = "deletion_worker"


def _worker_rows(activity: list[ActivityClaim]) -> list[ActivityClaim]:
    return sorted((a for a in activity if a.source_system == WORKER), key=lambda a: (a.event_time, a.activity_id))


def _anchored(attrs: list[Attribution]) -> set[str]:
    return {a.resource_id for a in attrs if a.label == "CONFIRMED" and a.in_scope
            and any(e.kind == "HARD" and e.identifier.startswith("user_id:") for e in a.edges)}


def strategy_a(cases: list[CaseContext], result: AttributionResult, activity: list[ActivityClaim]) -> dict[str, tuple]:
    by_id = {c.case_id: c for c in cases}
    rows = _worker_rows(activity)
    account_rows = {a.resource_id: a for a in rows if a.event_type == "DELETE_RECORD" and a.target_system == "account_service"}
    out: dict[str, tuple] = {}
    for batch in result.batches:
        amb = batch["ambiguous"]
        if not amb:
            continue
        cs = sorted({c for c, _ in amb})
        rs = sorted({r for _, r in amb}, key=lambda r: (account_rows[r].event_time, r))
        if len(cs) != len(rs):
            continue
        anchors = {}
        for cid in cs:
            lo, hi = by_id[cid].burst_window
            own = _anchored(result.per_case.get(cid, []))
            times = [parse_ts(a.event_time) for a in rows if a.resource_id in own and lo <= parse_ts(a.event_time) <= hi]
            if not times:
                break
            anchors[cid] = min(times)
        if len(anchors) != len(cs) or len(set(anchors.values())) != len(cs):
            continue
        order = sorted(cs, key=lambda c: anchors[c])
        pairs = list(zip(order, rs))
        if all((c, r) in set(map(tuple, amb)) and parse_ts(account_rows[r].event_time) < anchors[c] for c, r in pairs):
            for c, r in pairs:
                out[r] = (c, account_rows[r], f"strategy A: batch {batch['batch']} rank {order.index(c) + 1} of "
                                              f"{len(order)} by user_id-anchored burst start")
    return out


def strategy_b(cases: list[CaseContext], result: AttributionResult, activity: list[ActivityClaim]) -> dict[str, tuple]:
    rows = _worker_rows(activity)
    deletion = {c.case_id for c in cases if c.is_deletion}
    confirmed = {}
    for cid, attrs in result.per_case.items():
        if cid in deletion:
            for a in attrs:
                if a.label == "CONFIRMED" and a.in_scope:
                    confirmed.setdefault(a.resource_id, set()).add(cid)
    ambiguous: dict[str, set[str]] = {}
    for cid, attrs in result.per_case.items():
        for a in attrs:
            if a.label == "AMBIGUOUS":
                ambiguous.setdefault(a.resource_id, set()).add(cid)
    has_account = {cid for cid, attrs in result.per_case.items()
                   if any(a.label in ("CONFIRMED", "INFERRED") and a.in_scope and a.system_id == "account_service" for a in attrs)}
    bursts, cur = [], []
    for a in rows:
        if cur and parse_ts(a.event_time) - parse_ts(cur[-1].event_time) > BURST_GAP:
            bursts.append(cur)
            cur = []
        cur.append(a)
    if cur:
        bursts.append(cur)
    out: dict[str, tuple] = {}
    for bi, burst in enumerate(bursts):
        owners = set().union(*(confirmed.get(a.resource_id, set()) for a in burst))
        if len(owners) != 1:
            continue
        (cid,) = owners
        accounts = [a for a in burst if a.resource_id in ambiguous and a.target_system == "account_service"
                    and a.event_type == "DELETE_RECORD"]
        for a in burst:
            if a.resource_id not in ambiguous or cid not in ambiguous[a.resource_id]:
                continue
            if a in accounts and (cid in has_account or len(accounts) > 1):
                continue
            out[a.resource_id] = (cid, a, f"strategy B: burst {bi} (gap > {int(BURST_GAP.total_seconds() // 60)} min "
                                          f"segmentation) holds CONFIRMED resources of this case only")
    return out


def apply(result: AttributionResult, assignments: dict[str, tuple]) -> int:
    """Turn AMBIGUOUS pairs into INFERRED_ORDER for the assigned case; drop the rival pairs."""
    changed = 0
    for rid, (cid, row, why) in sorted(assignments.items()):
        for case_id, attrs in result.per_case.items():
            for i, a in enumerate(attrs):
                if a.resource_id != rid or a.label != "AMBIGUOUS":
                    continue
                if case_id == cid:
                    attrs[i] = Attribution(case_id, rid, a.system_id, "INFERRED_ORDER", why, True, a.scope_reason,
                                           a.edges + [Edge(case_id, rid, "ORDER", row.activity_id, "ordering",
                                                           row.event_time, "SECONDARY")])
                    changed += 1
                else:
                    result.eliminated.setdefault(case_id, []).append(
                        {"resource_id": rid, "activity_id": row.activity_id, "reason": f"assigned to {cid} by {why[:10]}"})
            result.per_case[case_id] = [a for a in attrs if not (a.resource_id == rid and a.label == "AMBIGUOUS")]
    return changed
