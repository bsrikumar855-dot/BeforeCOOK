"""G5: the independent spec audit passes on the committed output.csv and catches every injected defect class."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evaluation"))
import spec_audit  # noqa: E402


def test_spec_audit_clean_and_negative_control_complete():
    ctx = spec_audit.load_context(spec_audit.REPO / "dataset", spec_audit.REPO / "code" / "traces")
    header, rows = spec_audit.read(spec_audit.REPO / "output.csv")
    assert spec_audit.audit(header, rows, ctx) == []
    caught = spec_audit.negative_control(header, rows, ctx)
    assert all(caught.values()), [c for c, ok in caught.items() if not ok]


def test_case_id_order_is_numeric_suffix_and_string_order_is_caught():
    assert sorted(["case_100", "case_99", "case_550", "case_10", "case_9"], key=spec_audit.case_number) == \
        ["case_9", "case_10", "case_99", "case_100", "case_550"]
    ctx = spec_audit.load_context(spec_audit.REPO / "dataset", spec_audit.REPO / "code" / "traces")
    header, rows = spec_audit.read(spec_audit.REPO / "output.csv")
    ids = [r[0] for r in rows]
    assert ids == ctx["ids"] == sorted(ids, key=spec_audit.case_number)   # output = input order = numeric order
    assert ids != sorted(ids)                                              # the eval ids are not zero-padded
    found = {c for c, _, _ in spec_audit.audit(header, sorted(rows, key=lambda r: r[0]), ctx)}
    assert {"row_order", "case_id_sort"} <= found
