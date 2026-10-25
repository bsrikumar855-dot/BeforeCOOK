"""Screenshot extraction: the vision model transcribes record rows; code validates and normalises them.

The model never supplies evidence_id (provenance is the image's own file_id) and its
resource_id / system_id values are accepted only if they agree with the prefix -> system
map learned from the structured evidence (DESIGN.md D-VLM). Invalid rows are dropped,
counted and logged. Replies are cached by sha256(task + prompt_version + image bytes).
"""
from __future__ import annotations

import hashlib
import json
import re

from ..identity import EMAIL_RE, IdentityUniverse
from ..llm_client import AllProvidersFailed, CacheMiss, LLMClient, ResponseCache, ValidationFailed, content_key
from ..loaders import parse_ts
from ..schema import EvidenceFile, Observation
from .ocr_grounding import absence_proof, grounded, ocr_ids

TASK = "vision"
# Earlier vision prompts kept byte-exact, so every cached reply was produced by a prompt that ships (Round 6 R5a).
LEGACY_PROMPTS = ("image_extraction_v1.md",)
RESOURCE_RE = re.compile(r"^([a-z]+(?:_[a-z]+)*)_(\d+)$")
# At most 4 words of 32 letters: unbounded, a long underscore-joined run was quadratic (G8).
TOKEN_RE = re.compile(r"(?<![a-z0-9])[a-z]{1,32}(?:_[a-z]{1,32}){0,3}_\d{1,12}")
PRESENCE = {"PRESENT", "ABSENT", "UNKNOWN"}


def valid_visibility(value: object) -> bool:
    """Exactly true, false or "UNKNOWN" (0/1 and null are rejected: 0 == False in Python)."""
    return value is True or value is False or value == "UNKNOWN"


def _validate_reply(payload: object) -> dict:
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise ValidationFailed('top level must be {"screen_title": ..., "records": [...]}')
    for i, rec in enumerate(payload["records"]):
        if not isinstance(rec, dict) or not isinstance(rec.get("resource_id"), str):
            raise ValidationFailed(f"record {i} must be an object with a string resource_id")
        if not valid_visibility(rec.get("direct_identifiers_visible")):
            raise ValidationFailed(f'record {i}: direct_identifiers_visible must be true, false or "UNKNOWN" (not null)')
        if not isinstance(rec.get("direct_identifiers_reason"), str):
            raise ValidationFailed(f"record {i}: direct_identifiers_reason must be a string")
    return payload


def upgrade_legacy(reply: dict, version: str) -> dict:
    """Replies cached under the earlier prompt allowed null visibility. Null means 'not decided', so it becomes
    "UNKNOWN" (never false); true/false keep their meaning."""
    records = []
    for rec in reply.get("records", []):
        rec = dict(rec)
        if not valid_visibility(rec.get("direct_identifiers_visible")):
            rec["direct_identifiers_visible"] = "UNKNOWN"
            rec.setdefault("direct_identifiers_reason", f"legacy extraction ({version}) returned null")
        rec.setdefault("direct_identifiers_reason", f"legacy extraction ({version})")
        records.append(rec)
    return {**reply, "records": records}


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
        self.reextract: set[str] = set()          # file ids to send to the model again (operator flag)
        self.legacy_used: list[dict] = []
        self.system_categories: dict[str, list[str]] = {}   # system -> categories a stated record_type may take
        self.rejected_categories: list[dict] = []
        self.ocr = None                      # OcrReader: grounding check for resource ids (C9); None = off
        self.ungrounded: list[dict] = []
        self.grounding_report: dict[str, dict] = {}
        self.absence_checks: list[dict] = []        # F1: every UNKNOWN-visibility record checked, with the outcome

    def render(self, template: str, prefix_system: dict[str, str]) -> tuple[str, str]:
        """(rendered prompt, its version): the systems and record types are substituted before hashing."""
        systems = sorted(set(prefix_system.values()))
        prompt = (template.replace("{SYSTEMS}", ", ".join(f"`{s}`" for s in systems))
                  .replace("{RECORD_TYPES}", ", ".join(f"`{c}`" for c in self.categories)))
        return prompt, "p" + hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]

    def shipped_versions(self, prefix_system: dict[str, str]) -> set[str]:
        """Rendered versions of the current and the legacy vision prompts that ship in code/prompts/."""
        from ..llm_client import PROMPTS_DIR
        out = {self.render(self.client.prompt(TASK)[0], prefix_system)[1]}
        for name in LEGACY_PROMPTS:
            out.add(self.render((PROMPTS_DIR / name).read_text(encoding="utf-8"), prefix_system)[1])
        return out

    def __call__(self, ef: EvidenceFile, raw: bytes, prefix_system: dict[str, str]) -> list[Observation]:
        template, _ = self.client.prompt(TASK)
        prompt, version = self.render(template, prefix_system)
        self._shipped = self.shipped_versions(prefix_system)
        key = content_key(TASK, version, raw)
        hit = self.cache.get(TASK, key)
        if hit is None and ef.file_id not in self.reextract:
            hit = self._legacy_entry(ef)
        if hit is not None:
            reply = hit["response"]
            # the entry actually read (a legacy reply lives under its own, older key)
            self.client.ledger.cache_hits.append({"task": TASK, "key": hit.get("key", key), "provider": hit.get("provider"),
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
        ocr_text = self.ocr.text(ef.file_id, raw) if self.ocr is not None else None
        return self._to_observations(ef, reply, prefix_system, ocr_text)

    def _legacy_entry(self, ef: EvidenceFile) -> dict | None:
        """A reply cached under an earlier prompt version for these exact image bytes, upgraded to the schema."""
        root = self.cache.root / TASK
        entries = sorted(root.glob("*.json")) if root.exists() else []
        for p in entries:
            entry = json.loads(p.read_text(encoding="utf-8"))
            # only replies whose prompt ships with the code (current or LEGACY_PROMPTS) are reused
            if entry.get("input_sha256") == ef.sha256 and entry.get("prompt_version") in getattr(self, "_shipped", set()):
                self.legacy_used.append({"file_id": ef.file_id, "prompt_version": entry.get("prompt_version")})
                return {**entry, "response": upgrade_legacy(entry["response"], entry.get("prompt_version", "?"))}
        return None

    def _to_observations(self, ef: EvidenceFile, reply: dict, prefix_system: dict[str, str],
                         ocr_text: str | None = None) -> list[Observation]:
        out: list[Observation] = []
        report = {"vlm_ids": [], "grounded": [], "ungrounded": [], "ocr_ids": [], "ocr_only": []}
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
            shown = alias_of or rid            # the token the screen actually shows
            report["vlm_ids"].append(shown)
            if ocr_text is not None and not grounded(shown, ocr_text):
                report["ungrounded"].append(shown)
                self.ungrounded.append({"file_id": ef.file_id, "resource_id": rid, "shown_as": shown})
                self._drop(ef, rid, "UNGROUNDED: token absent from the image's OCR text")
                continue
            report["grounded"].append(shown)
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
            # A category claim is kept only if it is one of the resolved system's categories: the category picks
            # the treatment, so an inconsistent claim (e.g. USER_PROFILE on an identity_service tombstone) would
            # let the model choose the operation.
            allowed = self.system_categories.get(system, self.categories)
            if rtype in self.categories and rtype in allowed:
                fields["record_type"] = rtype
            elif rtype not in (None, "", "UNKNOWN"):
                fields["model_record_type_rejected"] = str(rtype)[:40]
                self.rejected_categories.append({"file_id": ef.file_id, "resource_id": rid, "claimed": rtype,
                                                 "system_id": system})
            for k in ("enabled", "restore_allowed"):
                if isinstance(rec.get(k), bool):
                    fields[k] = rec[k]
            email = rec.get("email")
            if isinstance(email, str) and EMAIL_RE.fullmatch(email.strip()):
                fields["email"] = email.strip()
            for k in ("phone", "name", "email_hash"):
                if isinstance(rec.get(k), str) and rec[k].strip():
                    fields[k if k != "name" else "full_name"] = rec[k].strip()
            # Identifier values are not checked against OCR (it mangles them); only an exact match against the
            # identity tables links anyone. Others are kept as content but flagged as unknown identifiers.
            unknown = [f"{k}:{fields[k]}" for k in ("email", "phone", "email_hash") if k in fields
                       and not self.universe.owners_of(k, fields[k].lower() if k == "email" else fields[k])]
            if unknown:
                fields["unknown_identifiers"] = unknown
            uid = rec.get("user_id")
            if isinstance(uid, str) and uid.strip() in self.universe.user_ids:
                fields["customer_reference"] = uid.strip()
            visible = rec.get("direct_identifiers_visible")
            visible = visible if valid_visibility(visible) else "UNKNOWN"
            if visible is True:
                fields["direct_identifiers"] = "1"
            elif visible is False and not any(k in fields for k in ("email", "phone", "full_name", "email_hash")):
                fields["direct_identifiers"] = "0"
            # "UNKNOWN" (or a legacy null) sets nothing: exposure stays UNKNOWN unless captured fields decide it,
            # or (F1) deterministic OCR proves the record's own detail page shows no identifier at all.
            elif visible == "UNKNOWN" and not any(k in fields for k in ("email", "phone", "full_name", "email_hash")):
                proven, why = absence_proof(shown, len(reply.get("records", [])), ocr_text, set(prefix_system))
                self.absence_checks.append({"file_id": ef.file_id, "resource_id": rid, "proven": proven, "reason": why})
                if proven:
                    fields["direct_identifiers"] = "0"
                    fields["absence_proof"] = why[:160]
            fields["direct_identifiers_visible"] = visible
            if isinstance(rec.get("direct_identifiers_reason"), str):
                fields["direct_identifiers_reason"] = rec["direct_identifiers_reason"][:160]
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
        if ocr_text is not None:
            report["ocr_ids"] = sorted(ocr_ids(ocr_text, set(prefix_system)))
            report["ocr_only"] = sorted(set(report["ocr_ids"]) - set(report["vlm_ids"]) -
                                        {self.aliases.get(x, x) for x in report["vlm_ids"]})
            self.grounding_report[ef.file_id] = report
        return out

    def _drop(self, ef: EvidenceFile, rid: str, reason: str) -> None:
        self.dropped.append({"file_id": ef.file_id, "resource_id": rid, "reason": reason})
