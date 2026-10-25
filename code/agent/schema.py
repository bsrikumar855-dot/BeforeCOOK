"""Typed records shared by parsers, identity, attribution, state and rules.

These records are the seam of the system: rules read the enum fields below,
never free text. See DESIGN.md section 4.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# --- enums (string constants keep JSON traces readable) ----------------------

PRESENCE = ("PRESENT", "ABSENT", "UNKNOWN")
EXPOSURE = ("DIRECT_IDENTIFIERS", "STABLE_REFERENCE_ONLY", "NO_IDENTIFIERS", "REDACTED", "UNKNOWN")
FLOW_STATE = ("ENABLED", "DISABLED", "UNKNOWN")
RESTORE_STATE = ("RESTORABLE", "BLOCKED", "UNKNOWN")
COPY_ROLES = ("PRIMARY", "READ_REPLICA", "REPORTING_REPLICA", "FAILOVER_REPLICA", "REGIONAL_REPLICA", "SINGLE")
SOURCE_KINDS = ("SQL_TABLE", "JSON_COLLECTION", "JSON_POINT_LOOKUP", "RECORD_LEDGER", "LOG_EVENT",
                "PDF_TEXT", "IMAGE_EXTRACTION")
IDENTIFIER_KINDS = ("email", "email_hash", "phone", "user_id", "name")
HARD_IDENTIFIER_KINDS = ("email", "email_hash", "phone", "user_id")
LINK_PROVENANCE = ("SAME_RECORD", "TRACE_JOIN", "DOCUMENT_COMENTION", "CATALOG_LINK", "MODEL_EXTRACTION")

ATTRIBUTION_LABELS = ("CONFIRMED", "INFERRED", "INFERRED_CLAIM", "INFERRED_OFFSET", "INFERRED_ORDER",
                      "AMBIGUOUS", "CONFLICT")
INTENT_LABELS = ("DELETION", "NON_DELETION", "UNCLEAR")

PHASES = ("FINDING", "REMEDIATION", "VERIFICATION")
# Operation order inside each phase, exactly as tabulated in problem_statement.md.
OPERATION_ORDER = (
    "REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII", "REPORT_ACTIVE_DATA_FLOW", "REPORT_RESTORE_RISK",
    "DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD",
    "VERIFY_RECORD_ABSENT", "VERIFY_RECORD_PRESENT", "VERIFY_RECORD_REDACTED", "VERIFY_RECORD_ANONYMIZED",
    "VERIFY_DATA_FLOW_DISABLED", "VERIFY_RESTORE_BLOCKED",
)
OPERATION_PHASE = {op: ("FINDING" if i < 4 else "REMEDIATION" if i < 9 else "VERIFICATION")
                   for i, op in enumerate(OPERATION_ORDER)}
OPERATION_STATE = {
    "REPORT_REMAINING_RECORD": "PRESENT", "REPORT_EXPOSED_PII": "PRESENT",
    "REPORT_ACTIVE_DATA_FLOW": "ACTIVE", "REPORT_RESTORE_RISK": "ACTIVE",
    "DISABLE_DATA_FLOW": "DISABLED", "BLOCK_RESTORE": "BLOCKED", "DELETE_RECORD": "ABSENT",
    "REDACT_RECORD": "REDACTED", "ANONYMIZE_RECORD": "ANONYMIZED",
    "VERIFY_RECORD_ABSENT": "ABSENT", "VERIFY_RECORD_PRESENT": "PRESENT", "VERIFY_RECORD_REDACTED": "REDACTED",
    "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED", "VERIFY_DATA_FLOW_DISABLED": "DISABLED",
    "VERIFY_RESTORE_BLOCKED": "BLOCKED",
}
STATUSES = ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE")
STEP_KEYS = ("step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id")


@dataclass(frozen=True)
class Identifier:
    kind: str           # IDENTIFIER_KINDS
    value: str          # emails lower-cased, others exact
    field: str          # source field name (e.g. correlation_key)
    provenance: str     # LINK_PROVENANCE


@dataclass
class Observation:
    """One sighting of one resource inside one evidence file."""

    obs_id: str
    file_id: str
    file_captured_at: str
    observed_at: str | None
    source_kind: str
    system_id: str | None
    copy_role: str
    container: str
    resource_id: str
    fields: dict[str, object]
    identifiers: list[Identifier] = field(default_factory=list)
    extraction: str = "DETERMINISTIC"          # DETERMINISTIC | MODEL
    # Filled by record_semantics.annotate():
    record_shape: str = ""
    presence: str = "UNKNOWN"
    exposure: str = "UNKNOWN"
    flow_state: str | None = None
    restore_state: str | None = None
    stated_category: str | None = None
    state_bearing: bool = True

    @property
    def time(self) -> str:
        """Observation time; falls back to the file capture time."""
        return self.observed_at or self.file_captured_at


@dataclass(frozen=True)
class FullSnapshot:
    """A file dumps a whole table/collection for a system, so omission can mean absence."""

    file_id: str
    system_id: str
    container: str
    captured_at: str


@dataclass
class EvidenceFile:
    file_id: str
    path: str                  # relative to the dataset directory
    origin: str
    captured_at: str
    fmt: str
    status: str = "PARSED"     # PARSED | TEXT_LAYER | NEEDS_EXTRACTION | EXTRACTED | PARSE_ERROR | MISSING
    notes: list[str] = field(default_factory=list)
    sha256: str = ""


@dataclass(frozen=True)
class ActivityClaim:
    activity_id: str
    event_time: str
    event_type: str
    source_system: str
    target_system: str
    resource_id: str
    reported_result: str
    details: str


@dataclass
class Edge:
    """Evidence that a resource relates to a case's subject."""

    case_id: str
    resource_id: str
    kind: str          # HARD | CATALOG | TIMING | SOURCE_REFERENCE
    source_id: str     # file_id or activity_id
    identifier: str    # e.g. "email:<value>" or "deletion_worker:DELETE_RECORD"
    time: str
    strength: str      # HARD | SECONDARY


@dataclass
class Attribution:
    case_id: str
    resource_id: str
    system_id: str | None
    label: str                       # ATTRIBUTION_LABELS
    rationale: str
    in_scope: bool
    scope_reason: str
    edges: list[Edge] = field(default_factory=list)
    provenance: dict = field(default_factory=dict)   # INFERRED_* provenance contract (Round 5)


@dataclass
class ResourceState:
    resource_id: str
    system_id: str
    presence: str
    exposure: str
    flow_state: str | None
    restore_state: str | None
    proof_file_id: str | None
    proof_basis: str                 # MENTION | FULL_SNAPSHOT | NONE
    observation_ids: list[str] = field(default_factory=list)


@dataclass
class Verdict:
    resource_id: str
    system_id: str
    category: str
    category_basis: str
    required_action: str
    compliant: bool | None           # None = not emitted (logged)
    finding_op: str | None
    remediation_op: str | None
    verification_op: str | None
    proof_file_id: str | None
    note: str = ""
    provable: bool = True            # False: emitted only as a future check in an INCOMPLETE case
