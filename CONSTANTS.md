# CONSTANTS.md: every number, window, rule and enum the solution depends on

Provenance labels (only these; skill `orchestrate-interview/references/CONSTANTS-template.md`):
**MEASURED** (swept: the losing alternative and its number are on record), **SPEC** (fixed by problem_statement.md /
README / AGENTS.md), **STANDARD** (canonical value, not tuned), **BOUND** (an external limit: RFC, file format, provider
limit or price), **JUDGEMENT** (reasoned, not measured; the reasoning is given). No value below was "chosen after
evaluation" unless its row names the sweep.

Completeness: the main table explains every constant that can change a decision, a cost or a cache key. The generated
register at the end lists **every** numeric literal in `code/` (tests and 0/1/-1 excluded) and every key in
`code/config/*.yaml`; `code/tests/test_constants_coverage.py` fails if one is missing or if the enums below drift from
the code. Regenerate with `python code/analysis/constants_register.py`.

Label audit (G3, 2026-10-25): four labels were corrected. ±3 h window MEASURED → JUDGEMENT (C10, it was a user
decision); OCR underscore rendering MEASURED → JUDGEMENT (an observation of the raw OCR text, not a sweep); scope window
"SPEC-derived" → JUDGEMENT (not an allowed label; the lower bound is ours); free-tier quota and paid prices "PROVIDER"
→ BOUND.

## Main table

| Constant | Value | Where | Purpose | Label | Evidence / rejected alternative | What breaks if it changes |
|---|---|---|---|---|---|---|
| Deletion-burst window | ±3 h around `reported_at` | `code/agent/attribution.py:28` `BURST_HALF_WIDTH` | Timing edges: deletion-worker rows inside it become attribution *candidates* (never confirmations) | JUDGEMENT | User decision in Phase 1 (DESIGN.md D-ATTR). Not swept | Wider: more candidates, more AMBIGUOUS batches, fewer INFERRED picks. Narrower: real burst rows missed |
| Scope window | `[requested_at, audit_at]` | `code/agent/attribution.py:45-47` `in_lifecycle` | A hard-linked resource is in scope only through identity evidence inside the request's own lifecycle (or the case's burst) | JUDGEMENT | Keeps 84/84 expected hard/catalog-linked sample pairs and excludes the 3 from users' earlier requests. Rejected: `requested_at − 30 days` (worked, but 30 is an arbitrary tuned number) | Earlier requests of the same user leak into a later case |
| E1-B burst gap (rejected) | 25 min | `code/analysis/e1_order_inference.py:28` `BURST_GAP` | Segment interleaved bursts by time gaps | JUDGEMENT | First trough of the unlabelled gap histogram. Measured as a *strategy* (1 correct, 1 incorrect on 19 AMBIGUOUS sample pairs) and rejected; the value itself was not swept | Nothing shipped depends on it |
| Request-text cap | 5,000 characters | `code/agent/intent.py:16` `MAX_CLASSIFY_CHARS`; `code/agent/explain.py` (ad-hoc input limit) | Longer texts are classified on their first 5,000 characters; explain mode rejects longer input | JUDGEMENT | Longest dataset request is 252 characters; bounds prompt size and cost; fixture X18 (55k chars) passes (C7a). Rejected: treating long text as an input issue (stopped the whole run with exit 2) | Smaller: long genuine requests could be cut before the deletion sentence |
| Intent batch size | 40 texts per call | `code/config/models.yaml:71`; fallback 20 at `code/agent/intent.py:96` | Fewer calls within free-tier daily quotas | JUDGEMENT | Not swept. Every text keeps its own cache entry, so the batch size changes cost, not labels (C4: 42/42 agreement across two models) | Larger: longer prompts, a failed batch costs more texts; smaller: more calls against quota |
| Schema retries | 2 | `code/config/models.yaml:102` `limits.schema_retries` | Extra attempts on the same model when its JSON fails validation, with the validator's error fed back | JUDGEMENT | Not swept; two validator failures occurred in the first live run and were fixed by the retry | More: cost on a model that cannot comply; fewer: earlier fallback |
| 429 retries per attempt | 3 | `code/config/models.yaml:105` | Rate-limit (per-minute) retries before moving to the next (model, key) | JUDGEMENT | Not swept; a daily-quota 429 skips immediately (`_is_daily_quota`) | Longer stalls vs earlier fallback |
| Backoff | initial 4 s, ×2, cap 60 s | `code/config/models.yaml:103-104`; multiplier `code/agent/llm_client.py:255,259` | Exponential backoff on 429 / 5xx | STANDARD | Conventional exponential backoff with a cap; parameters not tuned | Only wall-clock time |
| Call timeout | 90 s (generation), 30 s (model listing) | `code/config/models.yaml:101`; `code/agent/llm_client.py:171` | Network timeouts | JUDGEMENT | Vision calls with thinking took tens of seconds; listing is small | A timeout counts as a failed attempt → fallback |
| Requests per minute | flash 8, flash_lite 12, flash_new 8, groq_oss 20 | `code/config/models.yaml:34,39,44,48` | Client-side throttle per (key, model) | JUDGEMENT | Kept under the provider's per-minute limits; the exact free-tier RPM per model was not re-verified (UNKNOWN), the comment's "10 RPM" for flash is from the provider page at build time | Higher: more 429s; lower: slower live runs |
| Investigator tool cap (E2, not shipped) | 8 calls per batch; 40 rows per tool result | `code/config/models.yaml:89`, `code/analysis/e2_investigator.py:91`; `code/analysis/e2_investigator_tools.py:18` | Bound the experiment's loop and tool output | JUDGEMENT | Not swept; E2 accepted 0/126 proposals for a structural reason (DESIGN.md D-E2), so the cap is not the limiting factor | Nothing shipped depends on it |
| Citation rule C | latest admissible file mentioning the resource, else the latest admissible full snapshot of its system | `code/agent/state.py:8,53` | Which evidence_id a step cites | MEASURED | 74/75 cited sample steps. Losing: rule A 71/75, A_email 44/75, A′ scope 36/75 and snapshot 12/75 | Citations stop matching the reference |
| Step sort rule | phase order, then operation-table order, then `system_id`, then `resource_id` (string, ascending) | `code/agent/steps.py:41` `sort_key`; table `code/agent/schema.py:28` | Order inside `steps_json` | SPEC | problem_statement.md / README ordering rule; the string (not numeric) order of `resource_id` is an **untested assumption** (no sample has two resources in one group; DESIGN.md D-SORT) | Sequence accuracy |
| Row sort | `case_id` by numeric suffix | `code/agent/validation.py:123` `case_sort_key` | Order of output rows | JUDGEMENT | Matches the order of deletion_requests.csv; the spec says "sorted by case_id" without defining the order (untested assumption, D-ROWSORT) | A strict string-sort grader would see a different order |
| Exposure rank | DIRECT_IDENTIFIERS 4 > STABLE_REFERENCE_ONLY 3 > REDACTED 2 > NO_IDENTIFIERS 1 | `code/agent/state.py:16` | When copies disagree, the most exposed copy decides | JUDGEMENT | Conservative: one exposed replica keeps the record non-compliant ("every relevant copy") | Exposed replicas could be reported compliant |
| OCR passes (C10) | grayscale, then base; 2x LANCZOS; 2x LANCZOS `--psm 11`; 3x LANCZOS + autocontrast `--psm 6`; texts united | `code/agent/parsers/ocr_grounding.py:29` `PASSES` | Ground screenshot ids in what is visibly on screen | JUDGEMENT | Supplied by an independent multi-pass re-check after C9's single pass missed 5 visible ids; with them 32/32 ids are grounded (was 27/32). Not swept further | Fewer passes: visible ids dropped as UNGROUNDED |
| OCR engine | Tesseract 5.5.3.20260724 via pytesseract 0.3.13 | `requirements.txt`; every entry in `code/cache/ocr/` | OCR | JUDGEMENT | Version recorded per cache entry; cache key = sha256(image + engine version + pass config) | A new engine version misses every OCR cache entry (live OCR needed) |
| OCR underscore rendering | an id's `_` matches `_`, space, `.` or nothing; letters and digits exact | `code/agent/parsers/ocr_grounding.py:31` `SEP` | Match ids as Tesseract renders them | JUDGEMENT | Observed in the raw OCR text: `export 1261`, `search 1874`, `data_flow.9846`, `data_flow1121` (C10). Rejected: fuzzy matching (edit distance, l/1, O/0) | Stricter: visible ids missed; looser: look-alike ids accepted |
| Hash-prefix lengths | prompt version 12 hex; text nonce 10 hex; call id 16 hex | `code/agent/llm_client.py:143,310`; `code/agent/intent.py:36`; `code/agent/parsers/png_vlm.py:83` | Content-addressed cache keys, prompt versions, unguessable fence tags | JUDGEMENT | 48–64 bits: collisions negligible at this scale | Changing the version length invalidates every cache entry |
| Prompt versions in use | intent `pfb7d2f0c21e6`; investigator `p15424f9426dd`; vision (rendered prompt) `p3a6e61c90ba3`, with 10 replies from `pb1da0fa903ab` reused through the legacy upgrade (C2) | sha256 of the prompt text, `code/agent/llm_client.py:143`; `code/agent/parsers/png_vlm.py:83` | Cache keys depend on the exact prompt | JUDGEMENT | Recorded on every cache entry; see G6 for the legacy entries | Editing a prompt misses its cache |
| Model ids | vision `gemini-2.5-flash` → `gemini-3.5-flash`; intent `gemini-3.5-flash-lite` → `gemini-3.5-flash`; Groq `openai/gpt-oss-120b` unverified | `code/config/models.yaml` `models.*.id` | Perception models | MEASURED (intent) / JUDGEMENT (vision) | Intent: 3.5-flash-lite 42/42 at 5,711 tokens vs 3.5-flash 42/42 at 12,372 (C4). Vision: 2.5-flash produced the committed extractions; 3.5-flash is the probed fallback | Different labels/extractions → different rows |
| Decoding temperature | 0 | `code/agent/llm_client.py:273,293` | Most deterministic decoding available; replay comes from the cache anyway | STANDARD | Conventional for extraction/classification | More variable live replies |
| thinking_budget | 0 for gemini-2.5-flash; not sent for 3.x | `code/config/models.yaml` `models.*.thinking_budget` | Gemini 3.x rejects the field (HTTP 400) | BOUND | Provider behaviour observed 2026-10-24 | HTTP 400 on every 3.x call |
| HTTP status handling | 200 ok; 429 retry or quota; 500/502/503/504/-1 backoff; 404 model unavailable for the run | `code/agent/llm_client.py:249-262` | Transport semantics | BOUND | RFC 9110 and the provider's error bodies | Wrong retry/fallback decisions |
| Exit codes | 0 ok, 2 invalid, 3 cache miss, 4 model failure / degraded | `code/main.py:32` | Contract with the caller; output.csv replaced only on 0 | JUDGEMENT | README table; tests in `code/tests/test_exit_codes.py` | Callers misread failures |
| Explain output bounds | 25 items per list, 8 observations per resource, 4 edges, 40,000 characters | `code/agent/explain.py:29-30` | Bounded interview output | JUDGEMENT | — | Only output length |
| Email shape (explain input) | local ≤ 64, domain ≤ 255, TLD 2–24 | `code/agent/explain.py:28` | Reject malformed ad-hoc input | BOUND | RFC 5321 §4.5.3.1 for 64/255; TLD bound is a judgement | Valid long TLDs rejected |
| Free-tier quota | 20 requests/day/project for gemini-2.5-flash | provider | Why intent moved off 2.5-flash | BOUND | HTTP 429 `GenerateRequestsPerDayPerProjectPerModel-FreeTier`, 2026-10-24 | — |
| Paid list prices | per model, USD per 1M tokens | `code/config/models.yaml` `paid_list_price_usd_per_million_tokens` | Cost estimate only (actual cost $0) | BOUND | Gemini pricing page, retrieved 2026-10-24; gemini-3.5-flash has no published price (UNKNOWN) | Only the estimate line |
| Coupling-probe constants | seed 20261025; renamed ids start at 7,000,000 | `code/evaluation/coupling_probes.py` | Reproducible perturbations | JUDGEMENT | 7-digit range cannot collide with dataset ids | Only the probe |
| NO_ATTRIBUTABLE_RESOURCE | INCOMPLETE `[]` allowed only with this trace reason code | `code/agent/pipeline.py:180`; `code/agent/validation.py:82` | M2(b): nothing attributable can be planned or verified | JUDGEMENT | Approved policy (C5); currently case_63 (eval) and case_04 (sample) | Validator would reject or accept the wrong empty rows |

## Spec enums (typed from problem_statement.md; the coverage test compares them with `code/agent/schema.py`)

- STATUSES: COMPLETE, INCOMPLETE, NOT_APPLICABLE
- PHASES: FINDING, REMEDIATION, VERIFICATION
- STEP_KEYS: step_no, phase, system_id, resource_id, operation, state, evidence_id
- OPERATIONS (phase, state, in table order):
  - FINDING: REPORT_REMAINING_RECORD=PRESENT, REPORT_EXPOSED_PII=PRESENT, REPORT_ACTIVE_DATA_FLOW=ACTIVE, REPORT_RESTORE_RISK=ACTIVE
  - REMEDIATION: DISABLE_DATA_FLOW=DISABLED, BLOCK_RESTORE=BLOCKED, DELETE_RECORD=ABSENT, REDACT_RECORD=REDACTED, ANONYMIZE_RECORD=ANONYMIZED
  - VERIFICATION: VERIFY_RECORD_ABSENT=ABSENT, VERIFY_RECORD_PRESENT=PRESENT, VERIFY_RECORD_REDACTED=REDACTED, VERIFY_RECORD_ANONYMIZED=ANONYMIZED, VERIFY_DATA_FLOW_DISABLED=DISABLED, VERIFY_RESTORE_BLOCKED=BLOCKED
- INTENT_LABELS (ours, JUDGEMENT): DELETION, NON_DELETION, UNCLEAR
- ATTRIBUTION_LABELS (ours, JUDGEMENT): CONFIRMED, INFERRED, AMBIGUOUS, CONFLICT

<!-- BEGIN GENERATED REGISTER -->

### Literal register (generated by `code/analysis/constants_register.py`)

| File | Values | Role | Label | What breaks if changed |
|---|---|---|---|---|
| `code/agent/attribution.py` | 4, 5, 7, 8 | regex capture-group index | STANDARD | the wrong capture is read |
| `code/agent/attribution.py` | 3 | ±3 h deletion-burst half-width (timedelta(hours=3)); also 3 identifiers shown in a rationale | JUDGEMENT | candidate sets grow or shrink; see main table |
| `code/agent/explain.py` | 40000 | character cap of --explain output | JUDGEMENT | only output length |
| `code/agent/explain.py` | 4 | edges shown per attribution in --explain | JUDGEMENT | only output length |
| `code/agent/explain.py` | 25 | items per list in --explain output | JUDGEMENT | only output length |
| `code/agent/explain.py` | 8 | observations shown per resource in --explain | JUDGEMENT | only output length |
| `code/agent/explain.py` | 80, 200 | slice: length cap on diagnostic text, or a fixed prefix/suffix strip | JUDGEMENT | only how much diagnostic text is kept (caps), or the strip of a fixed marker such as 'log:' or '~>' |
| `code/agent/intent.py` | 20 | fallback batch size if models.yaml omits tasks.intent.batch_size (yaml sets 40) | JUDGEMENT | only when the yaml key is missing |
| `code/agent/intent.py` | 10 | hash-prefix length (cache keys, prompt versions, nonces, call ids) | JUDGEMENT | existing cache keys / prompt versions no longer match: every cached reply misses |
| `code/agent/intent.py` | 5000 | request-text classification cap (C7a) | JUDGEMENT | see main table |
| `code/agent/intent.py` | 300 | slice: length cap on diagnostic text, or a fixed prefix/suffix strip | JUDGEMENT | only how much diagnostic text is kept (caps), or the strip of a fixed marker such as 'log:' or '~>' |
| `code/agent/llm_client.py` | 200, 404, 429, 500, 502, 503, 504 | HTTP status code | BOUND | RFC 9110 / provider API semantics: retry, quota and 404 handling |
| `code/agent/llm_client.py` | 2 | exponential backoff multiplier (delay *= 2) | STANDARD | retry spacing |
| `code/agent/llm_client.py` | 10 | fallback requests_per_minute if a model omits it | JUDGEMENT | only when the yaml key is missing |
| `code/agent/llm_client.py` | 12 | hash-prefix length (cache keys, prompt versions, nonces, call ids) | JUDGEMENT | existing cache keys / prompt versions no longer match: every cached reply misses |
| `code/agent/llm_client.py` | 30 | network timeout in seconds (model listing / key probe) | JUDGEMENT | slow providers time out sooner or later; no decision changes |
| `code/agent/llm_client.py` | 60.0 | seconds per minute in the RPM throttle | STANDARD | throttle interval wrong |
| `code/agent/llm_client.py` | 16, 200, 300 | slice: length cap on diagnostic text, or a fixed prefix/suffix strip | JUDGEMENT | only how much diagnostic text is kept (caps), or the strip of a fixed marker such as 'log:' or '~>' |
| `code/agent/loaders.py` | 2 | repository-root depth of this file | STANDARD | paths resolve outside the repo |
| `code/agent/parsers/__init__.py` | 2, 5, 6 | regex capture-group index | STANDARD | the wrong capture is read |
| `code/agent/parsers/ocr_grounding.py` | 2 | OCR pass upscale factor 2x | JUDGEMENT | see main table (OCR passes) |
| `code/agent/parsers/ocr_grounding.py` | 3 | OCR pass upscale factor 3x | JUDGEMENT | see main table (OCR passes) |
| `code/agent/parsers/pdf_text.py` | 16 | hexadecimal base | STANDARD | CMap / hex decoding breaks |
| `code/agent/parsers/pdf_text.py` | 2 | regex group / '~>' strip in the PDF decoder | STANDARD | PDF text decoding |
| `code/agent/parsers/pdf_text.py` | 3 | regex group in the PDF decoder | STANDARD | PDF text decoding |
| `code/agent/parsers/png_vlm.py` | 12 | hash-prefix length (cache keys, prompt versions, nonces, call ids) | JUDGEMENT | existing cache keys / prompt versions no longer match: every cached reply misses |
| `code/agent/parsers/png_vlm.py` | 40, 120, 160, 300 | slice: length cap on diagnostic text, or a fixed prefix/suffix strip | JUDGEMENT | only how much diagnostic text is kept (caps), or the strip of a fixed marker such as 'log:' or '~>' |
| `code/agent/parsers/record_semantics.py` | 4 | slice: length cap on diagnostic text, or a fixed prefix/suffix strip | JUDGEMENT | only how much diagnostic text is kept (caps), or the strip of a fixed marker such as 'log:' or '~>' |
| `code/agent/pipeline.py` | 3 | traceback lines kept in a degraded trace | JUDGEMENT | only diagnostic text |
| `code/agent/schema.py` | 4 | index where REMEDIATION operations start in the spec operation table | SPEC | phase of every operation; validator rejects every row |
| `code/agent/schema.py` | 9 | index where VERIFICATION operations start in the spec operation table | SPEC | phase of every operation; validator rejects every row |
| `code/agent/state.py` | 4 | exposure rank DIRECT_IDENTIFIERS (most exposed wins across copies) | JUDGEMENT | which copy's exposure decides |
| `code/agent/state.py` | 2 | exposure rank REDACTED | JUDGEMENT | which copy's exposure decides |
| `code/agent/state.py` | 3 | exposure rank STABLE_REFERENCE_ONLY | JUDGEMENT | which copy's exposure decides |
| `code/analysis/backfill_usage_metadata.py` | 2 | repository-root depth of this file | STANDARD | paths resolve outside the repo |
| `code/analysis/category_coverage.py` | 2 | repository-root depth of this file | STANDARD | paths resolve outside the repo |
| `code/analysis/compare_intent.py` | 2 | a sentence used <= 2 times marks a free-text (non-template) request | JUDGEMENT | which texts the C4 comparison treats as free text |
| `code/analysis/compare_intent.py` | 90 | slice: length cap on diagnostic text, or a fixed prefix/suffix strip | JUDGEMENT | only how much diagnostic text is kept (caps), or the strip of a fixed marker such as 'log:' or '~>' |
| `code/analysis/constants_register.py` | 2, 3, 4, 5, 6, 7, 8, 9, 10, 20, 24, 25, 30, 40, 60.0, 64, 100, 200, 255, 400, 401, 403, 404, 429, 500, 502, 503, 504, 5000, 40000, 7000000, 20261025 | this register's own classification tables (values classified elsewhere) | JUDGEMENT | only the register |
| `code/analysis/e1_order_inference.py` | 25 | E1-B burst gap in minutes (first trough of the unlabelled gap histogram) | JUDGEMENT | rejected experiment only; see main table |
| `code/analysis/e1_order_inference.py` | 10 | slice: length cap on diagnostic text, or a fixed prefix/suffix strip | JUDGEMENT | only how much diagnostic text is kept (caps), or the strip of a fixed marker such as 'log:' or '~>' |
| `code/analysis/e1_order_inference.py` | 60 | unit conversion (minutes, hours, days, millions, percent) | STANDARD | wrong units in printed numbers |
| `code/analysis/e2_investigator.py` | 8 | fallback investigator tool-call cap (yaml max_tool_calls = 8) | JUDGEMENT | rejected experiment only |
| `code/analysis/e2_investigator.py` | 10 | hash-prefix length (cache keys, prompt versions, nonces, call ids) | JUDGEMENT | existing cache keys / prompt versions no longer match: every cached reply misses |
| `code/analysis/e2_investigator.py` | 300, 6000 | slice: length cap on diagnostic text, or a fixed prefix/suffix strip | JUDGEMENT | only how much diagnostic text is kept (caps), or the strip of a fixed marker such as 'log:' or '~>' |
| `code/analysis/e2_investigator.py` | 2 | strip of the 'c:' node prefix | JUDGEMENT | rejected experiment only |
| `code/analysis/e2_investigator_tools.py` | 40 | rows returned per investigator tool call | JUDGEMENT | rejected experiment only |
| `code/analysis/measure_ambiguity.py` | 70 | slice: length cap on diagnostic text, or a fixed prefix/suffix strip | JUDGEMENT | only how much diagnostic text is kept (caps), or the strip of a fixed marker such as 'log:' or '~>' |
| `code/analysis/probe_keys.py` | 400, 401, 403, 404, 429 | HTTP status code | BOUND | RFC 9110 / provider API semantics: retry, quota and 404 handling |
| `code/analysis/probe_keys.py` | 8 | PNG bit depth of the probe image | BOUND | PNG specification |
| `code/analysis/probe_keys.py` | 4294967295 | PNG chunk layout of the synthetic probe image | BOUND | PNG specification (ISO/IEC 15948); an invalid probe image |
| `code/analysis/probe_keys.py` | 2 | PNG colour type 2 (RGB) of the probe image | BOUND | PNG specification |
| `code/analysis/probe_keys.py` | 30, 45 | network timeout in seconds (model listing / key probe) | JUDGEMENT | slow providers time out sooner or later; no decision changes |
| `code/analysis/probe_keys.py` | 4 | synthetic 4x4 probe image size | JUDGEMENT | key probe only |
| `code/analysis/profile_dataset.py` | 2 | forensics: minimum group size / histogram bucket / '~>' strip | JUDGEMENT | forensic printout only |
| `code/analysis/profile_dataset.py` | 40 | forensics: rows shown | JUDGEMENT | forensic printout only |
| `code/analysis/profile_dataset.py` | 4, 5, 6, 8 | forensics: section number | JUDGEMENT | forensic printout only |
| `code/analysis/profile_dataset.py` | 7 | forensics: section number / candidate-count bucket 7+ | JUDGEMENT | forensic printout only |
| `code/analysis/profile_dataset.py` | 100 | forensics: separator width | JUDGEMENT | forensic printout only |
| `code/analysis/profile_dataset.py` | 3 | forensics: ±3 h window and minimum name-token length | JUDGEMENT | forensic printout only |
| `code/analysis/profile_dataset.py` | 16 | hexadecimal base | STANDARD | CMap / hex decoding breaks |
| `code/analysis/profile_dataset.py` | 10 | regex capture-group index | STANDARD | the wrong capture is read |
| `code/analysis/profile_dataset.py` | 12, 30, 140 | slice: length cap on diagnostic text, or a fixed prefix/suffix strip | JUDGEMENT | only how much diagnostic text is kept (caps), or the strip of a fixed marker such as 'log:' or '~>' |
| `code/analysis/profile_dataset.py` | 60, 3600, 86400 | unit conversion (minutes, hours, days, millions, percent) | STANDARD | wrong units in printed numbers |
| `code/evaluation/coupling_probes.py` | 30 | changed cases listed in the probe report | JUDGEMENT | report length |
| `code/evaluation/coupling_probes.py` | 60 | characters of a rationale kept as a rule kind | JUDGEMENT | rule grouping |
| `code/evaluation/coupling_probes.py` | 10 | examples listed in the probe report | JUDGEMENT | report length |
| `code/evaluation/coupling_probes.py` | 7000000 | first number of the renamed id range (cannot collide with dataset ids) | JUDGEMENT | collisions with original ids |
| `code/evaluation/coupling_probes.py` | 2 | regex group / argv offset in the probe harness | STANDARD | probe harness |
| `code/evaluation/coupling_probes.py` | 20261025 | seed of the row-shuffle probe | JUDGEMENT | which permutation is tested |
| `code/evaluation/coupling_probes.py` | 400, 900 | slice: length cap on diagnostic text, or a fixed prefix/suffix strip | JUDGEMENT | only how much diagnostic text is kept (caps), or the strip of a fixed marker such as 'log:' or '~>' |
| `code/evaluation/custom_cases/build_fixtures.py` | 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 32, 700 | fictional fixture data (case numbers, step numbers, hash filler, padding repeat) | JUDGEMENT | only that fixture; expected outputs are written next to it |
| `code/evaluation/main.py` | 2 | index of the 'correct' flag in an attribution tuple | JUDGEMENT | report arithmetic |
| `code/evaluation/main.py` | 10, 20, 120, 160, 200, 300 | slice: length cap on diagnostic text, or a fixed prefix/suffix strip | JUDGEMENT | only how much diagnostic text is kept (caps), or the strip of a fixed marker such as 'log:' or '~>' |
| `code/evaluation/main.py` | 100.0, 1000000.0 | unit conversion (minutes, hours, days, millions, percent) | STANDARD | wrong units in printed numbers |
| `code/main.py` | 3 | exit code: cache miss under --from-cache | JUDGEMENT | callers' exit-code handling (README table) |
| `code/main.py` | 4 | exit code: model failure / degraded run | JUDGEMENT | callers' exit-code handling (README table) |
| `code/main.py` | 2 | exit code: output validation failed / invalid explain input | JUDGEMENT | callers' exit-code handling (README table) |

### Config keys (generated from `code/config/*.yaml`; list items collapsed to `[]`)

| Key | Values |
|---|---|
| `models.yaml:providers.gemini.endpoint` | https://generativelanguage.googleapis.com/v1beta |
| `models.yaml:providers.gemini.key_slots.primary` | GEMINI_API_KEY |
| `models.yaml:providers.gemini.key_slots.fallback_1` | GEMINI_API_KEY_FALLBACK_1 |
| `models.yaml:providers.gemini.key_slots.fallback_2` | GEMINI_API_KEY_FALLBACK_2 |
| `models.yaml:providers.gemini.key_slots.fallback_3` | GEMINI_API_KEY_FALLBACK_3 |
| `models.yaml:providers.gemini.key_slots.fallback_4` | GEMINI_API_KEY_FALLBACK_4 |
| `models.yaml:providers.gemini.key_slots.fallback_5` | GEMINI_API_KEY_FALLBACK_5 |
| `models.yaml:providers.gemini.key_slots.fallback_6` | GEMINI_API_KEY_FALLBACK_6 |
| `models.yaml:providers.groq.endpoint` | https://api.groq.com/openai/v1 |
| `models.yaml:providers.groq.key_slots.primary` | GROQ_API_KEY |
| `models.yaml:models.flash.provider` | gemini |
| `models.yaml:models.flash.id` | gemini-2.5-flash |
| `models.yaml:models.flash.requests_per_minute` | 8 |
| `models.yaml:models.flash.thinking_budget` | 0 |
| `models.yaml:models.flash_lite.provider` | gemini |
| `models.yaml:models.flash_lite.id` | gemini-3.5-flash-lite |
| `models.yaml:models.flash_lite.requests_per_minute` | 12 |
| `models.yaml:models.flash_lite.thinking_budget` | None |
| `models.yaml:models.groq_oss.provider` | groq |
| `models.yaml:models.groq_oss.id` | openai/gpt-oss-120b |
| `models.yaml:models.groq_oss.requests_per_minute` | 20 |
| `models.yaml:models.flash_new.provider` | gemini |
| `models.yaml:models.flash_new.id` | gemini-3.5-flash |
| `models.yaml:models.flash_new.requests_per_minute` | 8 |
| `models.yaml:models.flash_new.thinking_budget` | None |
| `models.yaml:tasks.vision.prompt_file` | image_extraction.md |
| `models.yaml:tasks.vision.attempts[].model` | flash, flash_new |
| `models.yaml:tasks.vision.attempts[].key_slot` | primary, fallback_1, fallback_2, fallback_3 … (7 values) |
| `models.yaml:tasks.intent.prompt_file` | intent.md |
| `models.yaml:tasks.intent.batch_size` | 40 |
| `models.yaml:tasks.intent.attempts[].model` | flash_lite, flash_new |
| `models.yaml:tasks.intent.attempts[].key_slot` | primary, fallback_1, fallback_2, fallback_3 … (7 values) |
| `models.yaml:tasks.investigator.prompt_file` | investigator.md |
| `models.yaml:tasks.investigator.max_tool_calls` | 8 |
| `models.yaml:tasks.investigator.attempts[].model` | groq_oss, flash_lite |
| `models.yaml:tasks.investigator.attempts[].key_slot` | primary, fallback_1, fallback_2, fallback_3 … (7 values) |
| `models.yaml:limits.timeout_seconds` | 90 |
| `models.yaml:limits.schema_retries` | 2 |
| `models.yaml:limits.backoff_initial_seconds` | 4 |
| `models.yaml:limits.backoff_max_seconds` | 60 |
| `models.yaml:limits.max_429_retries_per_attempt` | 3 |
| `models.yaml:paid_list_price_usd_per_million_tokens.gemini-2.5-flash.input` | 0.3 |
| `models.yaml:paid_list_price_usd_per_million_tokens.gemini-2.5-flash.output` | 2.5 |
| `models.yaml:paid_list_price_usd_per_million_tokens.gemini-2.5-flash-lite.input` | 0.1 |
| `models.yaml:paid_list_price_usd_per_million_tokens.gemini-2.5-flash-lite.output` | 0.4 |
| `models.yaml:paid_list_price_usd_per_million_tokens.gemini-3.8-flash.input` | 0.75 |
| `models.yaml:paid_list_price_usd_per_million_tokens.gemini-3.8-flash.output` | 3.75 |
| `models.yaml:paid_list_price_usd_per_million_tokens.gemini-3.6-flash.input` | 0.75 |
| `models.yaml:paid_list_price_usd_per_million_tokens.gemini-3.6-flash.output` | 3.75 |
| `models.yaml:paid_list_price_usd_per_million_tokens.gemini-3.5-flash-lite.input` | 0.3 |
| `models.yaml:paid_list_price_usd_per_million_tokens.gemini-3.5-flash-lite.output` | 2.5 |
| `models.yaml:paid_list_price_usd_per_million_tokens.gemini-3.1-flash-lite.input` | 0.25 |
| `models.yaml:paid_list_price_usd_per_million_tokens.gemini-3.1-flash-lite.output` | 1.5 |
| `models.yaml:paid_list_price_usd_per_million_tokens.gemini-3-flash-preview.input` | 0.5 |
| `models.yaml:paid_list_price_usd_per_million_tokens.gemini-3-flash-preview.output` | 3.0 |

<!-- END GENERATED REGISTER -->
