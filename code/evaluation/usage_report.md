# Usage report

Model usage behind the final full-dataset run that produced `output.csv`, built by `code/evaluation/main.py`
from the cache entries that run read (listed in its ledger, `code/evaluation/usage_ledger.json`) plus its live
calls. Every cache entry stores the provider's usage metadata (`usage_metadata`: provider, model, served model
version, key alias, input/output/thinking tokens, timestamp) of the call that produced it. A batched intent call
produced up to 40 entries; it is counted once (by call_id).

- Requests in the eval run: 518 (the pipeline also classifies the 32 sample requests in the same batches)
- Cache entries read by this run: 409; live calls in this run: 0
- Distinct provider calls behind the run: 25 (25 replayed from cache, 0 live)

## Per model

| Provider | Model | Task | Calls | Cache entries used | Input tokens | Output tokens | Thinking tokens | Total tokens | Est. paid-list cost (USD) |
|---|---|---|---|---|---|---|---|---|---|
| gemini | gemini-2.5-flash | intent | 8 | 160 | 15974 | 6177 | 0 | 22151 | 0.0202 |
| gemini | gemini-2.5-flash | vision | 10 | 10 | 12424 | 9788 | 0 | 22212 | 0.0282 |
| gemini | gemini-3.5-flash | vision | 1 | 1 | 2176 | 281 | 3502 | 5959 | UNKNOWN (no published price) |
| gemini | gemini-3.5-flash-lite | intent | 6 | 238 | 20048 | 9873 | 0 | 29921 | 0.0307 |
| **all** | | | 25 | 409 | 50622 | 26119 | 3502 | 80243 | 0.0791 + UNKNOWN |
| local OCR | tesseract 5.5.3.20260724 | screenshot id grounding | 11 images x 4 passes | | 0 | 0 | 0 | 0 | 0.0000 (local, no API) |

## Per key alias

Aliases only (`primary`, `fallback_1` …); key values never appear in any file. Keys of one Google Cloud project
share a quota pool and the API does not reveal the project, so pools are UNKNOWN.

| Provider / alias | Calls | Live | From cache | Total tokens |
|---|---|---|---|---|
| gemini/primary | 25 | 0 | 25 | 80243 |

## Totals and cost

- Total tokens: 80243; average per eval request: 154.9 (input 97.7, output 50.4); calls per request 0.048
- Actual cost: $0. Every call used the Gemini API free tier; the OCR engine runs locally.
- Estimated cost at paid list price: $0.0791 total, $0.000153 per request (excluding gemini-3.5-flash: no published price, UNKNOWN).
- Price source: Gemini Developer API pricing page, paid tier (https://ai.google.dev/gemini-api/docs/pricing; page last updated 2026-10-22 UTC, retrieved 2026-10-24). Rates are copied into code/config/models.yaml.
- Fallback / quota / validation events in this run: none
- Call timestamps: {'UNKNOWN': 25}. Calls made before C8 did not store their time; their entries carry
  `timestamp: UNKNOWN` and `timestamp_upper_bound` (the commit that first added the entry). Calls made after C8
  store the provider-call time.

## Disclosures

- **Lost early ledger.** The first live run (2026-10-24) failed 12 intent batches (`gemini-2.5-flash-lite` HTTP 404 on
  every key, `gemini-2.5-flash` 20 requests/day cap) and two vision replies failed the output validator and were
  retried. Those failed or rejected calls produced no cache entry, and their ledger was overwritten by the rerun, so
  their tokens are not in this report (UNKNOWN; at most 12 intent and 2 vision calls). Intent was switched to
  `gemini-3.5-flash-lite` (DESIGN.md D-MODEL-IDS).
- The provider may serve a different version than the requested id: the served `model_version` is kept in every
  entry (for example, requests to `gemini-3.5-flash` reported `gemini-3.6-flash`).
- The ledger of this run lists cache entries, not new calls: a `--from-cache` run makes no network call.

## Experiment calls (not part of the dataset run)

Model-reply cache entries the final run did not read, one row per cache folder and model, counted once per call:
custom and adversarial fixtures, the C4 intent-model comparison, the E2 investigator turns and screenshot
re-extractions. Groq was never called (no GROQ_API_KEY).

| Cache folder / model | Calls | Total tokens |
|---|---|---|
| intent/gemini-3.5-flash | 2 | 12372 |
| intent/gemini-3.5-flash-lite | 11 | 13301 |
| investigator/gemini-3.5-flash-lite | 28 | 41780 |
| vision/gemini-2.5-flash | 1 | 2216 |
