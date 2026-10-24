"""No rule may be keyed on a case_id, email or resource_id from the public samples.

The decision code (agent/, main.py, prompts/) is searched for every sample literal.
A negative control proves the search would catch one.
"""
import csv
import json
import re
from pathlib import Path

import pytest

CODE_DIR = Path(__file__).resolve().parents[1]
DATASET = CODE_DIR.parent / "dataset"
DECISION_FILES = sorted(list((CODE_DIR / "agent").rglob("*.py")) + [CODE_DIR / "main.py"]
                        + list((CODE_DIR / "prompts").glob("*.md")) + list((CODE_DIR / "config").glob("*.yaml")))


def sample_literals() -> set[str]:
    lits: set[str] = set()
    with open(DATASET / "sample_deletion_requests.csv", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            lits |= {r["case_id"], r["email"].lower()}
    with open(DATASET / "sample_output.csv", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            for s in json.loads(r["steps_json"]):
                lits |= {s["resource_id"], s["evidence_id"]} - {""}
    return lits


def find_literals(text: str, literals: set[str]) -> list[str]:
    return sorted(l for l in literals if re.search(r"(?<![\w.@-])" + re.escape(l) + r"(?![\w@-])", text, re.I))


@pytest.mark.skipif(not (DATASET / "sample_output.csv").exists(), reason="dataset not present")
def test_decision_code_contains_no_sample_literal():
    lits = sample_literals()
    hits = {str(p.relative_to(CODE_DIR)): find_literals(p.read_text(encoding="utf-8"), lits) for p in DECISION_FILES}
    assert {k: v for k, v in hits.items() if v} == {}


@pytest.mark.skipif(not (DATASET / "sample_output.csv").exists(), reason="dataset not present")
def test_negative_control_the_search_detects_a_planted_literal():
    lits = sample_literals()
    planted = next(iter(sorted(l for l in lits if l.startswith("account_"))))
    assert find_literals(f'if resource_id == "{planted}":', lits) == [planted]
