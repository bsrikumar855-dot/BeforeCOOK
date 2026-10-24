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
    g = ap.add_argument_group("explain mode (read-only; never writes output.csv)")
    g.add_argument("--case", help="case_id to trace")
    g.add_argument("--explain", action="store_true", help="print the stage-by-stage trace of --case or an ad-hoc request")
    g.add_argument("--email", help="ad-hoc request: subject email")
    g.add_argument("--audit-at", help="ad-hoc request: ISO-8601 audit time")
    g.add_argument("--request-text", help="ad-hoc request: request text (at most 5,000 characters; data, not instructions)")
    g.add_argument("--requested-at", help="ad-hoc request: ISO-8601 request time (default: --audit-at)")
    g.add_argument("--reported-at", help="ad-hoc request: ISO-8601 report time (default: --audit-at)")
    args = ap.parse_args(argv)

    if args.explain or args.case or args.email:
        return explain_mode(args)

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
    allowed_empty = {cid for cid, o in rr.outcomes.items() if o.trace.get("reason_code") == "NO_ATTRIBUTABLE_RESOURCE"}
    errors = validate_output_file(tmp, [r["case_id"] for r in rr.dataset.requests], allowed_empty)
    failures = rr.summary["model_failures"]
    n_fail = failures["intent_cases"] + failures["vlm_files_skipped"] + failures.get("evidence_files_rejected", 0)
    s = rr.summary["eval"]
    print(f"rows={s['cases']} status={s['status']} degraded={len(s['degraded'])} "
          f"model_failures={failures['intent_cases']} vlm_files_skipped={failures['vlm_files_skipped']} "
          f"model_calls={len(rr.ledger.calls)} cache_hits={len(rr.ledger.cache_hits)} output={args.output}")
    code = EXIT_MODEL_FAILURE if n_fail else (EXIT_INVALID if errors else EXIT_OK)
    if code == EXIT_OK:
        tmp.replace(args.output)
        return EXIT_OK
    reason = (f"{n_fail} degrade event(s): model failures or rejected evidence files "
              f"{[r['file_id'] for r in rr.summary.get('evidence_rejected', [])]}" if n_fail
              else "invalid output: " + "; ".join(errors))
    if args.allow_degraded:
        degraded = args.output.with_name(args.output.stem + ".degraded.csv")
        tmp.replace(degraded)
        print(f"{reason}; output.csv untouched, diagnostic rows in {degraded}", file=sys.stderr)
    else:
        tmp.unlink(missing_ok=True)
        print(f"{reason}; output.csv untouched (use --allow-degraded for diagnostic rows)", file=sys.stderr)
    return code


def explain_mode(args) -> int:
    from agent.explain import ADHOC_CASE_ID, ExplainInputError, adhoc_dataset, explain, validate_adhoc
    adhoc = args.email is not None or args.audit_at is not None or args.request_text is not None
    tmp_root = None
    try:
        if not args.explain:
            raise ExplainInputError("--case/--email need --explain")
        if adhoc == bool(args.case):
            raise ExplainInputError("give either --case, or --email with --audit-at and --request-text")
        dataset = args.dataset
        if adhoc:
            validate_adhoc(args.email or "", args.audit_at or "", args.request_text or "", args.requested_at,
                           args.reported_at)
            dataset = adhoc_dataset(args.dataset, args.email, args.audit_at, args.request_text, args.requested_at,
                                    args.reported_at)
            tmp_root = dataset.parent
        rr = pipeline.run(dataset, from_cache=args.from_cache)
        print(explain(rr, ADHOC_CASE_ID if adhoc else args.case), end="")
        return EXIT_OK
    except ExplainInputError as exc:
        print(f"ERROR (explain): {exc}", file=sys.stderr)
        return EXIT_INVALID
    except CacheMiss as exc:
        print(f"ERROR (--from-cache): {exc}", file=sys.stderr)
        return EXIT_CACHE_MISS
    finally:
        if tmp_root is not None:
            import shutil
            shutil.rmtree(tmp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
