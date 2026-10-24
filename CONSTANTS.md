# CONSTANTS.md: every tuned or chosen number, with its provenance

Labels: **SPEC** (stated in problem_statement.md / AGENTS.md), **MEASURED** (chosen by a measurement recorded in the
repo), **JUDGEMENT** (a choice; the evidence that supports it is named), **PROVIDER** (a provider limit or price).

| Constant | Value | Where | Label | Evidence |
|---|---|---|---|---|
| OCR passes (C10) | grayscale, then: base; 2x LANCZOS (default psm); 2x LANCZOS `--psm 11`; 3x LANCZOS + autocontrast `--psm 6`; texts united | `code/agent/parsers/ocr_grounding.py` `PASSES` | JUDGEMENT | Single-pass C9 left 5 visible ids unmatched; an independent multi-pass re-check found audit_record_002, search_1874 and data_flow_9846 only at 2x upscale. With these passes and the underscore fix, 32/32 vision ids are grounded (was 27/32); OCR-only reads include false hits (`account_5`, `backup_92871`), so OCR never adds ids |
| OCR engine | Tesseract 5.5.3.20260724 via pytesseract 0.3.13 | `requirements.txt`, `code/cache/ocr/` | JUDGEMENT | Version recorded in every OCR cache entry; cache key = sha256(image bytes + engine version + pass config) |
| OCR underscore rendering | an id's `_` matches one of `_`, space, `.`, or nothing; letters and digits exact | `ocr_grounding.token_pattern` | MEASURED | Raw OCR text: `export 1261`, `search 1874`, `data_flow.9846`, `data_flow1121` for ids shown with underscores (C10) |
| Max characters classified | 5,000 | `code/agent/intent.py` `MAX_CLASSIFY_CHARS` | JUDGEMENT | Longest dataset request is 252 characters; the cap bounds prompt size and cost (C7a, fixture X18) |
| Intent batch size | 40 texts per call | `code/config/models.yaml` | JUDGEMENT | Keeps the run inside free-tier daily request quotas; every text still has its own cache entry (D-INTENT-BATCH) |
| Deletion-burst window | ±3 h around `reported_at` | `code/agent/attribution.py` `BURST_HALF_WIDTH` | JUDGEMENT | User decision in Phase 1 (DESIGN.md D-ATTR); timing edges only generate candidates, they never confirm an attribution |
| Scope window | `[requested_at, audit_at]` | `code/agent/attribution.py` | SPEC-derived | The request's own lifecycle; replaced a tuned 30-day margin (rejection log) |
| Free-tier quota | 20 requests/day/project for gemini-2.5-flash | provider | PROVIDER | HTTP 429 `GenerateRequestsPerDayPerProjectPerModel-FreeTier` (2026-10-24) |
| Paid list prices | per model, USD per 1M tokens | `code/config/models.yaml` | PROVIDER | Gemini pricing page, retrieved 2026-10-24 (usage_report.md) |
