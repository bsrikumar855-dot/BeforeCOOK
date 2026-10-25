import json

import pytest

from agent.schema import ActivityClaim, Verdict
from agent.steps import build_steps, serialize
from agent.validation import EvidenceCatalog, case_sort_key, validate_row

CATALOG = EvidenceCatalog([{"file_id": "ev1", "captured_at": "2026-09-10T00:00:00Z"},
                           {"file_id": "ev_late", "captured_at": "2026-09-20T00:00:00Z"}],
                          [ActivityClaim("activity_1", "2026-09-01T00:00:00Z", "X", "s", "t", "r", "SUCCESS", "")], [])
AUDIT = "2026-09-15T12:00:00Z"


def verdict(rid, system, compliant, ops, proof="ev1", provable=True):
    return Verdict(rid, system, "C", "b", "A", compliant, ops[0], ops[1], ops[2], proof, provable=provable)


def test_order_and_evidence_rules_match_the_contract():
    vs = [verdict("search_1", "search_service", False, ("REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT")),
          verdict("data_flow_1", "integration_service", False, ("REPORT_ACTIVE_DATA_FLOW", "DISABLE_DATA_FLOW",
                                                                 "VERIFY_DATA_FLOW_DISABLED")),
          verdict("account_1", "account_service", True, (None, None, "VERIFY_RECORD_ABSENT")),
          verdict("audit_record_1", "security_service", True, (None, None, "VERIFY_RECORD_PRESENT"), provable=False)]
    status, steps, dropped = build_steps(vs)
    assert status == "INCOMPLETE" and not dropped
    assert [s["operation"] for s in steps] == ["REPORT_REMAINING_RECORD", "REPORT_ACTIVE_DATA_FLOW", "DISABLE_DATA_FLOW",
                                               "DELETE_RECORD", "VERIFY_RECORD_ABSENT", "VERIFY_RECORD_ABSENT",
                                               "VERIFY_RECORD_PRESENT", "VERIFY_DATA_FLOW_DISABLED"]
    assert [s["system_id"] for s in steps if s["operation"] == "VERIFY_RECORD_ABSENT"] == ["account_service", "search_service"]
    assert [s["step_no"] for s in steps] == list(range(1, 9))
    assert all(s["evidence_id"] == "ev1" for s in steps if s["phase"] == "FINDING")
    assert all(s["evidence_id"] == "" for s in steps if s["phase"] != "FINDING")
    assert validate_row(status, serialize(steps), AUDIT, CATALOG) == []


def test_complete_cites_and_drops_unprovable():
    vs = [verdict("account_1", "account_service", True, (None, None, "VERIFY_RECORD_ABSENT")),
          verdict("audit_record_1", "security_service", True, (None, None, "VERIFY_RECORD_PRESENT"), proof=None,
                  provable=False)]
    status, steps, dropped = build_steps(vs)
    assert status == "COMPLETE" and len(steps) == 1 and steps[0]["evidence_id"] == "ev1"
    assert [v.resource_id for v in dropped] == ["audit_record_1"]


def test_serialization_is_byte_stable():
    vs = [verdict("account_1", "account_service", True, (None, None, "VERIFY_RECORD_ABSENT"))]
    a = serialize(build_steps(vs)[1])
    b = serialize(build_steps(vs)[1])
    assert a == b and json.loads(a)[0] == {"step_no": 1, "phase": "VERIFICATION", "system_id": "account_service",
                                           "resource_id": "account_1", "operation": "VERIFY_RECORD_ABSENT",
                                           "state": "ABSENT", "evidence_id": "ev1"}


GOOD = [{"step_no": 1, "phase": "VERIFICATION", "system_id": "account_service", "resource_id": "account_1",
         "operation": "VERIFY_RECORD_ABSENT", "state": "ABSENT", "evidence_id": "ev1"}]


@pytest.mark.parametrize("mutate,expected", [
    (lambda s: "[{", "malformed JSON"),
    (lambda s: json.dumps([{**s[0], "step_no": 2}]), "not consecutive"),
    (lambda s: json.dumps([{**s[0], "evidence_id": "ev_late"}]), "captured after audit_at"),
    (lambda s: json.dumps([{**s[0], "evidence_id": "evidence_404"}]), "unknown evidence id"),
    (lambda s: json.dumps([{**s[0], "state": "PRESENT"}]), "does not match"),
    (lambda s: json.dumps([{**s[0], "phase": "FINDING"}]), "not allowed in phase"),
    (lambda s: json.dumps([s[0], {**s[0], "step_no": 2}]), "duplicate step"),
    (lambda s: json.dumps([{**s[0], "evidence_id": ""}]), "evidence_id required"),
    (lambda s: json.dumps([{**s[0], "extra": 1}]), "keys"),
])
def test_validator_negative_controls(mutate, expected):
    errors = validate_row("COMPLETE", mutate(GOOD), AUDIT, CATALOG)
    assert any(expected in e for e in errors), errors


def test_out_of_order_operations_are_caught():
    steps = [{"step_no": 1, "phase": "VERIFICATION", "system_id": "security_service", "resource_id": "audit_record_1",
              "operation": "VERIFY_RECORD_PRESENT", "state": "PRESENT", "evidence_id": "ev1"},
             {**GOOD[0], "step_no": 2}]
    assert any("out of order" in e for e in validate_row("COMPLETE", json.dumps(steps), AUDIT, CATALOG))


def test_not_applicable_requires_empty_array():
    assert validate_row("NOT_APPLICABLE", "[]", AUDIT, CATALOG) == []
    assert validate_row("NOT_APPLICABLE", json.dumps(GOOD), AUDIT, CATALOG)


def test_case_ids_sort_numerically():
    assert sorted(["case_100", "case_33", "case_550"], key=case_sort_key) == ["case_33", "case_100", "case_550"]
    assert sorted(["case_100", "case_99", "case_550", "case_10", "case_9"], key=case_sort_key) == \
        ["case_9", "case_10", "case_99", "case_100", "case_550"]


def test_output_file_in_string_order_is_rejected(tmp_path):
    from agent.validation import validate_output_file
    ids = ["case_99", "case_100"]
    p = tmp_path / "out.csv"
    p.write_text("case_id,deletion_status,steps_json\ncase_100,NOT_APPLICABLE,[]\ncase_99,NOT_APPLICABLE,[]\n",
                 encoding="utf-8")
    assert "rows not sorted by case_id" in validate_output_file(p, ids)


def test_self_contradicting_rows_are_rejected():
    from agent.validation import self_contradictions
    rows = [{"case_id": "a", "deletion_status": "INCOMPLETE", "steps_json": "[]"},
            {"case_id": "b", "deletion_status": "COMPLETE", "steps_json": "[]"},
            {"case_id": "c", "deletion_status": "NOT_APPLICABLE", "steps_json": "[]"},
            {"case_id": "d", "deletion_status": "COMPLETE", "steps_json": json.dumps(GOOD)}]
    assert self_contradictions(rows) == [("a", "INCOMPLETE with no steps"), ("b", "COMPLETE without a VERIFICATION step")]


def test_empty_incomplete_allowed_only_with_reason_code():
    from agent.validation import self_contradictions
    rows = [{"case_id": "a", "deletion_status": "INCOMPLETE", "steps_json": "[]"}]
    assert self_contradictions(rows, allowed_empty={"a"}) == []
    assert self_contradictions(rows, allowed_empty={"b"}) == [("a", "INCOMPLETE with no steps")]
