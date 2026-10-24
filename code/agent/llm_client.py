"""Provider abstraction: complete(task, prompt, image=None, validator) -> validated JSON.

* Ordered (model, key slot) attempts per task come from code/config/models.yaml.
* Fallback on HTTP 429 / 5xx, timeout, or output that fails validation after 2 retries.
* Per (key slot, model) token bucket; exponential backoff on 429; concurrency 1.
* A daily-quota exhaustion marks that (model, key slot) exhausted and moves on; it never crashes the run.
* Keys are read from the environment (or an uncommitted .env) and are never logged or cached.
* Every reply is cached content-addressed under code/cache/ with the provider and model that answered;
  --from-cache makes zero network calls and fails loudly on a missing entry.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from .loaders import REPO_ROOT

CONFIG_PATH = REPO_ROOT / "code" / "config" / "models.yaml"
# ORCHESTRATE_CACHE_DIR points a run at another cache copy (the coupling probes use a renamed copy).
CACHE_DIR = Path(os.environ["ORCHESTRATE_CACHE_DIR"]) if os.environ.get("ORCHESTRATE_CACHE_DIR") else REPO_ROOT / "code" / "cache"
PROMPTS_DIR = REPO_ROOT / "code" / "prompts"


class CacheMiss(RuntimeError):
    """--from-cache was requested and an entry is missing: fail loudly."""


class AllProvidersFailed(RuntimeError):
    pass


class ValidationFailed(ValueError):
    pass


def load_env_file(path: Path = REPO_ROOT / ".env") -> list[str]:
    """Load KEY=VALUE lines into os.environ without overriding existing variables. Returns names only."""
    loaded = []
    if not path.exists():
        return loaded
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name, value = name.strip(), value.strip().strip('"').strip("'")
        if name and value and name not in os.environ:
            os.environ[name] = value
            loaded.append(name)
    return loaded


def content_key(task: str, prompt_version: str, payload: bytes | str) -> str:
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    return hashlib.sha256(task.encode() + b"\x00" + prompt_version.encode() + b"\x00" + data).hexdigest()


@dataclass
class ModelReply:
    data: Any
    provider: str
    model: str
    key_slot: str
    usage: dict
    call_id: str
    attempts: list[dict] = field(default_factory=list)


@dataclass
class UsageLedger:
    calls: list[dict] = field(default_factory=list)
    cache_hits: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)       # fallbacks, quota exhaustion, validation failures
    models_verified: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {"calls": self.calls, "cache_hits": self.cache_hits, "events": self.events,
                "models_verified": self.models_verified}


class ResponseCache:
    def __init__(self, root: Path = CACHE_DIR):
        self.root = root

    def path(self, task: str, key: str) -> Path:
        return self.root / task / f"{key}.json"

    def get(self, task: str, key: str) -> dict | None:
        p = self.path(task, key)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def put(self, task: str, key: str, record: dict) -> None:
        p = self.path(task, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        record = {**record, "usage_metadata": usage_metadata(record)}
        p.write_text(json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8",
                     newline="\n")                       # LF on every OS: cache files are byte-compared (C3)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def usage_metadata(record: dict) -> dict:
    """The provider usage behind a cache entry, in one place (C8). A batched call's usage is the whole call's and is
    repeated in every entry it produced; reports count each call_id once."""
    u = record.get("call_usage") or {}
    return {"provider": record.get("provider"), "model": record.get("model"), "model_version": u.get("model_version"),
            "key_slot": record.get("key_slot"), "call_id": record.get("call_id"),
            "input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens"),
            "thinking_tokens": u.get("thinking_tokens"), "total_tokens": u.get("total_tokens"),
            "timestamp": u.get("timestamp") or "UNKNOWN", "entries_sharing_call": record.get("batch_size", 1)}


class LLMClient:
    def __init__(self, from_cache: bool = False, config_path: Path = CONFIG_PATH, ledger: UsageLedger | None = None):
        self.config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        self.from_cache = from_cache
        self.ledger = ledger or UsageLedger()
        self.exhausted: set[tuple[str, str]] = set()
        self.last_call: dict[tuple[str, str], float] = {}
        self.call_counter = 0
        if not from_cache:
            for name in load_env_file():
                self.ledger.events.append({"event": "env_file_loaded", "variable": name})

    # ---- configuration --------------------------------------------------------------
    def prompt(self, task: str) -> tuple[str, str]:
        """(prompt text, prompt version = sha256 prefix of the prompt file)."""
        text = (PROMPTS_DIR / self.config["tasks"][task]["prompt_file"]).read_text(encoding="utf-8")
        return text, "p" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]

    def task_setting(self, task: str, name: str, default: Any = None) -> Any:
        return self.config["tasks"][task].get(name, default)

    def _key(self, slot: str, provider: str = "gemini") -> str | None:
        env_name = self.config["providers"][provider]["key_slots"].get(slot)
        return (os.environ.get(env_name) or None) if env_name else None

    def available_slots(self) -> list[str]:
        return [s for s in self.config["providers"]["gemini"]["key_slots"] if self._key(s)]

    # ---- model verification -----------------------------------------------------------
    def verify_models(self) -> dict:
        """List the provider's models with each available key and check every configured id."""
        endpoint = self.config["providers"]["gemini"]["endpoint"]
        configured = sorted({m["id"] for m in self.config["models"].values()})
        report = {"configured": configured, "by_slot": {}}
        for slot in self.config["providers"]["gemini"]["key_slots"]:
            key = self._key(slot)
            if not key:
                report["by_slot"][slot] = "key not set"
                continue
            names, page = set(), ""
            try:
                while True:
                    url = f"{endpoint}/models?pageSize=1000" + (f"&pageToken={page}" if page else "")
                    req = urllib.request.Request(url, headers={"x-goog-api-key": key})
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        body = json.loads(resp.read().decode("utf-8"))
                    names |= {m["name"].split("/", 1)[-1] for m in body.get("models", [])
                              if "generateContent" in m.get("supportedGenerationMethods", [])}
                    page = body.get("nextPageToken", "")
                    if not page:
                        break
                report["by_slot"][slot] = {mid: (mid in names) for mid in configured}
                report.setdefault("flash_family_available", sorted(n for n in names if "flash" in n))
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                report["by_slot"][slot] = f"model list failed: {type(exc).__name__}"
        self.ledger.models_verified = report
        return report

    # ---- the one entry point ------------------------------------------------------------
    def complete(self, task: str, prompt: str, validator: Callable[[Any], Any], image: bytes | None = None,
                 image_mime: str = "image/png") -> ModelReply:
        if self.from_cache:
            raise CacheMiss(f"{task}: no cached reply and --from-cache forbids network calls")
        limits = self.config["limits"]
        attempts_log: list[dict] = []
        for attempt in self.config["tasks"][task]["attempts"]:
            model_cfg = self.config["models"][attempt["model"]]
            slot, model_id = attempt["key_slot"], model_cfg["id"]
            if (slot, model_id) in self.exhausted:
                attempts_log.append({"model": model_id, "key_slot": slot, "outcome": "skipped_exhausted"})
                continue
            provider = model_cfg.get("provider", "gemini")
            key = self._key(slot, provider)
            if not key:
                attempts_log.append({"model": model_id, "key_slot": slot, "outcome": "skipped_no_key"})
                continue
            feedback = ""
            for schema_try in range(limits["schema_retries"] + 1):
                outcome, payload = self._call_with_backoff(model_cfg, slot, key, prompt + feedback, image, image_mime)
                if outcome != "ok":
                    attempts_log.append({"model": model_id, "key_slot": slot, "outcome": outcome})
                    if outcome in ("quota_exhausted", "model_unavailable"):
                        # Both are permanent for this run: skip this (model, key) for every later request.
                        self.exhausted.add((slot, model_id))
                        self.ledger.events.append({"event": outcome, "task": task, "model": model_id,
                                                   "key_slot": slot})
                    break
                text, usage, call_id = payload
                try:
                    data = validator(json.loads(text))
                except (json.JSONDecodeError, ValidationFailed, KeyError, TypeError, ValueError) as exc:
                    attempts_log.append({"model": model_id, "key_slot": slot, "outcome": "invalid_output",
                                         "error": str(exc)[:200]})
                    self.ledger.events.append({"event": "validation_failed", "task": task, "model": model_id,
                                               "key_slot": slot, "try": schema_try + 1, "error": str(exc)[:200]})
                    feedback = ("\n\nYour previous reply was rejected by the validator: "
                                f"{str(exc)[:300]}\nReturn only JSON that satisfies the schema above.")
                    continue
                attempts_log.append({"model": model_id, "key_slot": slot, "outcome": "ok"})
                if len(attempts_log) > 1:
                    self.ledger.events.append({"event": "fallback_used", "task": task, "answered_by": model_id,
                                               "key_slot": slot, "attempts": attempts_log})
                return ModelReply(data, provider, model_id, slot, usage, call_id, attempts_log)
        self.ledger.events.append({"event": "all_providers_failed", "task": task, "attempts": attempts_log})
        raise AllProvidersFailed(f"{task}: every configured attempt failed: {attempts_log}")

    # ---- transport ----------------------------------------------------------------------
    def _throttle(self, slot: str, model_cfg: dict) -> None:
        interval = 60.0 / max(1, model_cfg.get("requests_per_minute", 10))
        k = (slot, model_cfg["id"])
        wait = self.last_call.get(k, 0.0) + interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self.last_call[k] = time.monotonic()

    def _call_with_backoff(self, model_cfg: dict, slot: str, key: str, prompt: str, image: bytes | None,
                           image_mime: str) -> tuple[str, Any]:
        limits = self.config["limits"]
        delay = limits["backoff_initial_seconds"]
        for _ in range(limits["max_429_retries_per_attempt"] + 1):
            self._throttle(slot, model_cfg)
            status, body = self._post(model_cfg, key, prompt, image, image_mime)
            if status == 200:
                return "ok", self._record(model_cfg, slot, body)
            if status == 429:
                if _is_daily_quota(body):
                    return "quota_exhausted", None
                time.sleep(min(delay, limits["backoff_max_seconds"]))
                delay *= 2
                continue
            if status in (500, 502, 503, 504, -1):
                time.sleep(min(delay, limits["backoff_max_seconds"]))
                delay *= 2
                continue
            if status == 404:
                return "model_unavailable", None       # e.g. "no longer available to new users"
            return f"http_{status}", None
        return "retries_exhausted", None

    def _post(self, model_cfg: dict, key: str, prompt: str, image: bytes | None, image_mime: str) -> tuple[int, dict]:
        if model_cfg.get("provider") == "groq":
            return self._post_openai_compatible(model_cfg, key, prompt)
        endpoint = self.config["providers"]["gemini"]["endpoint"]
        parts: list[dict] = [{"text": prompt}]
        if image is not None:
            parts.append({"inline_data": {"mime_type": image_mime, "data": base64.b64encode(image).decode("ascii")}})
        generation: dict = {"temperature": 0, "responseMimeType": "application/json"}
        if model_cfg.get("thinking_budget") is not None:
            generation["thinkingConfig"] = {"thinkingBudget": model_cfg["thinking_budget"]}
        body = json.dumps({"contents": [{"role": "user", "parts": parts}], "generationConfig": generation}).encode()
        req = urllib.request.Request(f"{endpoint}/models/{model_cfg['id']}:generateContent", data=body, method="POST",
                                     headers={"Content-Type": "application/json", "x-goog-api-key": key})
        try:
            with urllib.request.urlopen(req, timeout=self.config["limits"]["timeout_seconds"]) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read().decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return exc.code, {}
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            return -1, {}

    def _post_openai_compatible(self, model_cfg: dict, key: str, prompt: str) -> tuple[int, dict]:
        """Groq chat completions (OpenAI-compatible), JSON-object responses, temperature 0."""
        endpoint = self.config["providers"][model_cfg["provider"]]["endpoint"]
        body = json.dumps({"model": model_cfg["id"], "temperature": 0, "response_format": {"type": "json_object"},
                           "messages": [{"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request(f"{endpoint}/chat/completions", data=body, method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
        try:
            with urllib.request.urlopen(req, timeout=self.config["limits"]["timeout_seconds"]) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read().decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return exc.code, {}
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            return -1, {}

    def _record(self, model_cfg: dict, slot: str, body: dict) -> tuple[str, dict, str]:
        self.call_counter += 1
        call_id = uuid.uuid4().hex[:16]          # unique across runs, so cached usage is never double counted
        provider = model_cfg.get("provider", "gemini")
        if provider == "groq":
            u = body.get("usage", {})
            usage = {"input_tokens": u.get("prompt_tokens", 0), "output_tokens": u.get("completion_tokens", 0),
                     "thinking_tokens": 0, "total_tokens": u.get("total_tokens", 0), "model_version": body.get("model"),
                     "timestamp": _now()}
            self.ledger.calls.append({"call_id": call_id, "provider": provider, "model": model_cfg["id"],
                                      "key_slot": slot, **usage})
            text = ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            return text, usage, call_id
        meta = body.get("usageMetadata", {})
        usage = {"input_tokens": meta.get("promptTokenCount", 0),
                 "output_tokens": meta.get("candidatesTokenCount", 0),
                 "thinking_tokens": meta.get("thoughtsTokenCount", 0),
                 "total_tokens": meta.get("totalTokenCount", 0),
                 "model_version": body.get("modelVersion", model_cfg["id"]), "timestamp": _now()}
        self.ledger.calls.append({"call_id": call_id, "provider": "gemini", "model": model_cfg["id"],
                                  "key_slot": slot, **usage})
        parts = (body.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        return text, usage, call_id


def _is_daily_quota(body: dict) -> bool:
    err = body.get("error", {})
    blob = json.dumps(err).lower()
    return "perday" in blob or "per_day" in blob or "daily" in blob
