"""Translate raw record fields into the enum fields of an Observation.

This is the only place that interprets field values such as status_code=404,
result=NOT_FOUND, restore_allowed=1 or message_text="[REDACTED]". Rules read
the resulting enums, never the raw fields.
"""
from __future__ import annotations

from ..identity import EMAIL_RE, NULL_TEXT
from ..schema import Observation

DIRECT_FIELDS = {"email", "phone", "email_hash", "full_name", "display_name", "name", "requester_email",
                 "requester_name", "requester_phone", "owner_email", "owner_phone", "customer_email", "uploaded_by"}
REFERENCE_FIELDS = {"customer_reference", "subject_reference", "user_id", "subject_ref"}
FOUND_RESULTS = {"FOUND", "IDENTIFIABLE", "ANONYMIZED", "REDACTED", "ACTIVE", "BLOCKED", "DISABLED"}
NON_STATE_EVENTS = {"thumbnail_failed"}


def _flat(fields: dict) -> dict:
    """One level of nesting is flattened (search documents keep identifiers under 'document')."""
    out = {}
    for k, v in fields.items():
        if isinstance(v, dict) and k in ("document", "profile"):
            for k2, v2 in v.items():
                out[k2] = v2
        else:
            out[k] = v
    return out


def _present_value(v: object) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        return v.strip().lower() not in NULL_TEXT
    return True


def _truthy(v: object) -> bool | None:
    if v in (True, 1, "1", "true", "True", "TRUE"):
        return True
    if v in (False, 0, "0", "false", "False", "FALSE"):
        return False
    return None


def annotate(obs: Observation) -> Observation:
    f = _flat(obs.fields)
    event = obs.container[4:] if obs.container.startswith("log:") else ""
    if event.endswith("_started") or event in NON_STATE_EVENTS:
        obs.state_bearing = False

    record_type = f.get("record_type")
    result = str(f.get("result") or "").upper()
    if record_type:
        obs.stated_category = str(record_type)

    # ---- presence -------------------------------------------------------------
    presence = "UNKNOWN"
    if obs.source_kind in ("SQL_TABLE", "JSON_COLLECTION", "PDF_TEXT"):
        presence = "PRESENT"                       # a row/document exists in the snapshot
    if record_type:
        presence = "ABSENT" if result == "NOT_FOUND" else ("PRESENT" if result in FOUND_RESULTS else presence)
    for key in ("status_code", "http_status"):
        if key in f and f[key] is not None:
            presence = "ABSENT" if str(f[key]) == "404" else ("PRESENT" if str(f[key]).startswith("2") else presence)
    if "rows" in f and f["rows"] is not None:
        presence = "ABSENT" if str(f["rows"]) == "0" else "PRESENT"
    if not record_type and result in ("HIT", "MISS"):
        presence = "PRESENT" if result == "HIT" else "ABSENT"
    if "hits_total" in f and f["hits_total"] is not None:
        presence = "ABSENT" if str(f["hits_total"]) == "0" else "PRESENT"
    if "available" in f and _truthy(f["available"]) is not None:
        presence = "PRESENT" if _truthy(f["available"]) else "ABSENT"
    if "marker_found" in f and _truthy(f["marker_found"]) is not None:
        presence = "ABSENT" if _truthy(f["marker_found"]) else "PRESENT"
    if "archive_present" in f and _truthy(f["archive_present"]) is not None:
        presence = "PRESENT" if _truthy(f["archive_present"]) else "ABSENT"
    if presence == "UNKNOWN" and obs.source_kind in ("JSON_POINT_LOOKUP", "LOG_EVENT", "IMAGE_EXTRACTION") \
            and obs.state_bearing and any(k in f for k in DIRECT_FIELDS | REFERENCE_FIELDS |
                                          {"enabled", "restore_allowed", "decision", "eligible", "matched_value",
                                           "message_text", "integrity_check", "access", "email_opt_in"}):
        presence = "PRESENT"                       # a read-back that returned record content
    if obs.source_kind == "IMAGE_EXTRACTION" and f.get("presence") in ("PRESENT", "ABSENT"):
        presence = str(f["presence"])
    obs.presence = presence if obs.state_bearing else "UNKNOWN"

    # ---- exposure (only the record's own fields; trace-joined lookup keys do not count) ----
    direct = any(_present_value(f.get(k)) for k in DIRECT_FIELDS if k in f)
    if _present_value(f.get("matched_value")):
        direct = True
    if str(f.get("direct_identifiers", "")) == "1":
        direct = True
    text = f.get("message_text")
    if isinstance(text, str) and text.strip().upper() != "[REDACTED]" and EMAIL_RE.search(text):
        direct = True
    reference = any(_present_value(f.get(k)) for k in REFERENCE_FIELDS if k in f)
    has_content = any(k in f for k in DIRECT_FIELDS) or "direct_identifiers" in f or "message_text" in f
    if record_type and result in ("IDENTIFIABLE", "ANONYMIZED", "REDACTED"):
        exposure = {"IDENTIFIABLE": "DIRECT_IDENTIFIERS", "ANONYMIZED": "NO_IDENTIFIERS", "REDACTED": "REDACTED"}[result]
    elif direct:
        exposure = "DIRECT_IDENTIFIERS"
    elif isinstance(text, str) and text.strip().upper() == "[REDACTED]":
        exposure = "REDACTED"
    elif has_content:
        exposure = "STABLE_REFERENCE_ONLY" if reference else "NO_IDENTIFIERS"
    else:
        exposure = "UNKNOWN"
    obs.exposure = exposure if obs.state_bearing else "UNKNOWN"

    # ---- data flow / restore state -----------------------------------------------
    enabled = _truthy(f.get("enabled")) if "enabled" in f else None
    if enabled is not None:
        obs.flow_state = "ENABLED" if enabled else "DISABLED"
    elif record_type == "DATA_FLOW":
        obs.flow_state = {"ACTIVE": "ENABLED", "DISABLED": "DISABLED"}.get(result, "UNKNOWN")

    restore = _truthy(f.get("restore_allowed")) if "restore_allowed" in f else None
    if restore is not None:
        obs.restore_state = "RESTORABLE" if restore else "BLOCKED"
    elif record_type == "BACKUP_ARCHIVE":
        obs.restore_state = {"ACTIVE": "RESTORABLE", "BLOCKED": "BLOCKED"}.get(result, "UNKNOWN")
    elif "decision" in f:
        obs.restore_state = {"deny": "BLOCKED", "allow": "RESTORABLE"}.get(str(f["decision"]).lower(), "UNKNOWN")
    elif "deletion_block" in f or "eligible" in f:
        blocked = _truthy(f.get("deletion_block"))
        eligible = _truthy(f.get("eligible"))
        obs.restore_state = "BLOCKED" if blocked else ("RESTORABLE" if eligible else "UNKNOWN")

    # ---- record shape (flags read by the category rules) ---------------------------
    flags = []
    if "email_opt_in" in f:
        flags.append("email_opt_in")
    if ("attachment" in obs.container or "file" in f or "file_name" in f
            or f.get("storage_class") == "ticket_attachment" or f.get("doc_kind") == "attachment"):
        flags.append("attachment")
    base = obs.container.split(":")[-1]
    obs.record_shape = f"{obs.system_id or '?'}:{base}" + "".join(f"+{x}" for x in flags)
    return obs
