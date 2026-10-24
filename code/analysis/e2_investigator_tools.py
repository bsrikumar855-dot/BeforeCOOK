"""NOT SHIPPED experiment E2 (kept for reproducibility; not invoked by code/main.py).

Read-only tools for the investigator loop (experiment E2): pure functions over the parsed observation store.

Every call is recorded in the batch trace with an id ("r1", "r2", ...). The verifier accepts a citation only if
that id exists in this batch's trace, so a proposal cannot cite evidence that was never retrieved.
"""
from __future__ import annotations

import json

from agent.attribution import AttributionResult, CaseContext
from agent.identity import IdentityUniverse
from agent.loaders import parse_ts
from agent.parsers import EvidenceIndex
from agent.schema import ActivityClaim

MAX_ROWS = 40              # keep tool output small for the model


class ToolBox:
    TOOLS = {
        "find_by_identifier": "find_by_identifier(value): observations whose record carries this email/phone/hash/user_id",
        "activity_window": "activity_window(system, start, end): deletion-worker rows for a target system in [start, end]",
        "identity_history": "identity_history(user_id): the user's current and historical identifiers with provenance",
        "resource_observations": "resource_observations(resource_id, until): observations of a resource up to a time",
        "batch_members": "batch_members(batch_id): the cases and ambiguous resources of a batch",
    }

    def __init__(self, index: EvidenceIndex, activity: list[ActivityClaim], universe: IdentityUniverse,
                 batches: dict[str, dict], cases: dict[str, CaseContext]):
        self.index, self.universe, self.batches, self.cases = index, universe, batches, cases
        self.worker = sorted((a for a in activity if a.source_system == "deletion_worker"),
                             key=lambda a: (a.event_time, a.activity_id))
        self.trace: list[dict] = []

    def call(self, tool: str, args: dict) -> dict:
        fn = getattr(self, f"_{tool}", None) if tool in self.TOOLS else None
        try:
            result = fn(**args) if fn else {"error": f"unknown tool {tool!r}"}
        except TypeError as exc:
            result = {"error": f"bad arguments: {exc}"}
        entry = {"id": f"r{len(self.trace) + 1}", "tool": tool, "args": args, "result": result}
        self.trace.append(entry)
        return entry

    # ---- tools -------------------------------------------------------------------------
    def _owners(self, kind: str, value: str) -> list[str]:
        return sorted(self.universe.owners_of(kind, value))

    def _find_by_identifier(self, value: str) -> list[dict]:
        v = str(value).strip()
        out = []
        for o in self.index.observations:
            for i in o.identifiers:
                if i.kind != "name" and i.value.lower() == v.lower():
                    out.append({"obs_id": o.obs_id, "file_id": o.file_id, "resource_id": o.resource_id,
                                "field": i.field, "provenance": i.provenance, "kind": i.kind,
                                "owner_user_ids": self._owners(i.kind, i.value), "time": o.time})
        return out[:MAX_ROWS]

    def _activity_window(self, system: str, start: str, end: str) -> list[dict]:
        lo, hi = parse_ts(start), parse_ts(end)
        if lo is None or hi is None:
            return [{"error": "start/end must be ISO-8601 timestamps"}]
        return [{"activity_id": a.activity_id, "time": a.event_time, "event_type": a.event_type,
                 "resource_id": a.resource_id, "reported_result": a.reported_result}
                for a in self.worker if a.target_system == system and lo <= parse_ts(a.event_time) <= hi][:MAX_ROWS]

    def _identity_history(self, user_id: str) -> dict:
        s = self.universe.resolve("investigator", next((u["email"] for u in self.universe.users
                                                        if u["user_id"] == user_id), ""))
        return {"user_id": user_id, "identifiers": {k: v for k, v in s.identifiers.items() if v}}

    def _resource_observations(self, resource_id: str, until: str) -> list[dict]:
        cut = parse_ts(until)
        out = []
        for o in self.index.by_resource.get(resource_id, []):
            if cut is not None and parse_ts(o.time) > cut:
                continue
            out.append({"obs_id": o.obs_id, "file_id": o.file_id, "time": o.time, "presence": o.presence,
                        "exposure": o.exposure,
                        "identifiers": [{"kind": i.kind, "value": i.value, "field": i.field,
                                         "owner_user_ids": self._owners(i.kind, i.value)}
                                        for i in o.identifiers if i.kind != "name"]})
        return out[:MAX_ROWS]

    def _batch_members(self, batch_id: str) -> dict:
        b = self.batches.get(batch_id)
        return b if b is not None else {"error": f"unknown batch {batch_id!r}"}

    def trace_json(self) -> str:
        return json.dumps(self.trace, sort_keys=True, ensure_ascii=False)
