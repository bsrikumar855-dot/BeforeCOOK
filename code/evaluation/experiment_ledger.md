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
