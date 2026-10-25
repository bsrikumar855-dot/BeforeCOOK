"""rc7-X1: an id a screenshot shows (OCR) but the model did not extract is a citable mention of a KNOWN resource,
never a source of state; an OCR misread names no known resource and adds nothing."""
from agent.pipeline import add_ocr_mentions
from agent.state import reconstruct
from factories import index, obs, worker

AUDIT = "2026-09-27T13:00:00Z"


def _idx():
    text = obs("ev1", "2026-09-15T11:59:39Z", "support_9", "support_platform", {"phone": "+44 7700 900901"},
               observed="2026-09-15T11:51:46Z", kind="LOG_EVENT", container="log:attachment_pii_scan")
    return index([text], {"ev1": "2026-09-15T11:59:39Z", "ev4": "2026-09-26T11:00:00Z"})


def test_screenshot_mention_becomes_the_citation_but_not_the_state():
    idx = _idx()
    before = reconstruct("support_9", "support_platform", AUDIT, idx)
    added = add_ocr_mentions(idx, {"ev4": ["support_9"]}, [])
    after = reconstruct("support_9", "support_platform", AUDIT, idx)
    assert added == ["ev4#ocr:support_9"] and "ev4" in idx.mentions["support_9"]
    assert (before.proof_file_id, after.proof_file_id) == ("ev1", "ev4")          # latest admissible mention
    assert (after.presence, after.exposure) == (before.presence, before.exposure)  # state still from ev1


def test_misread_or_late_screenshot_adds_nothing_citable():
    idx = _idx()
    assert add_ocr_mentions(idx, {"ev4": ["support_99", "backup_92871"]}, []) == []     # names no known resource
    late = _idx()
    late.files["ev4"].captured_at = "2026-09-28T00:00:00Z"                             # after audit_at
    add_ocr_mentions(late, {"ev4": ["support_9"]}, [])
    assert reconstruct("support_9", "support_platform", AUDIT, late).proof_file_id == "ev1"


def test_activity_alone_makes_a_resource_known():
    idx = index([], {"ev4": "2026-09-26T11:00:00Z"})
    rows = [worker("activity_1", "2026-08-28T12:46:35Z", "DELETE_RECORD", "support_platform", "support_7")]
    assert add_ocr_mentions(idx, {"ev4": ["support_7"]}, rows) == ["ev4#ocr:support_7"]
    state = reconstruct("support_7", "support_platform", AUDIT, idx)
    assert state.proof_file_id == "ev4" and state.presence == "UNKNOWN"   # a mention is never presence proof
