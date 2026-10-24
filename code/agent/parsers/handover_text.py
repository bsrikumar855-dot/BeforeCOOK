"""Plain-text shift handover: '- <time> | <system> | <resource> | <record_type> | <result> | <subject>'."""
from __future__ import annotations

import re

from ..identity import IdentityUniverse
from ..schema import EvidenceFile
from .json_records import ledger_observation
from .parsed_file import ParsedFile

LINE_RE = re.compile(r"^-\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(.+?)\s*$")


def parse_handover(ef: EvidenceFile, text: str, universe: IdentityUniverse) -> ParsedFile:
    out = ParsedFile(text=text)
    for n, line in enumerate(text.splitlines(), 1):
        m = LINE_RE.match(line.strip())
        if not m:
            continue
        ts, system, rid, rtype, result, subject = m.groups()
        rec = {"ts": ts, "system": system, "resource_id": rid, "record_type": rtype, "result": result,
               "subject_reference": subject}
        out.observations.append(ledger_observation(ef, universe, rec, "handover_line", f"L{n}"))
    if not out.observations:
        ef.notes.append("no handover lines recognised")
    return out
