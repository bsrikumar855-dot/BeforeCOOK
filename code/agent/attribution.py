"""Resource attribution: which resources belong to which case (DESIGN.md D-ATTR, D-SCOPE).

Edges
  HARD / CATALOG  identity evidence (email, phone, email_hash, user_id; data-catalog links)
  TIMING          a deletion_worker row inside the case's burst window (reported_at +- 3h).
                  Timing only generates candidates; it never proves ownership on its own.
Constraints
  * a resource hard-linked to one person is eliminated from every other person's cases
  * one deletion-worker account delete per case per burst (the only one-to-one constraint)
Labels
  CONFIRMED  direct unambiguous identity link to the case's subject
  INFERRED   the edge appears in every maximum assignment of its batch (rationale recorded)
  AMBIGUOUS  several assignments are equally supported      -> no steps
  CONFLICT   hard links to different people                  -> no steps
"""
from __future__ import annotations

import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta

from .identity import IdentityUniverse, Subject
from .loaders import parse_ts
from .parsers import CATALOG_RE, EvidenceIndex
from .schema import ActivityClaim, Attribution, Edge

BURST_HALF_WIDTH = timedelta(hours=3)        # user decision: +-3h generates candidates only
LOOKUP_FIELDS = {"query", "lookup_key", "match_key", "subject_key", "scan_key", "correlation_key", "subject_ref"}
WORKER = "deletion_worker"


@dataclass
class CaseContext:
    case_id: str
    request: dict
    subject: Subject
    is_deletion: bool

    @property
    def burst_window(self):
        rep = parse_ts(self.request["reported_at"])
        return rep - BURST_HALF_WIDTH, rep + BURST_HALF_WIDTH

    def in_lifecycle(self, when: str) -> bool:
        t = parse_ts(when)
        return parse_ts(self.request["requested_at"]) <= t <= parse_ts(self.request["audit_at"])


@dataclass
class OwnerEdge:
    resource_id: str
    user_id: str
    source_id: str
    identifier: str
    time: str
    provenance: str
    field: str = ""

    @property
    def is_content(self) -> bool:
        """The identifier is part of the record itself, not the key it was looked up with or a catalog claim."""
        return self.provenance in ("SAME_RECORD", "MODEL_EXTRACTION") and self.field not in LOOKUP_FIELDS


@dataclass
class AttributionResult:
    per_case: dict[str, list[Attribution]] = field(default_factory=dict)
    eliminated: dict[str, list[dict]] = field(default_factory=dict)
    batches: list[dict] = field(default_factory=list)
    conflicts: dict[str, list[str]] = field(default_factory=dict)      # resource -> users
    content_conflicts: dict[str, dict] = field(default_factory=dict)   # resource -> {content ids, linked users}
    variant: str = "baseline"
    bands: dict[str, dict] = field(default_factory=dict)              # A2 offset bands (report + use)
    joint: dict = field(default_factory=dict)                         # A3 uniqueness report
    claim_conflicts: list[str] = field(default_factory=list)          # A1: no consistent candidate
    band_training: list[dict] = field(default_factory=list)           # A2: the CONFIRMED pairs the bands came from


def record_content(index: EvidenceIndex) -> dict[str, set[str]]:
    """resource -> identifiers found in the record's own fields (email/phone/hash/user_id), any known owner or none."""
    content: dict[str, set[str]] = defaultdict(set)
    for o in index.observations:
        for i in o.identifiers:
            if i.kind != "name" and i.field not in LOOKUP_FIELDS and i.provenance in ("SAME_RECORD", "MODEL_EXTRACTION"):
                content[o.resource_id].add(f"{i.kind}:{i.value}")
    return content


def resource_owners(index: EvidenceIndex, activity: list[ActivityClaim],
                    universe: IdentityUniverse) -> dict[str, list[OwnerEdge]]:
    """Every hard identity edge from all evidence (any capture time: discovery only, D11)."""
    owners: dict[str, list[OwnerEdge]] = defaultdict(list)
    for o in index.observations:
        hard = [i for i in o.identifiers if i.kind != "name"]
        content_users = {u for i in hard if i.field not in LOOKUP_FIELDS and i.provenance == "SAME_RECORD"
                         for u in universe.owners_of(i.kind, i.value)}
        for i in hard:
            users = universe.owners_of(i.kind, i.value)
            # A record's own identifiers outrank the key it was looked up with.
            if content_users and (i.field in LOOKUP_FIELDS or i.provenance == "TRACE_JOIN") and not users & content_users:
                continue
            prov = "MODEL_EXTRACTION" if o.extraction == "MODEL" else i.provenance
            for u in users:
                owners[o.resource_id].append(OwnerEdge(o.resource_id, u, o.file_id, f"{i.kind}:{i.value}", o.time,
                                                       prov, i.field))
    for a in activity:
        if a.event_type != "LINK_RESOURCE_IDENTITY":
            continue
        m = CATALOG_RE.search(a.details)
        if not m:
            continue
        rid, email, user = (m.group(1), m.group(3), m.group(4)) if m.group(1) else (m.group(5), m.group(7), m.group(8))
        for kind, value in (("email", email.lower()), ("user_id", user)):
            for u in universe.owners_of(kind, value):
                owners[rid].append(OwnerEdge(rid, u, a.activity_id, f"{kind}:{value}", a.event_time, "CATALOG_LINK"))
    return owners


def _max_matching(cases: list[str], adj: dict[str, list[str]], banned: tuple[str, str] | None = None) -> dict[str, str]:
    match_row: dict[str, str] = {}

    def augment(c: str, seen: set[str]) -> bool:
        for r in adj[c]:
            if (c, r) == banned or r in seen:
                continue
            seen.add(r)
            if r not in match_row or augment(match_row[r], seen):
                match_row[r] = c
                return True
        return False

    for c in cases:
        augment(c, set())
    return {c: r for r, c in match_row.items()}


# ---- Round 5 attribution filters (DESIGN.md A1-A3; constants in CONSTANTS.md) -----------------------------------
# A1: the deletion workflow's claim about a job derives from its worker's result for that job, so the subject's
# primary-profile account delete must carry a result consistent with the case's reported_status. Linkage only,
# never state. Applied to deletion_worker -> account_service DELETE_RECORD rows only.
CLAIM_RESULTS = {"COMPLETE": {"SUCCESS"}, "NOT_STARTED": {"FAILED"}, "PARTIALLY_COMPLETE": {"PARTIAL", "SUCCESS"}}
# A2: offset bands per (event_type, target_system, reported_result), learned at run time from CONFIRMED pairs only.
BAND_MIN_N = 10                    # a group needs at least this many CONFIRMED offsets
BAND_MAD_K = 4                     # half-width = max(BAND_MAD_K * MAD, BAND_MIN_HALF_S)
BAND_MIN_HALF_S = 120
MULTIMODAL_SIDE_SHARE = 0.10       # multimodal: an internal gap wider than the full band with >= 10% on each side
VARIANTS = {"baseline": set(), "a1": {"claim"}, "a1a2": {"claim", "offset"},
            "a1a2a3": {"claim", "offset", "joint"}, "a1a2a3o": {"claim", "offset", "joint", "ordering"},
            "a1o": {"claim", "ordering"}}
DEFAULT_VARIANT = "a1"                 # Round 5: the only variant that met the SHIP rule (DESIGN.md R5)
ENUMERATION_LIMIT = 200_000        # joint check: maximum matchings enumerated per batch before giving up (reported)


def band_training_pairs(cases: list[CaseContext], by_case: dict[str, dict[str, Attribution]],
                        bursts: dict[str, list[ActivityClaim]]) -> list[dict]:
    """Offsets of deletion-worker rows on resources CONFIRMED to the case by a direct identity link (HARD edge).
    Never INFERRED* pairs, never sample answers. A row in the bursts of two cases of the same subject is skipped."""
    subject_cases: dict[str, list[str]] = defaultdict(list)
    for c in cases:
        for u in c.subject.user_ids:
            subject_cases[u].append(c.case_id)
    req = {c.case_id: c.request for c in cases}
    pairs = []
    for c in cases:
        if not c.is_deletion:
            continue
        siblings = {s for u in c.subject.user_ids for s in subject_cases[u] if s != c.case_id}
        for rid, at in sorted(by_case[c.case_id].items()):
            if at.label != "CONFIRMED" or not any(e.kind == "HARD" for e in at.edges):
                continue
            for a in bursts[c.case_id]:
                if a.resource_id != rid or any(a in bursts.get(s, []) for s in siblings):
                    continue
                pairs.append({"case_id": c.case_id, "resource_id": rid, "activity_id": a.activity_id, "label": at.label,
                              "group": "|".join((a.event_type, a.target_system, a.reported_result)),
                              "offset_s": int((parse_ts(a.event_time) - parse_ts(req[c.case_id]["reported_at"])).total_seconds())})
    return pairs


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return float(s[n // 2]) if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def offset_bands(cases: list[CaseContext], by_case: dict[str, dict[str, Attribution]],
                 bursts: dict[str, list[ActivityClaim]]) -> dict[str, dict]:
    groups: dict[str, list[int]] = defaultdict(list)
    training = band_training_pairs(cases, by_case, bursts)
    for p in training:
        groups[p["group"]].append(p["offset_s"])
    out = {}
    for g, offs in sorted(groups.items()):
        med = _median(offs)
        mad = _median([abs(x - med) for x in offs])
        half = max(BAND_MAD_K * mad, BAND_MIN_HALF_S)
        low, high = int(med - half), int(med + half)
        outside = sorted(x for x in offs if not low <= x <= high)
        s = sorted(offs)
        side = max(1, int(MULTIMODAL_SIDE_SHARE * len(s) + 0.999))
        gaps = [(s[i + 1] - s[i], i + 1) for i in range(len(s) - 1)]
        multimodal = any(gap > 2 * half and side <= cut <= len(s) - side for gap, cut in gaps)
        reason = ("n < %d" % BAND_MIN_N if len(offs) < BAND_MIN_N else
                  "multimodal" if multimodal else
                  f"{len(outside)} CONFIRMED offsets outside their own band" if outside else "")
        out[g] = {"n": len(offs), "median_s": med, "mad_s": mad, "min_s": s[0], "max_s": s[-1], "half_s": half,
                  "low_s": low, "high_s": high, "outside": len(outside), "outside_offsets": outside[:10],
                  "multimodal": multimodal, "used": not reason, "bandless_reason": reason}
    out["_training"] = training                     # popped by attribute(); kept for the non-circularity test
    return out


def _components(adj: dict[str, list[str]]) -> list[tuple[list[str], list[str]]]:
    row_cases: dict[str, list[str]] = defaultdict(list)
    for cid, rids in adj.items():
        for r in rids:
            row_cases[r].append(cid)
    seen, comps = set(), []
    for start in sorted(adj):
        if start in seen:
            continue
        stack, cs, rs = [start], set(), set()
        while stack:
            cid = stack.pop()
            if cid in cs:
                continue
            cs.add(cid)
            for r in adj[cid]:
                if r not in rs:
                    rs.add(r)
                    stack.extend(row_cases[r])
        seen |= cs
        comps.append((sorted(cs), sorted(rs)))
    return comps


def forced_edges(adj: dict[str, list[str]], conflict: set[str]):
    """(forced, possible, components): forced = in every maximum one-to-one assignment; possible = in some."""
    forced, possible = set(), set()
    comps = _components(adj)
    for cs, _ in comps:
        size = len(_max_matching(cs, adj))
        for cid in cs:
            for r in adj[cid]:
                if len(_max_matching(cs, adj, banned=(cid, r))) < size:
                    forced.add((cid, r))
                    continue
                reduced = {c: [x for x in adj[c] if x != r] for c in cs if c != cid}
                if len(_max_matching([c for c in cs if c != cid], reduced)) + 1 == size:
                    possible.add((cid, r))
    return forced, possible | forced, comps


def verify_unique(adj: dict[str, list[str]], conflict: set[str], comps, forced: set) -> dict:
    """A3: enumerate every maximum one-to-one assignment per batch and confirm that the assigned pairs are exactly
    the pairs common to all of them; report batches with several solutions."""
    report = {"batches": len(comps), "unique_solution": 0, "several_solutions": 0, "not_enumerated": 0,
              "mismatch_with_forced": []}
    for bi, (cs, _) in enumerate(comps):
        size = len(_max_matching(cs, adj))
        sols, count = [], 0

        def rec(i: int, used: set[str], chosen: list[tuple[str, str]]) -> None:
            nonlocal count
            if count > ENUMERATION_LIMIT:
                return
            if len(chosen) + (len(cs) - i) < size:
                return
            if i == len(cs):
                if len(chosen) == size:
                    count += 1
                    sols.append(frozenset(chosen))
                return
            for r in adj[cs[i]]:
                if r not in used:
                    rec(i + 1, used | {r}, chosen + [(cs[i], r)])
            rec(i + 1, used, chosen)

        rec(0, set(), [])
        if count > ENUMERATION_LIMIT:
            report["not_enumerated"] += 1
            continue
        report["unique_solution" if len(set(sols)) == 1 else "several_solutions"] += 1
        common = frozenset.intersection(*sols) if sols else frozenset()
        mine = {p for p in forced if p[0] in cs}
        if set(common) != mine:
            report["mismatch_with_forced"].append(bi)
    return report


def provenance(cid, a, rid, use, rejection, rivals_of, adj, rows_of, eliminated, bands, group, offset_s, status_of,
               assigned) -> dict:
    """The provenance contract for an INFERRED_* pick: filters, claim vs status, band, hard links, rejected owners."""
    g = "|".join(group(a))
    b = bands.get(g)
    rejected = []
    for other in sorted(rivals_of):
        if other == cid or rid not in adj.get(other, []):
            continue
        why = rejection(other, rows_of.get(rid, a), use)
        rejected.append({"case_id": other, "reason": why or "every maximum one-to-one assignment gives this row "
                                                          "to the assigned case"})
    return {"filters": sorted(use), "account_result": a.reported_result, "case_status": status_of[cid],
            "event_group": g, "offset_s": offset_s(cid, a),
            "band": [b["low_s"], b["high_s"]] if b else None,
            "supporting_hard_links": sorted({s for e in eliminated.get(cid, []) for s in e.get("sources", [])}),
            "rejected_alternatives": rejected}


def order_tiebreak(cases: list[CaseContext], by_case: dict[str, dict[str, Attribution]], result,
                   rows_of: dict[str, ActivityClaim], bursts: dict[str, list[ActivityClaim]]) -> None:
    """E1-A anchored monotone ordering, used ONLY on residual AMBIGUOUS account batches (label INFERRED_ORDER)."""
    by_id = {c.case_id: c for c in cases}
    for batch in result.batches:
        amb = [tuple(p) for p in batch["ambiguous"] if p[0] not in batch["conflict_cases"]]
        if not amb:
            continue
        cs = sorted({c for c, _ in amb})
        rs = sorted({r for _, r in amb}, key=lambda r: (rows_of[r].event_time, r))
        if len(cs) != len(rs):
            continue
        anchors = {}
        for cid in cs:
            own = {r for r, at in by_case[cid].items() if at.label == "CONFIRMED" and at.in_scope
                   and any(e.kind == "HARD" and e.identifier.startswith("user_id:") for e in at.edges)}
            times = [parse_ts(x.event_time) for x in bursts[cid] if x.resource_id in own]
            if times:
                anchors[cid] = min(times)
        if len(anchors) != len(cs) or len(set(anchors.values())) != len(cs):
            continue
        order = sorted(cs, key=lambda c: anchors[c])
        pairs = list(zip(order, rs))
        if not all((c, r) in amb and parse_ts(rows_of[r].event_time) < anchors[c] for c, r in pairs):
            continue
        for c, r in pairs:
            at = by_case[c][r]
            by_case[c][r] = Attribution(c, r, at.system_id, "INFERRED_ORDER",
                                        f"ordering tiebreak: batch {batch['batch']} rank {order.index(c) + 1} of "
                                        f"{len(order)} by user_id-anchored burst start", True, at.scope_reason,
                                        at.edges, {"filters": ["ordering"], "rank": order.index(c) + 1,
                                                   "anchor": anchors[c].strftime("%Y-%m-%dT%H:%M:%SZ"),
                                                   "rejected_alternatives": [{"case_id": o, "reason": "ordering rank"}
                                                                             for o in cs if o != c]})
            for o in cs:
                if o != c and r in by_case[o] and by_case[o][r].label == "AMBIGUOUS":
                    del by_case[o][r]
                    result.eliminated.setdefault(o, []).append({"resource_id": r, "activity_id": rows_of[r].activity_id,
                                                                "reason": f"ordering tiebreak gave it to {c}",
                                                                "sources": []})


def attribute(cases: list[CaseContext], index: EvidenceIndex, activity: list[ActivityClaim],
              universe: IdentityUniverse, variant: str | None = None) -> AttributionResult:
    variant = variant or os.environ.get("ORCHESTRATE_ATTRIBUTION") or DEFAULT_VARIANT
    result = AttributionResult()
    result.variant = variant
    owners = resource_owners(index, activity, universe)
    owner_users = {rid: sorted({e.user_id for e in edges}) for rid, edges in owners.items()}
    result.conflicts = {rid: us for rid, us in owner_users.items() if len(us) > 1}
    content = record_content(index)

    worker_rows = sorted((a for a in activity if a.source_system == WORKER),
                         key=lambda a: (a.event_time, a.activity_id))
    by_case: dict[str, dict[str, Attribution]] = {c.case_id: {} for c in cases}

    def burst_rows(c: CaseContext) -> list[ActivityClaim]:
        lo, hi = c.burst_window
        cut = parse_ts(c.request["audit_at"])
        return [a for a in worker_rows if lo <= parse_ts(a.event_time) <= hi and parse_ts(a.event_time) <= cut]

    bursts = {c.case_id: burst_rows(c) if c.is_deletion else [] for c in cases}

    # ---- 1. hard identity edges ---------------------------------------------------
    for c in cases:
        users = set(c.subject.user_ids)
        if not users:
            continue
        burst_ids = {a.resource_id for a in bursts[c.case_id]}
        for rid, edges in owners.items():
            mine = [e for e in edges if e.user_id in users]
            if not mine:
                continue
            case_edges = [Edge(c.case_id, rid, "CATALOG" if e.provenance == "CATALOG_LINK" else "HARD", e.source_id,
                               e.identifier, e.time, "HARD") for e in mine]
            lifecycle = [e for e in mine if c.in_lifecycle(e.time)]
            if lifecycle:
                scope, reason = True, f"identity evidence within [requested_at, audit_at]: {lifecycle[0].source_id}"
            elif rid in burst_ids:
                scope, reason = True, "resource is in the case's own deletion burst"
            else:
                scope, reason = False, "identity evidence only outside the request lifecycle (earlier/later episode)"
            own = content.get(rid, set())
            if rid in result.conflicts:
                label, rationale = "CONFLICT", f"hard links to different users {result.conflicts[rid]}"
            elif own and not any(e.is_content for e in mine):
                # The record's own identifiers name someone else (possibly a person absent from the identity
                # tables) while only a catalog claim or a lookup key ties it to this subject: contradiction.
                provs = sorted({e.provenance for e in mine})
                label = "CONFLICT"
                rationale = (f"record's own identifiers {sorted(own)[:3]} do not belong to the subject, while "
                             f"{', '.join(provs)} links it to the subject")
                result.content_conflicts[rid] = {"own_identifiers": sorted(own), "linked_by": provs,
                                                 "subject_user_ids": sorted(users)}
            else:
                provs = sorted({e.provenance for e in mine})
                label, rationale = "CONFIRMED", f"direct identity link via {', '.join(provs)}"
            by_case[c.case_id][rid] = Attribution(c.case_id, rid, index.system_of(rid), label, rationale, scope,
                                                  reason, case_edges)

    # ---- 2. timing candidates -----------------------------------------------------
    timing_cands: dict[str, list[tuple[str, ActivityClaim]]] = defaultdict(list)   # resource -> (case, row)
    for c in cases:
        for a in bursts[c.case_id]:
            if a.resource_id in owner_users:
                if not set(owner_users[a.resource_id]) & set(c.subject.user_ids):
                    result.eliminated.setdefault(c.case_id, []).append(
                        {"resource_id": a.resource_id, "activity_id": a.activity_id,
                         "reason": f"hard-linked to {owner_users[a.resource_id]}",
                         # the evidence behind the elimination: an INFERRED pick in this batch depends on it (G1)
                         "sources": sorted({e.source_id for e in owners[a.resource_id]})})
                continue                                  # owned resources were handled by hard edges
            timing_cands[a.resource_id].append((c.case_id, a))

    # ---- 3. timing-only resources: filters (A1 claim consistency, A2 offset bands) + joint assignment ----------
    # Filters only REMOVE timing candidates; they never add one and never touch a hard-linked resource (hard-linked
    # resources are not timing candidates). A resource is assigned only when exactly one owner remains after the
    # filters, jointly with the one-to-one account constraint; if a filter would remove every candidate of a case
    # (or of a resource), nothing is removed and the pairs stay AMBIGUOUS (CLAIM_CONFLICT / OFFSET_CONFLICT).
    filters = VARIANTS[variant]
    req_of = {c.case_id: c.request for c in cases}
    status_of = {c.case_id: c.request.get("reported_status", "") for c in cases}
    if "offset" in filters:
        result.bands = offset_bands(cases, by_case, bursts)
        result.band_training = result.bands.pop("_training")
    bands = {g: b for g, b in result.bands.items() if b["used"]}

    def is_account_delete(a: ActivityClaim) -> bool:
        return a.event_type == "DELETE_RECORD" and a.target_system == "account_service"

    def group(a: ActivityClaim) -> tuple[str, str, str]:
        return (a.event_type, a.target_system, a.reported_result)

    def offset_s(cid: str, a: ActivityClaim) -> int:
        return int((parse_ts(a.event_time) - parse_ts(req_of[cid]["reported_at"])).total_seconds())

    def claim_ok(cid: str, a: ActivityClaim) -> bool:
        allowed = CLAIM_RESULTS.get(status_of[cid])
        return allowed is None or a.reported_result in allowed

    def band_ok(cid: str, a: ActivityClaim) -> bool:
        b = bands.get("|".join(group(a)))
        return b is None or b["low_s"] <= offset_s(cid, a) <= b["high_s"]

    def rejection(cid: str, a: ActivityClaim, use: set[str]) -> str | None:
        if "claim" in use and is_account_delete(a) and not claim_ok(cid, a):
            return (f"A1: account result {a.reported_result} is inconsistent with case status {status_of[cid]} "
                    f"(allowed {sorted(CLAIM_RESULTS[status_of[cid]])})")
        if "offset" in use and not band_ok(cid, a):
            b = bands["|".join(group(a))]
            return f"A2: offset {offset_s(cid, a)} s outside band [{b['low_s']}, {b['high_s']}] s of {'|'.join(group(a))}"
        return None

    # Cases whose account is already known through identity do not take another account delete.
    has_account = {cid for cid, attrs in by_case.items()
                   if any(at.label == "CONFIRMED" and at.in_scope and at.system_id == "account_service"
                          for at in attrs.values())}

    # ---- 3a. account deletes: one-to-one per batch, uniqueness under each filter subset -----------------------
    all_adj: dict[str, list[str]] = defaultdict(list)
    rows_of: dict[str, ActivityClaim] = {}
    for rid, pairs in timing_cands.items():
        for cid, a in pairs:
            if is_account_delete(a) and cid not in has_account:
                all_adj[cid].append(rid)
                rows_of[rid] = a
    for cid in all_adj:
        all_adj[cid] = sorted(set(all_adj[cid]))

    def filtered(use: set[str]) -> tuple[dict[str, list[str]], set[str]]:
        """Adjacency with rejected candidates removed; cases whose candidates would all be removed keep them all
        and are returned as conflict cases (never assignable)."""
        adj, conflict = {}, set()
        for cid, rids in all_adj.items():
            kept = [r for r in rids if rejection(cid, rows_of[r], use) is None]
            if not kept:
                conflict.add(cid)
                kept = rids
            adj[cid] = kept
        return adj, conflict

    subsets = [set()]
    if "claim" in filters:
        subsets.append({"claim"})
    if "offset" in filters:
        subsets.append(filters & {"claim", "offset"})
    stages = []
    for use in subsets:
        adj, conflict = filtered(use)
        stages.append((use, adj, conflict, forced_edges(adj, conflict)))
    use, adj, conflict, (forced, possible, comps) = stages[-1]
    for bi, (cs, rs) in enumerate(comps):
        result.batches.append({"batch": bi, "kind": "account_delete", "cases": cs, "rows": rs,
                               "forced": sorted(p for p in forced if p[0] in cs),
                               "ambiguous": sorted(p for p in possible - forced if p[0] in cs),
                               "conflict_cases": sorted(set(cs) & conflict)})
    if "joint" in filters:
        result.joint = verify_unique(adj, conflict, comps, forced)
    batch_of = {cid: bi for bi, (cs, _) in enumerate(comps) for cid in cs}
    for cid in sorted(all_adj):
        bi = batch_of.get(cid)
        cs, rs = comps[bi] if bi is not None else ([cid], all_adj[cid])
        for r in all_adj[cid]:
            a = rows_of[r]
            edge = Edge(cid, r, "TIMING", a.activity_id, f"{WORKER}:{a.event_type}", a.event_time, "SECONDARY")
            why_not = rejection(cid, a, use) if cid not in conflict else None
            if (cid, r) in forced and cid not in conflict:
                first = next(i for i, st in enumerate(stages) if (cid, r) in st[3][0] and cid not in st[2])
                first_use = stages[first][0]
                label = "INFERRED" if not first_use else ("INFERRED_CLAIM" if first_use == {"claim"} else "INFERRED_OFFSET")
                rationale = (f"account delete {a.activity_id} is in every maximum one-to-one assignment of batch {bi} "
                             f"(cases {cs}, rows {rs}) after eliminating rows hard-linked to other people"
                             + (f" and applying filters {sorted(stages[first][0])}" if stages[first][0] else ""))
                prov = provenance(cid, a, r, use, rejection, sorted(c for c in all_adj if r in all_adj[c]), all_adj,
                                  rows_of, result.eliminated, bands, group,
                                  offset_s, status_of, forced) if label != "INFERRED" else {}
            elif why_not:
                result.eliminated.setdefault(cid, []).append({"resource_id": r, "activity_id": a.activity_id,
                                                              "reason": why_not, "sources": []})
                continue
            else:
                label = "AMBIGUOUS"
                rivals = sorted(c for c, x in possible | forced if x == r and c != cid)
                note = " (CLAIM_CONFLICT: no candidate consistent with the case status)" if cid in conflict and \
                    "claim" in use and all(not claim_ok(cid, rows_of[x]) for x in all_adj[cid]) else \
                    (" (OFFSET_CONFLICT: no candidate inside its band)" if cid in conflict else "")
                rationale = f"batch {bi}: several assignments equally supported (rival cases for {r}: {rivals}){note}"
                prov = {}
            by_case[cid][r] = Attribution(cid, r, index.system_of(r), label, rationale, True,
                                          "resource is in the case's own deletion burst", [edge], prov)
    result.claim_conflicts = sorted(c for c in conflict if "claim" in use
                                    and all(not claim_ok(c, rows_of[x]) for x in all_adj[c]))

    # ---- 3b. other worker rows: one owner case after the offset filter, else AMBIGUOUS -----------------------
    for rid, pairs in sorted(timing_cands.items()):
        pairs = [(cid, a) for cid, a in pairs if not is_account_delete(a)]
        if not pairs:
            continue
        cids = sorted({cid for cid, _ in pairs})
        kept = sorted({cid for cid, a in pairs if rejection(cid, a, filters & {"offset"}) is None}) or cids
        for cid, a in pairs:
            if rid in by_case[cid]:
                continue
            edge = Edge(cid, rid, "TIMING", a.activity_id, f"{WORKER}:{a.event_type}", a.event_time, "SECONDARY")
            prov = {}
            if len(cids) == 1:
                label, rationale = "INFERRED", (f"only deletion case whose burst window contains {a.activity_id}; "
                                                f"no identity link to anyone else")
            elif len(kept) == 1 and cid in kept:
                label = "INFERRED_OFFSET"
                rationale = (f"only deletion case whose offset band for {'|'.join(group(a))} contains {a.activity_id} "
                             f"(burst windows of {cids}); no identity link to anyone else")
                prov = provenance(cid, a, rid, filters & {"offset"}, rejection, cids, {c: [rid] for c in cids},
                                  {rid: a}, result.eliminated, bands, group, offset_s, status_of, {(cid, rid)})
            elif cid not in kept:
                result.eliminated.setdefault(cid, []).append({"resource_id": rid, "activity_id": a.activity_id,
                                                              "reason": rejection(cid, a, filters & {"offset"}),
                                                              "sources": []})
                continue
            else:
                label, rationale = "AMBIGUOUS", f"{a.activity_id} lies in the burst windows of cases {kept}"
            by_case[cid][rid] = Attribution(cid, rid, index.system_of(rid), label, rationale, True,
                                            "resource is in the case's own deletion burst", [edge], prov)

    # ---- 3c. ordering tiebreak on residual account ties (variant only; INFERRED_ORDER) ----------------------
    if "ordering" in filters:
        order_tiebreak(cases, by_case, result, rows_of, bursts)

    # ---- 4. resource-to-resource references (search document -> source account) -------
    for cid, attrs in by_case.items():
        for at in list(attrs.values()):
            if at.label != "CONFIRMED" or not at.in_scope:
                continue
            for o in index.by_resource.get(at.resource_id, []):
                src = o.fields.get("source_resource_id")
                if isinstance(src, str) and src not in attrs and src not in owner_users:
                    attrs[src] = Attribution(cid, src, index.system_of(src), "INFERRED",
                                             f"source_resource_id of CONFIRMED {at.resource_id} in {o.file_id}",
                                             True, "referenced by an in-scope CONFIRMED resource",
                                             [Edge(cid, src, "SOURCE_REFERENCE", o.file_id,
                                                   f"source_resource_id:{at.resource_id}", o.time, "SECONDARY")])

    result.per_case = {cid: [attrs[r] for r in sorted(attrs)] for cid, attrs in by_case.items()}
    return result


def classify_request_rows(activity: list[ActivityClaim], owners_by_resource: dict[str, list[str]],
                          case: CaseContext) -> list[str]:
    """CLASSIFY_REQUEST non-deletion rows for an account linked to the case's subject (<= audit_at)."""
    cut = parse_ts(case.request["audit_at"])
    hits = []
    for a in activity:
        if a.event_type != "CLASSIFY_REQUEST" or parse_ts(a.event_time) > cut:
            continue
        if not re.search(r"non-deletion", a.details):
            continue
        if set(owners_by_resource.get(a.resource_id, [])) & set(case.subject.user_ids):
            hits.append(a.activity_id)
    return hits
