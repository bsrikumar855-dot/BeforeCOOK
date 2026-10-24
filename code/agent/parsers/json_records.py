"""JSON evidence: snapshot bundles, per-service read-backs, lookup ledgers, collection dumps.

The shape of the document decides the parser, never the filename (several
files are named after one thing and contain another).
"""
from __future__ import annotations

import json

from ..identity import IdentityUniverse
from ..schema import EvidenceFile, Observation
from .parsed_file import ParsedFile, PendingSnapshot


def _attach(obs: Observation, universe: IdentityUniverse, record: dict, provenance: str = "SAME_RECORD") -> None:
    def walk(key: str, value: object) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                walk(k, v)
        elif isinstance(value, list):
            for v in value:
                walk(key, v)
        elif key != "resource_id":
            obs.identifiers.extend(universe.classify(key, value, provenance))
    walk("", record)


def ledger_observation(ef: EvidenceFile, universe: IdentityUniverse, rec: dict, container: str, idx: str,
                       source_kind: str = "RECORD_LEDGER") -> Observation:
    """A ledger line: (time, system, resource_id, record_type, result, subject)."""
    obs = Observation(f"{ef.file_id}#{idx}", ef.file_id, ef.captured_at, rec.get("recorded_at") or rec.get("ts"),
                      source_kind, rec.get("system"), "SINGLE", container, rec["resource_id"],
                      {"record_type": rec.get("record_type"), "result": rec.get("result")})
    subject = rec.get("subject_reference") if "subject_reference" in rec else rec.get("lookup_value")
    obs.identifiers.extend(universe.classify("subject_reference", subject, "SAME_RECORD"))
    return obs


def parse_json(ef: EvidenceFile, text: str, universe: IdentityUniverse) -> ParsedFile:
    out = ParsedFile(text=text)
    doc = json.loads(text)
    if "snapshots" in doc:
        for si, snap in enumerate(doc["snapshots"]):
            records = snap.get("records", [])
            nested = [r for r in records if "system" in r and "record_type" in r]
            container = f"{snap['data_store']}:{snap['collection']}"
            if nested:                                   # history ledger stored inside a bundle
                for ri, rec in enumerate(nested):
                    out.observations.append(ledger_observation(ef, universe, rec, container, f"s{si}r{ri}"))
                continue
            out.snapshots.append(PendingSnapshot(snap["data_store"], snap["collection"],
                                                 [r["resource_id"] for r in records], snap["captured_at"]))
            for ri, rec in enumerate(records):
                body = {k: v for k, v in rec.items() if k != "resource_id"}
                obs = Observation(f"{ef.file_id}#s{si}r{ri}", ef.file_id, ef.captured_at, snap["captured_at"],
                                  "JSON_COLLECTION", snap["data_store"], "SINGLE", snap["collection"],
                                  rec["resource_id"], body)
                _attach(obs, universe, rec)
                out.observations.append(obs)
    elif "service_exports" in doc:
        for system in sorted(doc["service_exports"]):
            for coll in sorted(doc["service_exports"][system]):
                for ri, rec in enumerate(doc["service_exports"][system][coll]):
                    idx = f"{system}.{coll}.{ri}"
                    if coll == "index_queries":
                        hits = rec.get("hits") or {}
                        docs = hits.get("documents") or []
                        if not docs:
                            obs = Observation(f"{ef.file_id}#{idx}", ef.file_id, ef.captured_at, rec.get("observed_at"),
                                              "JSON_POINT_LOOKUP", system, "SINGLE", coll, rec["resource_id"],
                                              {"hits_total": hits.get("total", 0)})
                            _attach(obs, universe, {"query": rec.get("query")})
                            out.observations.append(obs)
                        for di, d in enumerate(docs):
                            body = {**{k: v for k, v in d.items() if k != "resource_id"}, "hits_total": hits.get("total")}
                            obs = Observation(f"{ef.file_id}#{idx}.{di}", ef.file_id, ef.captured_at,
                                              rec.get("observed_at"), "JSON_POINT_LOOKUP", system, "SINGLE", coll,
                                              d["resource_id"], body)
                            _attach(obs, universe, {**d, "query": rec.get("query")})
                            out.observations.append(obs)
                    else:
                        body = {k: v for k, v in rec.items() if k not in ("resource_id", "observed_at")}
                        obs = Observation(f"{ef.file_id}#{idx}", ef.file_id, ef.captured_at, rec.get("observed_at"),
                                          "JSON_POINT_LOOKUP", system, "SINGLE", coll, rec["resource_id"], body)
                        _attach(obs, universe, body)
                        out.observations.append(obs)
    elif isinstance(doc.get("records"), list) and doc["records"] and "record_type" in doc["records"][0]:
        for ri, rec in enumerate(doc["records"]):
            out.observations.append(ledger_observation(ef, universe, rec, "record_lookup", f"r{ri}"))
    elif isinstance(doc.get("records"), list):
        coll = doc.get("collection", "records")
        out.snapshots.append(PendingSnapshot(None, coll, [r["resource_id"] for r in doc["records"]],
                                             doc.get("captured_at") or ef.captured_at))
        for ri, rec in enumerate(doc["records"]):
            body = {k: v for k, v in rec.items() if k != "resource_id"}
            obs = Observation(f"{ef.file_id}#r{ri}", ef.file_id, ef.captured_at, doc.get("captured_at"),
                              "JSON_COLLECTION", None, "SINGLE", coll, rec["resource_id"], body)
            _attach(obs, universe, rec)
            out.observations.append(obs)
    else:
        ef.status = "PARSE_ERROR"
        ef.notes.append("unrecognised JSON shape")
    return out
