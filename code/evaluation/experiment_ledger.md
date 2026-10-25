# Experiment ledger — round rc7 (branch `fix/rc7`, from rc6 `d87f1c4`)

One row per experiment in this round, kept or rejected. Earlier rounds are in DESIGN.md (decisions log, change
records rc5-*/rc6-*, rejection log). Cost is wall time on the cached, keyless pipeline; **no model call** was made in
this round. Labels: MEASURED (computed here), JUDGEMENT, UNKNOWN.

Baseline (rc6): samples status 31/32, precision 178/178, recall 178/193, exact sequence 19/32; eval 153 COMPLETE /
355 INCOMPLETE / 10 NOT_APPLICABLE; 123 tests.

| Id | Hypothesis | Evidence that motivated it | Change | Test / benchmark | Regressions, side effects | Cost | Decision |
|---|---|---|---|---|---|---|---|
| X0 | The anonymised analytics records (7 of the 12 ATTRIBUTION sample misses) carry an owner signal | evidence_92/94 rows show `audience_cohort` | none | 4 cohort values; the attribute appears only on analytics records, and no person-level source names a cohort | — | minutes | REJECTED before implementation: a coincidental attribute is not identity evidence |
| X2 | Some eval texts are mislabelled by intent | intent decides status for whole rows | none | 10 NOT_APPLICABLE texts all non-deletion; 62 non-English or verb-less texts all clear deletion requests (5 languages) | — | seconds | NO DEFECT |
| X3 | case_63 should be COMPLETE (sample case_04 is COMPLETE in a similar position) | case_04 reference | none | samples map NOT_STARTED → INCOMPLETE 7/10; case_63 reported NOT_STARTED; evidence_60 shows marketing_001 belongs to someone absent from the identity tables | — | minutes | KEEP C5 (INCOMPLETE []) |
| X4 | An attributed record whose state no evidence decides still needs its required check in an INCOMPLETE plan | INCOMPLETE sample rows carry every relevant record's final-state check, including 4 accounts no file mentions (cases 10, 23, 29, 30) | `rules.Policy._check_only` | eval: 3 rows +VERIFY_RECORD_ABSENT "" (case_526, 528, 531); samples 0 rows; statuses unchanged; counterfactual test fails on rc6 code | none measured; risk = a wrong A1 owner (single-case batches) | 1 h | KEEP |
| X1 | A screenshot mention of a known resource should count for citation rule C | rc6-P1 used the mention for scope only; extracted screenshot rows already count | `pipeline.add_ocr_mentions` | 1 mention added across 11 screenshots; case_34 support_9538 evidence_01 → evidence_04; samples 0 rows | which file the reference cites is UNKNOWN (no sample cites a PNG) | 30 min | KEEP (JUDGEMENT: uniform rule C) |
| X5 | String vs numeric `resource_id` order could break sequences | D-SORT is an untested assumption | none | 0 eval rows have two resources in one (phase, operation, system) group | — | seconds | NO EXPOSURE |
| A1 | Committed eval-side artifacts are current | the report quotes them | re-ran rule_branch_audit, citation_exposure, distribution_sanity, measure_ambiguity | M1 was stale: 904 pairs / 347 cases (committed 1,247 / 381 predated A1); still 0 proven present, 0 flips | interview facts corrected | minutes | KEEP |
| D1 | Evaluation artifacts are byte-deterministic | attribution_variants.json changed between runs | iterate case ids sorted; sorted counters | byte-identical under PYTHONHASHSEED 1/2/3; all other artifact scripts stable under 2 seeds | first test version shadowed code/main.py (7 failures), caught by the suite and fixed before commit | 30 min | KEEP |
| R1 | The 247-line `attribution.attribute` can be split with no behaviour change | readability of the core for a code reviewer | extract named phases; type annotations (163/165) | output.csv, 553 traces and all 6 attribution variants byte-identical; 129 tests | none | 1 h | KEEP |
| N1 | The report's section 6 is current | its closing line said "19 of 22 missing steps" (measured: 15) | closing line generated from section 1; later change records added | generated text matches section 1 | — | 15 min | KEEP |

Result: samples unchanged (31/32, 178/178, 178/193, 19/32 — none of this round's changes touches a sample row); eval
rows changed vs rc6: 4 (case_34 citation, case_526/528/531 +1 step each); 129 tests. Expected score effect: small and
UNKNOWN in sign for X1, positive in expectation for X4 (the added check is in the reference whether the account is
present or absent, if the A1 owner is right).

# Round rc8 (from rc7 `3fa3254`)

Audit against the October README and problem statement (both re-read from the official repository; the problem
statement is byte-identical to ours) and the published BTS rubric. No model call (no key is configured), no output
change.

| Id | Hypothesis | Evidence that motivated it | Change | Test / benchmark | Regressions, side effects | Cost | Decision |
|---|---|---|---|---|---|---|---|
| P1 | A capacity rule (a case owns at most one resource per system) forces some AMBIGUOUS timing rows | no sample reference has two resources of one system in one case (111 case-system pairs) | none; probe `code/analysis/capacity_probe.py` | 283 AMBIGUOUS resources: 0 forced among rows with two or more candidate cases. 1 more (`account_294`) only if every worker row must belong to one of its candidates, and that assumption fails on the samples: `account_4857`'s only AMBIGUOUS candidate is case_32, whose reference does not include it (case_31 owns it) | — | 30 min | REJECTED: no gain. This is the third attack on timing-only ownership (after E1/ordering and E2); the line is closed |
| P2 | The 3 NOT_DERIVABLE sample steps are recoverable from text the earlier search could not see | an earlier grep could not read compressed PDF text | none | the 4 PDF text layers, the 4-pass OCR text of the 11 screenshots and every other dataset file: `account_004`, `audit_record_6645` and `audit_record_5569` appear only in `sample_output.csv` | — | minutes | CONFIRMED not derivable |
| P3 | The samples can decide the 6 pre-request D-SCOPE exclusions | the exclusions are a JUDGEMENT | none | 30 of 32 sample cases are single-request subjects, and the scope rule excludes 0 records among them, so the samples cannot separate "request window" from "every linked record". case_45: including its 5 excluded audit records would give it 6 security_service resources, against 0/32 sample cases with two in one system, so the exclusion is evidence-supported. case_34 `audit_record_3753` (linked only by a log line 9 days before the request): undecided; all 24 sample audit records are linked inside their window | — | 30 min | KEEP (case_45 evidence-supported; case_34 JUDGEMENT) |
| P4 | Facts the interview needs are visible to the code judge | the October README: automated code evaluation ignores `.csv`, `.tsv`, `.json`, `.jsonl`, `.sql` and `.log` files | none | the results of 15 audits whose artifacts are JSON (coupling, cache, security, clean room, determinism, D-SCOPE review, M1 and others) are all quoted in shipped Markdown (evaluation_report.md, DESIGN.md or README.md) | — | 15 min | NO DEFECT |
| P5 | The shipped design doc states the current status | DESIGN.md line 3 read "No production code yet"; §12 said PNG content is UNKNOWN | DESIGN.md status line; §12 keeps the Phase 0 text and adds a **Now:** note per changed limit | docs only; `sync_docs.py` copy; tests | — | 15 min | KEEP |
| P6 | A trace label is consistent across cases | 26 resources owned by one case are labelled AMBIGUOUS for a rival case (e.g. `account_4857`: INFERRED_CLAIM for case_31, AMBIGUOUS for case_32) | none | 0 resources are owned by two cases; 37 of the 904 eval AMBIGUOUS pairs have a resource another case owns (867 unresolved); output unaffected, since AMBIGUOUS pairs emit no steps | relabelling would rewrite 553 traces for no output change | 15 min | DOCUMENTED, not changed |
| P7 | An agent loop in the decision path would improve the architecture criterion | BTS: "Is this an agent, or a hardcoded workflow with LLM calls?" | none | needs live model calls (no key is configured); E2 measured 0/126 accepted on the samples | — | — | NOT RUN (blocked; JUDGEMENT that the deterministic core fits the task stands) |

Result: output.csv byte-identical to rc7; samples unchanged (31/32, 178/178, 178/193, 19/32).
