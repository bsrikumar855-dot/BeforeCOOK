"""E2 (not shipped) tools and verifier (pure code, no model calls), including the 3-proposal negative control."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "analysis"))
from e2_investigator_tools import ToolBox  # noqa: E402
from e2_investigator_verifier import verify  # noqa: E402
from factories import index, obs, universe, worker

BATCH = {"pairs": [["c1", "analytics_1"], ["c2", "analytics_1"], ["c1", "account_7"], ["c2", "account_7"]]}
SUBJECTS = {"c1": {"user_901"}, "c2": {"user_902"}}


def toolbox():
    o = obs("ev1", "2026-09-10T00:00:00Z", "analytics_1", "analytics_platform", {"customer_reference": "user_901"})
    idx = index([o], {"ev1": "2026-09-10T00:00:00Z"})
    rows = [worker("a1", "2026-09-02T11:00:00Z", "ANONYMIZE_RECORD", "analytics_platform", "analytics_1")]
    return ToolBox(idx, rows, universe(), {"b1": {"batch_id": "b1", **BATCH}}, {})


def test_tools_record_every_call_with_ids():
    tb = toolbox()
    r1 = tb.call("find_by_identifier", {"value": "user_901"})
    r2 = tb.call("activity_window", {"system": "analytics_platform", "start": "2026-09-02T10:00:00Z",
                                     "end": "2026-09-02T12:00:00Z"})
    r3 = tb.call("resource_observations", {"resource_id": "analytics_1", "until": "2026-09-15T00:00:00Z"})
    r4 = tb.call("no_such_tool", {})
    assert [t["id"] for t in tb.trace] == ["r1", "r2", "r3", "r4"]
    assert r1["result"][0]["owner_user_ids"] == ["user_901"] and r2["result"][0]["activity_id"] == "a1"
    assert r3["result"][0]["identifiers"][0]["value"] == "user_901" and "error" in r4["result"]


def test_verifier_accepts_only_identity_supported_proposals():
    tb = toolbox()
    tb.call("resource_observations", {"resource_id": "analytics_1", "until": "2026-09-15T00:00:00Z"})
    good = [{"case_id": "c1", "resource_id": "analytics_1", "decision": "ASSIGN", "cited_tool_results": ["r1"]}]
    d = verify(good, BATCH, tb.trace, SUBJECTS, {}, {"account_7"}, set())
    assert [x.accepted for x in d] == [True]


def test_verifier_negative_control_rejects_three_wrong_proposals():
    tb = toolbox()
    tb.call("resource_observations", {"resource_id": "analytics_1", "until": "2026-09-15T00:00:00Z"})
    wrong = [
        # 1. contradicts a hard link: analytics_1 is owned by user_901, proposed for c2
        {"case_id": "c2", "resource_id": "analytics_1", "decision": "ASSIGN", "cited_tool_results": ["r1"]},
        # 2. double assignment of a one-to-one account delete to a case that already has one
        {"case_id": "c1", "resource_id": "account_7", "decision": "ASSIGN", "cited_tool_results": ["r1"]},
        # 3. cites a tool result that does not exist
        {"case_id": "c1", "resource_id": "analytics_1", "decision": "ASSIGN", "cited_tool_results": ["r99"]},
    ]
    d = verify(wrong, BATCH, tb.trace, SUBJECTS, {"analytics_1": ["user_901"]}, {"account_7"}, {"c1"})
    assert [x.accepted for x in d] == [False, False, False]
    assert "contradicts hard link" in d[0].reason and "one-to-one" in d[1].reason and "does not exist" in d[2].reason


def test_verifier_rejects_ordering_only_and_unopen_pairs():
    tb = toolbox()
    tb.call("activity_window", {"system": "analytics_platform", "start": "2026-09-02T10:00:00Z",
                                "end": "2026-09-02T12:00:00Z"})
    props = [{"case_id": "c2", "resource_id": "account_7", "decision": "ASSIGN", "cited_tool_results": ["r1"]},
             {"case_id": "c9", "resource_id": "account_7", "decision": "ASSIGN", "cited_tool_results": ["r1"]}]
    d = verify(props, BATCH, tb.trace, SUBJECTS, {}, {"account_7"}, set())
    assert [x.accepted for x in d] == [False, False]
    assert "no cited result" in d[0].reason and "not an unresolved pair" in d[1].reason
