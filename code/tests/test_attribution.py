from agent.attribution import CaseContext, attribute
from factories import index, obs, universe, worker

REP = "2026-09-02T12:00:00Z"


def ctx(case_id: str, email: str, deletion: bool = True, rep: str = REP) -> CaseContext:
    req = {"case_id": case_id, "email": email, "requested_at": "2026-08-30T00:00:00Z", "reported_at": rep,
           "audit_at": "2026-09-15T12:00:00Z", "request_text": "x", "reported_status": "COMPLETE"}
    return CaseContext(case_id, req, universe().resolve(case_id, email), deletion)


def labels(result, case_id):
    return {a.resource_id: a.label for a in result.per_case[case_id]}


def test_direct_identity_link_is_confirmed():
    o = obs("ev1", "2026-09-10T00:00:00Z", "search_1", "search_service", {"email": "rhea.vale@example.test"})
    r = attribute([ctx("c1", "rhea.vale@example.test")], index([o], {"ev1": "2026-09-10T00:00:00Z"}), [], universe())
    assert labels(r, "c1") == {"search_1": "CONFIRMED"}
    assert r.per_case["c1"][0].in_scope


def test_historical_hash_link_is_confirmed():
    o = obs("ev1", "2026-09-10T00:00:00Z", "analytics_1", "analytics_platform",
            {"correlation_key": "d" * 64, "email_opt_in": True}, kind="LOG_EVENT", container="log:contact_lookup_completed")
    r = attribute([ctx("c1", "rhea.vale@example.test")], index([o], {"ev1": "2026-09-10T00:00:00Z"}), [], universe())
    assert labels(r, "c1") == {"analytics_1": "CONFIRMED"}


def test_two_cases_one_unowned_account_delete_is_ambiguous():
    rows = [worker("a1", "2026-09-02T11:00:00Z", "DELETE_RECORD", "account_service", "account_7")]
    cases = [ctx("c1", "rhea.vale@example.test"), ctx("c2", "omar.quint@example.test")]
    r = attribute(cases, index([], {}), rows, universe())
    assert labels(r, "c1") == {"account_7": "AMBIGUOUS"} and labels(r, "c2") == {"account_7": "AMBIGUOUS"}


def test_elimination_makes_the_remaining_row_inferred():
    rows = [worker("a1", "2026-09-02T11:00:00Z", "DELETE_RECORD", "account_service", "account_7"),
            worker("a2", "2026-09-02T11:05:00Z", "DELETE_RECORD", "account_service", "account_8")]
    owned = obs("ev1", "2026-09-10T00:00:00Z", "account_8", "account_service", {"email": "omar.quint@example.test"})
    cases = [ctx("c1", "rhea.vale@example.test"), ctx("c2", "omar.quint@example.test")]
    r = attribute(cases, index([owned], {"ev1": "2026-09-10T00:00:00Z"}), rows, universe())
    assert labels(r, "c2") == {"account_8": "CONFIRMED"}
    assert labels(r, "c1") == {"account_7": "INFERRED"}
    assert "every maximum" in r.per_case["c1"][0].rationale


def test_row_owned_by_someone_else_is_eliminated_not_assigned():
    rows = [worker("a1", "2026-09-02T11:00:00Z", "DELETE_RECORD", "search_service", "search_5")]
    owned = obs("ev1", "2026-09-10T00:00:00Z", "search_5", "search_service", {"email": "rhea.valentine@example.test"})
    r = attribute([ctx("c1", "rhea.vale@example.test")], index([owned], {"ev1": "2026-09-10T00:00:00Z"}), rows, universe())
    assert labels(r, "c1") == {}
    assert r.eliminated["c1"][0]["resource_id"] == "search_5"


def test_conflicting_hard_links_are_conflict():
    a = obs("ev1", "2026-09-10T00:00:00Z", "support_3", "support_platform", {"requester_email": "rhea.vale@example.test"})
    b = obs("ev2", "2026-09-11T00:00:00Z", "support_3", "support_platform", {"customer_reference": "user_902"})
    r = attribute([ctx("c1", "rhea.vale@example.test")],
                  index([a, b], {"ev1": "2026-09-10T00:00:00Z", "ev2": "2026-09-11T00:00:00Z"}), [], universe())
    assert labels(r, "c1") == {"support_3": "CONFLICT"}


def _catalog(resource_id: str, email: str, user: str):
    from agent.schema import ActivityClaim
    return ActivityClaim("activity_c1", "2026-09-05T10:00:00Z", "LINK_RESOURCE_IDENTITY", "data_catalog",
                         "marketing_platform", resource_id, "SUCCESS",
                         f"Data catalog linked {resource_id} ({resource_id}) to {email} and {user}.")


def test_catalog_link_contradicted_by_record_content_is_conflict():
    img = obs("ev1", "2026-09-06T00:00:00Z", "marketing_1", "marketing_platform",
              {"email": "someone.else@example.test", "phone": "+82 10 0000 0000"}, kind="IMAGE_EXTRACTION",
              container="image_row")
    r = attribute([ctx("c1", "rhea.vale@example.test")], index([img], {"ev1": "2026-09-06T00:00:00Z"}),
                  [_catalog("marketing_1", "rhea.vale@example.test", "user_901")], universe())
    att = r.per_case["c1"][0]
    assert att.label == "CONFLICT" and "do not belong to the subject" in att.rationale
    assert "marketing_1" in r.content_conflicts


def test_catalog_link_corroborated_by_record_content_is_confirmed():
    img = obs("ev1", "2026-09-06T00:00:00Z", "marketing_1", "marketing_platform",
              {"email": "rhea.vale@example.test"}, kind="IMAGE_EXTRACTION", container="image_row")
    r = attribute([ctx("c1", "rhea.vale@example.test")], index([img], {"ev1": "2026-09-06T00:00:00Z"}),
                  [_catalog("marketing_1", "rhea.vale@example.test", "user_901")], universe())
    assert labels(r, "c1") == {"marketing_1": "CONFIRMED"} and not r.content_conflicts


def test_shared_context_record_naming_subject_and_another_person_is_not_conflict():
    o = obs("ev1", "2026-09-06T00:00:00Z", "support_9", "support_platform",
            {"customer_reference": "user_901", "message_text": "cc someone.else@example.test"})
    r = attribute([ctx("c1", "rhea.vale@example.test")], index([o], {"ev1": "2026-09-06T00:00:00Z"}), [], universe())
    assert labels(r, "c1") == {"support_9": "CONFIRMED"}


def test_link_outside_request_lifecycle_is_out_of_scope():
    o = obs("ev1", "2026-03-01T00:00:00Z", "tombstone_1", "identity_service", {"subject_reference": "user_901"})
    r = attribute([ctx("c1", "rhea.vale@example.test")], index([o], {"ev1": "2026-03-01T00:00:00Z"}), [], universe())
    att = r.per_case["c1"][0]
    assert att.label == "CONFIRMED" and not att.in_scope


def test_non_deletion_cases_do_not_take_burst_rows():
    rows = [worker("a1", "2026-09-02T11:00:00Z", "DELETE_RECORD", "account_service", "account_7")]
    cases = [ctx("c1", "rhea.vale@example.test"), ctx("c2", "omar.quint@example.test", deletion=False)]
    r = attribute(cases, index([], {}), rows, universe())
    assert labels(r, "c1") == {"account_7": "INFERRED"} and labels(r, "c2") == {}
