"""Identity resolution: request email -> user_id -> identifier set, with provenance.

Matching is exact (emails case-insensitive). Names are recorded but never
used on their own to link a record: three names are shared by different
users and the evidence plants near-name decoys (DESIGN.md ID4).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .schema import Identifier

# Bounded by RFC 5321 (local part <= 64, domain <= 253) and never started inside an address: an unbounded
# local part rescanned to the end of the line at every position (G8: > 30 s on a 1 MB line).
EMAIL_RE = re.compile(r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,253}\.[A-Za-z]{2,24}")
HEX64_RE = re.compile(r"\b[0-9a-f]{64}\b")
PHONE_RE = re.compile(r"^\+\d[\d ()\-]{6,}$")
NAME_FIELDS = {"name", "full_name", "display_name", "requester_name"}
NULL_TEXT = {"", "null", "none", "[redacted]"}


@dataclass
class Subject:
    case_id: str
    request_email: str
    user_ids: list[str]
    identifiers: dict[str, dict[str, str]]           # kind -> {value: provenance}
    resolution: list[str] = field(default_factory=list)

    @property
    def user_id(self) -> str | None:
        return self.user_ids[0] if len(self.user_ids) == 1 else None


class IdentityUniverse:
    """Every identifier value known from user_identities + user_audit_log, mapped to its owner."""

    def __init__(self, users: list[dict[str, str]], audit_log: list[dict[str, str]]):
        self.users = users
        self.audit_log = audit_log
        self.user_ids = {u["user_id"] for u in users}
        self.owner: dict[tuple[str, str], set[str]] = {}
        for u in users:
            self._own("user_id", u["user_id"], u["user_id"])
            self._own("email", u["email"].lower(), u["user_id"])
            self._own("phone", u["phone"], u["user_id"])
            self._own("email_hash", u["email_hash"], u["user_id"])
        for a in audit_log:
            kind = a["field_name"]
            for side in ("old_value", "new_value"):
                value = a[side].lower() if kind == "email" else a[side]
                self._own(kind, value, a["user_id"])
        self.known_hashes = {v for (k, v) in self.owner if k == "email_hash"}

    def _own(self, kind: str, value: str, user_id: str) -> None:
        if value:
            self.owner.setdefault((kind, value), set()).add(user_id)

    def owners_of(self, kind: str, value: str) -> set[str]:
        return self.owner.get((kind, value), set())

    def classify(self, key: str, value: object, provenance: str) -> list[Identifier]:
        """Turn one field value into zero or more identifiers (matched by value, never by shape)."""
        if value is None or isinstance(value, (bool, int, float)):
            return []
        text = str(value).strip()
        if text.lower() in NULL_TEXT:
            return []
        out: list[Identifier] = []
        if text in self.user_ids:
            out.append(Identifier("user_id", text, key, provenance))
        for m in EMAIL_RE.findall(text):
            out.append(Identifier("email", m.lower(), key, provenance))
        for m in HEX64_RE.findall(text):
            # 64-hex counts only when it is a known email hash: integrity seals share the shape.
            if m in self.known_hashes:
                out.append(Identifier("email_hash", m, key, provenance))
        if PHONE_RE.match(text):
            out.append(Identifier("phone", text, key, provenance))
        if key in NAME_FIELDS and not out:
            out.append(Identifier("name", text, key, provenance))
        return out

    def resolve(self, case_id: str, email: str) -> Subject:
        e = (email or "").strip().lower()
        resolution: list[str] = []
        users: set[str] = set()
        for u in self.users:
            if u["email"].lower() == e:
                users.add(u["user_id"])
                resolution.append(f"{u['user_id']} via user_identities.email (current)")
        for a in self.audit_log:
            if a["field_name"] != "email":
                continue
            for side in ("old_value", "new_value"):
                if a[side].lower() == e:
                    users.add(a["user_id"])
                    resolution.append(f"{a['user_id']} via user_audit_log.{side} {a['audit_id']}")
        identifiers: dict[str, dict[str, str]] = {k: {} for k in ("user_id", "email", "phone", "email_hash", "name")}
        if e:
            identifiers["email"][e] = "request.email"
        for uid in sorted(users):
            identifiers["user_id"][uid] = "user_identities.user_id"
            for u in self.users:
                if u["user_id"] == uid:
                    identifiers["email"].setdefault(u["email"].lower(), "user_identities.email")
                    identifiers["phone"].setdefault(u["phone"], "user_identities.phone")
                    identifiers["email_hash"].setdefault(u["email_hash"], "user_identities.email_hash")
                    identifiers["name"].setdefault(u["name"], "user_identities.name")
            for a in self.audit_log:
                if a["user_id"] != uid:
                    continue
                kind = a["field_name"]
                for side in ("old_value", "new_value"):
                    v = a[side].lower() if kind == "email" else a[side]
                    if v:
                        identifiers.setdefault(kind, {}).setdefault(v, f"user_audit_log.{side} {a['audit_id']}")
        return Subject(case_id, email, sorted(users), identifiers, resolution)


def subject_owns(subject: Subject, identifier: Identifier) -> bool:
    if identifier.kind == "name":
        return False
    return identifier.value in subject.identifiers.get(identifier.kind, {})
