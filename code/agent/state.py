"""Reconstruct each resource's state at audit_at and pick the evidence that proves it.

T1  admissible = file captured_at <= audit_at AND the observation's own time <= audit_at (inclusive)
T2  each SQL table (primary / replica) is a separate copy; any present copy makes the record present
T3  per copy, the latest admissible observation wins (records can flip inside one file)
T4  activity results are claims and never set state (this module never reads activity)
T5  no admissible mention but an admissible full snapshot of the system exists -> absent by omission
Citation rule C: proof = latest admissible file that mentions the resource, else that snapshot.
"""
from __future__ import annotations

from .loaders import parse_ts
from .parsers import EvidenceIndex, admissible
from .schema import Observation, ResourceState

EXPOSURE_RANK = {"DIRECT_IDENTIFIERS": 4, "STABLE_REFERENCE_ONLY": 3, "REDACTED": 2, "NO_IDENTIFIERS": 1}


def _copy_key(o: Observation) -> str:
    return o.container.split(":")[0] if o.source_kind == "SQL_TABLE" else "SINGLE"


def _latest_per_copy(obs: list[Observation]) -> list[Observation]:
    best: dict[str, Observation] = {}
    for o in sorted(obs, key=lambda x: (parse_ts(x.time), x.obs_id)):
        best[_copy_key(o)] = o
    return [best[k] for k in sorted(best)]


def _presence(obs: list[Observation]) -> str:
    latest = _latest_per_copy([o for o in obs if o.presence != "UNKNOWN"])
    if any(o.presence == "PRESENT" for o in latest):
        return "PRESENT"
    if latest:
        return "ABSENT"
    return "UNKNOWN"


def _exposure(obs: list[Observation]) -> str:
    latest = _latest_per_copy([o for o in obs if o.exposure != "UNKNOWN"])
    if not latest:
        return "UNKNOWN"
    return max((o.exposure for o in latest), key=lambda e: EXPOSURE_RANK[e])


def _latest_value(obs: list[Observation], attr: str) -> str | None:
    vals = [o for o in obs if getattr(o, attr) not in (None, "UNKNOWN")]
    if not vals:
        return None
    return getattr(max(vals, key=lambda o: (parse_ts(o.time), o.obs_id)), attr)


def reconstruct(resource_id: str, system_id: str, audit_at: str, index: EvidenceIndex) -> ResourceState:
    cut = parse_ts(audit_at)
    obs = [o for o in index.by_resource.get(resource_id, []) if admissible(o, audit_at)]
    if obs:
        files = sorted({o.file_id for o in obs}, key=lambda f: (parse_ts(index.files[f].captured_at), f))
        proof = files[-1]
        in_proof = [o for o in obs if o.file_id == proof and o.state_bearing]
        state_bearing = [o for o in obs if o.state_bearing]
        presence = _presence(in_proof)
        if presence == "UNKNOWN":
            presence = _presence(state_bearing)
        exposure = _exposure(in_proof)
        if exposure == "UNKNOWN":
            exposure = _exposure(state_bearing)
        flow = _latest_value(in_proof, "flow_state") or _latest_value(state_bearing, "flow_state")
        restore = _latest_value(in_proof, "restore_state") or _latest_value(state_bearing, "restore_state")
        return ResourceState(resource_id, system_id, presence, exposure, flow, restore, proof, "MENTION",
                             [o.obs_id for o in in_proof] or [o.obs_id for o in obs])
    snaps = [s for s in index.snapshots_by_system.get(system_id, [])
             if parse_ts(index.files[s.file_id].captured_at) <= cut and parse_ts(s.captured_at) <= cut]
    if snaps:
        best = max(snaps, key=lambda s: (parse_ts(index.files[s.file_id].captured_at), s.file_id))
        return ResourceState(resource_id, system_id, "ABSENT", "UNKNOWN", None, None, best.file_id,
                             "FULL_SNAPSHOT", [])
    return ResourceState(resource_id, system_id, "UNKNOWN", "UNKNOWN", None, None, None, "NONE", [])
