#!/usr/bin/env python3
"""Model-comparison strategy for intent: the same prompt on two text models, over the 32 sample requests
(truth: sample_output.csv, all deletion) and the 10 eval texts that are not built from deletion templates
(truth: NON_DELETION, corroborated by the 10 CLASSIFY_REQUEST rows; this truth is an INFERENCE, not a label).

Each model gets its own cache root, so the production intent cache is not touched.
    python3 code/analysis/compare_intent.py [--from-cache]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.intent import IntentClassifier  # noqa: E402
from agent.llm_client import CACHE_DIR, LLMClient, ResponseCache, UsageLedger  # noqa: E402
from agent.loaders import DEFAULT_DATASET_DIR, REPO_ROOT, load_dataset  # noqa: E402

MODELS = {"gemini-3.5-flash-lite": "flash_lite", "gemini-3.5-flash": "flash_new"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-cache", action="store_true")
    args = ap.parse_args()
    ds = load_dataset(DEFAULT_DATASET_DIR)
    truth = {r["request_text"]: "DELETION" for r in ds.sample_requests}
    classify_accounts = {a.resource_id for a in ds.activity if a.event_type == "CLASSIFY_REQUEST"}
    # the 10 non-template eval texts: sentences that occur at most twice across the eval set
    import re
    sents = Counter(s for r in ds.requests for s in re.split(r"(?<=[.!?。？！])\s+", r["request_text"].strip()) if s)
    for r in ds.requests:
        if any(sents[s] <= 2 for s in re.split(r"(?<=[.!?。？！])\s+", r["request_text"].strip()) if s):
            truth[r["request_text"]] = "NON_DELETION"
    report = {"texts": len(truth), "truth_source": "samples: sample_output.csv (DELETION); 10 eval texts: "
              f"NON_DELETION, corroborated by {len(classify_accounts)} CLASSIFY_REQUEST rows (inference)", "models": {}}
    labels = {}
    for model_id, cfg_name in MODELS.items():
        ledger = UsageLedger()
        client = LLMClient(from_cache=args.from_cache, ledger=ledger)
        client.config["tasks"]["intent"]["attempts"] = [
            {"model": cfg_name, "key_slot": slot} for slot in client.config["providers"]["gemini"]["key_slots"]]
        clf = IntentClassifier(client, ResponseCache(CACHE_DIR / f"compare_{model_id}"))
        res = clf.classify(list(truth))
        labels[model_id] = {t: r.label for t, r in res.items()}
        correct = sum(1 for t, want in truth.items() if labels[model_id].get(t) == want)
        report["models"][model_id] = {
            "accuracy": f"{correct}/{len(truth)}",
            "by_truth": {k: f"{sum(1 for t, w in truth.items() if w == k and labels[model_id].get(t) == k)}/"
                            f"{sum(1 for w in truth.values() if w == k)}" for k in ("DELETION", "NON_DELETION")},
            "failures": sum(1 for r in res.values() if r.label is None),
            "calls": len(ledger.calls), "tokens": sum(c.get("total_tokens", 0) for c in ledger.calls),
            "answered_by": dict(Counter(f"{c['model']}@{c['key_slot']}" for c in ledger.calls))}
    a, b = list(MODELS)
    report["agreement"] = f"{sum(1 for t in truth if labels[a].get(t) == labels[b].get(t))}/{len(truth)}"
    report["disagreements"] = [{"text": t[:90], a: labels[a].get(t), b: labels[b].get(t)}
                               for t in truth if labels[a].get(t) != labels[b].get(t)]
    print(json.dumps(report, indent=1, ensure_ascii=False))
    (REPO_ROOT / "code" / "evaluation" / "intent_model_comparison.json").write_text(
        json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
