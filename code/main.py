"""Ghost Data Still Exists: deletion-audit agent.

    python3 code/main.py                 # live: model calls for uncached items, then deterministic rules
    python3 code/main.py --from-cache    # zero network calls, no keys needed; fails loudly on a missing entry
    python3 code/main.py --check-models  # list the provider's models and check the ids in code/config/models.yaml

Writes ./output.csv (518 eval rows), code/evaluation/sample_predictions.csv (32 sample rows),
per-case traces under code/traces/, and the usage ledger code/evaluation/usage_ledger.json.

Exit codes:
    0  ok: output.csv written
    2  output validation failed: output.csv left untouched
    3  cache miss under --from-cache: output.csv left untouched
    4  model failure (an intent call or a screenshot extraction failed): output.csv left untouched;
       with --allow-degraded the diagnostic rows go to output.degraded.csv instead
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent import pipeline  # noqa: E402
from agent.llm_client import CacheMiss, LLMClient  # noqa: E402
from agent.loaders import DEFAULT_DATASET_DIR, REPO_ROOT  # noqa: E402
from agent.validation import validate_output_file  # noqa: E402

CODE_DIR = REPO_ROOT / "code"
EXIT_OK, EXIT_INVALID, EXIT_CACHE_MISS, EXIT_MODEL_FAILURE = 0, 2, 3, 4


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Deletion-audit agent",
                                 epilog="exit codes: 0 ok; 2 validation failed; 3 cache miss under --from-cache; "
                                        "4 model failure (output.csv is never overwritten on 2, 3 or 4)")
    ap.add_argument("--from-cache", action="store_true", help="use only cached model replies (no network, no keys)")
    ap.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_DIR)
    ap.add_argument("--output", type=Path, default=REPO_ROOT / "output.csv")
    ap.add_argument("--sample-output", type=Path, default=CODE_DIR / "evaluation" / "sample_predictions.csv")
    ap.add_argument("--traces", type=Path, default=CODE_DIR / "traces")
    ap.add_argument("--ledger", type=Path, default=CODE_DIR / "evaluation" / "usage_ledger.json")
    ap.add_argument("--allow-degraded", action="store_true",
                    help="on exit 2 or 4, write the diagnostic rows to <output>.degraded.csv (never to output.csv)")
    ap.add_argument("--reextract", action="append", default=[], metavar="FILE_ID",
                    help="send this screenshot to the vision model again under the current prompt (repeatable)")
    ap.add_argument("--check-models", action="store_true")
    args = ap.parse_args(argv)

    if args.check_models:
        report = LLMClient().verify_models()
        print(json.dumps(report, indent=1))
        (CODE_DIR / "evaluation" / "models_verified.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
        return EXIT_OK

    try:
        rr = pipeline.run(args.dataset, from_cache=args.from_cache, reextract=set(args.reextract))
    except CacheMiss as exc:
        print(f"ERROR (--from-cache): {exc}", file=sys.stderr)
        return EXIT_CACHE_MISS
    # Rows are written to a temporary file; output.csv is replaced only by a clean, valid run.
    tmp = args.output.with_name(args.output.name + ".tmp")
    pipeline.write_outputs(rr, tmp, args.sample_output, args.traces)
    args.ledger.parent.mkdir(parents=True, exist_ok=True)
    args.ledger.write_text(json.dumps(rr.ledger.to_json(), indent=1, sort_keys=True) + "\n", encoding="utf-8")
    errors = validate_output_file(tmp, [r["case_id"] for r in rr.dataset.requests])
    failures = rr.summary["model_failures"]
    n_fail = failures["intent_cases"] + failures["vlm_files_skipped"]
    s = rr.summary["eval"]
    print(f"rows={s['cases']} status={s['status']} degraded={len(s['degraded'])} "
          f"model_failures={failures['intent_cases']} vlm_files_skipped={failures['vlm_files_skipped']} "
          f"model_calls={len(rr.ledger.calls)} cache_hits={len(rr.ledger.cache_hits)} output={args.output}")
    code = EXIT_MODEL_FAILURE if n_fail else (EXIT_INVALID if errors else EXIT_OK)
    if code == EXIT_OK:
        tmp.replace(args.output)
        return EXIT_OK
    reason = (f"{n_fail} model failure(s)" if n_fail else "invalid output: " + "; ".join(errors))
    if args.allow_degraded:
        degraded = args.output.with_name(args.output.stem + ".degraded.csv")
        tmp.replace(degraded)
        print(f"{reason}; output.csv untouched, diagnostic rows in {degraded}", file=sys.stderr)
    else:
        tmp.unlink(missing_ok=True)
        print(f"{reason}; output.csv untouched (use --allow-degraded for diagnostic rows)", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
