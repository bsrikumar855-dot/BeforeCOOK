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
