"""Build the evidence index: every manifest file parsed into Observations.

Order matters: structured files first (they state systems explicitly), then
the prefix -> system map is learned from them, then PDFs (which need the
known prefixes) and images (whose extracted IDs are validated against it).
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..identity import IdentityUniverse
from ..loaders import Dataset, parse_ts
from ..schema import EvidenceFile, FullSnapshot, Observation
from .file_checks import check as check_file, resolve_inside
from .handover_text import parse_handover
from .json_records import parse_json
from .log_lines import parse_log
from .parsed_file import ParsedFile
from .pdf_text import parse_pdf
from .record_semantics import annotate
from .sql_snapshots import parse_sql

# Activity rows whose target_system is a sink, not the owner of the resource id.
NON_OWNER_EVENTS = {"SYNC_RECORD", "INDEX_RECORD", "UPDATE_RECORD", "RETAIN_RECORD", "CREATE_EXPORT",
                    "RESTORE_BACKUP", "CLASSIFY_REQUEST"}
CATALOG_RE = re.compile(r"linked (\S+) \(([^)]+)\) to (\S+?) and (\S+?)[.;]|confirmed (\S+) \(([^)]+)\) belongs only to (\S+) and (\S+?)[;.]")

ImageExtractor = Callable[[EvidenceFile, bytes, dict], list[Observation]]


def prefix_of(resource_id: str) -> str:
    m = re.match(r"^(.*)_\d+$", resource_id)
    return m.group(1) if m else resource_id


@dataclass
class EvidenceIndex:
    files: dict[str, EvidenceFile]
    observations: list[Observation]
    prefix_system: dict[str, str]
    snapshots: list[FullSnapshot]
    aliases: dict[str, str]
    text: dict[str, str] = field(default_factory=dict)
    by_resource: dict[str, list[Observation]] = field(default_factory=dict)
    mentions: dict[str, set[str]] = field(default_factory=dict)     # resource_id -> file_ids
    snapshots_by_system: dict[str, list[FullSnapshot]] = field(default_factory=dict)

    @property
    def known_systems(self) -> set[str]:
        return set(self.prefix_system.values())

    def system_of(self, resource_id: str) -> str | None:
        return self.prefix_system.get(prefix_of(resource_id))


def catalog_aliases(dataset: Dataset, include_user_ids: bool = False) -> dict[str, str]:
    """Data-catalog rows map an external reference (e.g. a statement number) to a resource id.

    Aliases that are user ids are excluded by default: in free text a user id names a person, not a record.
    Screenshot rows use them (include_user_ids=True) because there the alias is the row's identifier.
    """
    user_ids = {u["user_id"] for u in dataset.users}
    aliases = {}
    for a in dataset.activity:
        if a.event_type != "LINK_RESOURCE_IDENTITY":
            continue
        m = CATALOG_RE.search(a.details)
        if not m:
            continue
        rid, alias = (m.group(1), m.group(2)) if m.group(1) else (m.group(5), m.group(6))
        if alias != rid and (include_user_ids or alias not in user_ids):
            aliases[alias] = rid
    return aliases


def build_evidence_index(dataset: Dataset, universe: IdentityUniverse,
                         image_extractor: ImageExtractor | None = None) -> EvidenceIndex:
    files: dict[str, EvidenceFile] = {}
    parsed: dict[str, ParsedFile] = {}
    raw_bytes: dict[str, bytes] = {}
    for row in dataset.manifest:
        fmt = Path(row["file_path"]).suffix.lower().lstrip(".")
        ef = EvidenceFile(row["file_id"], row["file_path"], row["origin"], row["captured_at"], fmt)
        files[ef.file_id] = ef
        path = resolve_inside(dataset.root, row["file_path"])       # G8: never read outside the dataset
        if path is None:
            ef.status = "REJECTED_PATH"
            ef.notes.append("manifest path is absolute or escapes the dataset directory")
            continue
        if not path.is_file():
            ef.status = "MISSING"
            ef.notes.append("file named by the manifest does not exist")
            continue
        raw = path.read_bytes()
        ef.sha256 = hashlib.sha256(raw).hexdigest()
        rejected = check_file(raw, fmt)
        if rejected:
            ef.status, why = rejected
            ef.notes.append(why)
            continue
        raw_bytes[ef.file_id] = raw
        try:
            if fmt == "sql":
                parsed[ef.file_id] = parse_sql(ef, raw.decode("utf-8", errors="replace"), universe)
            elif fmt == "json":
                parsed[ef.file_id] = parse_json(ef, raw.decode("utf-8", errors="replace"), universe)
            elif fmt == "txt":
                parsed[ef.file_id] = parse_handover(ef, raw.decode("utf-8", errors="replace"), universe)
            elif fmt == "log":
                parsed[ef.file_id] = parse_log(ef, raw.decode("utf-8", errors="replace"), universe)
            elif fmt not in ("pdf", "png"):
                ef.status = "PARSE_ERROR"
                ef.notes.append(f"unsupported format {fmt}")
        except Exception as exc:          # one bad file never stops the run
            ef.status = "PARSE_ERROR"
            ef.notes.append(f"{type(exc).__name__}: {exc}")

    # Learn resource prefix -> system from rows that state both.
    votes: dict[str, Counter] = defaultdict(Counter)
    for pf in parsed.values():
        for o in pf.observations:
            if o.system_id:
                votes[prefix_of(o.resource_id)][o.system_id] += 1
    for a in dataset.activity:
        if a.event_type not in NON_OWNER_EVENTS:
            votes[prefix_of(a.resource_id)][a.target_system] += 1
    prefix_system = {p: sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] for p, c in votes.items()}
    aliases = catalog_aliases(dataset)

    for fid, ef in files.items():
        if ef.fmt == "pdf" and fid in raw_bytes:
            try:
                parsed[fid] = parse_pdf(ef, raw_bytes[fid], universe, set(prefix_system), aliases)
            except Exception as exc:
                ef.status = "PARSE_ERROR"
                ef.notes.append(f"{type(exc).__name__}: {exc}")
        elif ef.fmt == "png" and fid in raw_bytes:
            ef.status = "NEEDS_EXTRACTION"
            if image_extractor is not None:
                obs = image_extractor(ef, raw_bytes[fid], prefix_system)
                parsed[fid] = ParsedFile(text=" ".join(o.resource_id for o in obs), observations=obs)

    observations: list[Observation] = []
    snapshots: list[FullSnapshot] = []
    text: dict[str, str] = {}
    for fid in sorted(parsed, key=lambda f: (files[f].captured_at, f)):
        pf = parsed[fid]
        text[fid] = pf.text
        for o in pf.observations:
            o.system_id = o.system_id or prefix_system.get(prefix_of(o.resource_id))
            annotate(o)
            observations.append(o)
        for s in pf.snapshots:
            system = s.system_id
            if system is None and s.resource_ids:
                found = Counter(prefix_system.get(prefix_of(r)) for r in s.resource_ids)
                system = sorted(found.items(), key=lambda kv: (-kv[1], str(kv[0])))[0][0]
            if system:
                snapshots.append(FullSnapshot(fid, system, s.container, s.captured_at))

    idx = EvidenceIndex(files, observations, prefix_system, snapshots, aliases, text)
    for o in observations:
        idx.by_resource.setdefault(o.resource_id, []).append(o)
        idx.mentions.setdefault(o.resource_id, set()).add(o.file_id)
    for s in snapshots:
        idx.snapshots_by_system.setdefault(s.system_id, []).append(s)
    return idx


def admissible(obs: Observation, audit_at: str) -> bool:
    """State evidence: file captured <= audit_at AND the observation's own time <= audit_at (inclusive)."""
    cut = parse_ts(audit_at)
    return parse_ts(obs.file_captured_at) <= cut and parse_ts(obs.time) <= cut
