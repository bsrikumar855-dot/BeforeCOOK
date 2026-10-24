"""Ghost Data Still Exists: deletion-audit agent.

    python3 code/main.py                 # live: model calls for uncached items, then deterministic rules
    python3 code/main.py --from-cache    # zero network calls, no keys needed; fails loudly on a missing entry
    python3 code/main.py --check-models  # list the provider's models and check the ids in code/config/models.yaml

Writes ./output.csv (518 eval rows), code/evaluation/sample_predictions.csv (32 sample rows),
per-case traces under code/traces/, and the usage ledger code/evaluation/usage_ledger.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent.llm_client import CacheMiss, LLMClient  # noqa: E402
from agent.loaders import DEFAULT_DATASET_DIR, REPO_ROOT  # noqa: E402
from agent.pipeline import run, write_outputs  # noqa: E402
from agent.validation import validate_output_file  # noqa: E402

CODE_DIR = REPO_ROOT / "code"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Deletion-audit agent")
    ap.add_argument("--from-cache", action="store_true", help="use only cached model replies (no network, no keys)")
    ap.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_DIR)
    ap.add_argument("--output", type=Path, default=REPO_ROOT / "output.csv")
    ap.add_argument("--sample-output", type=Path, default=CODE_DIR / "evaluation" / "sample_predictions.csv")
    ap.add_argument("--traces", type=Path, default=CODE_DIR / "traces")
    ap.add_argument("--ledger", type=Path, default=CODE_DIR / "evaluation" / "usage_ledger.json")
    ap.add_argument("--check-models", action="store_true")
    args = ap.parse_args(argv)

    if args.check_models:
        report = LLMClient().verify_models()
        print(json.dumps(report, indent=1))
        return 0

    try:
        rr = run(args.dataset, from_cache=args.from_cache)
    except CacheMiss as exc:
        print(f"ERROR (--from-cache): {exc}", file=sys.stderr)
        return 3
    # Write to a temporary file and only replace output.csv once the whole file validates:
    # a partial or invalid output.csv is never left behind.
    tmp = args.output.with_name(args.output.name + ".tmp")
    write_outputs(rr, tmp, args.sample_output, args.traces)
    args.ledger.parent.mkdir(parents=True, exist_ok=True)
    args.ledger.write_text(json.dumps(rr.ledger.to_json(), indent=1, sort_keys=True) + "\n", encoding="utf-8")
    errors = validate_output_file(tmp, [r["case_id"] for r in rr.dataset.requests])
    s = rr.summary["eval"]
    print(f"rows={s['cases']} status={s['status']} degraded={len(s['degraded'])} "
          f"model_calls={len(rr.ledger.calls)} cache_hits={len(rr.ledger.cache_hits)} output={args.output}")
    if errors:
        tmp.unlink(missing_ok=True)
        print("OUTPUT INVALID, output.csv not written: " + "; ".join(errors), file=sys.stderr)
        return 2
    tmp.replace(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
