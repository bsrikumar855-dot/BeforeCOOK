"""Screenshot extraction: the vision model transcribes record rows; code validates and normalises them.

The model never supplies evidence_id (provenance is the image's own file_id) and its
resource_id / system_id values are accepted only if they agree with the prefix -> system
map learned from the structured evidence (DESIGN.md D-VLM). Invalid rows are dropped,
counted and logged. Replies are cached by sha256(task + prompt_version + image bytes).
"""
from __future__ import annotations

import hashlib
import re

from ..identity import EMAIL_RE, IdentityUniverse
from ..llm_client import AllProvidersFailed, CacheMiss, LLMClient, ResponseCache, ValidationFailed, content_key
from ..loaders import parse_ts
from ..schema import EvidenceFile, Observation

TASK = "vision"
RESOURCE_RE = re.compile(r"^([a-z]+(?:_[a-z]+)*)_(\d+)$")
TOKEN_RE = re.compile(r"(?<![a-z0-9])[a-z]+(?:_[a-z]+)*_\d+")
PRESENCE = {"PRESENT", "ABSENT", "UNKNOWN"}


def _validate_reply(payload: object) -> dict:
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise ValidationFailed('top level must be {"screen_title": ..., "records": [...]}')
    for i, rec in enumerate(payload["records"]):
        if not isinstance(rec, dict) or not isinstance(rec.get("resource_id"), str):
            raise ValidationFailed(f"record {i} must be an object with a string resource_id")
    return payload


class ImageExtractor:
    def __init__(self, client: LLMClient, cache: ResponseCache, universe: IdentityUniverse, categories: list[str]):
        self.client = client
        self.cache = cache
        self.universe = universe
        self.categories = categories
        self.dropped: list[dict] = []
        self.accepted: list[dict] = []
        self.failures: list[dict] = []
        # Data-catalog aliases (external reference -> resource id). A screen may show a record under its
        # alias, e.g. a file name or the customer key the catalog maps to the resource.
        self.aliases: dict[str, str] = {}

    def __call__(self, ef: EvidenceFile, raw: bytes, prefix_system: dict[str, str]) -> list[Observation]:
        template, _ = self.client.prompt(TASK)
        systems = sorted(set(prefix_system.values()))
        prompt = (template.replace("{SYSTEMS}", ", ".join(f"`{s}`" for s in systems))
                  .replace("{RECORD_TYPES}", ", ".join(f"`{c}`" for c in self.categories)))
        version = "p" + hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]
        key = content_key(TASK, version, raw)
        hit = self.cache.get(TASK, key)
        if hit is not None:
            reply = hit["response"]
            self.client.ledger.cache_hits.append({"task": TASK, "key": key, "provider": hit.get("provider"),
                                                  "model": hit.get("model"), "call_id": hit.get("call_id")})
        else:
            try:
                r = self.client.complete(TASK, prompt, _validate_reply, image=raw)
            except CacheMiss as exc:
                raise CacheMiss(f"{exc} (image {ef.file_id} missing from code/cache/vision)") from exc
            except AllProvidersFailed as exc:
                self.failures.append({"file_id": ef.file_id, "error": str(exc)[:300]})
                ef.notes.append("image extraction failed; observations skipped")
                return []
            reply = r.data
            self.cache.put(TASK, key, {"key": key, "task": TASK, "prompt_version": version, "input_sha256": ef.sha256,
                                       "file_id": ef.file_id, "response": reply, "provider": r.provider,
                                       "model": r.model, "key_slot": r.key_slot, "call_id": r.call_id,
                                       "batch_size": 1, "call_usage": r.usage})
        ef.status = "EXTRACTED"
        return self._to_observations(ef, reply, prefix_system)

    def _to_observations(self, ef: EvidenceFile, reply: dict, prefix_system: dict[str, str]) -> list[Observation]:
        out: list[Observation] = []
        for i, rec in enumerate(reply.get("records", [])):
            rid = str(rec.get("resource_id", "")).strip()
            alias_of = None
            if rid in self.aliases:                    # exact match on a catalog alias only
                alias_of, rid = rid, self.aliases[rid]
            m = RESOURCE_RE.match(rid)
            salvaged = None
            if not m or m.group(1) not in prefix_system:
                # The model sometimes joins two visible identifiers ("user_11_account_5762"). Salvage only when
                # exactly one token carries a known resource prefix; a user-id token must be a known user.
                tokens = TOKEN_RE.findall(rid)
                resources = [t for t in tokens if RESOURCE_RE.match(t) and RESOURCE_RE.match(t).group(1) in prefix_system
                             and t not in self.universe.user_ids]
                users = [t for t in tokens if t in self.universe.user_ids]
                if len(resources) == 1 and len(tokens) == len(resources) + len(users):
                    salvaged, rid = rid, resources[0]
                    m = RESOURCE_RE.match(rid)
                    if users and not rec.get("user_id"):
                        rec = {**rec, "user_id": users[0]}
                else:
                    self._drop(ef, rid, "resource_id prefix is not a known system prefix")
                    continue
            system = prefix_system[m.group(1)]
            claimed = rec.get("system_id")
            overridden = None
            if claimed not in (None, "", system):
                if not alias_of:
                    self._drop(ef, rid, f"system_id {claimed!r} contradicts prefix map ({system})")
                    continue
                overridden = claimed                   # the catalog names the resource, so its system wins
            fields: dict[str, object] = {}
            if alias_of:
                fields["catalog_alias"] = alias_of
                if alias_of in self.universe.user_ids:
                    fields["subject_reference"] = alias_of
                if overridden:
                    fields["model_system_claim_overridden"] = overridden
            presence = str(rec.get("presence") or "UNKNOWN").upper()
            fields["presence"] = presence if presence in PRESENCE else "UNKNOWN"
            rtype = rec.get("record_type")
            if rtype in self.categories:
                fields["record_type"] = rtype
            for k in ("enabled", "restore_allowed"):
                if isinstance(rec.get(k), bool):
                    fields[k] = rec[k]
            email = rec.get("email")
            if isinstance(email, str) and EMAIL_RE.fullmatch(email.strip()):
                fields["email"] = email.strip()
            for k in ("phone", "name", "email_hash"):
                if isinstance(rec.get(k), str) and rec[k].strip():
                    fields[k if k != "name" else "full_name"] = rec[k].strip()
            uid = rec.get("user_id")
            if isinstance(uid, str) and uid.strip() in self.universe.user_ids:
                fields["customer_reference"] = uid.strip()
            if rec.get("direct_identifiers_visible") is False and not any(
                    k in fields for k in ("email", "phone", "full_name", "email_hash")):
                fields["direct_identifiers"] = "0"
            if isinstance(rec.get("status_text"), str):
                fields["status_text"] = rec["status_text"][:120]
            if salvaged:
                fields["salvaged_from"] = salvaged
            observed = rec.get("observed_at")
            t = parse_ts(observed) if isinstance(observed, str) else None
            observed_at = observed if t and t <= parse_ts(ef.captured_at) else None
            obs = Observation(f"{ef.file_id}#img{i}", ef.file_id, ef.captured_at, observed_at, "IMAGE_EXTRACTION",
                              system, "SINGLE", "image_row", rid, fields, extraction="MODEL")
            for k, v in fields.items():
                if k in ("email", "phone", "full_name", "email_hash", "customer_reference", "subject_reference"):
                    obs.identifiers.extend(self.universe.classify(k, v, "MODEL_EXTRACTION"))
            out.append(obs)
            self.accepted.append({"file_id": ef.file_id, "resource_id": rid, "system_id": system,
                                  **({"salvaged_from": salvaged} if salvaged else {}),
                                  **({"catalog_alias": alias_of} if alias_of else {}),
                                  **({"system_claim_overridden": overridden} if overridden else {})})
        return out

    def _drop(self, ef: EvidenceFile, rid: str, reason: str) -> None:
        self.dropped.append({"file_id": ef.file_id, "resource_id": rid, "reason": reason})
