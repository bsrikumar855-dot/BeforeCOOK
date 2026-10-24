"""Load and validate the participant-facing CSV files.

Paths are always resolved relative to a dataset directory, so the same code
runs on the challenge dataset and on the custom evaluation fixtures.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .schema import ActivityClaim

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET_DIR = REPO_ROOT / "dataset"

REQUEST_COLUMNS = ("case_id", "email", "requested_at", "reported_status", "reported_at", "audit_at", "request_text")
REPORTED_STATUSES = ("COMPLETE", "PARTIALLY_COMPLETE", "NOT_STARTED")


def parse_ts(value: str | None) -> datetime | None:
    """ISO-8601 with a trailing Z (the only format observed in the dataset)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def read_csv(path: Path) -> list[dict[str, str]]:
    # utf-8-sig tolerates a BOM; errors="replace" keeps one bad byte from aborting the load.
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
        return list(csv.DictReader(fh))


@dataclass
class Dataset:
    root: Path
    requests: list[dict[str, str]]
    sample_requests: list[dict[str, str]]
    users: list[dict[str, str]]
    audit_log: list[dict[str, str]]
    activity: list[ActivityClaim]
    rules: list[dict[str, str]]
    manifest: list[dict[str, str]]
    input_issues: dict[str, list[str]] = field(default_factory=dict)   # case_id -> problems

    @property
    def all_requests(self) -> list[dict[str, str]]:
        return self.sample_requests + self.requests


def validate_request(row: dict[str, str]) -> list[str]:
    problems = []
    for col in REQUEST_COLUMNS:
        if not (row.get(col) or "").strip():
            problems.append(f"missing {col}")
    for col in ("requested_at", "reported_at", "audit_at"):
        if row.get(col) and parse_ts(row[col]) is None:
            problems.append(f"unparsable {col}")
    if row.get("reported_status") and row["reported_status"] not in REPORTED_STATUSES:
        problems.append("unknown reported_status")
    return problems


def load_dataset(dataset_dir: Path = DEFAULT_DATASET_DIR, include_samples: bool = True) -> Dataset:
    root = Path(dataset_dir)
    requests = read_csv(root / "deletion_requests.csv")
    samples_path = root / "sample_deletion_requests.csv"
    samples = read_csv(samples_path) if include_samples and samples_path.exists() else []
    activity = [ActivityClaim(r["activity_id"], r["event_time"], r["event_type"], r["source_system"],
                              r["target_system"], r["resource_id"], r["reported_result"], r["details"])
                for r in read_csv(root / "system_activity.csv")]
    ds = Dataset(root=root, requests=requests, sample_requests=samples,
                 users=read_csv(root / "user_identities.csv"), audit_log=read_csv(root / "user_audit_log.csv"),
                 activity=activity, rules=read_csv(root / "deletion_rules.csv"),
                 manifest=read_csv(root / "evidence_files.csv"))
    seen: set[str] = set()
    for row in ds.all_requests:
        problems = validate_request(row)
        if row.get("case_id") in seen:
            problems.append("duplicate case_id")
        seen.add(row.get("case_id", ""))
        if problems:
            ds.input_issues[row.get("case_id", "?")] = problems
    return ds
