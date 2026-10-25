"""rc7: committed evaluation artifacts must not depend on set or hash order (attribution_variants.json did)."""
import importlib.util
import sys
from pathlib import Path

# Load by path and restore sys.path: the module prepends code/evaluation, whose main.py would otherwise shadow
# code/main.py for every later test that imports `main`.
_saved = list(sys.path)
_spec = importlib.util.spec_from_file_location(
    "attribution_variants", Path(__file__).resolve().parents[1] / "evaluation" / "attribution_variants.py")
attribution_variants = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(attribution_variants)
sys.path[:] = _saved


def _traces():
    edge = {"kind": "TIMING"}
    return {c: {"attribution": [{"resource_id": f"account_{i}", "label": label, "edges": [edge]}]}
            for i, (c, label) in enumerate([("case_9", "INFERRED_CLAIM"), ("case_10", "INFERRED"), ("case_2", "INFERRED")])}


def test_timing_pairs_and_counters_do_not_follow_set_order():
    traces = _traces()
    for order in (["case_9", "case_10", "case_2"], ["case_2", "case_10", "case_9"]):
        pairs = attribution_variants.timing_pairs(traces, set(order))
        assert [p["case_id"] for p in pairs] == ["case_10", "case_2", "case_9"]
        assert list(attribution_variants.denominators(traces, set(order))["assignments"]) == ["INFERRED", "INFERRED_CLAIM"]
