# INTERVIEW_FACTS.md — verified facts for the interview (not scripted answers; not shipped in code.zip)

Every value below was checked on 2026-10-25 against the file or commit named next to it. Labels: **MEASURED** (computed
by a script in this repo), **JUDGEMENT** (a reasoned choice), **UNKNOWN** (not verifiable from the repo; the needed
source is named). Corrections to figures quoted in the final brief are marked **[corrected]**.

## Fact table

| Fact | Value | Label | Source |
|---|---|---|---|
| Citation rule C vs alternatives (the 75 cited steps in `dataset/sample_output.csv`; match = our cited evidence_id equals the reference) | C 74/75; A (latest file mentioning the id, PDF text layer read) 71/75; A_raw (raw bytes only) 70/75; A_ids 71/75; A_email 44/75; A′_scope 36/75; A′_full 12/75 | MEASURED | DESIGN.md §5.6; commit 807cc74 era |
| The brief's "A 70" | is A_raw (70/75); A with the PDF text layer is 71/75 **[corrected]** | MEASURED | DESIGN.md §5.6 |
| Attribution, sample labelled pairs (pairs in the reference answers whose label is CONFIRMED/INFERRED* and in scope) | baseline 89/111; now 96/111 | MEASURED | DESIGN.md (Phase 1 gate); `code/evaluation/evaluation_report.md` §2 |
| A1 claim consistency on known-good links | 252/252 hard-linked account deletes agree (COMPLETE→SUCCESS 133, NOT_STARTED→FAILED 50, PARTIALLY_COMPLETE→PARTIAL 69) | MEASURED | `code/evaluation/attribution_variants.json` (a1_falsification); evaluation_report.md §4e |
| A1 effect on samples | 7/19 timing-only pairs resolved, 7 correct, 0 incorrect, 0 broken; recall 171→178/193; sequence 16→19/32 | MEASURED | evaluation_report.md §4e; commit c32b830 |
| A2 rejected | PARTIAL account band [−86.3, −39.9] min removed case_02's correct account_002, whose delete (activity_3376, 07:30:03Z) is +5 min after reported_at 07:25:00Z; 1 step + 1 status broken; 0 extra resolutions | MEASURED | evaluation_report.md §4e; DESIGN.md R5-A2 |
| Ordering tiebreak rejected | case_23 → account_9155, reference account is account_410 (1 incorrect; +2 correct) | MEASURED | evaluation_report.md §4e; DESIGN.md rejection log |
| E1-A / E1-B (19 timing-only AMBIGUOUS sample pairs) | A: 4 correct, 1 incorrect (account_4990 → case_54; owner case_22), 14 AMBIGUOUS. B: 1 correct, 1 incorrect (analytics_5335 → case_547; owner case_21), 17 AMBIGUOUS | MEASURED | DESIGN.md rejection log |
| E2 investigator | 11 sample batches, 28 calls, 41,780 tokens; 126 proposals, all AMBIGUOUS, 0 accepted (126 = proposals, not pairs or cases) | MEASURED | DESIGN.md rejection log, D-E2 |
| E2 "ceiling 0" | an argument, not a measurement: the verifier accepts only identity-link support, and any such link is already a hard link | JUDGEMENT | DESIGN.md D-E2 |
| M1 | 904 AMBIGUOUS eval pairs in 347 cases on the current attribution (1,247 in 381 before A1; the committed measurement was stale until rc7 re-ran it); 0 withheld resources proven PRESENT; 0 COMPLETE→INCOMPLETE flips | MEASURED | `code/evaluation/measurements.json` (re-run rc7) |
| C0 incident | first live run: 358 of 550 cases silently fell back to NOT_APPLICABLE (gemini-2.5-flash-lite 404 + 2.5-flash daily cap); C0 made any model failure exit 4 **[corrected: 358, not 342]** | MEASURED | DESIGN.md C0 row; evaluation_report.md §6 |
| C7a | not a crash: a request_text over 5,000 chars was an input issue → INCOMPLETE [] → the self-contradiction gate stopped the whole run (exit 2); fixed by classifying the first 5,000 chars **[corrected]** | MEASURED | DESIGN.md C7/C7a; fixture X18 |
| C9 → C10 | single-pass OCR dropped 5 visible ids (audit_record_002, search_1874, data_flow_9846, export_1258, export_1261); causes: underscore read as space/period/nothing, small text at base resolution; fix: 4-pass OCR + underscore-tolerant exact matching; 32/32 ids grounded | MEASURED | DESIGN.md C10; commit e140ebf |
| Seven quadratic regexes | EMAIL_RE, log KV_RE, two screenshot id token regexes, three lazy PDF block regexes (> 30 s on 1 MB); fixed with RFC 5321 bounds, word-start lookbehinds, bounded id prefixes, a linear block scan; worst now ≈ 0.5 s | MEASURED | `code/evaluation/security_audit.json`; commit 5a1c354 |
| Coupling probes | 5/5 pass; 19,760/19,760 id tokens renamed (10,558 ids, 19 prefixes) | MEASURED | `code/evaluation/coupling_probes.json` |
| Spec-audit negative control | 18/18 injected defect classes caught (rc5 added `case_id_sort`); 0 violations | MEASURED | `code/evaluation/spec_audit.json` |
| Independent re-derivation | status 23/24; 101/103 of our steps matched (101/115 of its steps); triage 0 OUR_BUG, 3 SUBAGENT_ERROR, 8 AMBIGUOUS_SPEC | MEASURED | `code/evaluation/independent_rederivation/{comparison,triage}.json`; commit 14d13b2 |
| Intent model choice | 42/42 for both models (32 samples + 10 non-deletion texts); flash-lite 5,711 tokens vs flash 12,372 (46.2%) | MEASURED | `code/evaluation/intent_model_comparison.json`; commit e05bc17 |
| Legacy prompt v1 | 10 screenshot replies came from the af50210 prompt; shipped byte-exact (`code/prompts/image_extraction_v1.md`, rendered hash pb1da0fa903ab); re-extraction under the current prompt changed 2 eval rows (case_61 → INCOMPLETE [], exit 2) and was rejected | MEASURED | DESIGN.md R6-R5, G6; commit 2c148fe |
| F1 | 1 eligible record (evidence_42 tombstone_17); 1 eval row changed: case_46 + VERIFY_RECORD_REDACTED evidence_42 (status COMPLETE unchanged); samples unchanged | MEASURED | DESIGN.md F1; commit 8d0c804 |
| NOT_DERIVABLE | account_004, audit_record_6645, audit_record_5569 appear in no parsed source | MEASURED | evaluation_report.md §1 residual table |
| Privacy (free tier) | README states free-tier Gemini use may let the provider use prompts; only request texts and the 11 screenshots are sent; data is synthetic. The provider's current terms were not re-read | MEASURED (repo facts) / UNKNOWN (provider policy: needs the provider's terms page) | README.md "Privacy note" |
| Usage behind output.csv | 25 distinct provider calls behind the cached run, 80,243 tokens, 154.9 tokens per eval request; actual cost $0; scope = cache entries the final run read (early failed calls UNKNOWN, ≤ 12 intent + 2 vision) | MEASURED | `code/evaluation/usage_report.md` |
| Eval-set correctness | no labels exist | UNKNOWN | needs the hidden reference |

| Row order (rc5 G1) | output = deletion_requests.csv order = numeric case_id suffix; string order differs (unpadded eval ids); grader collation | MEASURED / UNKNOWN | DESIGN.md D-ROWSORT; `spec_audit.py` `case_id_sort` |
| D-SCOPE exposure (rc5 G3) | 20 eval cases exclude records as OUT_OF_SCOPE; 16 are single-request subjects, never seen in the samples | MEASURED (counts) / UNKNOWN (correctness) | evaluation_report.md §7 D |
| D-SCOPE review (rc6-P1) | 16 single-request cases inspected (28 excluded records after the fix): 22 records / 14 cases linked only after audit_at (exclusion required by AGENTS.md §6.2); 6 records / 2 cases linked only before the request (JUDGEMENT); 1 defect fixed: case_34 support_9538 (evidence_04 in-window page) | MEASURED / JUDGEMENT | `code/evaluation/dscope_review.json`; DESIGN.md rc6-P1 |
| Identity evidence for AMBIGUOUS accounts (rc6-P2) | 159 pairs / 157 eval cases; 0 unused same-line co-occurrences; 11 file-level co-mentions are 404 profile lookups without identifiers | MEASURED | DESIGN.md rc6-P2 |
| Required check for undecidable state (rc7-X4) | 3 eval rows +VERIFY_RECORD_ABSENT "" (case_526, 528, 531); 0 samples; samples show the required check for unmentioned accounts in INCOMPLETE rows (cases 10, 23, 29, 30) | MEASURED | DESIGN.md rc7-X4 |
| Screenshot mention for citation (rc7-X1) | case_34 support_9538 evidence_01 → evidence_04; 1 mention added across 11 screenshots; 0 samples; reference choice UNKNOWN (no sample cites a PNG) | MEASURED / UNKNOWN | DESIGN.md rc7-X1 |
| Artifact determinism (rc7-D1) | attribution_variants.json byte-identical under 3 seeds (was seed-dependent); other artifact scripts already stable | MEASURED | DESIGN.md rc7-D1 |
| Core refactor (rc7-R1) | attribute() 247 lines → phases; 163/165 decision functions typed; outputs, 553 traces, 6 variants byte-identical | MEASURED | DESIGN.md rc7-R1 |

Count of UNKNOWN facts: 4 (provider privacy policy; eval-set correctness; grader row collation; the 6 pre-request D-SCOPE records). Ten adversarial questions with sources: `INTERVIEW_MOCK.md`.

## Nine topics

| Topic | Mechanism (file:function) | Key metric (denominator, source) | Rejected alternative (measured) | Limitation / untested |
|---|---|---|---|---|
| Architecture | deterministic core `code/agent/pipeline.py:run` → `audit_case`; perception only in `intent.py:IntentClassifier.classify` and `parsers/png_vlm.py:ImageExtractor.__call__` | Mermaid import graph verified against AST (`code/tests/test_architecture_diagram.py`) | E2 agent loop: 0/126 accepted | Two perception tasks only; no model in decisions by design |
| Deterministic vs LLM | model labels intent / reads screenshots; code decides status, steps, ids, citations (`rules.py:Policy.verdict`, `steps.py:build_steps`) | intent 42/42 on two models | keyword intent rules (fail on negation; never built) | intent label is the one injection surface (X14/X15 held) |
| Arbitration and rule order | `rules.py:Policy.category` (stated record_type → shape flags → worker hint → system default); `Policy.verdict`; `steps.py:sort_key` | spec audit 0 violations, 18/18 control (17/17 before rc5 added `case_id_sort`) | trusting the system name for category (email_opt_in → DELETE, X2) | resource_id string sort within a group untested on samples |
| End-to-end flow | `main.py:main` → `pipeline.run` (load, `parsers.build_evidence_index` with `file_checks.check`, intent, `attribution.attribute`, `state.reconstruct`, rules, steps, `validation.validate_row`/`validate_output_file`) | clean-folder run byte-identical | — | exit 4 on any degrade means no partial release |
| Evidence and citation | `state.py:reconstruct` (admissible = captured ≤ audit_at; latest per copy; absence by full snapshot) | rule C 74/75 | A 71/75, A′ 36/75 | 34 eval fallback citations rest on snapshots (R3 found 0 stronger) |
| Uncertainty | AMBIGUOUS/CONFLICT → no steps (`attribution.attribute`); C5 open-control rule; RETAIN_UNPROVEN; fail-closed on dropped evidence (`pipeline.audit_case`); F1 absence proof (`ocr_grounding.absence_proof`) | 131 eval cases still have an AMBIGUOUS account (evaluation_report.md §4e) | ordering/offset tiebreaks (1 incorrect each) | correct owners of AMBIGUOUS pairs unknown |
| AI-assisted workflow | change record + SHIP rule per change; tests gate commits; independent re-derivation agent | 129 tests (rc7); 0 OUR_BUG in 24 cases | — | the agent's own errors were caught only by measurement (C9, C7a, G8) |
| Trade-offs | recall vs wrong-owner risk (`attribution.forced_edges` uniqueness) | +A1 recall 178/193 at 0 incorrect | +A1+ordering 180/193 with 1 incorrect | precision kept at 178/178 by refusing ties |
| Limitations | episode scope `CaseContext.in_lifecycle`; C2 visibility tri-state | residual: 12 ATTRIBUTION + 3 NOT_DERIVABLE steps (evaluation_report.md §1) | treat every subject record as in scope (independent agent's reading) | eval correctness UNKNOWN |

## Commands (verified, exit 0)

```bash
python3 code/main.py --case case_25 --explain --from-cache
python3 code/main.py --case case_05 --explain --from-cache
python3 code/main.py --case case_46 --explain --from-cache
python3 code/main.py --explain --from-cache --email s.yoon04@gmail.com --audit-at 2026-09-15T12:00:00Z --requested-at 2026-08-31T12:00:00Z --reported-at 2026-09-02T12:00:00Z --request-text "Please permanently delete my account and the personal data linked to it."
```

case_05 = replica drift (account_005 present in `customer_profiles_failover_replica`, evidence_54); case_46 = F1.

## Ten hard follow-ups and where the answer lives

1. Why is a timing-only account sometimes assigned and sometimes not? → `attribution.forced_edges`, A1 table (evaluation_report.md §4e).
2. Why trust reported_status for linkage when the spec calls it a claim? → DESIGN.md R5-A1 (linkage only; 252/252 on hard links).
3. Why was A2 rejected when it resolved more eval pairs? → case_02 +5 min vs band (DESIGN.md R5-A2).
4. How do you know a COMPLETE row isn't missing a record? → you don't for AMBIGUOUS ones; RETAIN_UNPROVEN and fail-closed rules (DESIGN.md §8.1, D-RETAIN).
5. What exactly does 74/75 measure? → DESIGN.md §5.6 (cited sample steps whose evidence_id equals the reference).
6. Could a prompt injection change a status? → threat model DESIGN.md §7.1; fixtures X14–X19.
7. What happens if an evidence file is corrupt or missing? → `file_checks.check`, fail-closed, exit 4 (security_audit.json).
8. Why is case_63 INCOMPLETE with no steps? → C5/M2(b) NO_ATTRIBUTABLE_RESOURCE; R2 triage case_63.
9. Why is tombstone_17 now verified but was UNKNOWN before? → C2 vs F1 conditions (DESIGN.md F1; test_absence_proof.py).
10. How reproducible is the run? → content-addressed cache, 3-seed determinism, clean-folder test (clean_room.json), prompt provenance test.

## With more time

| Improvement | Expected metric effect | Label |
|---|---|---|
| An identity signal for the 5 AMBIGUOUS sample accounts (cases 10, 23, 28, 29, 30) and 131 eval cases | up to +5 sample steps and up to 5 sequence rows; eval effect unknown | ESTIMATED (upper bound) / UNKNOWN |
| Timing-only analytics attribution (7 residual sample steps) | up to +7 sample steps | ESTIMATED (upper bound; no evidenced method found) |
| Reference-validated episode scope for single-request subjects (R2 AMBIGUOUS_SPEC items) | unknown sign | UNKNOWN |
| Re-extract legacy screenshots with the model that produced them once quota allows | 0 expected row changes if faithful | UNKNOWN |
