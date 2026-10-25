# INTERVIEW_MOCK.md — ten adversarial questions and the facts that answer them

A preparation sheet, not a transcript: no mock interview was conducted and no interview score exists. Not shipped in
code.zip (`code/package.py` ships only README.md, requirements.txt, `code/`, `evaluation/`). Every figure was checked on
2026-10-25 against the file named next to it, on branch `fix/rc6`. Labels: **MEASURED** (computed by a script in this
repository), **JUDGEMENT** (a reasoned choice the spec does not settle), **UNKNOWN** (needs the hidden reference).
Companion fact table: `INTERVIEW_FACTS.md`.

---

## 1. Architecture: why does deterministic code make the final decisions?

- **Facts.** The model does two jobs: request intent (`code/agent/intent.py` `IntentClassifier.classify`, prompt
  `code/prompts/intent.md`) and reading the 11 screenshots (`code/agent/parsers/png_vlm.py` `ImageExtractor`). Status,
  operations, ids and citations come from code: `attribution.attribute` → `state.reconstruct` → `rules.Policy.verdict`
  → `steps.build_steps` → `validation.validate_output_file` (README "How it works"; the import graph is checked against
  the AST by `code/tests/test_architecture_diagram.py`). A screenshot id the model reports survives only if its exact
  token is in local multi-pass OCR text (`parsers/ocr_grounding.py`). Since rc6, an OCR id the model missed is used
  only as scope evidence for a record that is already owned (`attribution.page_scope`).
- **Key metric.** Intent: 42/42 correct on both models compared (32 samples + 10 non-deletion texts;
  `code/evaluation/intent_model_comparison.json`). Adversarial fixtures X14–X19 (injections, an empty
  text, a 50k-character text, invalid UTF-8): 6/6 pass (`code/evaluation/evaluation_report.md` §4 Safety).
- **Likely follow-up.** "Isn't the intent label itself an injection surface?" Yes, it is the one model output that
  changes a status. It is a closed enum, UNCLEAR maps to NOT_APPLICABLE, and every NON_DELETION is cross-checked against
  the request's own activity rows (`intent.decide`). X14 and X15 cover it.
- **Uncertainty.** Six adversarial fixtures are a small sample. Live intent replies are cached, so a different
  model version could label a borderline text differently (UNKNOWN).

## 2. Why was E2 removed, and what do its measurements actually establish?

- **Facts.** E2 was a tool-calling investigator with five read-only tools and a deterministic verifier
  (`code/analysis/e2_investigator*.py`). Run on the 19 AMBIGUOUS timing-only sample pairs: 11 batches, 28 calls,
  41,780 tokens, 126 proposals, 0 accepted (DESIGN.md D-E2 and rejection log; `code/evaluation/e2_results.json` is a
  cache replay of that run).
- **Key metric.** 0/126 accepted, on 19 pairs (MEASURED).
- **Likely follow-up.** "So the ceiling is zero?" The measurement is 0/126 on the samples. The zero ceiling is an
  argument (JUDGEMENT): the verifier accepts only identity-link support, and every identity link the tools can reach is
  already a hard link that attribution marks CONFIRMED. E2 was never run on the eval set (about 300 calls for an
  expected 0).
- **Uncertainty.** A verifier that accepted other kinds of support could resolve more pairs, but it could also accept
  wrong owners. That was never measured.

## 3. Attribution ambiguity: why omit instead of guessing?

- **Facts.** AMBIGUOUS and CONFLICT pairs produce no steps (`attribution.attribute`; DESIGN D-ATTR). 131 eval deletion
  cases have an AMBIGUOUS primary account (`evaluation_report.md` §4e and §7A). M1 (re-run in rc7 on the current
  attribution): 904 AMBIGUOUS eval pairs in 347 cases (1,247 / 381 before A1). 0 withheld resources are proven PRESENT,
  so 0 COMPLETE → INCOMPLETE flips (`code/evaluation/measurements.json`).
  The ordering tiebreak made 10 sample assignments instead of 7, and 1 was wrong: case_23 → account_9155, while the
  reference account is account_410 (`attribution_variants.json` a1o). rc6-P2 found 0 unused same-line identity
  co-occurrences for the 159 AMBIGUOUS account pairs (DESIGN rc6-P2).
- **Key metric.** Sample precision 178/178, recall 178/193. 5 of the 15 missing sample steps are AMBIGUOUS accounts
  (cases 10, 23, 28, 29, 30).
- **Likely follow-up.** "You trade recall for safety. How much eval recall does that cost?" UNKNOWN: there are no eval
  labels. The upper bound on the samples is +5 steps.
- **Uncertainty.** The true owners of the AMBIGUOUS pairs are unknown, and so is the official score effect.

## 4. D-SCOPE and the single-request limitation

- **Facts.** A hard-linked record is in scope only through identity evidence dated inside [requested_at, audit_at], the
  case's own deletion burst, or (rc6-P1) an in-window screenshot of the subject's own page
  (`attribution.CaseContext.in_lifecycle`, `attribution.page_scope`). Basis: 84/84 expected sample pairs kept and the 3
  other-request pairs excluded (DESIGN D-SCOPE). Review of the 16 single-request subjects
  (`code/evaluation/dscope_review.json`, DESIGN rc6-P1 review): 22 records in 14 cases are linked only by evidence
  captured after audit_at, so excluding them is required (AGENTS.md §6.2: "Evidence created later must not change the
  answer for that case"). 6 records in 2 cases (case_34 audit_record_3753; case_45 audit_record_001/002/004/005/006) are
  linked only by admissible evidence dated before the request.
- **Key metric.** 1 defect found and fixed: case_34 support_9538 (1 of 518 eval rows changed, 0 of 32 samples).
- **Likely follow-up.** "Why keep excluding the 6?" The spec never defines a request episode. For case_45, each of the
  24 sample cases with an audit record lists exactly one, and the five excluded records appear only as older rows on one
  screenshot, while the kept audit_record_003 is also catalog-linked. That is evidence of a generator pattern, not a
  spec rule.
- **Uncertainty.** Grader agreement on the 6 pre-request records is UNKNOWN (JUDGEMENT).

## 5. F1: screenshot-based absence proof and its boundary

- **Facts.** `ocr_grounding.absence_proof` accepts a screenshot as proof that a record holds no direct identifiers only
  when there is exactly one extracted record, its id is grounded, no other resource id is on the page, at least three
  detail labels follow the header id, and no email, "@" between non-space characters, phone-shaped or 64-hex token
  appears anywhere in the multi-pass OCR text. Eligible: 1 record. Eval rows changed: 1 (case_46 +VERIFY_RECORD_REDACTED
  identity_service/tombstone_17 citing evidence_42, captured 2026-03-10T06:00:00Z ≤ audit_at 2026-03-11T08:00:00Z).
  Samples: 0 rows changed (DESIGN F1; `evaluation_report.md` §7C).
- **Key metric.** 1/1 eligible record proven; 0/32 sample rows changed.
- **Likely follow-up.** "Absence from a view is not absence from storage." Agreed. That is why F1 needs the record's own
  detail page and a scan of the whole image, and why it is labelled JUDGEMENT. The sample tombstones cite a JSON record
  bundle (evidence_51), not a screenshot.
- **Uncertainty.** The spec does not define visual proof of redaction, so grader agreement is UNKNOWN.

## 6. case_63: INCOMPLETE with no steps

- **Facts.** The only candidate, marketing_001, is CONFLICT. Its own screenshot (evidence_60) names Soo-jin Park /
  sj.park@naver.com, not the subject, and only catalog claim activity_4008 links it. The output is `INCOMPLETE,[]` with
  trace reason `NO_ATTRIBUTABLE_RESOURCE`, the only reason the validator accepts for an empty INCOMPLETE
  (`validation.self_contradictions`; DESIGN M2/C5).
- **Key metric.** 1 such eval row. The same rule turns sample case_04 into INCOMPLETE [], the single sample status
  miss (31/32). Its reference is COMPLETE with two verification steps we cannot derive: account_004 appears in no
  participant file, and analytics_901 is AMBIGUOUS.
- **Likely follow-up.** "Why not COMPLETE []?" The spec requires COMPLETE rows to carry "verification steps proving
  the current state", and an empty COMPLETE proves nothing. The independent re-derivation chose COMPLETE []. The rule
  costs one sample status and is a deliberate JUDGEMENT.
- **Uncertainty.** The grader may expect COMPLETE for case_63 (UNKNOWN).

## 7. Citation selection and admissibility at audit_at

- **Facts.** Rule C: cite the latest admissible file (captured ≤ audit_at) that mentions the resource id, else the
  latest full snapshot of its system (`state.reconstruct`; DESIGN §5.6). Alternatives measured on the 75 cited sample
  steps: A 71/75, A_raw 70/75, A_ids 71/75, A_email 44/75, A′_scope 36/75, A′_full 12/75. The spec audit's negative
  control injects a citation captured after audit_at and catches it (`spec_audit.py`, 18/18 classes).
- **Key metric.** C matches 74/75 (MEASURED). The miss is case_04 / account_004 (expected evidence_54; C picks
  evidence_90, an empty inventory).
- **Likely follow-up.** "case_34 support_9538 cites evidence_01, but evidence_04 is later and shows the id." Rule C
  counts parsed mentions, and evidence_04's mention is OCR-only. Using it for scope but not for citation is deliberate
  (rc6-P1 changed scope only), and the right citation is UNKNOWN.
- **Uncertainty.** Whether an empty inventory counts as a full snapshot (UNKNOWN).

## 8. Failed experiments, measured trade-offs, rejected alternatives

- **Facts (DESIGN rejection log, `evaluation_report.md` §4e and §6).**
  - A2 offset bands broke case_02's correct account_002 (+5 min outside the band).
  - Ordering tiebreak: +2 correct, 1 wrong owner.
  - E1-A: 4 correct, 1 incorrect, 14 AMBIGUOUS. E1-B: 1 correct, 1 incorrect, 17 AMBIGUOUS.
  - E2: 0/126 accepted.
  - Re-extracting the legacy screenshots with the current prompt changed 2 eval rows (case_61 → INCOMPLETE [] with
    exit 2; case_34 gained an unverifiable finding) and was rejected.
  - The first live run silently turned 358 of 550 cases into NOT_APPLICABLE (a model 404 and a daily cap). Since C0,
    any model failure exits 4.
- **Key metric.** A1 alone: 7/19 timing-only pairs resolved, 0 incorrect; recall 171 → 178/193; sequence 16 → 19/32.
- **Likely follow-up.** "rc6 now includes support_9538, which the rejected re-extraction also produced. Isn't that the
  same change?" No. The re-extraction replaced 10 model replies and broke case_61. rc6-P1 makes no model call: it
  uses the existing OCR text only as scope evidence for an already-owned record, and changes exactly 1 row.
- **Uncertainty.** None of these trade-offs has been measured on the eval set (no labels).

## 9. Reproducibility, cache provenance, generalization probes

- **Facts.** `python3 code/main.py --from-cache` needs no keys, network or Tesseract. 574 cache files: 0 misses, 0
  orphans (`cache_audit.json`). All 530 model replies map to prompts shipped in `code/prompts/`, including the
  byte-exact legacy vision prompt `image_extraction_v1.md` (`test_prompt_provenance.py`). Determinism: 3 processes
  (PYTHONHASHSEED 1/2/3, the third keyless), 553 files byte-identical. Coupling probes 5/5: all 19,760 id tokens renamed,
  rows shuffled, timestamps reformatted, emails upper-cased, and one evidence file dropped
  (`coupling_probes.json`). Clean-folder reproduction from code.zip is byte-identical (`clean_room.json`).
- **Key metric.** 5/5 probes; 0/574 cache misses; 3/3 identical runs.
- **Likely follow-up.** "Would a live run reproduce your output?" Not guaranteed (README "Reproduce"). The cache
  makes the submitted run reproducible, not the model.
- **Uncertainty.** Behaviour on a differently generated dataset beyond these five perturbations is UNKNOWN.

## 10. Known limitations and what more time would buy

- **Facts.** `evaluation_report.md` §7.
  - Residual sample misses: 12 ATTRIBUTION and 3 NOT_DERIVABLE steps. account_004, audit_record_6645 and
    audit_record_5569 appear in no participant file at all.
  - Eval correctness is UNKNOWN.
  - Kit Code ZIP heuristic: 82.4, an ESTIMATE (`code/evaluation/score_estimate.md`).
- **Key metric.** Status 31/32; precision 178/178; recall 178/193; exact sequence 19/32 (MEASURED, samples).
- **What more time would buy.**
  - An identity signal for the AMBIGUOUS accounts: up to +5 sample steps, ESTIMATED upper bound.
  - A reference-validated episode definition for the 6 pre-request records: unknown sign.
  - Re-extracting the legacy screenshots with the model that produced them: 0 expected changes, UNKNOWN.
- **Likely follow-up.** "Which limitation would you fix first?" The AMBIGUOUS accounts: they are the largest measured
  recall gap. But no admissible signal exists today (rc6-P2), so any fix would be a guess.
- **Uncertainty.** All eval-side effects are UNKNOWN.
