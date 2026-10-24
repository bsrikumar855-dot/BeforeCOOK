"""Explain mode: trace one case through every stage, for live interview probing (G2; skill: input-tracing).

`python3 code/main.py --case <case_id> --explain [--from-cache]` prints, for one case: identity resolution with
provenance, candidate edges and attribution labels with rationale, the observations behind each attributed or
candidate resource (time, file, container, state), the OCR grounding of any screenshot-derived observation, the
rules that fired, the intent path and the final steps.

Ad-hoc mode (`--email --audit-at --request-text`, optional `--requested-at --reported-at`) appends one synthetic
request to a temporary copy of the dataset and traces it the same way, so a new case can be traced live.

Contract: inputs are validated (email shape, ISO-8601 timestamps, request text at most 5,000 characters) and
rejected with a message otherwise; the request text is data that only the intent classifier sees. Output covers
only the resolved subject's attributed resources and its candidate set, is bounded in size, and nothing is
written except a temporary dataset copy (never output.csv, traces or reports).
"""
from __future__ import annotations

import csv
import json
import re
import shutil
import tempfile
from pathlib import Path

from .intent import MAX_CLASSIFY_CHARS
from .loaders import parse_ts

EMAIL_SHAPE = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,255}\.[A-Za-z]{2,24}")
MAX_ITEMS = 25                 # per list in the printout
MAX_CHARS = 40_000             # whole printout
ADHOC_CASE_ID = "case_adhoc"
EMPTY = '""'


class ExplainInputError(ValueError):
    """Rejected explain input (exit 2)."""


def validate_adhoc(email: str, audit_at: str, request_text: str, requested_at: str | None,
                   reported_at: str | None) -> None:
    if not EMAIL_SHAPE.fullmatch(email or ""):
        raise ExplainInputError(f"--email is not an email address: {email[:80]!r}")
    for name, value in (("--audit-at", audit_at), ("--requested-at", requested_at), ("--reported-at", reported_at)):
        if value is None and name != "--audit-at":
            continue
        if not value or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})", value) \
                or parse_ts(value) is None:
            raise ExplainInputError(f"{name} must be an ISO-8601 timestamp with a zone, e.g. 2026-09-15T12:00:00Z")
    if not (request_text or "").strip():
        raise ExplainInputError("--request-text is empty")
    if len(request_text) > MAX_CLASSIFY_CHARS:
        raise ExplainInputError(f"--request-text is {len(request_text)} characters; the limit is {MAX_CLASSIFY_CHARS}")


def adhoc_dataset(src: Path, email: str, audit_at: str, request_text: str, requested_at: str | None,
                  reported_at: str | None) -> Path:
    """A temporary dataset copy with one extra request row; the caller removes the directory."""
    tmp = Path(tempfile.mkdtemp(prefix="explain_"))
    dst = tmp / "dataset"
    shutil.copytree(src, dst)
    path = dst / "deletion_requests.csv"
    with open(path, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    fields = list(rows[0])
    rows.append({"case_id": ADHOC_CASE_ID, "email": email, "requested_at": requested_at or audit_at,
                 "reported_status": "NOT_STARTED", "reported_at": reported_at or audit_at, "audit_at": audit_at,
                 "request_text": request_text})
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return dst


def _cap(items: list, n: int = MAX_ITEMS) -> tuple[list, str]:
    return items[:n], (f"   … {len(items) - n} more not shown" if len(items) > n else "")


def explain(rr, case_id: str) -> str:
    if case_id not in rr.outcomes:
        raise ExplainInputError(f"unknown case_id {case_id!r}")
    o = rr.outcomes[case_id]
    t = o.trace
    grounding = (rr.summary.get("ocr_grounding") or {}).get("per_image", {})
    L = [f"== {case_id}: {o.status}", ""]
    req = t.get("request", {})
    L += ["-- request", *(f"   {k}: {v}" for k, v in req.items()), ""]
    it = t.get("intent") or {}
    L += ["-- intent (model labels the text; code decides the path)",
          f"   label={it.get('label')} language={it.get('language')} negation={it.get('negation_detected')} "
          f"model={it.get('model')}",
          f"   CLASSIFY_REQUEST rows: {it.get('classify_request_rows') or 'none'}",
          f"   path: {it.get('path')}" + (f" (text truncated to {it['truncated_to']} chars)" if it.get("truncated_to") else ""), ""]
    subj = t.get("subject", {})
    L += ["-- identity resolution", f"   user_ids: {subj.get('user_ids')}  resolution: {subj.get('resolution')}"]
    for kind, vals in (subj.get("identifiers") or {}).items():
        for value, prov in vals.items():
            L.append(f"   {kind}: {value}   <- {prov}")
    L.append("")
    if o.status == "NOT_APPLICABLE":
        L += ["-- no deletion authorized: no attribution, no steps", ""]
        return _bounded(L)
    atts, more = _cap(t.get("attribution", []))
    L += ["-- attribution (candidate set for this subject)"]
    for a in atts:
        L.append(f"   {a['resource_id']} [{a['system_id']}] {a['label']} in_scope={a.get('in_scope')}: {a.get('rationale')}")
        for e in (a.get("edges") or [])[:4]:
            L.append(f"      edge {e['kind']}/{e['strength']} {e['identifier']} from {e['source_id']} at {e['time']}")
    L += [more] if more else []
    elim, more = _cap(t.get("eliminated", []))
    for e in elim:
        L.append(f"   eliminated {e['resource_id']} ({e['activity_id']}): {e['reason']} via {e.get('sources')}")
    L += [more, ""] if more else [""]
    L += ["-- resources: observations, state at audit_at, rule fired"]
    res, more = _cap(t.get("resources", []))
    for r in res:
        st = r.get("state", {})
        L.append(f"   {r['resource_id']} [{r['system_id']}] category={r['category']} ({r['category_basis']}) "
                 f"action={r['required_action']}")
        L.append(f"      state: presence={st.get('presence')} exposure={st.get('exposure')} flow={st.get('flow_state')} "
                 f"restore={st.get('restore_state')} proof={st.get('proof_basis')} cited={r.get('proof_file_id')}")
        obs = sorted(rr.index.by_resource.get(r["resource_id"], []), key=lambda x: (x.time, x.obs_id))
        shown, omore = _cap(obs, 8)
        for ob in shown:
            g = grounding.get(ob.file_id)
            ocr = ""
            if g is not None:
                ocr = (" OCR=grounded" if r["resource_id"] in g["grounded"] or ob.fields.get("catalog_alias") else
                       " OCR=not grounded")
            late = " [captured after audit_at: discovery only, never state or citation]"                 if parse_ts(ob.file_captured_at) > parse_ts(req["audit_at"]) else ""
            L.append(f"      obs {ob.obs_id} t={ob.time} file_captured={ob.file_captured_at} {ob.source_kind} "
                     f"{ob.container} presence={ob.presence} exposure={ob.exposure}{ocr}{late}")
        L += [f"   {omore}"] if omore else []
        L.append(f"      rule: compliant={r.get('compliant')} operations={r.get('operations')}"
                 + (f" note={r['note']}" if r.get("note") else ""))
    L += [more] if more else []
    excl, more = _cap(t.get("excluded", []))
    if excl:
        L += ["", "-- excluded from steps"] + [f"   {e['resource_id']}: {e['reason']}" for e in excl] + ([more] if more else [])
    L += ["", "-- final steps"] + [f"   {s['step_no']}. {s['phase']} {s['operation']} {s['system_id']}/{s['resource_id']} "
                                   f"state={s['state']} evidence={s['evidence_id'] or EMPTY}" for s in o.steps]
    if t.get("reason_code"):
        L.append(f"   reason_code: {t['reason_code']}")
    return _bounded(L)


def _bounded(lines: list[str]) -> str:
    text = "\n".join(lines) + "\n"
    return text if len(text) <= MAX_CHARS else text[:MAX_CHARS] + f"\n… output truncated at {MAX_CHARS} characters\n"


def trace_summary(rr, case_id: str) -> dict:
    """Machine-readable form for tests: status, steps and the resources named in the explanation."""
    o = rr.outcomes[case_id]
    return {"status": o.status, "steps": o.steps,
            "resources": sorted({a["resource_id"] for a in o.trace.get("attribution", [])}),
            "json": json.dumps(o.trace, default=str)[:200]}
