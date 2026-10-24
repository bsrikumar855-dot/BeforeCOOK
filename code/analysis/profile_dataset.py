#!/usr/bin/env python3
"""Phase 0 data forensics for Ghost Data Still Exists.

Prints facts about the participant-facing dataset. It makes no decisions,
calls no model, and writes nothing. Python stdlib + sqlite3 only.

Usage:
    python code/analysis/profile_dataset.py              # all sections
    python code/analysis/profile_dataset.py --section 4  # one section

Sections:
    1 inventory          5 absent resource ids
    2 scope inventory    6 identity
    3 category mapping   7 temporal
    4 citation test      8 intent

sample_output.csv is read only in sections 4-7, to measure hypotheses
against the published sample answers. Nothing here is a prediction.
"""
from __future__ import annotations

import argparse
import base64
import collections
import csv
import json
import re
import sqlite3
import sys
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET = REPO_ROOT / "dataset"

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
HEX64_RE = re.compile(r"\b[0-9a-f]{64}\b")
PHONE_RE = re.compile(r"^\+\d[\d ()\-]{6,}$")
# Discovery only: tokens shaped like <prefix>_<digits>. Used to inventory
# which prefixes exist; matching against a case always uses exact values.
ID_TOKEN_RE = re.compile(r"\b([a-z]+(?:_[a-z]+)*)_(\d+)\b")
TS_PREFIX_RE = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)\s+(.*)$")
KV_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)=("((?:[^"\\]|\\.)*)"|[^\s"]+)')

# Keys that never carry state or identity (transport noise in logs).
NOISE_KEYS = {
    "instance", "pod", "region", "attempt", "duration_ms", "took_ms", "latency_ms",
    "wait_ms", "pool", "shard", "cache", "level", "severity", "logger", "seq",
    "retry_in_ms", "dependency", "route", "reason",
}
# Keys whose value is a person's name (identifier) rather than a label.
NAME_KEYS = {"name", "full_name", "display_name", "requester_name"}
# Keys whose value is the search key a lookup was issued with.
LOOKUP_KEYS = {"lookup_key", "match_key", "subject_key", "scan_key", "subject_ref", "correlation_key", "query"}

STEP_OP_ORDER = [
    "REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII", "REPORT_ACTIVE_DATA_FLOW", "REPORT_RESTORE_RISK",
    "DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD",
    "VERIFY_RECORD_ABSENT", "VERIFY_RECORD_PRESENT", "VERIFY_RECORD_REDACTED",
    "VERIFY_RECORD_ANONYMIZED", "VERIFY_DATA_FLOW_DISABLED", "VERIFY_RESTORE_BLOCKED",
]


# --------------------------------------------------------------------------- utils

def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def load_csv(name: str) -> list[dict[str, str]]:
    with open(DATASET / name, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def header(title: str) -> None:
    print()
    print("=" * 100)
    print(title)
    print("=" * 100)


def sub(title: str) -> None:
    print()
    print(f"--- {title}")


def pct(n: int, d: int) -> str:
    return f"{n}/{d}" + (f" ({100.0 * n / d:.1f}%)" if d else "")


# --------------------------------------------------------------------------- model

@dataclass
class Observation:
    """One sighting of one resource inside one evidence file."""

    file_id: str
    resource_id: str
    system_hint: str | None          # system stated by the file itself, if any
    container: str                   # table / collection / log event / document
    observed_at: str | None          # record/line time stated in the file
    fields: dict[str, object]        # raw state-bearing fields (no noise keys)
    identifiers: dict[str, set[str]] = field(default_factory=lambda: collections.defaultdict(set))
    id_provenance: str = "same_record"   # same_record | trace_join | document_comention


@dataclass
class Scope:
    """A statement that a file covers some system/container at a time."""

    file_id: str
    system: str | None
    container: str
    kind: str          # table_snapshot | collection_snapshot | point_lookup | log_event | record_lookup | document
    captured_at: str | None
    rows: int


@dataclass
class EvidenceFile:
    file_id: str
    path: Path
    origin: str
    captured_at: str
    fmt: str
    text: str = ""                                   # searchable text (PDF: extracted layer)
    observations: list[Observation] = field(default_factory=list)
    scopes: list[Scope] = field(default_factory=list)
    content_times: list[str] = field(default_factory=list)   # capture times stated inside the file
    notes: list[str] = field(default_factory=list)
    status: str = "PARSED"                           # PARSED | TEXT_LAYER | NEEDS_EXTRACTION


# --------------------------------------------------------------------------- identifiers

class IdentityUniverse:
    """Known identifier values from user_identities + user_audit_log (lower-cased)."""

    def __init__(self) -> None:
        self.users = load_csv("user_identities.csv")
        self.audit = load_csv("user_audit_log.csv")
        self.user_ids = {u["user_id"] for u in self.users}
        # 64-hex strings count as identifiers only when they are known email hashes
        # (integrity seals and checksums share the shape but identify nobody).
        self.known_hashes = {u["email_hash"] for u in self.users} | {
            a[s] for a in self.audit if a["field_name"] == "email_hash" for s in ("old_value", "new_value")}

    def classify(self, key: str, value: object) -> list[tuple[str, str]]:
        if value is None or isinstance(value, (bool, int, float)):
            return []
        text = str(value).strip()
        if not text or text.lower() in {"null", "none", "[redacted]"}:
            return []
        out: list[tuple[str, str]] = []
        if text in self.user_ids:
            out.append(("user_id", text))
        for m in EMAIL_RE.findall(text):
            out.append(("email", m.lower()))
        for m in HEX64_RE.findall(text):
            if m in self.known_hashes or key == "email_hash":
                out.append(("email_hash", m))
        if PHONE_RE.match(text):
            out.append(("phone", text))
        if key in NAME_KEYS and not out:
            out.append(("name", text))
        return out


def attach_identifiers(obs: Observation, universe: IdentityUniverse, record: dict) -> None:
    def walk(key: str, value: object) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                walk(k, v)
        elif isinstance(value, list):
            for v in value:
                walk(key, v)
        else:
            if key == "resource_id":
                return
            for kind, val in universe.classify(key, value):
                obs.identifiers[kind].add(val)
    walk("", record)


def strip_noise(record: dict) -> dict:
    return {k: v for k, v in record.items() if k not in NOISE_KEYS}


# --------------------------------------------------------------------------- parsers

def parse_sql(ef: EvidenceFile, universe: IdentityUniverse) -> None:
    script = ef.path.read_text(encoding="utf-8")
    ef.text = script
    m = re.search(r"^-- Captured at:\s*(\S+)", script, re.M)
    if m:
        ef.content_times.append(m.group(1))
    m = re.search(r"snapshot_completed_at=(\S+)", script)
    if m:
        ef.content_times.append(m.group(1))
    topo = re.search(r"^-- Database topology:\s*(\S+)", script, re.M)
    if topo:
        ef.notes.append(f"topology={topo.group(1)}")
    con = sqlite3.connect(":memory:")
    try:
        con.executescript(script)
    except sqlite3.Error as exc:
        ef.status = "PARSE_ERROR"
        ef.notes.append(f"sqlite error: {exc}")
        return
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    scope_rows = {}
    if "snapshot_scope" in tables:
        for system, cap in con.execute("SELECT origin_system, captured_at FROM snapshot_scope"):
            scope_rows[system] = cap
            ef.content_times.append(cap)
    for table in tables:
        if table == "snapshot_scope":
            continue
        cur = con.execute(f'SELECT * FROM "{table}"')
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        if "record_json" in cols:
            per_container: dict[tuple[str, str], int] = collections.Counter()
            for row in rows:
                rec = json.loads(row["record_json"])
                container = f"{table}:{row['collection_name']}"
                per_container[(row["origin_system"], container)] += 1
                obs = Observation(ef.file_id, row["resource_id"], row["origin_system"], container,
                                  row.get("updated_at"), strip_noise(rec))
                attach_identifiers(obs, universe, rec)
                ef.observations.append(obs)
            for (system, container), n in per_container.items():
                ef.scopes.append(Scope(ef.file_id, system, container, "table_snapshot", ef.content_times[0] if ef.content_times else None, n))
            if not rows:
                ef.scopes.append(Scope(ef.file_id, None, table, "table_snapshot", None, 0))
        else:
            for row in rows:
                rec = {k: v for k, v in row.items() if k != "resource_id"}
                obs = Observation(ef.file_id, row["resource_id"], None, table, row.get("updated_at"), strip_noise(rec))
                attach_identifiers(obs, universe, rec)
                ef.observations.append(obs)
            ef.scopes.append(Scope(ef.file_id, None, table, "table_snapshot",
                                   ef.content_times[0] if ef.content_times else None, len(rows)))
    for system, cap in scope_rows.items():
        ef.scopes.append(Scope(ef.file_id, system, "snapshot_scope", "table_snapshot", cap, -1))
    con.close()


def _record_lookup_obs(ef: EvidenceFile, universe: IdentityUniverse, rec: dict, container: str) -> None:
    obs = Observation(ef.file_id, rec["resource_id"], rec.get("system"), container,
                      rec.get("recorded_at") or rec.get("ts"),
                      {k: rec.get(k) for k in ("record_type", "result")})
    attach_identifiers(obs, universe, {"subject_reference": rec.get("subject_reference")})
    ef.observations.append(obs)


def parse_json(ef: EvidenceFile, universe: IdentityUniverse) -> None:
    raw = ef.path.read_text(encoding="utf-8")
    ef.text = raw
    doc = json.loads(raw)
    for key in ("exported_at", "captured_at"):
        if key in doc:
            ef.content_times.append(doc[key])
    if "snapshots" in doc:                                    # records-assurance bundle
        for snap in doc["snapshots"]:
            ef.content_times.append(snap["captured_at"])
            nested = [r for r in snap["records"] if "system" in r and "record_type" in r]
            if nested:
                for system, n in collections.Counter(r["system"] for r in nested).items():
                    ef.scopes.append(Scope(ef.file_id, system, f"{snap['data_store']}:{snap['collection']}",
                                           "record_lookup", snap["captured_at"], n))
            else:
                ef.scopes.append(Scope(ef.file_id, snap["data_store"], snap["collection"], "collection_snapshot",
                                       snap["captured_at"], len(snap["records"])))
            for rec in snap["records"]:
                if "system" in rec and "record_type" in rec:      # history ledger nested in a bundle
                    _record_lookup_obs(ef, universe, {**rec, "subject_reference": rec.get("lookup_value")},
                                       f"{snap['data_store']}:{snap['collection']}")
                    ef.content_times.append(rec["recorded_at"])
                    continue
                obs = Observation(ef.file_id, rec["resource_id"], snap["data_store"], snap["collection"],
                                  snap["captured_at"], strip_noise({k: v for k, v in rec.items() if k != "resource_id"}))
                attach_identifiers(obs, universe, rec)
                ef.observations.append(obs)
    elif "service_exports" in doc:                            # per-service point lookups
        for system, collections_ in doc["service_exports"].items():
            for coll, recs in collections_.items():
                ef.scopes.append(Scope(ef.file_id, system, coll, "point_lookup", doc.get("exported_at"), len(recs)))
                for rec in recs:
                    if coll == "index_queries":
                        docs = (rec.get("hits") or {}).get("documents") or []
                        if not docs:
                            obs = Observation(ef.file_id, rec["resource_id"], system, coll, rec.get("observed_at"),
                                              {"hits_total": (rec.get("hits") or {}).get("total", 0)})
                            attach_identifiers(obs, universe, {"query": rec.get("query")})
                            ef.observations.append(obs)
                        for d in docs:
                            obs = Observation(ef.file_id, d["resource_id"], system, coll, rec.get("observed_at"),
                                              strip_noise({**{k: v for k, v in d.items() if k != "resource_id"},
                                                           "hits_total": rec["hits"].get("total")}))
                            attach_identifiers(obs, universe, {**d, "query": rec.get("query")})
                            ef.observations.append(obs)
                    else:
                        body = {k: v for k, v in rec.items() if k not in ("resource_id", "observed_at")}
                        obs = Observation(ef.file_id, rec["resource_id"], system, coll, rec.get("observed_at"), strip_noise(body))
                        attach_identifiers(obs, universe, body)
                        ef.observations.append(obs)
    elif "records" in doc and doc["records"] and "record_type" in doc["records"][0]:   # lookup ledger
        for rec in doc["records"]:
            _record_lookup_obs(ef, universe, rec, "record_lookup")
            ef.content_times.append(rec["recorded_at"])
        for system, n in collections.Counter(r["system"] for r in doc["records"]).items():
            ef.scopes.append(Scope(ef.file_id, system, "record_lookup", "record_lookup", doc.get("exported_at"), n))
    elif "records" in doc:                                    # single-collection dump
        coll = doc.get("collection", "records")
        ef.scopes.append(Scope(ef.file_id, None, coll, "collection_snapshot", doc.get("captured_at"), len(doc["records"])))
        for rec in doc["records"]:
            obs = Observation(ef.file_id, rec["resource_id"], None, coll, doc.get("captured_at"),
                              strip_noise({k: v for k, v in rec.items() if k != "resource_id"}))
            attach_identifiers(obs, universe, rec)
            ef.observations.append(obs)
    else:
        ef.status = "UNRECOGNISED_JSON"


HANDOVER_RE = re.compile(r"^-\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(.+?)\s*$")


def parse_txt(ef: EvidenceFile, universe: IdentityUniverse) -> None:
    text = ef.path.read_text(encoding="utf-8")
    ef.text = text
    m = re.search(r"Snapshot:\s*(\S+)", text)
    if m:
        ef.content_times.append(m.group(1))
    systems = collections.Counter()
    for line in text.splitlines():
        hm = HANDOVER_RE.match(line.strip())
        if not hm:
            continue
        ts, system, rid, rtype, result, subject = hm.groups()
        systems[system] += 1
        _record_lookup_obs(ef, universe, {"ts": ts, "system": system, "resource_id": rid, "record_type": rtype,
                                          "result": result, "subject_reference": subject}, "handover_line")
    for system, n in systems.items():
        ef.scopes.append(Scope(ef.file_id, system, "handover_line", "record_lookup", ef.content_times[0] if ef.content_times else None, n))


def _kv(text: str) -> dict[str, str]:
    out = {}
    for k, raw, quoted in KV_RE.findall(text):
        out[k] = quoted.replace('\\"', '"') if raw.startswith('"') else raw
    return out


def normalise_log_line(line: str) -> dict | None:
    """Return {ts, service, trace, event, kv, syntax} for any of the observed log syntaxes."""
    m = TS_PREFIX_RE.match(line)
    if not m:
        return None
    ts, rest = m.groups()
    if rest.startswith("{"):
        d = json.loads(rest)
        return {"ts": ts, "service": d.get("service"), "trace": d.get("trace_id"), "event": d.get("event"),
                "kv": d, "syntax": "json_line"}
    sm = re.match(r"^<\d+>1 (\S+) (\S+) (\S+) - - (.*)$", rest)
    if sm:
        host, service, trace, body = sm.groups()
        kv = _kv(body)
        return {"ts": ts, "service": service, "trace": trace, "event": kv.get("event"), "kv": kv, "syntax": "rfc5424"}
    bm = re.match(r"^\[(\w+)\] \[([^\]]+)\] \[([^\]]+)\] ([a-z_]+): (.*)$", rest)
    if bm:
        level, service, trace, event, body = bm.groups()
        return {"ts": ts, "service": service, "trace": trace, "event": event, "kv": _kv(body), "syntax": "bracketed"}
    pm = re.match(r"^\| (\w+) \| ([^|]+?) \| ([^|]+?) \| ([a-z_]+) \| (.*)$", rest)
    if pm:
        level, service, trace, event, body = pm.groups()
        return {"ts": ts, "service": service, "trace": trace, "event": event, "kv": _kv(body), "syntax": "pipe"}
    if rest.startswith("requestId="):
        kv = _kv(rest)
        inner = _kv(kv.get("message", ""))
        return {"ts": ts, "service": kv.get("function"), "trace": kv.get("requestId"), "event": inner.get("event"),
                "kv": inner, "syntax": "cloud_message"}
    kv = _kv(rest)
    return {"ts": ts, "service": kv.get("service"), "trace": kv.get("trace_id"), "event": kv.get("event"),
            "kv": kv, "syntax": "key_value"}


def parse_log(ef: EvidenceFile, universe: IdentityUniverse) -> None:
    text = ef.path.read_text(encoding="utf-8")
    ef.text = text
    events = []
    syntaxes = collections.Counter()
    for n, line in enumerate(text.splitlines(), 1):
        ev = normalise_log_line(line)
        if ev is None:
            ef.notes.append(f"unparsed line {n}")
            continue
        ev["line"] = n
        events.append(ev)
        syntaxes[ev["syntax"]] += 1
        ef.content_times.append(ev["ts"])
    ef.notes.append("syntax=" + ",".join(f"{k}:{v}" for k, v in syntaxes.items()))
    by_trace: dict[str, list[dict]] = collections.defaultdict(list)
    for ev in events:
        if ev["trace"]:
            by_trace[ev["trace"]].append(ev)
    containers = collections.Counter()
    for ev in events:
        kv = ev["kv"]
        rid = kv.get("resource_id")
        if not rid:
            continue
        if kv.get("event") == "record_lookup" or ev["event"] == "record_lookup":
            _record_lookup_obs(ef, universe, {"ts": ev["ts"], **kv}, "record_lookup")
            containers[(kv.get("system"), "record_lookup")] += 1
            continue
        body = strip_noise({k: v for k, v in kv.items() if k not in ("resource_id", "event", "service", "trace_id", "message")})
        obs = Observation(ef.file_id, rid, None, f"log:{ev['event']}", ev["ts"], body)
        obs.fields["_service"] = ev["service"]
        attach_identifiers(obs, universe, body)
        own = {k for k, v in obs.identifiers.items() if v}
        if ev["trace"]:
            for other in by_trace[ev["trace"]]:
                if other is ev:
                    continue
                for k, v in other["kv"].items():
                    if k in LOOKUP_KEYS or k in NAME_KEYS or "email" in k:
                        for kind, val in universe.classify(k, v):
                            obs.identifiers[kind].add(val)
        if {k for k, v in obs.identifiers.items() if v} - own:
            obs.id_provenance = "trace_join"
        ef.observations.append(obs)
        containers[(ev["service"], f"log:{ev['event']}")] += 1
    for (service, container), n in containers.items():
        kind = "record_lookup" if container == "record_lookup" else "log_event"
        ef.scopes.append(Scope(ef.file_id, service, container, kind, None, n))


BS = b"\x5c"


def _pdf_objects(b: bytes) -> dict[int, bytes]:
    return {int(m.group(1)): m.group(2) for m in re.finditer(rb"(\d+) 0 obj\s*(.*?)\s*endobj", b, re.S)}


def _pdf_stream(obj: bytes) -> bytes:
    m = re.match(rb"<<(.*)>>\s*stream\r?\n(.*?)\r?\n?endstream", obj, re.S)
    if not m:
        return b""
    meta, data = m.group(1), m.group(2)
    for flt in re.findall(rb"/(ASCII85Decode|FlateDecode)", meta):
        if flt == b"ASCII85Decode":
            data = data.strip()
            data = base64.a85decode(data[:-2] if data.endswith(b"~>") else data)
        else:
            data = zlib.decompress(data)
    return data


def _pdf_cmap(data: bytes) -> dict[int, str]:
    out: dict[int, str] = {}
    for blk in re.findall(rb"beginbfchar(.*?)endbfchar", data, re.S):
        for a, u in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", blk):
            out[int(a, 16)] = bytes.fromhex(u.decode()).decode("utf-16-be")
    for blk in re.findall(rb"beginbfrange(.*?)endbfrange", data, re.S):
        for a, z, u in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", blk):
            for i in range(int(a, 16), int(z, 16) + 1):
                out[i] = chr(int(u, 16) + i - int(a, 16))
    return out


def pdf_text_layer(path: Path) -> str:
    """Deterministic text-layer extraction (no OCR). Empty string if none."""
    b = path.read_bytes()
    objs = _pdf_objects(b)
    fonts: dict[bytes, dict[int, str] | None] = {}
    for o in objs.values():
        if re.search(rb"/Type\s*/Font[\s>/]", o) and b"/Name /" in o:
            name = re.search(rb"/Name /([^\s/]+)", o).group(1)
            tu = re.search(rb"/ToUnicode (\d+) 0 R", o)
            fonts[name] = _pdf_cmap(_pdf_stream(objs[int(tu.group(1))])) if tu else None
    lines: list[str] = []
    pat = rb"/([^\s]+)\s+[\d.]+\s+Tf|\(((?:[^()\x5c]|\x5c.)*)\)\s*Tj|<([0-9A-Fa-f]+)>\s*Tj|\bET\b|(T\*|Td|TD)"
    for o in [objs[k] for k in sorted(objs)]:
        if b"/Type /Page" not in o or b"/Contents" not in o:
            continue
        content = _pdf_stream(objs[int(re.search(rb"/Contents (\d+) 0 R", o).group(1))])
        cur: list[str] = []
        font = None
        for tok in re.finditer(pat, content, re.S):
            if tok.group(1):
                font = tok.group(1)
            elif tok.group(2) is not None or tok.group(3):
                raw = (tok.group(2).replace(BS + b"(", b"(").replace(BS + b")", b")").replace(BS + BS, BS)
                       if tok.group(2) is not None else bytes.fromhex(tok.group(3).decode()))
                fmap = fonts.get(font)
                cur.append("".join(fmap.get(c, "?") for c in raw) if fmap else raw.decode("latin-1"))
            elif cur:
                lines.append("".join(cur))
                cur = []
        if cur:
            lines.append("".join(cur))
    return "\n".join(lines)


def parse_pdf(ef: EvidenceFile, universe: IdentityUniverse) -> None:
    text = pdf_text_layer(ef.path)
    ef.text = text
    if not text.strip():
        ef.status = "NEEDS_EXTRACTION"
        return
    ef.status = "TEXT_LAYER"
    doc_ids: dict[str, set[str]] = collections.defaultdict(set)
    for kind, val in [("email", e.lower()) for e in EMAIL_RE.findall(text)] + \
                     [("user_id", u) for u in re.findall(r"\b\S+\b", text) if u in universe.user_ids]:
        doc_ids[kind].add(val)
    for m in ID_TOKEN_RE.finditer(text):
        token = m.group(0)
        if token in universe.user_ids:
            continue
        obs = Observation(ef.file_id, token, None, "pdf_text", None, {})
        for k, v in doc_ids.items():
            obs.identifiers[k] |= v
        obs.id_provenance = "document_comention"
        ef.observations.append(obs)
    ef.scopes.append(Scope(ef.file_id, None, "pdf_text", "document", None, len(ef.observations)))


def load_evidence(universe: IdentityUniverse) -> list[EvidenceFile]:
    files = []
    for row in load_csv("evidence_files.csv"):
        path = DATASET / row["file_path"]
        fmt = path.suffix.lower().lstrip(".")
        ef = EvidenceFile(row["file_id"], path, row["origin"], row["captured_at"], fmt)
        if not path.exists():
            ef.status = "MISSING_ON_DISK"
            files.append(ef)
            continue
        magic = path.read_bytes()[:8]
        if fmt == "png" and magic != b"\x89PNG\r\n\x1a\n":
            ef.notes.append("extension says png but magic bytes differ")
        if fmt == "pdf" and not magic.startswith(b"%PDF"):
            ef.notes.append("extension says pdf but magic bytes differ")
        try:
            if fmt == "sql":
                parse_sql(ef, universe)
            elif fmt == "json":
                parse_json(ef, universe)
            elif fmt == "txt":
                parse_txt(ef, universe)
            elif fmt == "log":
                parse_log(ef, universe)
            elif fmt == "pdf":
                parse_pdf(ef, universe)
            elif fmt == "png":
                ef.status = "NEEDS_EXTRACTION"
            else:
                ef.status = "UNKNOWN_FORMAT"
        except Exception as exc:  # forensics must report, not crash
            ef.status = "PARSE_ERROR"
            ef.notes.append(f"{type(exc).__name__}: {exc}")
        files.append(ef)
    return files


# --------------------------------------------------------------------------- shared context

class Context:
    def __init__(self) -> None:
        self.universe = IdentityUniverse()
        self.rules = load_csv("deletion_rules.csv")
        self.requests = load_csv("deletion_requests.csv")
        self.sample_requests = load_csv("sample_deletion_requests.csv")
        self.sample_output = load_csv("sample_output.csv")
        self.activity = load_csv("system_activity.csv")
        self.manifest = load_csv("evidence_files.csv")
        self.files = load_evidence(self.universe)
        self.by_file = {f.file_id: f for f in self.files}
        self.prefix_system = self._learn_prefix_systems()
        for f in self.files:
            for o in f.observations:
                o.system_hint = o.system_hint or self.prefix_system.get(prefix_of(o.resource_id))
        self.obs_by_resource: dict[str, list[Observation]] = collections.defaultdict(list)
        for f in self.files:
            for o in f.observations:
                self.obs_by_resource[o.resource_id].append(o)
        self.activity_by_resource: dict[str, list[dict]] = collections.defaultdict(list)
        for a in self.activity:
            self.activity_by_resource[a["resource_id"]].append(a)

    def _learn_prefix_systems(self) -> dict[str, str]:
        """resource prefix -> system, learned from rows that state both explicitly."""
        votes: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
        for f in self.files:
            for o in f.observations:
                if o.system_hint:
                    votes[prefix_of(o.resource_id)][o.system_hint] += 1
        for a in self.activity:
            if a["event_type"] not in ("SYNC_RECORD", "INDEX_RECORD", "UPDATE_RECORD", "RETAIN_RECORD",
                                       "CREATE_EXPORT", "RESTORE_BACKUP", "CLASSIFY_REQUEST"):
                votes[prefix_of(a["resource_id"])][a["target_system"]] += 1
        self.prefix_votes = votes
        return {p: c.most_common(1)[0][0] for p, c in votes.items()}

    def sample_cases(self) -> list[tuple[dict, dict, list[dict]]]:
        reqs = {r["case_id"]: r for r in self.sample_requests}
        return [(reqs[o["case_id"]], o, json.loads(o["steps_json"])) for o in self.sample_output]

    def files_at_or_before(self, audit_at: str) -> list[EvidenceFile]:
        cut = parse_ts(audit_at)
        return [f for f in self.files if parse_ts(f.captured_at) <= cut]

    def resolve_identity(self, email: str) -> dict:
        """Current or historical email -> user_id(s), with provenance."""
        e = email.strip().lower()
        hits = []
        for u in self.universe.users:
            if u["email"].lower() == e:
                hits.append((u["user_id"], "user_identities.email (current)"))
        for a in self.universe.audit:
            if a["field_name"] == "email":
                if a["old_value"].lower() == e:
                    hits.append((a["user_id"], f"user_audit_log.old_value {a['audit_id']} (historical)"))
                if a["new_value"].lower() == e:
                    hits.append((a["user_id"], f"user_audit_log.new_value {a['audit_id']}"))
        users = sorted({h[0] for h in hits})
        return {"users": users, "hits": hits}

    def identifier_set(self, user_id: str) -> dict[str, dict[str, str]]:
        """kind -> {value: provenance} for one user (current snapshot + audit history)."""
        out: dict[str, dict[str, str]] = collections.defaultdict(dict)
        out["user_id"][user_id] = "user_identities.user_id"
        for u in self.universe.users:
            if u["user_id"] == user_id:
                out["email"][u["email"].lower()] = "user_identities.email"
                out["phone"][u["phone"]] = "user_identities.phone"
                out["email_hash"][u["email_hash"]] = "user_identities.email_hash"
                out["name"][u["name"]] = "user_identities.name"
        for a in self.universe.audit:
            if a["user_id"] != user_id:
                continue
            kind = a["field_name"]
            for side in ("old_value", "new_value"):
                v = a[side].lower() if kind == "email" else a[side]
                out[kind].setdefault(v, f"user_audit_log.{side} {a['audit_id']}")
        return out


def prefix_of(resource_id: str) -> str:
    m = re.match(r"^(.*)_\d+$", resource_id)
    return m.group(1) if m else resource_id


def mentions(f: EvidenceFile, token: str) -> bool:
    return bool(f.text) and re.search(r"(?<![A-Za-z0-9_])" + re.escape(token) + r"(?![A-Za-z0-9_])", f.text) is not None


def mentions_ci(f: EvidenceFile, token: str) -> bool:
    return bool(f.text) and re.search(r"(?<![A-Za-z0-9_.])" + re.escape(token) + r"(?![A-Za-z0-9_])", f.text, re.I) is not None


def raw_mentions(f: EvidenceFile, token: str) -> bool:
    """Mention test on raw file bytes (what a grep-only system sees; PDFs/PNGs opaque)."""
    try:
        raw = f.path.read_bytes().decode("utf-8", errors="ignore")
    except OSError:
        return False
    return re.search(r"(?<![A-Za-z0-9_])" + re.escape(token) + r"(?![A-Za-z0-9_])", raw) is not None


# --------------------------------------------------------------------------- section 1

def section_inventory(ctx: Context) -> None:
    header("1. INVENTORY")
    sub("CSV row counts (data rows, header excluded)")
    for name in ["deletion_requests.csv", "sample_deletion_requests.csv", "sample_output.csv", "user_identities.csv",
                 "user_audit_log.csv", "system_activity.csv", "deletion_rules.csv", "evidence_files.csv", "output.csv"]:
        print(f"  {name:<32} {len(load_csv(name)):>5}")
    case_ids = [r["case_id"] for r in ctx.requests]
    nums = sorted(int(c.split("_")[1]) for c in case_ids)
    print(f"  deletion_requests case_id range: case_{nums[0]}..case_{nums[-1]}, distinct={len(set(case_ids))}, "
          f"contiguous={nums == list(range(nums[0], nums[-1] + 1))}")
    snums = sorted(int(r["case_id"].split("_")[1]) for r in ctx.sample_requests)
    print(f"  sample case_id range: case_{snums[0]:02d}..case_{snums[-1]:02d}; overlap with eval ids: "
          f"{len(set(case_ids) & {r['case_id'] for r in ctx.sample_requests})}")
    print(f"  sample statuses: {dict(collections.Counter(o['deletion_status'] for o in ctx.sample_output))}")
    print(f"  eval reported_status: {dict(collections.Counter(r['reported_status'] for r in ctx.requests))}")

    sub("Evidence files by format / status")
    on_disk = sorted(p.relative_to(DATASET / "evidence").as_posix() for p in (DATASET / "evidence").rglob("*") if p.is_file())
    in_manifest = sorted(Path(r["file_path"]).relative_to("evidence").as_posix() for r in ctx.manifest)
    print(f"  manifest rows={len(ctx.manifest)} files on disk={len(on_disk)} "
          f"on_disk_not_in_manifest={sorted(set(on_disk) - set(in_manifest))} "
          f"in_manifest_not_on_disk={sorted(set(in_manifest) - set(on_disk))}")
    fmt_status = collections.Counter((f.fmt, f.status) for f in ctx.files)
    for (fmt, status), n in sorted(fmt_status.items()):
        print(f"  {fmt:<5} {status:<18} {n}")
    for f in ctx.files:
        bad = [n for n in f.notes if "magic" in n or "error" in n.lower() or n.startswith("unparsed")]
        if bad:
            print(f"  NOTE {f.file_id}: {bad[:3]}")

    sub("Record shapes per format (container -> field set, observation count)")
    shapes: dict[tuple, int] = collections.Counter()
    for f in ctx.files:
        for o in f.observations:
            container = re.sub(r"_(primary|read_replica|failover_replica|reporting_replica|replica_[a-z_]+)(?=:|$)", "_<role>", o.container)
            keys = tuple(sorted(k for k in o.fields if not k.startswith("_")))
            shapes[(f.fmt, o.system_hint, container, keys)] += 1
    for (fmt, system, container, keys), n in sorted(shapes.items(), key=lambda x: (x[0][0], str(x[0][1]), x[0][2])):
        print(f"  {fmt:<4} {str(system):<22} {container:<52} n={n:<4} keys={list(keys)}")

    sub("Log syntaxes per file")
    for f in ctx.files:
        if f.fmt == "log":
            syn = [n for n in f.notes if n.startswith("syntax=")]
            print(f"  {f.file_id} {f.path.name:<52} {syn[0] if syn else ''}")

    sub("Identifier-bearing fields (field -> identifier kinds seen, count)")
    idf: dict[tuple[str, str], int] = collections.Counter()
    for f in ctx.files:
        for o in f.observations:
            def walk(key, value):
                if isinstance(value, dict):
                    for k, v in value.items():
                        walk(k, v)
                elif isinstance(value, list):
                    for v in value:
                        walk(key, v)
                else:
                    for kind, _ in ctx.universe.classify(key, value):
                        idf[(key, kind)] += 1
            walk("", o.fields)
    for (key, kind), n in sorted(idf.items()):
        print(f"  {key:<24} {kind:<11} {n}")
    print("  Note: log lookup keys (lookup_key, match_key, subject_key, scan_key, subject_ref, correlation_key) are joined to")
    print("  the resource line through trace_id; see id_provenance=trace_join counts below.")
    prov = collections.Counter(o.id_provenance for f in ctx.files for o in f.observations if any(o.identifiers.values()))
    noid = sum(1 for f in ctx.files for o in f.observations if not any(o.identifiers.values()))
    print(f"  observations with identifiers by provenance: {dict(prov)}; observations with no identifier: {noid}")

    sub("Case-insensitive identifier variants in evidence (same email, different case)")
    raw_emails = collections.defaultdict(set)
    for f in ctx.files:
        for e in EMAIL_RE.findall(f.text):
            raw_emails[e.lower()].add(e)
    variants = {k: v for k, v in raw_emails.items() if len(v) > 1 or any(x != k for x in v)}
    print(f"  distinct emails in evidence text: {len(raw_emails)}; with non-lowercase spelling: {len(variants)}")
    for k, v in sorted(variants.items())[:8]:
        print(f"    {k} <- {sorted(v)}")

    sub("resource_id prefix -> system_id votes (from rows that state the system)")
    for p, votes in sorted(ctx.prefix_votes.items()):
        flag = "  CONFLICT" if len(votes) > 1 else ""
        print(f"  {p:<16} {dict(votes)}{flag}")
    unmapped = collections.Counter(prefix_of(o.resource_id) for f in ctx.files for o in f.observations if not o.system_hint)
    print(f"  observation prefixes with no learned system: {dict(unmapped)}")

    sub("Timestamp formats")
    fmts = collections.Counter()
    for name, cols in [("deletion_requests.csv", ["requested_at", "reported_at", "audit_at"]),
                       ("user_identities.csv", ["updated_at"]), ("user_audit_log.csv", ["event_time"]),
                       ("system_activity.csv", ["event_time"]), ("evidence_files.csv", ["captured_at"])]:
        for r in load_csv(name):
            for c in cols:
                shape = re.sub(r"\d", "9", r[c])
                fmts[(name, c, shape)] += 1
    for (name, c, shape), n in sorted(fmts.items()):
        print(f"  {name:<24} {c:<12} {shape:<24} {n}")
    pdf_dates = sorted({m for f in ctx.files if f.fmt == "pdf" for m in re.findall(r"\d\d [A-Z][a-z]{2} \d{4}", f.text)})
    print(f"  PDF text uses human dates (e.g. {pdf_dates[:3]}); log lines prefix ISO-8601 Z; SQL/JSON use ISO-8601 Z")

    sub("Capture-time consistency (manifest captured_at vs times stated inside the file)")
    for f in ctx.files:
        if not f.content_times:
            continue
        cap = parse_ts(f.captured_at)
        times = [t for t in (parse_ts(x) for x in f.content_times) if t]
        later = [t for t in times if t > cap]
        flags = []
        if f.fmt in ("sql", "txt") and f.content_times and parse_ts(f.content_times[0]) != cap:
            flags.append(f"header={f.content_times[0]}")
        if later:
            flags.append(f"{len(later)} stated times AFTER manifest captured_at (max {max(later).isoformat()})")
        if times and min(times) < cap and f.fmt == "log":
            flags.append(f"log lines span {min(times).strftime('%Y-%m-%dT%H:%M:%SZ')}..{max(times).strftime('%Y-%m-%dT%H:%M:%SZ')}")
        fname = re.search(r"(20\d{6})", f.path.name)
        if fname and fname.group(1) != f.captured_at[:10].replace("-", ""):
            flags.append(f"filename date {fname.group(1)} != captured_at date")
        if flags:
            print(f"  {f.file_id} cap={f.captured_at} {'; '.join(flags)}")

    sub("Records whose own timestamp is AFTER the file's manifest captured_at")
    for f in ctx.files:
        cap = parse_ts(f.captured_at)
        for o in f.observations:
            t = parse_ts(o.observed_at)
            if t and t > cap:
                same = [(x.observed_at, x.fields.get("result") or x.fields.get("status_code")) for x in f.observations
                        if x.resource_id == o.resource_id and x is not o]
                print(f"  {f.file_id} cap={f.captured_at} {o.resource_id} observed_at={o.observed_at} "
                      f"fields={o.fields} other sightings in file={same}")

    sub("Duplicate / conflicting observations of one resource inside one file (lookup-start lines excluded)")
    shown = 0
    for f in ctx.files:
        grp = collections.defaultdict(list)
        for o in f.observations:
            if not o.container.endswith("_started"):
                grp[o.resource_id].append(o)
        for rid, obs in grp.items():
            if len(obs) < 2:
                continue
            sigs = {json.dumps({k: v for k, v in o.fields.items() if k not in ("updated_at", "_service")}, sort_keys=True, default=str) for o in obs}
            containers = sorted({o.container for o in obs})
            if len(sigs) > 1 and shown < 40:
                detail = [(o.observed_at, {k: v for k, v in o.fields.items() if k in ("result", "rows", "http_status", "marker_found", "record_type")})
                          for o in obs]
                print(f"  {f.file_id} {rid:<18} {len(obs)} sightings in {containers}: {detail}")
                shown += 1
    print(f"  (conflict rows shown: {shown})")
    dup_rows = 0
    by_res_file = collections.Counter((f.file_id, o.resource_id, o.container) for f in ctx.files for o in f.observations)
    dup_rows = sum(1 for k, n in by_res_file.items() if n > 1)
    print(f"  same resource repeated within the same container of one file: {dup_rows}")

    sub("Replica drift inside SQL snapshots (resource present in one table role but not its sibling)")
    for f in ctx.files:
        if f.fmt != "sql":
            continue
        roles = collections.defaultdict(set)
        for o in f.observations:
            base = re.sub(r"_(primary|read_replica|failover_replica|reporting_replica|replica_[a-z_]+)(?=:|$)", "", o.container.split(":")[0])
            role = o.container.split(":")[0][len(base) + 1:] if o.container.split(":")[0] != base else "single"
            roles[(base, role)].add(o.resource_id)
        bases = collections.defaultdict(dict)
        for (base, role), ids in roles.items():
            bases[base][role] = ids
        for base, rmap in bases.items():
            if len(rmap) < 2:
                continue
            allids = set().union(*rmap.values())
            for rid in sorted(allids):
                present = sorted(r for r, ids in rmap.items() if rid in ids)
                if len(present) != len(rmap):
                    print(f"  {f.file_id} {base:<28} {rid:<20} present_in={present} absent_in={sorted(set(rmap) - set(present))}")
            for rid in sorted(set.intersection(*rmap.values())):
                contents = {r: [json.dumps({k: v for k, v in o.fields.items() if k != 'updated_at'}, sort_keys=True, default=str)
                                for o in f.observations if o.resource_id == rid and o.container.split(":")[0].startswith(base + "_" + r)] for r in rmap}
                distinct = {c for cs in contents.values() for c in cs}
                if len(distinct) > 1:
                    print(f"  {f.file_id} {base:<28} {rid:<20} content differs across {sorted(rmap)}")


# --------------------------------------------------------------------------- section 2

def section_scope(ctx: Context) -> None:
    header("2. SCOPE INVENTORY (file -> systems/containers covered, kind, capture time)")
    print("  kind: table_snapshot = SQL table dump; collection_snapshot = JSON collection dump; point_lookup = per-resource")
    print("  service read-back; record_lookup = ledger lines (system,resource,record_type,result,subject); log_event = log line")
    print("  carrying a resource_id; document = PDF text layer. Systems for SQL flat tables / PDFs / logs come from the")
    print("  learned prefix map (section 1), not from the file.")
    for f in ctx.files:
        print(f"\n  {f.file_id} {f.captured_at} {f.fmt:<4} {f.status:<16} origin={f.origin} file={f.path.name}")
        if f.status == "NEEDS_EXTRACTION":
            print(f"      NEEDS_EXTRACTION (no deterministic text layer). origin={f.origin}; filename hint only: {f.path.stem}")
            continue
        notes = [n for n in f.notes if n.startswith("topology") or n.startswith("syntax")]
        if notes:
            print(f"      {'; '.join(notes)}")
        per_sys = collections.defaultdict(collections.Counter)
        for o in f.observations:
            per_sys[o.system_hint][o.container] += 1
        declared = collections.defaultdict(list)
        for s in f.scopes:
            if s.container == "snapshot_scope":
                declared["snapshot_scope"].append(f"{s.system}@{s.captured_at}")
            elif s.rows == 0:
                declared["empty"].append(f"{s.system or '?'}:{s.container}")
        for system, cont in sorted(per_sys.items(), key=lambda x: str(x[0])):
            print(f"      {str(system):<22} " + ", ".join(f"{c}={n}" for c, n in sorted(cont.items())))
        for k, v in declared.items():
            print(f"      declared {k}: {v}")


# --------------------------------------------------------------------------- section 3

CATEGORY_RULES = [
    # (predicate description, predicate, category)  -- forensics proposal, not the production engine
    ("record_type field states the category", lambda o: o.fields.get("record_type"), None),
    ("analytics record with email_opt_in", lambda o: o.system_hint == "analytics_platform" and "email_opt_in" in o.fields, "MARKETING_PROFILE?"),
    ("account_service profile", lambda o: o.system_hint == "account_service", "USER_PROFILE"),
    ("search_service document/query", lambda o: o.system_hint == "search_service", "SEARCH_INDEX_ENTRY"),
    ("analytics profile/report", lambda o: o.system_hint == "analytics_platform", "ANALYTICS_PROFILE"),
    ("support record naming a file / attachment scan", lambda o: o.system_hint == "support_platform" and ("file" in o.fields or "attachment" in o.container), "SUPPORT_ATTACHMENT"),
    ("support record (message/ticket/conversation)", lambda o: o.system_hint == "support_platform", "SUPPORT_MESSAGE?"),
    ("export_service export", lambda o: o.system_hint == "export_service", "USER_EXPORT"),
    ("backup_service archive/restore entry", lambda o: o.system_hint == "backup_service", "BACKUP_ARCHIVE"),
    ("security_service audit record", lambda o: o.system_hint == "security_service", "SECURITY_AUDIT_LOG"),
    ("identity_service deleted reference", lambda o: o.system_hint == "identity_service", "ACCOUNT_TOMBSTONE"),
    ("legal_service hold", lambda o: o.system_hint == "legal_service", "LEGAL_HOLD_RECORD"),
    ("integration_service connector", lambda o: o.system_hint == "integration_service", "DATA_FLOW (not a deletion_rules category)"),
]


def propose_category(o: Observation) -> tuple[str, str]:
    for desc, pred, cat in CATEGORY_RULES:
        v = pred(o)
        if v:
            return (cat or str(v)), desc
    return "UNMAPPED", "no rule"


def section_category(ctx: Context) -> None:
    header("3. CATEGORY MAPPING (record shape -> deletion_rules.csv data_category)")
    sub("deletion_rules.csv")
    for r in ctx.rules:
        print(f"  {r['rule_id']} {r['data_category']:<22} {r['required_action']:<9} condition: {r['condition']}")
    rule_cats = {r["data_category"] for r in ctx.rules}

    sub("record_type values stated by ledger/handover/log record_lookup rows")
    rt = collections.Counter((o.fields.get("record_type"), o.fields.get("result")) for f in ctx.files for o in f.observations if o.fields.get("record_type"))
    for (t, res), n in sorted(rt.items()):
        print(f"  {t:<22} result={res:<13} n={n:<4} {'in rules' if t in rule_cats else 'NOT a deletion_rules category'}")

    sub("Shape -> proposed category (proposal for review; '?' = does not map cleanly)")
    seen = collections.Counter()
    for f in ctx.files:
        for o in f.observations:
            container = re.sub(r"_(primary|read_replica|failover_replica|reporting_replica|replica_[a-z_]+)(?=:|$)", "_<role>", o.container)
            cat, why = propose_category(o)
            seen[(str(o.system_hint), container, cat, why)] += 1
    for (system, container, cat, why), n in sorted(seen.items()):
        print(f"  {system:<20} {container:<48} -> {cat:<40} n={n:<4} [{why}]")
    covered = {c.rstrip("?") for (_, _, c, _) in seen}
    print(f"\n  deletion_rules categories with NO matching evidence shape: {sorted(rule_cats - covered)}")
    act_targets = collections.Counter(a["target_system"] for a in ctx.activity)
    print(f"  activity-only target systems (no evidence observation): "
          f"{sorted(s for s in act_targets if s not in {o.system_hint for f in ctx.files for o in f.observations})}")

    sub("Sample answers: expected treatment vs activity event_type on the same resource (claims, not truth)")
    treat_of = {"DELETE_RECORD": "DELETE", "REDACT_RECORD": "REDACT", "ANONYMIZE_RECORD": "ANONYMIZE",
                "DISABLE_DATA_FLOW": "DISABLE", "BLOCK_RESTORE": "BLOCK"}
    verify_of = {"VERIFY_RECORD_ABSENT": "DELETE", "VERIFY_RECORD_REDACTED": "REDACT", "VERIFY_RECORD_ANONYMIZED": "ANONYMIZE",
                 "VERIFY_RECORD_PRESENT": "RETAIN", "VERIFY_DATA_FLOW_DISABLED": "DISABLE", "VERIFY_RESTORE_BLOCKED": "BLOCK"}
    agree = collections.Counter()
    for req, out, steps in ctx.sample_cases():
        expected = {}
        for s in steps:
            if s["operation"] in treat_of:
                expected[s["resource_id"]] = treat_of[s["operation"]]
            elif s["operation"] in verify_of:
                expected.setdefault(s["resource_id"], verify_of[s["operation"]])
        for rid, treat in expected.items():
            acts = [a["event_type"] for a in ctx.activity_by_resource.get(rid, []) if a["source_system"] == "deletion_worker"]
            act = acts[0] if acts else None
            act_treat = {"DELETE_RECORD": "DELETE", "REDACT_RECORD": "REDACT", "ANONYMIZE_RECORD": "ANONYMIZE",
                         "DISABLE_DATA_FLOW": "DISABLE", "CHECK_DATA_FLOW": "DISABLE", "BLOCK_RESTORE": "BLOCK",
                         "CHECK_RESTORE_ACCESS": "BLOCK"}.get(act)
            agree[(prefix_of(rid), treat, act or "none", "agree" if act_treat == treat else ("no-activity" if not act else "DIFFER"))] += 1
    for (p, treat, act, verdict), n in sorted(agree.items()):
        print(f"  {p:<14} expected={treat:<10} deletion_worker_event={act:<22} {verdict:<11} n={n}")

    sub("Sample answers: expected treatment per resource prefix (what the samples show)")
    tp = collections.Counter()
    for req, out, steps in ctx.sample_cases():
        ops = collections.defaultdict(list)
        for s in steps:
            ops[s["resource_id"]].append(s["operation"])
        for rid, o in ops.items():
            tp[(prefix_of(rid), " + ".join(sorted(set(o), key=STEP_OP_ORDER.index)))] += 1
    for (p, o), n in sorted(tp.items()):
        print(f"  {p:<14} n={n:<3} {o}")


# --------------------------------------------------------------------------- section 4

def latest(files: list[EvidenceFile]) -> EvidenceFile | None:
    return max(files, key=lambda f: (parse_ts(f.captured_at), f.file_id)) if files else None


def subject_identifiers(ctx: Context, req: dict) -> tuple[list[str], dict[str, dict[str, str]]]:
    res = ctx.resolve_identity(req["email"])
    ids: dict[str, dict[str, str]] = collections.defaultdict(dict)
    for u in res["users"]:
        for k, v in ctx.identifier_set(u).items():
            ids[k].update(v)
    ids["email"].setdefault(req["email"].lower(), "request.email")
    return res["users"], ids


def system_covered(f: EvidenceFile, system: str) -> bool:
    if any(o.system_hint == system for o in f.observations):
        return True
    return any(s.system == system for s in f.scopes)


def full_snapshot_of(f: EvidenceFile, system: str) -> bool:
    """File dumps a whole table/collection for the system (so omission can mean absence)."""
    for s in f.scopes:
        if s.kind in ("table_snapshot", "collection_snapshot") and s.system == system:
            return True
    if f.fmt == "sql":
        for s in f.scopes:
            if s.kind == "table_snapshot" and s.system is None:
                sample = next((o for o in f.observations if o.container == s.container), None)
                if sample and sample.system_hint == system:
                    return True
    return False


def section_citation(ctx: Context) -> None:
    header("4. CITATION TEST (75 cited sample steps)")
    hyps = {
        "A_raw": "latest file <= audit_at whose RAW BYTES mention the resource_id (grep view; PDF/PNG opaque)",
        "A": "latest file <= audit_at whose text mentions the resource_id (PDF text layer included)",
        "A_email_cs": "latest file <= audit_at mentioning the request email (case-sensitive, exact spelling)",
        "A_email": "latest file <= audit_at mentioning the request email (case-insensitive)",
        "A_ids": "latest file <= audit_at mentioning any subject identifier (email/hash/phone/user_id, current+historical)",
        "Aprime_scope": "latest file <= audit_at covering the resource's system (observation or declared scope)",
        "Aprime_full": "latest file <= audit_at that is a full table/collection snapshot of the resource's system",
        "C": "A if any file <= audit_at mentions the resource_id, else Aprime_full",
        "D": "A if mentioned, else latest file <= audit_at whose scope covers the system AND that the case's other cited steps use",
    }
    for k, v in hyps.items():
        print(f"  {k:<13} {v}")
    rows = []
    for req, out, steps in ctx.sample_cases():
        cited = [s for s in steps if s["evidence_id"]]
        users, ids = subject_identifiers(ctx, req)
        window = ctx.files_at_or_before(req["audit_at"])
        id_values = [v for k in ("email", "email_hash", "phone", "user_id") for v in ids.get(k, {})]
        for s in cited:
            rid, system = s["resource_id"], s["system_id"]
            pred = {}
            pred["A_raw"] = latest([f for f in window if raw_mentions(f, rid)])
            pred["A"] = latest([f for f in window if mentions(f, rid)])
            pred["A_email_cs"] = latest([f for f in window if mentions(f, req["email"])])
            pred["A_email"] = latest([f for f in window if mentions_ci(f, req["email"])])
            pred["A_ids"] = latest([f for f in window if any(mentions_ci(f, v) for v in id_values)])
            pred["Aprime_scope"] = latest([f for f in window if system_covered(f, system)])
            pred["Aprime_full"] = latest([f for f in window if full_snapshot_of(f, system)])
            pred["C"] = pred["A"] or pred["Aprime_full"]
            rows.append((req, s, pred, cited))
    # D needs the other steps' A predictions
    for i, (req, s, pred, cited) in enumerate(rows):
        if pred["A"]:
            pred["D"] = pred["A"]
            continue
        others = {r[2]["A"].file_id for r in rows if r[0] is req and r[1] is not s and r[2]["A"]}
        window = ctx.files_at_or_before(req["audit_at"])
        cands = [f for f in window if f.file_id in others and system_covered(f, s["system_id"])]
        pred["D"] = latest(cands) or pred["Aprime_full"]
    sub("Match counts")
    for h in hyps:
        n = sum(1 for _, s, pred, _ in rows if pred[h] and pred[h].file_id == s["evidence_id"])
        print(f"  {h:<13} {pct(n, len(rows))}")
    sub("Every mismatch under A (with what the other rules say)")
    for req, s, pred, _ in rows:
        if pred["A"] and pred["A"].file_id == s["evidence_id"]:
            continue
        exp = ctx.by_file[s["evidence_id"]]
        others = {h: (pred[h].file_id if pred[h] else None) for h in ("A_raw", "Aprime_scope", "Aprime_full", "C", "D")}
        mentioned_anywhere = [f.file_id for f in ctx.files if mentions(f, s["resource_id"])]
        print(f"  {req['case_id']} {s['operation']:<24} {s['system_id']}/{s['resource_id']} expected={s['evidence_id']} "
              f"({exp.fmt}, {exp.captured_at}) A={pred['A'].file_id if pred['A'] else None}")
        print(f"      other rules: {others}; files mentioning the id at any time: {mentioned_anywhere}")
        print(f"      expected file mentions id: {mentions(exp, s['resource_id'])}; expected file covers system: "
              f"{system_covered(exp, s['system_id'])}; full snapshot of system: {full_snapshot_of(exp, s['system_id'])}")
    sub("Mismatches under C (the best deterministic rule found here)")
    for req, s, pred, _ in rows:
        if pred["C"] and pred["C"].file_id == s["evidence_id"]:
            continue
        window = ctx.files_at_or_before(req["audit_at"])
        competitors = sorted([f for f in window if full_snapshot_of(f, s["system_id"])], key=lambda f: f.captured_at)[-4:]
        print(f"  {req['case_id']} {s['system_id']}/{s['resource_id']} expected={s['evidence_id']} C={pred['C'].file_id if pred['C'] else None}")
        print(f"      latest full-snapshot candidates <= audit_at {req['audit_at']}: "
              f"{[(f.file_id, f.captured_at, f.path.name) for f in competitors]}")
        pngs = [(f.file_id, f.captured_at, f.origin) for f in window if f.status == "NEEDS_EXTRACTION"
                and parse_ts(f.captured_at) >= parse_ts(s.get('_x', '1970-01-01T00:00:00Z'))]
        print(f"      unreadable (NEEDS_EXTRACTION) files in window: {len(pngs)} -> UNKNOWN whether one mentions the id")
    sub("Is the expected evidence file the same for every cited step of a case?")
    by_case = collections.defaultdict(set)
    for req, s, _, _ in rows:
        by_case[req["case_id"]].add(s["evidence_id"])
    multi = {c: sorted(v) for c, v in by_case.items() if len(v) > 1}
    print(f"  cases with >1 distinct cited file: {multi if multi else 'none'} (of {len(by_case)} cases with citations)")
    sub("Expected evidence capture time relative to audit_at")
    gaps = []
    for req, s, _, _ in rows:
        gap = (parse_ts(req["audit_at"]) - parse_ts(ctx.by_file[s["evidence_id"]].captured_at)).total_seconds()
        gaps.append((gap, req["case_id"], s["evidence_id"]))
    print(f"  min gap (s)={min(gaps)[0]:.0f} {min(gaps)[1:]}; max gap (days)={max(gaps)[0] / 86400:.1f} {max(gaps)[1:]}; "
          f"gap==0 (captured_at == audit_at): {sorted({(c, e) for g, c, e in gaps if g == 0})}")
    print(f"  any expected file captured AFTER audit_at: {any(g < 0 for g, _, _ in gaps)}")


# --------------------------------------------------------------------------- section 5

def section_absent(ctx: Context) -> None:
    header("5. WHERE DO RESOURCE IDS OF ABSENT RECORDS COME FROM? (sample answers)")
    print("  'absent at audit_at' = VERIFY_RECORD_ABSENT with no FINDING/REMEDIATION on the same resource.")
    pattern = collections.Counter()
    for req, out, steps in ctx.sample_cases():
        users, ids = subject_identifiers(ctx, req)
        touched = {s["resource_id"] for s in steps if s["phase"] != "VERIFICATION"}
        for s in steps:
            if s["operation"] != "VERIFY_RECORD_ABSENT" or s["resource_id"] in touched:
                continue
            rid = s["resource_id"]
            ev = [o for o in ctx.obs_by_resource.get(rid, [])]
            linked = []
            for o in ev:
                hit = sorted({f"{k}:{v}" for k, vals in o.identifiers.items() for v in vals if v in ids.get(k, {})})
                if hit:
                    linked.append(f"{o.file_id}[{o.id_provenance}]{hit[0]}")
            acts = [f"{a['activity_id']}:{a['event_type']}:{a['reported_result']}" for a in ctx.activity_by_resource.get(rid, [])]
            link_rows = [a["activity_id"] for a in ctx.activity if a["event_type"] == "LINK_RESOURCE_IDENTITY" and rid in a["details"]]
            src = []
            if ev:
                src.append("evidence(linked)" if linked else "evidence(unlinked)")
            if acts:
                src.append("activity")
            if link_rows:
                src.append("LINK_RESOURCE_IDENTITY")
            key = "+".join(src) or "NOWHERE"
            pattern[key] += 1
            print(f"  {req['case_id']} {rid:<14} in_evidence={sorted({o.file_id for o in ev})} linked_by={linked[:2]} "
                  f"activity={acts} link_rows={link_rows} -> {key}")
    sub("Summary")
    for k, n in pattern.most_common():
        print(f"  {k:<44} {n}")
    sub("Global: account_service DELETE_RECORD rows by details template")
    tmpl = collections.Counter(re.sub(r"account_\d+", "<id>", a["details"]) for a in ctx.activity
                               if a["event_type"] == "DELETE_RECORD" and a["target_system"] == "account_service")
    for t, n in tmpl.most_common():
        print(f"  {n:>4} {t}")
    acc_rows = [a for a in ctx.activity if a["event_type"] == "DELETE_RECORD" and a["target_system"] == "account_service"]
    in_ev = sum(1 for a in acc_rows if a["resource_id"] in ctx.obs_by_resource)
    print(f"  account DELETE rows whose resource_id appears in some evidence file: {pct(in_ev, len(acc_rows))}")
    sub("Eval set (no labels used): account DELETE_RECORD candidates near each deletion case's reported_at")
    print("  window = [reported_at - 3h, reported_at + 3h]; linked = candidate's resource observed with a subject identifier")
    print("  (observation time <= audit_at). Shows how often attribution would need elimination rather than a direct link.")
    nondel = {r["case_id"] for r in ctx.requests if r["reported_status"] == "NOT_STARTED"}
    dist = collections.Counter()
    for r in ctx.requests:
        users, ids = subject_identifiers(ctx, r)
        rep = parse_ts(r["reported_at"]).timestamp()
        cut = parse_ts(r["audit_at"])
        cands = [a for a in acc_rows if abs(parse_ts(a["event_time"]).timestamp() - rep) <= 3 * 3600]
        linked = [a for a in cands if any(
            (parse_ts(o.observed_at) or parse_ts(ctx.by_file[o.file_id].captured_at)) <= cut and
            any(v in ids.get(k, {}) for k, vals in o.identifiers.items() for v in vals)
            for o in ctx.obs_by_resource.get(a["resource_id"], []))]
        dist[(min(len(cands), 7), min(len(linked), 2))] += 1
    for (n, l), c in sorted(dist.items()):
        print(f"  candidates={n if n < 7 else '7+':<3} linked={l if l < 2 else '2+'}: {c} cases")
    print(f"  (NOT_STARTED eval rows, which include the 10 non-deletion texts: {len(nondel)})")

    sub("Temporal proximity of the case's account DELETE_RECORD to reported_at (samples)")
    for req, out, steps in ctx.sample_cases():
        acc = [s["resource_id"] for s in steps if s["system_id"] == "account_service"]
        if not acc:
            continue
        for a in ctx.activity_by_resource.get(acc[0], []):
            if a["event_type"] != "DELETE_RECORD":
                continue
            d = (parse_ts(a["event_time"]) - parse_ts(req["reported_at"])).total_seconds() / 60
            lo, hi = parse_ts(req["reported_at"]).timestamp() - 3 * 3600, parse_ts(req["reported_at"]).timestamp() + 3 * 3600
            rivals = [x["resource_id"] for x in acc_rows if lo <= parse_ts(x["event_time"]).timestamp() <= hi]
            print(f"  {req['case_id']} {acc[0]:<14} delete_at-reported_at={d:+7.1f} min; account DELETE rows within +-3h of "
                  f"reported_at: {len(rivals)}")


# --------------------------------------------------------------------------- section 6

def section_identity(ctx: Context) -> None:
    header("6. IDENTITY RESOLUTION WITH PROVENANCE")
    sub("All requests: how the request email resolves")
    stats = collections.Counter()
    for r in ctx.sample_requests + ctx.requests:
        res = ctx.resolve_identity(r["email"])
        kinds = sorted({h[1].split(" ")[0] for h in res["hits"]})
        stats[(len(res["users"]), tuple(kinds))] += 1
    for (n, kinds), c in sorted(stats.items()):
        print(f"  users_matched={n} via={list(kinds)}: {c}")
    sub("Identifier collisions across users (same value owned by >1 user, current or historical)")
    owners = collections.defaultdict(set)
    for u in ctx.universe.users:
        owners[("email", u["email"].lower())].add(u["user_id"])
        owners[("phone", u["phone"])].add(u["user_id"])
        owners[("email_hash", u["email_hash"])].add(u["user_id"])
        owners[("name", u["name"])].add(u["user_id"])
    for a in ctx.universe.audit:
        for side in ("old_value", "new_value"):
            v = a[side].lower() if a["field_name"] == "email" else a[side]
            owners[(a["field_name"], v)].add(a["user_id"])
    coll = {k: v for k, v in owners.items() if len(v) > 1}
    print(f"  colliding values: {len(coll)}")
    for (kind, v), us in sorted(coll.items())[:30]:
        print(f"    {kind:<10} {v:<40} {sorted(us)}")
    sub("Near-name decoys: evidence names that share a token with a sample subject name but are not that subject")
    for req, out, steps in ctx.sample_cases():
        users, ids = subject_identifiers(ctx, req)
        for name in ids.get("name", {}):
            toks = {t.lower() for t in re.split(r"[\s\-]+", name) if len(t) > 3}
            near = set()
            for f in ctx.files:
                for o in f.observations:
                    for n in o.identifiers.get("name", ()):
                        if n != name and toks & {t.lower() for t in re.split(r"[\s\-]+", n)}:
                            near.add((n, f.file_id))
            if near:
                print(f"  {req['case_id']} subject='{name}' near names: {sorted(near)[:4]}")
        email_local = req["email"].split("@")[0].lower()
        near_e = sorted({(e, f.file_id) for f in ctx.files for e in (x.lower() for x in EMAIL_RE.findall(f.text))
                         if e != req["email"].lower() and e not in ids["email"] and (email_local in e or e.split("@")[0] in email_local)})
        if near_e:
            print(f"  {req['case_id']} email='{req['email']}' near emails: {near_e[:4]}")
    sub("Summary over expected sample resources: strongest available link, by information-time policy")
    print("  P1 = file captured_at <= audit_at; P2 = observation's own time <= audit_at (file may be later); P3 = any time")
    tally = collections.Counter()
    for req, out, steps in ctx.sample_cases():
        users, ids = subject_identifiers(ctx, req)
        cut = parse_ts(req["audit_at"])
        for rid in sorted({s["resource_id"] for s in steps}):
            best = None
            for policy in ("P1", "P2", "P3"):
                for o in ctx.obs_by_resource.get(rid, []):
                    f = ctx.by_file[o.file_id]
                    t_file = parse_ts(f.captured_at)
                    t_obs = parse_ts(o.observed_at) or t_file
                    ok = {"P1": t_file <= cut, "P2": t_obs <= cut, "P3": True}[policy]
                    if ok and any(v in ids.get(k, {}) for k, vals in o.identifiers.items() for v in vals):
                        best = policy
                        break
                if best:
                    break
            if not best:
                best = "ACTIVITY_ONLY" if rid in ctx.activity_by_resource else "NO_TEXT_SOURCE"
            tally[(prefix_of(rid), best)] += 1
    by_policy = collections.Counter()
    for (p, best), n in sorted(tally.items()):
        by_policy[best] += n
        print(f"  {p:<14} {best:<15} {n}")
    print(f"  totals: {dict(by_policy)} of {sum(by_policy.values())} expected (case, resource) pairs")

    sub("Per sample case: resolved user, identifier set, and how each expected resource links to the subject")
    for req, out, steps in ctx.sample_cases():
        res = ctx.resolve_identity(req["email"])
        users, ids = subject_identifiers(ctx, req)
        print(f"\n  {req['case_id']} email={req['email']} -> users={users} via {[h[1] for h in res['hits']]}")
        for kind in ("email", "phone", "email_hash", "name"):
            vals = ids.get(kind, {})
            if vals:
                print(f"      {kind:<10} " + "; ".join(f"{(v[:12] + '...') if kind == 'email_hash' else v} <{p}>" for v, p in vals.items()))
        cut = parse_ts(req["audit_at"])
        for rid in sorted({s["resource_id"] for s in steps}):
            links = []
            for o in ctx.obs_by_resource.get(rid, []):
                hit = sorted({f"{k}={v[:12] if k == 'email_hash' else v}" for k, vals in o.identifiers.items() for v in vals if v in ids.get(k, {})})
                if hit:
                    f = ctx.by_file[o.file_id]
                    when = "<=audit" if parse_ts(f.captured_at) <= cut else "AFTER-audit"
                    links.append(f"{o.file_id}({when},{o.id_provenance}):{','.join(hit)}")
            cat = [a["activity_id"] for a in ctx.activity if a["event_type"] == "LINK_RESOURCE_IDENTITY" and re.search(rf"\b{rid}\b", a["details"])]
            verdict = "LINKED" if links or cat else ("ACTIVITY_ONLY" if rid in ctx.activity_by_resource else "NO_TEXT_SOURCE")
            print(f"      {rid:<18} {verdict:<15} {links[:3]}{' catalog=' + str(cat) if cat else ''}")


# --------------------------------------------------------------------------- section 7

def section_temporal(ctx: Context) -> None:
    header("7. TEMPORAL: DELETE followed by SYNC / RESTORE / INDEX, and state at audit_at")
    print("  reported = activity reported_result (claim); observed = evidence observation (with capture time);")
    print("  reconstructed@audit = latest observation captured <= audit_at. These three are kept separate.")
    sub("Global counts of follow-up events after a DELETE_RECORD on the same resource_id")
    deletes = collections.defaultdict(list)
    for a in ctx.activity:
        if a["event_type"] == "DELETE_RECORD":
            deletes[a["resource_id"]].append(a)
    follow = collections.Counter()
    examples = collections.defaultdict(list)
    for a in ctx.activity:
        if a["event_type"] in ("SYNC_RECORD", "RESTORE_BACKUP", "INDEX_RECORD", "UPDATE_RECORD", "REFRESH_CACHE"):
            targets = {a["resource_id"]} | set(ID_TOKEN_RE.findall(a["details"]) and [m.group(0) for m in ID_TOKEN_RE.finditer(a["details"])])
            for rid in targets:
                for d in deletes.get(rid, []):
                    if d["event_time"] < a["event_time"]:
                        key = (a["event_type"], a["source_system"], a["target_system"])
                        follow[key] += 1
                        examples[key].append((d, a, rid))
    for key, n in follow.most_common():
        print(f"  {n:>4} DELETE then {key}")
    sub("RESTORE_BACKUP rows (all)")
    for a in ctx.activity:
        if a["event_type"] == "RESTORE_BACKUP":
            print(f"  {a['activity_id']} {a['event_time']} {a['resource_id']} -> {a['details']}")
            for rid in [m.group(0) for m in ID_TOKEN_RE.finditer(a["details"])]:
                for o in ctx.obs_by_resource.get(rid, []):
                    print(f"      observed {rid} in {o.file_id}@{ctx.by_file[o.file_id].captured_at} fields={dict(list(o.fields.items())[:4])}")
                for d in deletes.get(rid, []):
                    print(f"      earlier DELETE {d['activity_id']} {d['event_time']} {d['reported_result']}")
    sub("Integration SYNC_RECORD 'Active connector repopulated downstream resource' rows")
    sync_rows = [a for a in ctx.activity if a["event_type"] == "SYNC_RECORD" and a["source_system"] == "integration_service"]
    with_prior_delete = sum(1 for a in sync_rows if any(d["event_time"] < a["event_time"] for d in deletes.get(a["resource_id"], [])))
    print(f"  rows={len(sync_rows)}; with an earlier DELETE_RECORD on the same resource: {with_prior_delete}")
    sub("Per sample case timeline (resources in the expected answer + follow-ups), times relative to audit_at")
    for req, out, steps in ctx.sample_cases():
        cut = parse_ts(req["audit_at"])
        rids = sorted({s["resource_id"] for s in steps})
        expected = {s["resource_id"]: s["operation"] for s in steps if s["phase"] != "REMEDIATION"}
        print(f"\n  {req['case_id']} {out['deletion_status']} reported_status={req['reported_status']} audit_at={req['audit_at']}")
        for rid in rids:
            events = []
            for a in ctx.activity_by_resource.get(rid, []):
                dt = (parse_ts(a["event_time"]) - cut).total_seconds() / 3600
                events.append((a["event_time"], f"ACT {a['event_type']}:{a['reported_result']} ({dt:+.1f}h)"))
            for a in ctx.activity:
                if a["resource_id"] != rid and re.search(rf"\b{rid}\b", a["details"]):
                    dt = (parse_ts(a["event_time"]) - cut).total_seconds() / 3600
                    events.append((a["event_time"], f"ACT-mention {a['activity_id']} {a['event_type']} ({dt:+.1f}h)"))
            recon = None
            for o in ctx.obs_by_resource.get(rid, []):
                f = ctx.by_file[o.file_id]
                dt = (parse_ts(f.captured_at) - cut).total_seconds() / 3600
                state = {k: v for k, v in o.fields.items() if k in ("status_code", "rows", "http_status", "result", "enabled",
                                                                     "restore_allowed", "account_status", "available", "download_available",
                                                                     "marker_found", "direct_identifiers", "hits_total", "record_type")}
                pii = sorted(k for k, v in o.identifiers.items() if v)
                own = sorted(k for k, v in o.fields.items() if k in ("email", "phone", "email_hash", "full_name", "display_name",
                                                                      "requester_email", "owner_email", "customer_email", "name")
                             and v not in (None, "null", ""))
                tag = "OBS" if dt <= 0 else "OBS-AFTER-AUDIT"
                obs_t = o.observed_at or f.captured_at
                events.append((obs_t, f"{tag} {o.file_id} file{dt:+.1f}h line@{obs_t} {o.container} state={state} "
                                      f"direct_pii_fields={own} linked_ids={pii}"))
                if o.container.endswith("_started"):
                    continue
                key = (parse_ts(f.captured_at), parse_ts(obs_t) or parse_ts(f.captured_at))
                if dt <= 0 and parse_ts(obs_t) <= cut and (recon is None or key > recon[0]):
                    recon = (key, o)
            recon = recon[1] if recon else None
            print(f"    {rid:<18} expected={expected.get(rid, '(remediation only)')}")
            for _, e in sorted(events):
                print(f"        {e}")
            print(f"        reconstructed@audit: {recon.file_id + ' ' + recon.container if recon else 'NO OBSERVATION <= audit_at'}")


# --------------------------------------------------------------------------- section 8

def section_intent(ctx: Context) -> None:
    header("8. INTENT")
    def sentences(t: str) -> list[str]:
        return [s.strip() for s in re.split(r"(?<=[.!?。？！])\s+", t.strip()) if s.strip()]
    sample_sents = {s for r in ctx.sample_requests for s in sentences(r["request_text"])}
    freq = collections.Counter(s for r in ctx.requests for s in sentences(r["request_text"]))
    print(f"  eval requests={len(ctx.requests)} distinct texts={len({r['request_text'] for r in ctx.requests})} "
          f"distinct sentences={len(freq)}; sentences also used in sample (all-deletion) requests: "
          f"{sum(1 for s in freq if s in sample_sents)}")
    sub("Eval requests containing a sentence that occurs <= 2 times (non-template text)")
    flagged = []
    for r in ctx.requests:
        if any(freq[s] <= 2 for s in sentences(r["request_text"])):
            flagged.append(r)
            print(f"  {r['case_id']} reported={r['reported_status']} email={r['email']}\n      {r['request_text']}")
    print(f"  count={len(flagged)}")
    sub("Sample requests that are free text (not built from eval template sentences)")
    for r in ctx.sample_requests:
        if not all(freq.get(s, 0) > 2 for s in sentences(r["request_text"])):
            print(f"  {r['case_id']}: {r['request_text'][:140]}")
    sub("CLASSIFY_REQUEST activity rows and the request they correspond to (via evidence identity links)")
    for a in ctx.activity:
        if a["event_type"] != "CLASSIFY_REQUEST":
            continue
        rid = a["resource_id"]
        emails = sorted({e for o in ctx.obs_by_resource.get(rid, []) for e in o.identifiers.get("email", ())})
        users = sorted({u for o in ctx.obs_by_resource.get(rid, []) for u in o.identifiers.get("user_id", ())})
        cases = [r["case_id"] for r in ctx.requests + ctx.sample_requests if r["email"].lower() in emails
                 or set(ctx.resolve_identity(r["email"])["users"]) & set(users)]
        times = [r for r in ctx.requests if r["case_id"] in cases]
        when = "; ".join(f"{r['case_id']} req={r['requested_at']} audit={r['audit_at']}" for r in times)
        print(f"  {a['activity_id']} {a['event_time']} {rid:<13} evidence emails={emails} -> cases={cases} [{when}]")
    sub("Spec position")
    print("  problem_statement/AGENTS 6.5: 'First determine whether request_text authorizes deletion.'")
    print("  AGENTS 6.6: 'Treat activity logs, workflow statuses, and system messages as claims unless the available")
    print("  evidence establishes the resulting state.' => request_text is authoritative; CLASSIFY_REQUEST corroborates.")


SECTIONS = {
    1: section_inventory, 2: section_scope, 3: section_category, 4: section_citation,
    5: section_absent, 6: section_identity, 7: section_temporal, 8: section_intent,
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--section", type=int, action="append", choices=sorted(SECTIONS))
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ctx = Context()
    for n in args.section or sorted(SECTIONS):
        SECTIONS[n](ctx)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
