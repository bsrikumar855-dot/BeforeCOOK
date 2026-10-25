"""Request intent: request_text -> {DELETION, NON_DELETION, UNCLEAR} (DESIGN.md D-INTENT).

The model only labels intent. Uncached texts are sent in batches; each text still
gets its own content-addressed cache entry keyed by sha256(task + prompt_version + text).
The decision path (CLASSIFY_REQUEST corroboration, UNCLEAR -> NOT_APPLICABLE) is code.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass

from .llm_client import AllProvidersFailed, CacheMiss, LLMClient, ResponseCache, ValidationFailed, content_key
from .schema import INTENT_LABELS

TASK = "intent"
MAX_CLASSIFY_CHARS = 5000      # longer texts are classified on their first 5,000 characters (C7)


@dataclass
class IntentResult:
    label: str | None             # None = model failure
    language: str | None
    negation_detected: bool | None
    provider: str | None
    model: str | None
    cache_hit: bool
    error: str = ""


def _text_id(i: int) -> str:
    return f"t{i:02d}"


def _fence(text: str) -> str:
    # Unguessable per-text nonce so a message cannot close its own tag.
    nonce = hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]
    return text.replace(f"</msg-{nonce}>", "")


def build_batch_prompt(base_prompt: str, texts: list[str]) -> str:
    parts = [base_prompt.rstrip(), ""]
    for i, text in enumerate(texts, 1):
        nonce = hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]
        parts.append(f'<msg-{nonce} id="{_text_id(i)}">\n{_fence(text)}\n</msg-{nonce}>')
    return "\n".join(parts) + "\n"


def make_validator(n: int) -> Callable[[object], dict[str, dict]]:
    expected = {_text_id(i) for i in range(1, n + 1)}

    def validate(payload: object) -> dict[str, dict]:
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise ValidationFailed("top level must be {\"results\": [...]}")
        out: dict[str, dict] = {}
        for item in payload["results"]:
            if not isinstance(item, dict):
                raise ValidationFailed("each result must be an object")
            rid, label = item.get("id"), item.get("label")
            if rid not in expected:
                raise ValidationFailed(f"unknown id {rid!r}; expected exactly {sorted(expected)}")
            if label not in INTENT_LABELS:
                raise ValidationFailed(f"label {label!r} not in {list(INTENT_LABELS)}")
            neg = item.get("negation_detected")
            if not isinstance(neg, bool):
                raise ValidationFailed("negation_detected must be true or false")
            lang = item.get("language")
            out[rid] = {"label": label, "language": lang if isinstance(lang, str) else None, "negation_detected": neg}
        missing = expected - set(out)
        if missing:
            raise ValidationFailed(f"missing results for ids {sorted(missing)}")
        return out
    return validate


class IntentClassifier:
    def __init__(self, client: LLMClient, cache: ResponseCache) -> None:
        self.client = client
        self.cache = cache
        self.base_prompt, self.prompt_version = client.prompt(TASK)

    def classify(self, texts: list[str]) -> dict[str, IntentResult]:
        """Return text -> IntentResult for every distinct text."""
        results: dict[str, IntentResult] = {}
        pending: list[str] = []
        for text in sorted(set(texts)):
            key = content_key(TASK, self.prompt_version, text)
            hit = self.cache.get(TASK, key)
            if hit is not None:
                r = hit["response"]
                results[text] = IntentResult(r["label"], r.get("language"), r.get("negation_detected"),
                                             hit.get("provider"), hit.get("model"), True)
                self.client.ledger.cache_hits.append({"task": TASK, "key": key, "provider": hit.get("provider"),
                                                      "model": hit.get("model"), "call_id": hit.get("call_id")})
            else:
                pending.append(text)
        size = int(self.client.task_setting(TASK, "batch_size", 20))
        for start in range(0, len(pending), size):
            batch = pending[start:start + size]
            try:
                reply = self.client.complete(TASK, build_batch_prompt(self.base_prompt, batch), make_validator(len(batch)))
            except CacheMiss as exc:
                raise CacheMiss(f"{exc} ({len(pending)} intent texts missing from code/cache/intent)") from exc
            except AllProvidersFailed as exc:
                for text in batch:
                    results[text] = IntentResult(None, None, None, None, None, False, f"model_failure: {exc}"[:300])
                continue
            for i, text in enumerate(batch, 1):
                r = reply.data[_text_id(i)]
                key = content_key(TASK, self.prompt_version, text)
                self.cache.put(TASK, key, {"key": key, "task": TASK, "prompt_version": self.prompt_version,
                                           "input_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                                           "response": r, "provider": reply.provider, "model": reply.model,
                                           "key_slot": reply.key_slot, "call_id": reply.call_id,
                                           "batch_size": len(batch), "call_usage": reply.usage})
                results[text] = IntentResult(r["label"], r["language"], r["negation_detected"], reply.provider,
                                             reply.model, False)
        return results


def model_text(text: str) -> str | None:
    """What the model sees: None for an empty/whitespace text (it authorizes nothing, so no call is made);
    otherwise the text capped at MAX_CLASSIFY_CHARS, so padding cannot inflate a batch or push an appended
    instruction past the cap into the prompt."""
    if not (text or "").strip():
        return None
    return text[:MAX_CLASSIFY_CHARS]


def decide(result: IntentResult, classify_rows: list[str]) -> tuple[bool, str]:
    """(is_deletion, path). DELETION only if the model says so and no CLASSIFY_REQUEST says otherwise."""
    if result.label is None:
        return False, "model_failure->NOT_APPLICABLE"
    if result.label == "DELETION":
        if classify_rows:
            return False, "model_deletion_but_classify_non_deletion->NOT_APPLICABLE"
        return True, "model_deletion->DELETION"
    if result.label == "NON_DELETION":
        return False, ("model_non_deletion+classify_corroborates->NOT_APPLICABLE" if classify_rows
                       else "model_non_deletion->NOT_APPLICABLE")
    return False, "model_unclear->NOT_APPLICABLE"
