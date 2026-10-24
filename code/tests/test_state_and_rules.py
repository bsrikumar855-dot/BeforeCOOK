from agent.rules import Policy
from agent.schema import FullSnapshot
from agent.state import reconstruct
from factories import index, obs

AUDIT = "2026-09-15T12:00:00Z"
RULES = [{"rule_id": f"rule_{i}", "data_category": c, "condition": "", "required_action": a} for i, (c, a) in enumerate([
    ("USER_PROFILE", "DELETE"), ("SEARCH_INDEX_ENTRY", "DELETE"), ("SUPPORT_MESSAGE", "REDACT"),
    ("SUPPORT_ATTACHMENT", "DELETE"), ("ANALYTICS_PROFILE", "ANONYMIZE"), ("MARKETING_PROFILE", "DELETE"),
    ("SECURITY_AUDIT_LOG", "RETAIN"), ("BACKUP_ARCHIVE", "RETAIN"), ("ACCOUNT_TOMBSTONE", "REDACT")])]


def test_captured_at_equal_to_audit_is_admissible():
    o = obs("ev1", AUDIT, "search_1", "search_service", {"hits_total": 1, "email": "x@example.test"})
    s = reconstruct("search_1", "search_service", AUDIT, index([o], {"ev1": AUDIT}))
    assert (s.presence, s.proof_file_id) == ("PRESENT", "ev1")


def test_record_dated_after_audit_inside_admissible_file_is_ignored():
    old = obs("ev1", AUDIT, "account_1", "account_service", {"status_code": 404}, observed="2026-09-15T11:00:00Z")
    late = obs("ev1", AUDIT, "account_1", "account_service", {"status_code": 200}, observed="2026-09-17T11:00:00Z",
               container="lookup2")
    s = reconstruct("account_1", "account_service", AUDIT, index([old, late], {"ev1": AUDIT}))
    assert s.presence == "ABSENT"


def test_file_captured_after_audit_is_never_state():
    o = obs("ev9", "2026-09-16T00:00:00Z", "search_1", "search_service", {"hits_total": 1})
    s = reconstruct("search_1", "search_service", AUDIT, index([o], {"ev9": "2026-09-16T00:00:00Z"}))
    assert s.proof_basis == "NONE"


def test_replica_copy_makes_record_present():
    primary = obs("ev1", "2026-09-10T00:00:00Z", "account_2", "account_service", {"email": "x@example.test"},
                  kind="SQL_TABLE", container="customer_data_primary:customer_profiles", copy_role="PRIMARY")
    primary.presence = "ABSENT"
    replica = obs("ev1", "2026-09-10T00:00:00Z", "account_2", "account_service", {"email": "x@example.test"},
                  kind="SQL_TABLE", container="customer_data_read_replica:customer_profiles", copy_role="READ_REPLICA")
    s = reconstruct("account_2", "account_service", AUDIT, index([primary, replica], {"ev1": "2026-09-10T00:00:00Z"}))
    assert s.presence == "PRESENT"


def test_absence_by_omission_uses_latest_full_snapshot():
    snaps = [FullSnapshot("ev1", "account_service", "customer_profiles", "2026-09-01T00:00:00Z"),
             FullSnapshot("ev2", "account_service", "customer_profiles", "2026-09-14T00:00:00Z")]
    s = reconstruct("account_3", "account_service", AUDIT,
                    index([], {"ev1": "2026-09-01T00:00:00Z", "ev2": "2026-09-14T00:00:00Z"}, snaps))
    assert (s.presence, s.proof_file_id, s.proof_basis) == ("ABSENT", "ev2", "FULL_SNAPSHOT")


def test_email_opt_in_shape_decides_delete_not_anonymize():
    o = obs("ev1", "2026-09-10T00:00:00Z", "analytics_1", "analytics_platform", {"rows": "1", "email_opt_in": True},
            kind="LOG_EVENT", container="log:contact_lookup_completed")
    idx = index([o], {"ev1": "2026-09-10T00:00:00Z"})
    p = Policy(RULES)
    cat, basis = p.category("analytics_1", "analytics_platform", idx, ["DELETE_RECORD"])
    assert cat == "MARKETING_PROFILE" and "email_opt_in" in basis
    v = p.verdict(cat, basis, reconstruct("analytics_1", "analytics_platform", AUDIT, idx))
    assert (v.finding_op, v.remediation_op, v.verification_op) == ("REPORT_REMAINING_RECORD", "DELETE_RECORD",
                                                                    "VERIFY_RECORD_ABSENT")


def test_analytics_with_only_user_reference_is_still_attributable():
    o = obs("ev1", "2026-09-10T00:00:00Z", "analytics_2", "analytics_platform",
            {"customer_reference": "user_901", "email": None, "phone": None})
    idx = index([o], {"ev1": "2026-09-10T00:00:00Z"})
    v = Policy(RULES).verdict("ANALYTICS_PROFILE", "t", reconstruct("analytics_2", "analytics_platform", AUDIT, idx))
    assert v.finding_op == "REPORT_EXPOSED_PII" and v.remediation_op == "ANONYMIZE_RECORD"


def test_tombstone_keeps_stable_reference():
    o = obs("ev1", "2026-09-10T00:00:00Z", "tombstone_1", "identity_service",
            {"subject_reference": "user_901", "name": None, "email": None, "phone": None, "email_hash": None},
            kind="JSON_COLLECTION", container="deleted_customer_references")
    idx = index([o], {"ev1": "2026-09-10T00:00:00Z"})
    v = Policy(RULES).verdict("ACCOUNT_TOMBSTONE", "t", reconstruct("tombstone_1", "identity_service", AUDIT, idx))
    assert v.compliant is True and v.verification_op == "VERIFY_RECORD_REDACTED"


def test_support_category_uses_worker_hint_only_when_shape_is_ambiguous():
    o = obs("ev1", "2026-09-10T00:00:00Z", "support_1", "support_platform", {"rows": "1"},
            kind="LOG_EVENT", container="log:contact_query_completed")
    idx = index([o], {"ev1": "2026-09-10T00:00:00Z"})
    p = Policy(RULES)
    assert p.category("support_1", "support_platform", idx, ["DELETE_RECORD"])[0] == "SUPPORT_ATTACHMENT"
    assert p.category("support_1", "support_platform", idx, ["REDACT_RECORD"])[0] == "SUPPORT_MESSAGE"
    assert p.category("support_1", "support_platform", idx, [])[0] == "SUPPORT_MESSAGE"


def test_retained_record_without_admissible_proof_is_unprovable():
    s = reconstruct("audit_record_1", "security_service", AUDIT, index([], {}))
    v = Policy(RULES).verdict("SECURITY_AUDIT_LOG", "t", s)
    assert v.verification_op == "VERIFY_RECORD_PRESENT" and v.provable is False


def test_restorable_backup_needs_block():
    o = obs("ev1", "2026-09-10T00:00:00Z", "backup_1", "backup_service", {"restore_allowed": 1})
    idx = index([o], {"ev1": "2026-09-10T00:00:00Z"})
    v = Policy(RULES).verdict("BACKUP_ARCHIVE", "t", reconstruct("backup_1", "backup_service", AUDIT, idx))
    assert (v.finding_op, v.remediation_op) == ("REPORT_RESTORE_RISK", "BLOCK_RESTORE")


def test_undeterminable_restore_control_is_treated_as_open():
    o = obs("ev1", "2026-09-10T00:00:00Z", "backup_1", "backup_service", {"presence": "PRESENT"},
            kind="IMAGE_EXTRACTION", container="image_row")
    idx = index([o], {"ev1": "2026-09-10T00:00:00Z"})
    v = Policy(RULES).verdict("BACKUP_ARCHIVE", "t", reconstruct("backup_1", "backup_service", AUDIT, idx))
    assert (v.finding_op, v.remediation_op, v.verification_op) == ("REPORT_RESTORE_RISK", "BLOCK_RESTORE",
                                                                    "VERIFY_RESTORE_BLOCKED")
    assert v.proof_file_id == "ev1" and "CONTROL_UNDETERMINABLE" in v.note
