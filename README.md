<<<<<<< HEAD
# Ghost Data Still Exists: deletion-audit agent

HackerRank Orchestrate, October 2026. For every request in `dataset/deletion_requests.csv`, the agent decides whether
deletion was requested, reconstructs the subject's current and historical identifiers, attributes records to the
subject, rebuilds each record's state at `audit_at` from the evidence, applies `dataset/deletion_rules.csv`, and writes
`COMPLETE`, `INCOMPLETE` or `NOT_APPLICABLE` with an ordered `steps_json` plan to `output.csv`.

Design, decisions and rejected alternatives: [`DESIGN.md`](DESIGN.md). Measured results:
[`code/evaluation/evaluation_report.md`](code/evaluation/evaluation_report.md) and
[`code/evaluation/usage_report.md`](code/evaluation/usage_report.md).

## How it works

The model does exactly two jobs; code decides everything that reaches the CSV.

| Stage | Module | Model? |
|---|---|---|
| Load and validate CSVs | `code/agent/loaders.py` | no |
| Parse evidence into typed observations: SQL in in-memory SQLite, JSON, six log syntaxes with trace joins, the handover text, PDF text layers (stdlib decoder) | `code/agent/parsers/` | no |
| Read the 11 PNG screenshots into the same observation schema; output validated against known systems/prefixes | `code/agent/parsers/png_vlm.py` | **yes** (vision) |
| Request intent: `DELETION` / `NON_DELETION` / `UNCLEAR` | `code/agent/intent.py`, prompt `code/prompts/intent.md` | **yes** (text) |
| Identity: request email → user_id → identifier set with provenance | `code/agent/identity.py` | no |
| Attribution: global constrained assignment; labels CONFIRMED / INFERRED / AMBIGUOUS / CONFLICT | `code/agent/attribution.py` | no |
| State at `audit_at` and citation rule C | `code/agent/state.py` | no |
| Category → treatment from `deletion_rules.csv` | `code/agent/rules.py` | no |
| Step generation, ordering, serialization | `code/agent/steps.py` | no |
| Row and file validation | `code/agent/validation.py` | no |

Safety properties enforced in code:
- The model never writes `deletion_status`, `operation`, `state`, `system_id`, `resource_id` or `evidence_id`.
- `UNCLEAR` or a model failure becomes `NOT_APPLICABLE`.
- AMBIGUOUS or CONFLICT attributions produce no steps, so no remediation is ever planned against data that may belong to someone else.
- Evidence captured after `audit_at` is never used for state or citation.

## Setup

```bash
python3 -m pip install -r requirements.txt
```

Python 3.10 or newer is required. No other dependencies: HTTP calls use the standard library.

### API keys (only needed for live runs)

Keys are read only from environment variables, or from an uncommitted `.env` file at the repository root (it is gitignored):

```text
GEMINI_API_KEY=...              # primary key
GEMINI_API_KEY_FALLBACK_1=...   # used when the primary is exhausted or failing
GEMINI_API_KEY_FALLBACK_2=...   # used when fallback 1 is exhausted or failing
GEMINI_API_KEY_FALLBACK_3=...   # used when fallback 2 is exhausted or failing
```

Any subset works: a missing or empty key slot is skipped and logged.

The model ids, rate limits and fallback order live in `code/config/models.yaml`. Check them against the provider's
current model list with:

```bash
python3 code/main.py --check-models
```

## Run

Live (model calls only for items not already cached in `code/cache/`):

```bash
python3 code/main.py
```

Reproduce from the committed cache (zero network calls, no keys needed; fails loudly if a cache entry is missing):

```bash
python3 code/main.py --from-cache
```

Outputs:
- `output.csv`: 518 rows sorted by case_id, header `case_id,deletion_status,steps_json`. It is written to a temporary file and only replaces `output.csv` after the whole file validates.
- `code/evaluation/sample_predictions.csv`: predictions for the 32 public sample cases.
- `code/traces/<case_id>.json`: identifiers with provenance, candidate edges, attribution labels with rationale, state per resource with cited evidence, the rule that fired, the intent path and provider. `code/traces/_run_summary.json` holds the counts.
- `code/evaluation/usage_ledger.json`: calls, cache hits, tokens and fallback events for the run.

Evaluate (sample metrics, attribution metrics, root causes, custom cases X1–X5, usage report):

```bash
python3 code/evaluation/main.py --from-cache --determinism
```

Unit tests (pure code: no model calls, keys removed by a fixture):

```bash
python3 -m pytest code/tests -q
```

## Privacy note

Free-tier Gemini usage may allow the provider to use prompts to improve its products. The dataset is synthetic (an
imaginary company, fictional people), so no real personal data is sent. Keys are never logged, cached or committed.

## Packaging

```bash
zip -r code.zip README.md code -x "*/__pycache__/*" "*/.venv/*" "*/venv/*" "*/node_modules/*" "*/.git/*" "*/.DS_Store"
```

Challenge and submission page: https://wwww.hackerrank.com/contests/hackerrank-orchestrate-october26/challenges/ghost-data-still-exists
=======
# COOKED---October
>>>>>>> origin/main
