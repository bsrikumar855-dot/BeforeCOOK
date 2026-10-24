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
| Ground every screenshot resource id in local OCR text of the same image (an id not visible is dropped) | `code/agent/parsers/ocr_grounding.py` | no (Tesseract, local) |
| Request intent: `DELETION` / `NON_DELETION` / `UNCLEAR` | `code/agent/intent.py`, prompt `code/prompts/intent.md` | **yes** (text) |
| Identity: request email → user_id → identifier set with provenance | `code/agent/identity.py` | no |
| Attribution: global constrained assignment; labels CONFIRMED / INFERRED / AMBIGUOUS / CONFLICT | `code/agent/attribution.py` | no |
| State at `audit_at` and citation rule C | `code/agent/state.py` | no |
| Category → treatment from `deletion_rules.csv` | `code/agent/rules.py` | no |
| Step generation, ordering, serialization | `code/agent/steps.py` | no |
| Row and file validation | `code/agent/validation.py` | no |

Safety properties enforced in code:
- The model never writes `deletion_status`, `operation`, `state`, `system_id`, `resource_id` or `evidence_id`.
- `UNCLEAR` or a model failure becomes `NOT_APPLICABLE`; an empty request text is `NOT_APPLICABLE` without a call, and texts are capped at 5,000 characters for classification.
- A screenshot id the model reports is kept only if the exact token is in the image's OCR text.
- AMBIGUOUS or CONFLICT attributions produce no steps, so no remediation is ever planned against data that may belong to someone else.
- Evidence captured after `audit_at` is never used for state or citation.

## Setup

```bash
python3 -m pip install -r requirements.txt
```

Python 3.10 or newer is required. HTTP calls use the standard library.

### OCR engine (screenshot id grounding)

`pytesseract` (pinned in `requirements.txt`) needs the Tesseract binary; the committed results were produced with
Tesseract 5.5.3. Install it from your package manager (`apt install tesseract-ocr`, `brew install tesseract`, or the
UB Mannheim installer on Windows). The binary is found through `TESSERACT_CMD`, then `PATH`, then
`C:/Program Files/Tesseract-OCR/tesseract.exe`. OCR text is cached in `code/cache/ocr/` by sha256(image + engine
version), so `--from-cache` runs need no Tesseract at all; a live run without the cache and without Tesseract stops
with exit 3 instead of accepting ungrounded ids.

### API keys (only needed for live runs)

Keys are read only from environment variables, or from an uncommitted `.env` file at the repository root (it is gitignored):

```text
GEMINI_API_KEY=...              # primary key
GEMINI_API_KEY_FALLBACK_1=...   # used when the primary is exhausted or failing
GEMINI_API_KEY_FALLBACK_2=...   # used when fallback 1 is exhausted or failing
GEMINI_API_KEY_FALLBACK_3=...   # ... and so on, in order
GEMINI_API_KEY_FALLBACK_4=...
GEMINI_API_KEY_FALLBACK_5=...
GEMINI_API_KEY_FALLBACK_6=...
GROQ_API_KEY=...                # optional; not used by the committed run
TESSERACT_CMD=...               # optional path to the tesseract binary
```

Any subset works: a missing or empty key slot is skipped and logged. Probe results per key alias (never the values)
are in `code/evaluation/models_verified.json`; quota pools are UNKNOWN because keys of one project share a quota.

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
- `code/evaluation/usage_ledger.json`: live calls, the cache entries read, and fallback events for the run. Every cache entry carries the provider's usage metadata, so `usage_report.md` is rebuilt from the entries the run read.

Exit codes (also in `python3 code/main.py --help`):

| Code | Meaning | output.csv |
|---|---|---|
| 0 | ok | replaced with the new rows |
| 2 | output validation failed | untouched |
| 3 | cache miss under `--from-cache` (or no OCR cache and no Tesseract) | untouched |
| 4 | model failure (an intent call or a screenshot extraction failed) | untouched; `--allow-degraded` writes the diagnostic rows to `output.degraded.csv` |

Evaluate (sample metrics, attribution metrics, root causes, category coverage, custom cases X1–X19 including the
adversarial X14–X19, determinism over three processes, usage report):

```bash
python3 code/evaluation/main.py --from-cache --determinism
```

Unit tests (pure code: no model calls, keys removed by a fixture):

```bash
python3 -m pytest code/tests -q
```

## Privacy note

Free-tier Gemini usage may allow the provider to use prompts to improve its products. The dataset is synthetic (an
imaginary company, fictional people), so no real personal data is sent. Only request texts and the 11 screenshots
go to the provider; logs, JSON, SQL and PDFs are parsed locally, and OCR runs locally. Keys are never logged, cached or
committed; reports name key aliases only.

## Packaging

```bash
zip -r code.zip README.md code -x "*/__pycache__/*" "*/.venv/*" "*/venv/*" "*/node_modules/*" "*/.git/*" "*/.DS_Store"
```

Challenge and submission page: https://wwww.hackerrank.com/contests/hackerrank-orchestrate-october26/challenges/ghost-data-still-exists
