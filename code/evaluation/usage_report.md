# Usage report

Summarises the model usage behind the final full-dataset run that produced `output.csv`. Tokens come from the
provider's `usageMetadata` recorded on every call (live calls of this run plus the calls that produced the cache
entries this run reused). Provider: Google Gemini API, free tier (actual cost $0).

- Requests in the eval run: 518
- Live model calls in this run: 0; cache hits in this run: 409

| Provider | Model | Calls | Cache hits (this run) | Input tokens | Output tokens | Thinking tokens | Total tokens | Est. paid-list cost (USD) |
|---|---|---|---|---|---|---|---|---|
| gemini | gemini-2.5-flash | 19 | 171 | 29634 | 16945 | 0 | 46579 | 0.0513 |
| gemini | gemini-3.5-flash-lite | 6 | 238 | 20048 | 9873 | 0 | 29921 | 0.0307 |
| **all** | | 25 | 409 | 49682 | 26818 | 0 | 76500 | 0.0819 |

- Average tokens per eval request: 147.7
- Actual cost: $0 (free tier). Estimated cost at paid list price: $0.0819 total, $0.000158 per request.
- Price source: Gemini Developer API pricing page, paid tier (https://ai.google.dev/gemini-api/docs/pricing; page last updated 2026-10-22 UTC, retrieved 2026-10-24). Rates are copied into code/config/models.yaml.

- Fallback / quota / validation events in this run: {}
- Model ids listed by the provider for each key (`python3 code/main.py --check-models`): {"primary": {"gemini-2.5-flash": true, "gemini-3.5-flash-lite": true, "gemini-3.8-flash": true}, "fallback_1": {"gemini-2.5-flash": true, "gemini-3.5-flash-lite": true, "gemini-3.8-flash": true}, "fallback_2": {"gemini-2.5-flash": true, "gemini-3.5-flash-lite": true, "gemini-3.8-flash": true}, "fallback_3": {"gemini-2.5-flash": true, "gemini-3.5-flash-lite": true, "gemini-3.8-flash": true}}

## Notes on the counts

- Listed is not the same as callable. Probes on 2026-10-24 returned HTTP 404 "no longer available to new users" for
  `gemini-2.5-flash-lite` on every key and for `gemini-2.5-flash` on one key, and HTTP 429
  `GenerateRequestsPerDayPerProjectPerModel-FreeTier` (20 requests/day per project) for `gemini-2.5-flash` on two
  keys. The first live run therefore failed 12 intent batches; intent was switched to `gemini-3.5-flash-lite`
  (DESIGN.md D-MODEL-IDS) and the run repeated, reusing every cached reply.
- The table counts the calls whose replies are in the cache and were used by this run. Two earlier vision calls
  were rejected by the output validator and retried; their token usage was recorded only in the first run's
  ledger, which the rerun overwrote, so they are not included (UNKNOWN, at most two calls).
- The intent replies also cover the 32 sample requests (the pipeline runs samples and eval jointly for global
  attribution); averages divide by the 518 eval requests.
- Calls made while evaluating the custom fixtures (X1–X5) are not part of the dataset run and are not counted.
