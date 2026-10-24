"""C0: a model failure must exit 4 and never overwrite output.csv. No model calls (fixture X4, stubbed intent)."""
import hashlib
import os
from pathlib import Path

import pytest

import main as entry
from agent import pipeline
from agent.intent import IntentResult

FIXTURE = Path(__file__).resolve().parents[1] / "evaluation" / "custom_cases" / "X4"


class FailingClassifier:
    """Stands in for an intent client whose provider calls all failed."""

    def __init__(self, *a, **k):
        pass

    def classify(self, texts):
        return {t: IntentResult(None, None, None, None, None, False, "model_failure: stub") for t in set(texts)}


class DeletionClassifier(FailingClassifier):
    def classify(self, texts):
        return {t: IntentResult("DELETION", "en", False, "stub", "stub", False) for t in set(texts)}


def _args(tmp: Path, *extra: str) -> list[str]:
    return ["--from-cache", "--dataset", str(FIXTURE), "--output", str(tmp / "output.csv"),
            "--sample-output", str(tmp / "s.csv"), "--traces", str(tmp / "traces"),
            "--ledger", str(tmp / "ledger.json"), *extra]


def _fingerprint(p: Path) -> tuple:
    return hashlib.sha256(p.read_bytes()).hexdigest(), os.stat(p).st_mtime_ns


@pytest.fixture
def accepted(tmp_path):
    out = tmp_path / "output.csv"
    out.write_text("case_id,deletion_status,steps_json\naccepted,COMPLETE,[]\n", encoding="utf-8")
    return out


def test_model_failure_exits_4_and_leaves_output_untouched(tmp_path, accepted, monkeypatch):
    monkeypatch.setattr(pipeline, "IntentClassifier", FailingClassifier)
    before = _fingerprint(accepted)
    assert entry.main(_args(tmp_path)) == 4
    assert _fingerprint(accepted) == before
    assert not (tmp_path / "output.degraded.csv").exists()


def test_allow_degraded_writes_diagnostic_file_only(tmp_path, accepted, monkeypatch):
    monkeypatch.setattr(pipeline, "IntentClassifier", FailingClassifier)
    before = _fingerprint(accepted)
    assert entry.main(_args(tmp_path, "--allow-degraded")) == 4
    assert _fingerprint(accepted) == before
    assert (tmp_path / "output.degraded.csv").read_text(encoding="utf-8").startswith("case_id,deletion_status")


def test_summary_counts_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "IntentClassifier", FailingClassifier)
    rr = pipeline.run(FIXTURE, from_cache=True, include_samples=False)
    assert rr.summary["model_failures"]["intent_cases"] == 1
    assert len(rr.summary["eval"]["degraded"]) == 1


def test_normal_run_exits_0_and_writes_output(tmp_path, accepted, monkeypatch):
    monkeypatch.setattr(pipeline, "IntentClassifier", DeletionClassifier)
    assert entry.main(_args(tmp_path)) == 0
    assert "case_x04,COMPLETE" in accepted.read_text(encoding="utf-8")
