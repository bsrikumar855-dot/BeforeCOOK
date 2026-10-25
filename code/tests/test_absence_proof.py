"""F1: a screenshot proves absent direct identifiers only on the record's own detail page with no identifier on it."""
from agent.parsers.ocr_grounding import absence_proof
from agent.state import reconstruct
from factories import index, obs

PREFIXES = {"account", "tombstone", "user", "support", "export"}
DETAIL = ("Customer Reference Registry\nuser_17 Retained reference\nCustomer key - Account Platform\n"
          "Overview Linked records History\nReference type Created Most recent update Owner team\n"
          "Customer key 11 Jan 2024 10 Mar 2026 Account Platform\n@ Reference created\n@ Order link added")


def test_detail_page_without_identifiers_is_proof():
    ok, why = absence_proof("user_17", 1, DETAIL, PREFIXES)
    assert ok, why


def test_list_view_is_rejected():
    listing = DETAIL + "\nexport_1260 Ready\nexport_1259 Ready"
    assert absence_proof("user_17", 1, listing, PREFIXES)[0] is False          # other resource ids on the page
    assert absence_proof("user_17", 3, DETAIL, PREFIXES)[0] is False           # several records extracted


def test_detail_view_containing_an_email_phone_or_hash_is_rejected():
    assert absence_proof("user_17", 1, DETAIL + "\nContact leonie.g9352@outlook.com", PREFIXES)[0] is False
    assert absence_proof("user_17", 1, DETAIL + "\nPhone +81 90 6156 0005", PREFIXES)[0] is False
    assert absence_proof("user_17", 1, DETAIL + "\n" + "ab" * 32, PREFIXES)[0] is False


def test_ocr_uncertainty_keeps_unknown():
    assert absence_proof("user_17", 1, DETAIL.replace("user_17", "user_1?"), PREFIXES)[0] is False  # id not grounded
    assert absence_proof("user_17", 1, DETAIL + "\nleonie.g93@outl00k", PREFIXES)[0] is False      # mangled address
    assert absence_proof("user_17", 1, "", PREFIXES)[0] is False                                     # no OCR text
    assert absence_proof("user_17", 1, "user_17\nnothing else here", PREFIXES)[0] is False         # no detail labels


def test_screenshot_captured_after_audit_at_is_not_state():
    audit = "2026-03-11T08:00:00Z"
    late = obs("ev42", "2026-03-12T06:00:00Z", "tombstone_17", "identity_service", {"direct_identifiers": "0"},
               kind="IMAGE_EXTRACTION", container="image_row")
    s = reconstruct("tombstone_17", "identity_service", audit, index([late], {"ev42": "2026-03-12T06:00:00Z"}))
    assert s.proof_basis == "NONE"
