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


@dataclass
class AttributionResult:
    per_case: dict[str, list[Attribution]] = field(default_factory=dict)
    eliminated: dict[str, list[dict]] = field(default_factory=dict)
    batches: list[dict] = field(default_factory=list)
    conflicts: dict[str, list[str]] = field(default_factory=dict)      # resource -> users


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
                owners[o.resource_id].append(OwnerEdge(o.resource_id, u, o.file_id, f"{i.kind}:{i.value}", o.time, prov))
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


def attribute(cases: list[CaseContext], index: EvidenceIndex, activity: list[ActivityClaim],
              universe: IdentityUniverse) -> AttributionResult:
    result = AttributionResult()
    owners = resource_owners(index, activity, universe)
    owner_users = {rid: sorted({e.user_id for e in edges}) for rid, edges in owners.items()}
    result.conflicts = {rid: us for rid, us in owner_users.items() if len(us) > 1}

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
            if rid in result.conflicts:
                label, rationale = "CONFLICT", f"hard links to different users {result.conflicts[rid]}"
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
                         "reason": f"hard-linked to {owner_users[a.resource_id]}"})
                continue                                  # owned resources were handled by hard edges
            timing_cands[a.resource_id].append((c.case_id, a))

    def is_account_delete(a: ActivityClaim) -> bool:
        return a.event_type == "DELETE_RECORD" and a.target_system == "account_service"

    # Cases whose account is already known through identity do not take another account delete.
    has_account = {cid for cid, attrs in by_case.items()
                   if any(at.label == "CONFIRMED" and at.in_scope and at.system_id == "account_service"
                          for at in attrs.values())}

    # ---- 3a. account deletes: one-to-one per batch ----------------------------------
    adj: dict[str, list[str]] = defaultdict(list)
    rows_of: dict[str, ActivityClaim] = {}
    for rid, pairs in timing_cands.items():
        for cid, a in pairs:
            if is_account_delete(a) and cid not in has_account:
                adj[cid].append(rid)
                rows_of[rid] = a
    for cid in adj:
        adj[cid] = sorted(set(adj[cid]))
    # connected components
    comp_of: dict[str, int] = {}
    components: list[tuple[list[str], list[str]]] = []
    row_cases: dict[str, list[str]] = defaultdict(list)
    for cid, rids in adj.items():
        for r in rids:
            row_cases[r].append(cid)
    for start in sorted(adj):
        if start in comp_of:
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
        for cid in cs:
            comp_of[cid] = len(components)
        components.append((sorted(cs), sorted(rs)))

    for bi, (cs, rs) in enumerate(components):
        base = _max_matching(cs, adj)
        size = len(base)
        forced, possible = set(), set()
        # forced: removing the edge shrinks every maximum assignment
        for cid in cs:
            for r in adj[cid]:
                if len(_max_matching(cs, adj, banned=(cid, r))) < size:
                    forced.add((cid, r))
        # possible: some maximum assignment uses the edge (test by committing to it)
        for cid in cs:
            for r in adj[cid]:
                if (cid, r) in forced:
                    continue
                reduced = {c: [x for x in adj[c] if x != r] for c in cs if c != cid}
                rest = _max_matching([c for c in cs if c != cid], reduced)
                if len(rest) + 1 == size:
                    possible.add((cid, r))
        result.batches.append({"batch": bi, "kind": "account_delete", "cases": cs, "rows": rs,
                               "max_assignment": size, "forced": sorted(forced),
                               "ambiguous": sorted(possible - forced)})
        for cid, r in sorted(forced | possible):
            a = rows_of[r]
            edge = Edge(cid, r, "TIMING", a.activity_id, f"{WORKER}:{a.event_type}", a.event_time, "SECONDARY")
            if (cid, r) in forced:
                label = "INFERRED"
                rationale = (f"account delete {a.activity_id} is in every maximum one-to-one assignment of batch {bi} "
                             f"(cases {cs}, rows {rs}) after eliminating rows hard-linked to other people")
            else:
                label = "AMBIGUOUS"
                rivals = sorted(c for c, x in possible | forced if x == r and c != cid)
                rationale = f"batch {bi}: several assignments equally supported (rival cases for {r}: {rivals})"
            by_case[cid][r] = Attribution(cid, r, index.system_of(r), label, rationale, True,
                                          "resource is in the case's own deletion burst", [edge])

    # ---- 3b. other worker rows: unique candidate case or AMBIGUOUS -------------------
    for rid, pairs in sorted(timing_cands.items()):
        pairs = [(cid, a) for cid, a in pairs if not is_account_delete(a)]
        if not pairs:
            continue
        cids = sorted({cid for cid, _ in pairs})
        for cid, a in pairs:
            if rid in by_case[cid]:
                continue
            edge = Edge(cid, rid, "TIMING", a.activity_id, f"{WORKER}:{a.event_type}", a.event_time, "SECONDARY")
            if len(cids) == 1:
                label, rationale = "INFERRED", (f"only deletion case whose burst window contains {a.activity_id}; "
                                                f"no identity link to anyone else")
            else:
                label, rationale = "AMBIGUOUS", f"{a.activity_id} lies in the burst windows of cases {cids}"
            by_case[cid][rid] = Attribution(cid, rid, index.system_of(rid), label, rationale, True,
                                            "resource is in the case's own deletion burst", [edge])

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
