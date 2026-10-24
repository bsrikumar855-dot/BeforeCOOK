# Usage report

Summarises the model usage behind the final full-dataset run that produced `output.csv`. Tokens come from the
provider's `usageMetadata` recorded on every call (live calls of this run plus the calls that produced the cache
entries this run reused). Provider: Google Gemini API, free tier (actual cost $0).

- Requests in the eval run: 518
- Live model calls in this run: 0; cache hits in this run: 0

| Provider | Model | Calls | Cache hits (this run) | Input tokens | Output tokens | Thinking tokens | Total tokens | Est. paid-list cost (USD) |
|---|---|---|---|---|---|---|---|---|
| **all** | | 0 | 0 | 0 | 0 | 0 | 0 | 0.0000 |

- Average tokens per eval request: 0.0
- Actual cost: $0 (free tier). Estimated cost at paid list price: $0.0000 total, $0.000000 per request.
- Price source: Gemini Developer API pricing page, paid tier (https://ai.google.dev/gemini-api/docs/pricing; page last updated 2026-10-22 UTC, retrieved 2026-10-24). Rates are copied into code/config/models.yaml.

- Fallback / quota / validation events: Counter()
- Model ids verified against the provider list: see run log
