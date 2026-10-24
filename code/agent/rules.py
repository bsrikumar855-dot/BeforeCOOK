"""Policy: category -> required action (from deletion_rules.csv) -> verdict and step operations.

Category comes from the record's shape, not from its system name
(an analytics-platform record carrying email_opt_in is a marketing record ->
DELETE, not ANONYMIZE). Order of evidence for the category:
  1. a record_type the evidence states explicitly
  2. shape flags (email_opt_in -> MARKETING_PROFILE, attachment -> SUPPORT_ATTACHMENT)
  3. when the shape cannot choose between the system's categories, the treatment the
     deletion workflow itself attempted (a hint, recorded as such)
  4. the system's default category
"""
from __future__ import annotations

from .parsers import EvidenceIndex
from .schema import ResourceState, Verdict

DATA_FLOW = "DATA_FLOW"            # not a deletion_rules category; connectors are disabled, not deleted
SYSTEM_CATEGORIES = {
    "account_service": ["USER_PROFILE"],
    "search_service": ["SEARCH_INDEX_ENTRY"],
    "analytics_platform": ["ANALYTICS_PROFILE", "MARKETING_PROFILE", "PRODUCT_TELEMETRY"],
    "support_platform": ["SUPPORT_MESSAGE", "SUPPORT_ATTACHMENT"],
    "export_service": ["USER_EXPORT"],
    "backup_service": ["BACKUP_ARCHIVE"],
    "integration_service": [DATA_FLOW],
    "security_service": ["SECURITY_AUDIT_LOG"],
    "identity_service": ["ACCOUNT_TOMBSTONE"],
    "legal_service": ["LEGAL_HOLD_RECORD"],
    "marketing_platform": ["MARKETING_PROFILE"],
    "cache_service": ["CACHE_ENTRY"],
    "email_delivery_service": ["EMAIL_DELIVERY_LOG"],
    "object_storage": ["SUPPORT_ATTACHMENT"],
}
WORKER_ACTION = {"DELETE_RECORD": "DELETE", "REDACT_RECORD": "REDACT", "ANONYMIZE_RECORD": "ANONYMIZE"}


class Policy:
    def __init__(self, rules: list[dict[str, str]]):
        self.action = {r["data_category"]: r["required_action"] for r in rules}
        self.rule_id = {r["data_category"]: r["rule_id"] for r in rules}
        self.action[DATA_FLOW] = "DISABLE"

    def category(self, resource_id: str, system_id: str, index: EvidenceIndex,
                 worker_events: list[str]) -> tuple[str, str]:
        obs = sorted(index.by_resource.get(resource_id, []), key=lambda o: (o.time, o.obs_id))
        stated = [o for o in obs if o.stated_category in self.action]
        if stated:
            return stated[-1].stated_category, f"record_type stated in {stated[-1].file_id}"
        shapes = {flag for o in obs for flag in o.record_shape.split("+")[1:]}
        if "email_opt_in" in shapes and "MARKETING_PROFILE" in self.action:
            return "MARKETING_PROFILE", "shape: email_opt_in"
        candidates = SYSTEM_CATEGORIES.get(system_id, [])
        if "attachment" in shapes and "SUPPORT_ATTACHMENT" in candidates:
            return "SUPPORT_ATTACHMENT", "shape: attachment"
        hinted = [WORKER_ACTION[e] for e in worker_events if e in WORKER_ACTION]
        if hinted and len(candidates) > 1:
            matching = [c for c in candidates if self.action.get(c) == hinted[0]]
            if matching:
                return matching[0], f"shape ambiguous; deletion workflow attempted {hinted[0]} (hint)"
        if candidates:
            return candidates[0], f"default category for {system_id}"
        return "UNMAPPED", f"no category for system {system_id}"

    def verdict(self, category: str, basis: str, state: ResourceState) -> Verdict:
        action = self.action.get(category, "UNMAPPED")
        v = Verdict(state.resource_id, state.system_id, category, basis, action, None, None, None, None,
                    state.proof_file_id)
        p, e = state.presence, state.exposure
        if action == "RETAIN" and category != "BACKUP_ARCHIVE":
            # The required final state of a retained record is PRESENT whatever was observed. It is
            # provable (and citable in a COMPLETE case) only from an admissible observation showing it.
            v.compliant, v.verification_op = True, "VERIFY_RECORD_PRESENT"
            if not (p == "PRESENT" and state.proof_basis == "MENTION"):
                v.provable, v.proof_file_id = False, None
                v.note = "RETAIN_UNPROVEN: no admissible observation shows the retained record"
            return v
        if state.proof_basis == "NONE":
            v.note = "UNKNOWN_STATE: no admissible observation and no admissible full snapshot"
            return v
        if action == "DELETE":
            if p == "PRESENT":
                return self._fail(v, "REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT")
            if p == "ABSENT":
                return self._pass(v, "VERIFY_RECORD_ABSENT")
        elif action in ("ANONYMIZE", "REDACT"):
            done = "VERIFY_RECORD_ANONYMIZED" if action == "ANONYMIZE" else "VERIFY_RECORD_REDACTED"
            fix = "ANONYMIZE_RECORD" if action == "ANONYMIZE" else "REDACT_RECORD"
            # ANONYMIZE also removes the stable user reference; REDACT keeps it (tombstones, conversations).
            exposed = {"DIRECT_IDENTIFIERS", "STABLE_REFERENCE_ONLY"} if action == "ANONYMIZE" else {"DIRECT_IDENTIFIERS"}
            if p == "ABSENT":
                return self._pass(v, "VERIFY_RECORD_ABSENT")
            if p == "PRESENT" and e in exposed:
                return self._fail(v, "REPORT_EXPOSED_PII", fix, done)
            if p == "PRESENT" and e != "UNKNOWN":
                return self._pass(v, done)
            v.note = "EXPOSURE_UNKNOWN: record present but no admissible observation shows its fields"
            return v
        elif action == "RETAIN" and category == "BACKUP_ARCHIVE":
            if state.restore_state == "RESTORABLE":
                return self._fail(v, "REPORT_RESTORE_RISK", "BLOCK_RESTORE", "VERIFY_RESTORE_BLOCKED")
            if state.restore_state == "BLOCKED":
                return self._pass(v, "VERIFY_RESTORE_BLOCKED")
            v.note = "RESTORE_STATE_UNKNOWN"
            return v
        elif action == "DISABLE":
            if state.flow_state == "ENABLED":
                return self._fail(v, "REPORT_ACTIVE_DATA_FLOW", "DISABLE_DATA_FLOW", "VERIFY_DATA_FLOW_DISABLED")
            if state.flow_state == "DISABLED":
                return self._pass(v, "VERIFY_DATA_FLOW_DISABLED")
            v.note = "FLOW_STATE_UNKNOWN"
            return v
        v.note = v.note or f"no verdict for action={action} presence={p}"
        return v

    @staticmethod
    def _fail(v: Verdict, finding: str, remediation: str, verification: str) -> Verdict:
        v.compliant, v.finding_op, v.remediation_op, v.verification_op = False, finding, remediation, verification
        return v

    @staticmethod
    def _pass(v: Verdict, verification: str) -> Verdict:
        v.compliant, v.verification_op = True, verification
        return v
