"""Small synthetic builders for unit tests (fictional people, *.example.test emails)."""
from __future__ import annotations

from agent.identity import IdentityUniverse
from agent.parsers import EvidenceIndex
from agent.parsers.record_semantics import annotate
from agent.schema import ActivityClaim, EvidenceFile, FullSnapshot, Observation

USERS = [
    {"user_id": "user_901", "name": "Rhea Vale", "email": "rhea.vale@example.test", "phone": "+44 7700 900901",
     "email_hash": "a" * 64, "updated_at": "2026-01-01T00:00:00Z"},
    {"user_id": "user_902", "name": "Omar Quint", "email": "omar.quint@example.test", "phone": "+44 7700 900902",
     "email_hash": "b" * 64, "updated_at": "2026-01-01T00:00:00Z"},
    {"user_id": "user_903", "name": "Rhea Valentine", "email": "rhea.valentine@example.test",
     "phone": "+44 7700 900903", "email_hash": "c" * 64, "updated_at": "2026-01-01T00:00:00Z"},
]
AUDIT = [
    {"audit_id": "audit_9001", "user_id": "user_901", "event_time": "2025-06-01T00:00:00Z", "field_name": "email",
     "old_value": "rhea.old@example.test", "new_value": "rhea.vale@example.test"},
    {"audit_id": "audit_9002", "user_id": "user_901", "event_time": "2025-06-01T00:00:00Z",
     "field_name": "email_hash", "old_value": "d" * 64, "new_value": "a" * 64},
]


def universe() -> IdentityUniverse:
    return IdentityUniverse(USERS, AUDIT)


def obs(file_id: str, captured: str, resource_id: str, system: str, fields: dict, *, observed: str | None = None,
        kind: str = "JSON_POINT_LOOKUP", container: str = "lookup", copy_role: str = "SINGLE",
        uni: IdentityUniverse | None = None) -> Observation:
    o = Observation(f"{file_id}#{resource_id}#{container}", file_id, captured, observed, kind, system, copy_role,
                    container, resource_id, dict(fields))
    u = uni or universe()
    for k, v in fields.items():
        o.identifiers.extend(u.classify(k, v, "SAME_RECORD"))
    return annotate(o)


def index(observations: list[Observation], files: dict[str, str], snapshots: list[FullSnapshot] = (),
          prefix_system: dict[str, str] | None = None) -> EvidenceIndex:
    efs = {fid: EvidenceFile(fid, f"evidence/{fid}", "test", cap, "json") for fid, cap in files.items()}
    idx = EvidenceIndex(efs, list(observations), prefix_system or {
        "account": "account_service", "search": "search_service", "analytics": "analytics_platform",
        "support": "support_platform", "audit_record": "security_service", "backup": "backup_service",
        "data_flow": "integration_service", "tombstone": "identity_service"}, list(snapshots), {})
    for o in observations:
        idx.by_resource.setdefault(o.resource_id, []).append(o)
        idx.mentions.setdefault(o.resource_id, set()).add(o.file_id)
    for s in snapshots:
        idx.snapshots_by_system.setdefault(s.system_id, []).append(s)
    return idx


def worker(activity_id: str, when: str, event: str, target: str, resource_id: str, result: str = "SUCCESS") -> ActivityClaim:
    return ActivityClaim(activity_id, when, event, "deletion_worker", target, resource_id, result, "test row")
