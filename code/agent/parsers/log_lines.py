"""Log evidence in six observed syntaxes, normalised to (time, service, trace, event, fields).

Syntaxes: key=value, JSON per line, RFC 5424 syslog, bracketed, pipe-delimited
and cloud 'message="..."'. A lookup's subject key usually sits on the
*_started line and the result on the *_completed line; both share a trace id,
so identifiers are joined across the trace (provenance TRACE_JOIN).
"""
from __future__ import annotations

import json
import re

from ..identity import IdentityUniverse, NAME_FIELDS
from ..schema import EvidenceFile, Observation
from .json_records import ledger_observation
from .parsed_file import ParsedFile

TS_PREFIX_RE = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)\s+(.*)$")
# A key starts at a word boundary (G8: without the lookbehind every position of a long word rescanned it).
KV_RE = re.compile(r'(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]{0,127})=("((?:[^"\\]|\\.)*)"|[^\s"]+)')
SYSLOG_RE = re.compile(r"^<\d+>1 (\S+) (\S+) (\S+) - - (.*)$")
BRACKET_RE = re.compile(r"^\[(\w+)\] \[([^\]]+)\] \[([^\]]+)\] ([a-z_]+): (.*)$")
PIPE_RE = re.compile(r"^\| (\w+) \| ([^|]+?) \| ([^|]+?) \| ([a-z_]+) \| (.*)$")
TRANSPORT_KEYS = {"instance", "pod", "region", "attempt", "duration_ms", "took_ms", "latency_ms", "wait_ms", "pool",
                  "shard", "cache", "level", "severity", "logger", "retry_in_ms", "dependency", "route", "reason",
                  "service", "trace_id", "event", "message", "resource_id"}
LOOKUP_KEYS = {"lookup_key", "match_key", "subject_key", "scan_key", "subject_ref", "correlation_key", "query"}


def _kv(text: str) -> dict[str, str]:
    out = {}
    for key, raw, quoted in KV_RE.findall(text):
        out[key] = quoted.replace('\\"', '"') if raw.startswith('"') else raw
    return out


def normalise_line(line: str) -> dict | None:
    m = TS_PREFIX_RE.match(line)
    if not m:
        return None
    ts, rest = m.groups()
    if rest.startswith("{"):
        d = json.loads(rest)
        return {"ts": ts, "service": d.get("service"), "trace": d.get("trace_id"), "event": d.get("event"),
                "kv": {k: str(v) for k, v in d.items()}, "syntax": "json_line"}
    sm = SYSLOG_RE.match(rest)
    if sm:
        _, service, trace, body = sm.groups()
        kv = _kv(body)
        return {"ts": ts, "service": service, "trace": trace, "event": kv.get("event"), "kv": kv, "syntax": "rfc5424"}
    bm = BRACKET_RE.match(rest)
    if bm:
        _, service, trace, event, body = bm.groups()
        return {"ts": ts, "service": service, "trace": trace, "event": event, "kv": _kv(body), "syntax": "bracketed"}
    pm = PIPE_RE.match(rest)
    if pm:
        _, service, trace, event, body = pm.groups()
        return {"ts": ts, "service": service.strip(), "trace": trace.strip(), "event": event, "kv": _kv(body),
                "syntax": "pipe"}
    if rest.startswith("requestId="):
        outer = _kv(rest)
        inner = _kv(outer.get("message", ""))
        return {"ts": ts, "service": outer.get("function"), "trace": outer.get("requestId"),
                "event": inner.get("event"), "kv": inner, "syntax": "cloud_message"}
    kv = _kv(rest)
    return {"ts": ts, "service": kv.get("service"), "trace": kv.get("trace_id"), "event": kv.get("event"),
            "kv": kv, "syntax": "key_value"}


def parse_log(ef: EvidenceFile, text: str, universe: IdentityUniverse) -> ParsedFile:
    out = ParsedFile(text=text)
    events = []
    syntaxes: dict[str, int] = {}
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            ev = normalise_line(line)
        except (ValueError, json.JSONDecodeError):
            ev = None
        if ev is None:
            ef.notes.append(f"unparsed line {n}")
            continue
        ev["line"] = n
        events.append(ev)
        syntaxes[ev["syntax"]] = syntaxes.get(ev["syntax"], 0) + 1
    ef.notes.append("syntax=" + ",".join(f"{k}:{v}" for k, v in sorted(syntaxes.items())))

    by_trace: dict[str, list[dict]] = {}
    for ev in events:
        if ev["trace"]:
            by_trace.setdefault(ev["trace"], []).append(ev)

    for ev in events:
        kv = ev["kv"]
        rid = kv.get("resource_id")
        if not rid:
            continue
        if ev["event"] == "record_lookup":
            out.observations.append(ledger_observation(ef, universe, {"ts": ev["ts"], **kv}, "record_lookup",
                                                       f"L{ev['line']}", source_kind="LOG_EVENT"))
            continue
        body = {k: v for k, v in kv.items() if k not in TRANSPORT_KEYS}
        obs = Observation(f"{ef.file_id}#L{ev['line']}", ef.file_id, ef.captured_at, ev["ts"], "LOG_EVENT", None,
                          "SINGLE", f"log:{ev['event']}", rid, body)
        obs.fields["_service"] = ev["service"]
        for k, v in body.items():
            obs.identifiers.extend(universe.classify(k, v, "SAME_RECORD"))
        for other in by_trace.get(ev["trace"], []) if ev["trace"] else []:
            if other is ev:
                continue
            other_rid = other["kv"].get("resource_id")
            if other_rid and other_rid != rid:
                continue                                  # never borrow keys from another resource's line
            for k, v in other["kv"].items():
                if k in LOOKUP_KEYS or k in NAME_FIELDS or "email" in k:
                    obs.identifiers.extend(universe.classify(k, v, "TRACE_JOIN"))
        out.observations.append(obs)
    return out
