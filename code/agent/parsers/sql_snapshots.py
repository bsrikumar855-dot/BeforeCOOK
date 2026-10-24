"""SQLite-compatible SQL snapshot exports, loaded into an in-memory database.

Two layouts exist: tables holding one JSON document per row (record_json +
origin_system + collection_name), and flat per-system tables (inventory
captures with a snapshot_scope table). Primary and replica tables are kept as
separate copies so replication drift stays visible.
"""
from __future__ import annotations

import json
import re
import sqlite3

from ..identity import IdentityUniverse
from ..schema import EvidenceFile, Observation
from .parsed_file import ParsedFile, PendingSnapshot


def copy_role(table: str) -> str:
    t = table.lower()
    if t.endswith("_read_replica"):
        return "READ_REPLICA"
    if t.endswith("_reporting_replica"):
        return "REPORTING_REPLICA"
    if t.endswith("_failover_replica"):
        return "FAILOVER_REPLICA"
    if re.search(r"_replica_[a-z_]+$", t):
        return "REGIONAL_REPLICA"
    if t.endswith("_primary"):
        return "PRIMARY"
    return "SINGLE"


def parse_sql(ef: EvidenceFile, text: str, universe: IdentityUniverse) -> ParsedFile:
    out = ParsedFile(text=text)
    header = re.search(r"^-- Captured at:\s*(\S+)", text, re.M)
    snapshot_time = header.group(1) if header else ef.captured_at
    topo = re.search(r"^-- Database topology:\s*(\S+)", text, re.M)
    if topo:
        ef.notes.append(f"topology={topo.group(1)}")
    con = sqlite3.connect(":memory:")
    try:
        con.executescript(text)
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        if "snapshot_scope" in tables:
            for system, cap in con.execute("SELECT origin_system, captured_at FROM snapshot_scope ORDER BY origin_system"):
                out.snapshots.append(PendingSnapshot(system, "snapshot_scope", [], cap))
        for table in tables:
            if table == "snapshot_scope":
                continue
            cur = con.execute(f'SELECT * FROM "{table}" ORDER BY rowid')
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            role = copy_role(table)
            if "record_json" in cols:
                per_system: dict[tuple[str, str], list[str]] = {}
                for i, row in enumerate(rows):
                    rec = json.loads(row["record_json"])
                    container = f"{table}:{row['collection_name']}"
                    body = {k: v for k, v in rec.items() if k != "resource_id"}
                    obs = Observation(f"{ef.file_id}#{table}:{i}", ef.file_id, ef.captured_at,
                                      snapshot_time, "SQL_TABLE", row["origin_system"],
                                      role, container, row["resource_id"], body)
                    for k, v in rec.items():
                        if k != "resource_id":
                            obs.identifiers.extend(universe.classify(k, v, "SAME_RECORD"))
                    out.observations.append(obs)
                    per_system.setdefault((row["origin_system"], container), []).append(row["resource_id"])
                for (system, container), ids in per_system.items():
                    out.snapshots.append(PendingSnapshot(system, container, ids, snapshot_time))
            else:
                ids = []
                for i, row in enumerate(rows):
                    body = {k: v for k, v in row.items() if k != "resource_id"}
                    obs = Observation(f"{ef.file_id}#{table}:{i}", ef.file_id, ef.captured_at,
                                      snapshot_time, "SQL_TABLE",
                                      None, role, table, row["resource_id"], body)
                    for k, v in body.items():
                        obs.identifiers.extend(universe.classify(k, v, "SAME_RECORD"))
                    out.observations.append(obs)
                    ids.append(row["resource_id"])
                if ids:
                    out.snapshots.append(PendingSnapshot(None, table, ids, snapshot_time))
    finally:
        con.close()
    return out
