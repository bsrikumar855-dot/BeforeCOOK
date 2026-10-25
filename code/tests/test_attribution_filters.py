"""Round 5: A1 claim filter and A2 offset bands (offline, on the dataset)."""
import os

import pytest

from agent import attribution, pipeline
from agent.loaders import DEFAULT_DATASET_DIR


@pytest.fixture(scope="module")
def run_a1a2():
    old = os.environ.get("ORCHESTRATE_ATTRIBUTION")
    os.environ["ORCHESTRATE_ATTRIBUTION"] = "a1a2"
    try:
        yield pipeline.run(DEFAULT_DATASET_DIR, from_cache=True)
    finally:
        os.environ.pop("ORCHESTRATE_ATTRIBUTION") if old is None else os.environ.__setitem__("ORCHESTRATE_ATTRIBUTION", old)


def test_band_training_uses_only_confirmed_pairs(run_a1a2):
    pairs = run_a1a2.attribution.band_training
    assert pairs, "no training pairs"
    labels = {(a.case_id, a.resource_id): a.label for attrs in run_a1a2.attribution.per_case.values() for a in attrs}
    assert {p["label"] for p in pairs} == {"CONFIRMED"}
    assert all(labels[(p["case_id"], p["resource_id"])] == "CONFIRMED" for p in pairs)
    assert not any(labels[(p["case_id"], p["resource_id"])].startswith("INFERRED") for p in pairs)


def test_band_rule_constants_and_false_rejection_policy(run_a1a2):
    for g, b in run_a1a2.attribution.bands.items():
        assert b["half_s"] == max(attribution.BAND_MAD_K * b["mad_s"], attribution.BAND_MIN_HALF_S)
        if b["used"]:
            assert b["n"] >= attribution.BAND_MIN_N and b["outside"] == 0 and not b["multimodal"], g


def test_claim_filter_touches_only_primary_account_deletes(run_a1a2):
    acts = {a.activity_id: a for a in run_a1a2.dataset.activity}
    reqs = {r["case_id"]: r for r in run_a1a2.dataset.all_requests}
    for cid, attrs in run_a1a2.attribution.per_case.items():
        for a in attrs:
            if a.label != "INFERRED_CLAIM":
                continue
            row = acts[next(e.source_id for e in a.edges if e.kind == "TIMING")]
            assert (row.source_system, row.event_type, row.target_system) == ("deletion_worker", "DELETE_RECORD", "account_service")
            assert row.reported_result in attribution.CLAIM_RESULTS[reqs[cid]["reported_status"]]
            assert a.provenance["rejected_alternatives"] is not None and "claim" in a.provenance["filters"]
    for cid, elim in run_a1a2.attribution.eliminated.items():
        for e in elim:
            if e["reason"].startswith("A1"):
                assert acts[e["activity_id"]].target_system == "account_service"


def test_shipped_default_is_the_qualifying_variant():
    assert attribution.DEFAULT_VARIANT == "a1"
    assert attribution.VARIANTS["a1"] == {"claim"}
