#!/usr/bin/env python3
"""Probe every configured key alias x model with one tiny request per capability. Writes
code/evaluation/models_verified.json with key ALIASES only (never values).

Capabilities probed (not taken from documentation): listed, text generation, vision (tiny PNG), native
function calling. Error classes: 404_unavailable, 429_quota, auth, timeout, http_<code>.

    python3 code/analysis/probe_keys.py
"""
from __future__ import annotations

import base64
import json
import os
import struct
import sys
import urllib.error
import urllib.request
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml  # noqa: E402

from agent.llm_client import CONFIG_PATH, load_env_file  # noqa: E402
from agent.loaders import REPO_ROOT  # noqa: E402

MODELS = ["gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-2.5-flash"]
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta"


def tiny_png() -> bytes:
    """A 4x4 solid red PNG built with zlib (no image library)."""
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * 4 for _ in range(4))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def post(model: str, key: str, body: dict) -> tuple[str, dict]:
    req = urllib.request.Request(f"{ENDPOINT}/models/{model}:generateContent", data=json.dumps(body).encode(),
                                 method="POST", headers={"Content-Type": "application/json", "x-goog-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return "ok", json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        code = e.code
        return {404: "404_unavailable", 429: "429_quota", 400: "http_400", 401: "auth", 403: "auth"}.get(code, f"http_{code}"), {}
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        return "timeout", {}


def probe(key: str, model: str) -> dict:
    text = {"contents": [{"role": "user", "parts": [{"text": 'Reply with the JSON {"ok": true}'}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}}
    vision = {"contents": [{"role": "user", "parts": [
        {"text": "What single colour fills this image? One word."},
        {"inline_data": {"mime_type": "image/png", "data": base64.b64encode(tiny_png()).decode()}}]}],
        "generationConfig": {"temperature": 0}}
    tools = {"contents": [{"role": "user", "parts": [{"text": "Look up resource account_1 with the tool."}]}],
             "tools": [{"functionDeclarations": [{"name": "resource_observations", "description": "observations of a resource",
                                                  "parameters": {"type": "OBJECT", "properties": {"resource_id": {"type": "STRING"}},
                                                                 "required": ["resource_id"]}}]}],
             "toolConfig": {"functionCallingConfig": {"mode": "ANY"}}}
    out = {}
    s, _ = post(model, key, text)
    out["text"] = s
    if s != "ok":
        return out
    s, body = post(model, key, vision)
    answer = "".join(p.get("text", "") for p in (body.get("candidates") or [{}])[0].get("content", {}).get("parts", []))
    out["vision"] = ("ok" if "red" in answer.lower() else "wrong_answer") if s == "ok" else s
    s, body = post(model, key, tools)
    parts = (body.get("candidates") or [{}])[0].get("content", {}).get("parts", []) if s == "ok" else []
    out["tool_calling"] = ("ok" if any("functionCall" in p for p in parts) else "no_function_call") if s == "ok" else s
    return out


def listed(key: str) -> set[str]:
    names, page = set(), ""
    try:
        while True:
            req = urllib.request.Request(f"{ENDPOINT}/models?pageSize=1000" + (f"&pageToken={page}" if page else ""),
                                         headers={"x-goog-api-key": key})
            with urllib.request.urlopen(req, timeout=30) as r:
                body = json.loads(r.read().decode())
            names |= {m["name"].split("/", 1)[-1] for m in body.get("models", [])}
            page = body.get("nextPageToken", "")
            if not page:
                return names
    except (urllib.error.URLError, TimeoutError):
        return set()


def main() -> int:
    load_env_file()
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    report = {"probed_at": "see git commit time", "pool_note": "Keys of one Google Cloud project share one quota "
              "pool; the API does not reveal the project, so pools are UNKNOWN (failover, not proven extra capacity).",
              "slots": {}}
    for provider, p in cfg["providers"].items():
        for alias, env in p["key_slots"].items():
            key = os.environ.get(env)
            if not key:
                report["slots"][f"{provider}/{alias}"] = {"env_var": env, "status": "not set"}
                continue
            if provider != "gemini":
                report["slots"][f"{provider}/{alias}"] = {"env_var": env, "status": "set, not probed"}
                continue
            names = listed(key)
            report["slots"][f"{provider}/{alias}"] = {
                "env_var": env, "pool": "UNKNOWN",
                "models": {m: {"listed": m in names, **probe(key, m)} for m in MODELS}}
            print(alias, {m: v for m, v in report["slots"][f"{provider}/{alias}"]["models"].items()})
    (REPO_ROOT / "code" / "evaluation" / "models_verified.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
