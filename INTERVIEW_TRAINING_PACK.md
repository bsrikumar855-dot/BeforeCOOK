# INTERVIEW_TRAINING_PACK.md — rehearsal pack for the AI interview (rc7)

Preparation material only. No mock interview was conducted, nothing here predicts an interview or official score, and
this file is not in code.zip (`code/package.py` ships only README.md, requirements.txt, `code/` and `evaluation/`).

- **Where the questions come from.** The public interview description in AGENTS.md §6.9 ("architecture, investigation
  approach, remediation planning, and evaluation") and the topics the brief named. They do not come from any
  evaluator material.
- **Where the facts come from.** `code/evaluation/evaluation_report.md`, `DESIGN.md`, `code/evaluation/*.json`,
  `INTERVIEW_FACTS.md` and `INTERVIEW_MOCK.md`. Every figure was re-checked on 2026-10-25 against rc7 (`3fa3254`).
- **Labels.** **[M]** measured by a script in this repo · **[J]** engineering judgement · **[U]** unknown; the source
  that would settle it is named.

## How to answer (every question, about 90 seconds)

1. **Claim** (10 s): one sentence that answers the question.
2. **Mechanism** (25 s): the file and function that does it.
3. **Number** (15 s): one metric, said with its denominator.
4. **Rejected alternative** (15 s): what was tried or considered, and the measured reason it lost.
5. **Limit** (15 s): what is judgement or unknown. Say this before the interviewer has to ask.

If interrupted, go straight to the number and the limit. Never round a fraction into a percentage without its
denominator. Never say "accuracy" for the eval set: it has no labels.

---

## Q1. Architecture — walk me through the system

- **Answer.**
  - `code/main.py` → `pipeline.run` loads the CSVs and builds one evidence index. Each parser emits typed
    `Observation` records; `file_checks.check` fails closed on a bad file.
  - Per case, the pipeline then runs: intent → identity resolution → `attribution.attribute` → `state.reconstruct` at
    audit_at → `rules.Policy.category`/`verdict` from deletion_rules.csv → `steps.build_steps` →
    `validation.validate_row`.
  - A trace per case records every decision, and `--case <id> --explain` prints it.
- **Numbers.** 518/518 eval rows; 153 COMPLETE / 355 INCOMPLETE / 10 NOT_APPLICABLE; 3,012 steps; 0 validator
  violations **[M]**. Samples: status 31/32, step precision 178/178, recall 178/193, exact sequence 19/32 **[M]**.
- **Follow-up.** "Draw the dependency graph. How do you know the diagram matches the code?"
  → The README Mermaid graph is checked against the AST imports by `code/tests/test_architecture_diagram.py` **[M]**.
- **Weakness.** The pipeline can sound like a fixed script. Name the data-driven parts: categories come from
  `deletion_rules.csv`, ownership from identity evidence, and state from admissible files.
- **M / J / U.** Counts are M. Splitting the work into these stages is J. Eval correctness is U (no labels exist).

## Q2. Deterministic core vs model — why so little model?

- **Answer.**
  - The model does two perception jobs: request intent (a closed enum, `intent.IntentClassifier.classify`) and reading
    11 screenshots (`parsers/png_vlm.ImageExtractor`).
  - Code decides every output field: status, operation, resource id and citation.
  - A screenshot id survives only if its exact token is in local 4-pass OCR text (C9/C10).
  - `intent.decide`: a DELETION label becomes NOT_APPLICABLE when the request's own CLASSIFY_REQUEST activity row
    says otherwise. For NON_DELETION the row only corroborates; it does not change the outcome. Run summary: 508
    `model_deletion->DELETION` with 0 vetoes, and all 10 NOT_APPLICABLE rows corroborated **[M]**.
    Don't say "every label is cross-checked".
- **Numbers.**
  - Intent: 42/42 on both models compared (32 samples + 10 non-deletion texts). flash-lite used 5,711 tokens vs
    12,372 for flash **[M]**.
  - Usage behind output.csv: 25 distinct provider calls, 80,243 tokens, 154.9 tokens per eval request, actual cost $0
    (free tier) **[M]**.
- **Rejected alternative.** E2, a tool-calling investigator with a deterministic verifier: 28 calls, 41,780 tokens,
  126 proposals, 0 accepted **[M]**.
- **Follow-up.** "The rubric may reward agentic design. Isn't this just a workflow with two LLM calls?"
  → I built the agentic option and measured it (0/126 accepted). The model sits where perception is needed. Decisions
  stay auditable and reproducible from the cache. How the rubric weighs that is **[U]**.
- **Weakness.** Intent alone decides status for the 10 NOT_APPLICABLE rows. Say it first: it is the one model output
  that changes a status (adversarial fixtures X14/X15 cover it).
- **M / J / U.** 42/42 and 0/126 are M. "Ceiling 0" for E2 is J: any identity link its tools can reach is already a
  hard link. Behaviour of a different model version is U.

## Q3. Attribution — how do you know a record belongs to this person?

- **Answer.**
  - **ID1.** The request email (trimmed, lower-cased) is matched against current identities and against the
    `user_audit_log` history.
  - **ID2.** The identifier set is user_id, current email/phone/hash/name, and every historical value, each with its
    provenance.
  - **Hard edges.** Email, phone, hash, `customer_reference`/`subject_reference`, and catalog links.
  - **Timing edges.** Deletion-worker rows within ±3 h of reported_at; these only generate candidates.
  - **Assignment.** Constrained one-to-one per burst, for account deletes only. INFERRED = the edge is in every maximum
    assignment. A1 adds a claim-consistency filter: the worker's result must agree with the reported status.
- **Numbers.**
  - ID1: 550/550 requests resolve to exactly one user; 103 by a historical email only **[M]**.
  - Labelled sample pairs: 96/111 correctly attributed **[M]**.
  - A1 on known-good links: 252/252 agree. On samples it resolved 7/19 timing-only pairs, 7 correct, 0 wrong **[M]**.
- **Follow-up.** "The spec calls reported_status a claim. Why use it?"
  → For linkage only, never as proof of state, and validated 252/252 on hard-linked deletes (DESIGN R5-A1).
- **Weakness.** INFERRED_CLAIM in a single-case batch rests on A1 alone. The three rc7-X4 accounts (cases 526, 528,
  531) are exactly that.
- **M / J / U.** 252/252 and 7/7 are M. Using a claim for linkage is J. The owners of eval INFERRED pairs are U.

## Q4. Identity ambiguity — why omit 131 accounts instead of guessing?

- **Answer.**
  - AMBIGUOUS and CONFLICT pairs produce no steps.
  - Interleaved bursts make some owners undecidable. Example: on 2026-09-02, five account deletes fall inside five
    cases reported at 12:00.
  - A wrong owner plans deletion against another person's data, which is worse than a missing step.
- **Numbers.**
  - 131 eval deletion cases have an AMBIGUOUS primary account **[M]**.
  - M1: 904 AMBIGUOUS eval pairs in 347 cases. 0 withheld resources are proven PRESENT, so 0 COMPLETE→INCOMPLETE flips
    **[M]**.
  - rc6-P2: 0 unused same-line identity co-occurrences across 159 pairs **[M]**.
  - Sample cost: 5 of the 15 missing steps are AMBIGUOUS accounts **[M]**.
- **Rejected alternatives** (all **[M]**):
  - Ordering tiebreak: +2 correct, 1 wrong (case_23 → account_9155; the reference has account_410).
  - A2 offset bands: broke case_02's correct account_002, whose delete was 5 min after reported_at.
  - E1-A: 4 correct, 1 wrong. E1-B: 1 correct, 1 wrong.
- **Follow-up.** "Recall is the metric. You left points on the table."
  → Maybe: the eval effect is **[U]**, and the sample upper bound is +5 steps. Every guessing method I measured made a
  wrong owner on 19 pairs.
- **Weakness.** Do not call omission score-optimal. It is a safety choice whose score effect is unknown.
- **M / J / U.** Counts are M. "Omit rather than guess" is J. True owners are U.

## Q5. Audit-time constraints — what does "state at audit_at" mean in your code?

- **Answer.**
  - **Admissible** = the file's captured_at ≤ audit_at and the observation's time ≤ audit_at (`state.reconstruct`).
  - The latest admissible observation per copy wins. Absence needs a full snapshot of the system.
  - **Citation rule C:** the latest admissible file that mentions the id, else the latest full snapshot.
  - **Plan order** (`steps.sort_key`): phase, then the operation order of the spec table (disable flows and block
    restores before record changes), then system_id, then resource_id.
- **Numbers.**
  - Rule C matches 74/75 cited sample steps, against A 71/75, A_raw 70/75, A_email 44/75, A′_scope 36/75 **[M]**.
  - The sort reproduces 32/32 sample sequences when the step set is right **[M]**.
  - The spec audit catches an injected after-audit_at citation (negative control 18/18) **[M]**.
- **Follow-up.** "A record could be deleted at 11:59 and the snapshot is from 11:00. What do you report?"
  → The last admissible state. A later file cannot change the case (AGENTS.md §6.2). When nothing decides the state,
  rc7-X4 adds only the required check with evidence "", never a finding.
- **Weakness.** The single rule-C miss is case_04/account_004: C picks evidence_90, an empty inventory, and the
  reference cites evidence_54. Whether an empty inventory counts as a full snapshot is **[U]**. resource_id string vs
  numeric order is untested, but 0 eval rows expose it **[M]**.
- **M / J / U.** 74/75 is M. Rule C is J. Screenshot citations are U: 10 eval rows cite a PNG, and 0 of 75 sample
  citations do.

## Q6. Failed experiments — what didn't work?

- **Answer.** Every change had a SHIP rule: 0 wrong owners and 0 broken sample steps. That rule rejected:
  - A2, ordering, E1 and E2 (numbers in Q4 and Q2).
  - Re-extracting the screenshots with the current prompt: it changed 2 eval rows, and case_61 became INCOMPLETE [],
    which stopped the run with exit 2.
  - In rc7, X0: an analytics `audience_cohort` attribute is not identity evidence (4 values, and no person-level
    source names a cohort).
- **Incident.** The first live run silently turned 358 of 550 cases into NOT_APPLICABLE (a model 404 and a daily cap).
  Since C0, any model failure exits 4 and leaves output.csv untouched **[M]**.
- **Follow-up.** "What did you yourself get wrong?"
  - support_9538 had been triaged as ambiguous; it was a real D-SCOPE defect, fixed in rc6-P1.
  - A committed measurement was stale after A1 (1,247/381 → 904/347).
  - My first determinism test shadowed `code/main.py` and broke 7 tests; the suite caught it before commit.
- **Weakness.** Every rejection was measured on 32 samples only.
- **M / J / U.** Outcomes are M. The SHIP rule is J. Eval-side effects are U.

## Q7. Adversarial validation — how did you try to break it?

- **Answer.**
  - **Coupling probes 5/5:** rename all 19,760 id tokens, shuffle rows, reformat timestamps, upper-case emails, drop
    one evidence file **[M]**.
  - **Spec audit:** 0 violations; its negative control catches 18/18 injected defect classes **[M]**.
  - **Independent re-derivation:** an agent that never saw our code agreed on status for 23/24 cases and on 101/103
    of our steps. Triage: 0 OUR_BUG, 3 SUBAGENT_ERROR, 8 AMBIGUOUS_SPEC **[M]**.
  - **Custom cases X1–X19: 19/19 pass** **[M]**. X6–X13 cover categories the samples never reach. X14–X19 are
    adversarial: injections, an empty text, a 50k-character text, invalid UTF-8.
- **Follow-up.** "Your fixtures state record_type. Do they test category inference?"
  → No. They test category → treatment → steps. Inference from shape is checked only by the samples, e.g. sample
  case_07 `support_001` (shape: attachment → DELETE).
- **Weakness.** A probe shows the code is not keyed to literal ids. It does not show the code is correct on a
  differently generated dataset.
- **M / J / U.** Probe results are M. The choice of perturbations is J. Generalisation beyond them is U.

## Q8. Reproducibility — can I rerun your output?

- **Answer.**
  - `python3 code/main.py --from-cache` needs no key, no network and no Tesseract.
  - Cache: 574 files, 0 misses, 0 orphans **[M]**.
  - All 530 model replies map to shipped prompts, including the byte-exact legacy vision prompt **[M]**.
  - PYTHONHASHSEED 1/2/3 (the third keyless): 553 files byte-identical **[M]**. Evaluation artifacts are byte-stable
    too (rc7-D1).
  - `clean_room.py` unzips code.zip into an empty folder, builds a fresh venv and follows the README: output identical.
- **Hashes.** output.csv `ce35e3a5…`, code.zip `e29f1577…`. Rebuilding the zip from the rc7 tree reproduces it.
- **Follow-up.** "Would a live run give the same answer?"
  → Not guaranteed. The cache makes the submitted run reproducible, not the model (README "Reproduce").
- **Weakness.** 10 screenshot replies come from the legacy prompt. Re-extracting them is a known no-go (Q6).
- **M / J / U.** All of the above is M. Live-run equality is U.

## Q9. Security — secrets, untrusted evidence, injection

- **Answer.**
  - **Secrets.** Keys come only from environment variables or an uncommitted, gitignored `.env`. `code/package.py`
    excludes `dataset/`, `.env`, nested archives and temporary outputs. The release gate scanned the built zip and
    found no secrets.
  - **Untrusted files.** `file_checks.check` rejects path traversal, a wrong extension, truncated SQL, a corrupt PNG
    and a missing file. Affected cases fail closed (exit 4, output.csv untouched): 5/5 fixtures **[M]**.
  - **ReDoS.** 45 regexes probed with 1 MB adversarial lines against a 2.0 s bound: 44 pass, worst 0.553 s **[M]**.
    The 45th is built at run time and covered through its builder. Seven quadratic regexes were found and fixed
    (commit 5a1c354) **[M]**.
  - **Injection.** Screenshot ids must be OCR-grounded. An unknown email never links. Intent is a closed enum, and
    UNCLEAR → NOT_APPLICABLE.
- **Follow-up.** "Free-tier Gemini may train on prompts. Did you send personal data?"
  → Only request texts and the 11 screenshots were sent, and the data is synthetic (README "Privacy note"). The
  provider's current terms were not re-read: **[U]**.
- **Weakness.** Six injection fixtures are a small sample.
- **M / J / U.** Fixture and regex results are M. The threat model (DESIGN §7.1) is J. Provider policy is U.

## Q10. Limitations — what is weakest, and what would you do next?

- **Answer.**
  - **Residual sample misses: 15 steps.** 12 ATTRIBUTION (7 anonymised analytics, 5 AMBIGUOUS accounts) and 3
    NOT_DERIVABLE (account_004, audit_record_6645, audit_record_5569 appear in no participant file) **[M]**.
  - **Eval.** 131 cases without an account step. 6 pre-request D-SCOPE exclusions (case_34 audit_record_3753; case_45
    audit_record_001/002/004/005/006) are **[J]**. case_63 = INCOMPLETE [] **[J]**.
  - **Eval correctness: [U].**
- **case_63, if asked.** The only candidate (marketing_001) shows another person: Soo-jin Park on evidence_60. An empty
  COMPLETE proves nothing.
  - The same rule costs sample case_04, the single status miss (31/32).
  - case_63 reported NOT_STARTED, and samples map NOT_STARTED → INCOMPLETE in 7 of 10 cases **[M]**.
- **Follow-up.** "With one more week?"
  → Find a new admissible identity signal for the interleaved bursts. Validate the episode rule on a labelled
  single-request case. No guess-based fixes: rc6-P2 found no unused signal.
- **Weakness.** The kit's Code ZIP figure 82.4 is a heuristic ESTIMATE, not a score. Never quote it as one.
- **M / J / U.** Residuals are M. The case_63 and D-SCOPE choices are J. Official scores are U.

---

## Added in rc8: topics the official materials list that Q1–Q10 did not cover

The BTS article gives the judge's usual order: quick pitch → retrieval and architecture → safety and failure modes →
implementation and code familiarity → evaluation → production monitoring → what is novel. It also says the judge
"probes how the candidate worked with their AI tools; what they directed, what they accepted, and what they pushed
back on". The help article lists the same topics. Q11–Q14 cover the four that were missing.

## Q11. Opening pitch (60 seconds; it comes first)

- **Answer.**
  - The problem: a deletion workflow can say COMPLETE while copies survive in replicas, logs, exports, indexes and
    backups, and sync or restore jobs can bring deleted data back.
  - What the agent does: for each of 518 requests it decides whether deletion was asked for, then rebuilds the
    person's current and historical identifiers. It finds the records and the active flows, establishes their state
    at audit_at from admissible evidence only, applies deletion_rules.csv, and writes findings, an ordered fix plan and
    checks.
  - Design: a model reads intent and 11 screenshots; code makes every decision. When ownership can't be shown, the
    record gets no step, so remediation is never planned against another person's data.
  - **Close with one number:** samples status 31/32, step precision 178/178.
- **Weakness.** Don't open with the architecture debate. Lead with the problem and one measured number.

## Q12. Production monitoring — how would you know it's going wrong?

- **What exists today [M].**
  - Exit codes: 0 ok, 2 validation failed, 3 cache miss, 4 model failure. output.csv is untouched on 2/3/4;
    `--allow-degraded` writes `output.degraded.csv` instead.
  - Every run writes `code/traces/_run_summary.json`:
    - status mix (153/355/10);
    - attribution labels (AMBIGUOUS 904, CONFIRMED 1,475, INFERRED_CLAIM 95, INFERRED 40, CONFLICT 1);
    - intent paths, model failures (0), rejected evidence files (0), degraded cases;
    - screenshot rows accepted (24) and dropped, each with a reason.
  - `usage_ledger.json`: live calls, cache entries read, fallback events.
  - One trace per case, and `--explain` for one case.
- **What I'd alert on [J].**
  - The AMBIGUOUS share moving.
  - The UNGROUNDED screenshot-id rate.
  - The intent UNCLEAR / NON_DELETION share.
  - Fallback or 404 events. The first live run lost 358 of 550 cases this way, which is why C0 exists.
  - Rejected evidence files.
  - Status mix drift against the last release. `distribution_sanity.py` already compares the sample and eval
    distributions.
- **Follow-up.** "What would you add first?" → An alert on intent paths. A model change that flips borderline texts
  would move the NOT_APPLICABLE count, which today only the cache freezes.
- **Weakness.** None of these alerts is wired to a scheduler. Say "these are the signals the run already records",
  not "we monitor".

## Q13. What is novel?

- **Answers to choose from (each is in the code, with a number).**
  - Claims are linkage, never proof. Reported status is used only to tell interleaved owners apart (A1, 252/252
    consistent on hard links). State always comes from observations at audit_at.
  - The model proposes, OCR confirms. A screenshot id survives only if its exact token is in local multi-pass OCR text
    (C9/C10). 5 of 32 single-pass misses were fixed without letting OCR invent ids.
  - Abstention is measured, not hoped for. M1: 0 of the records withheld from 904 ambiguous pairs is proven PRESENT,
    so abstaining flips 0 statuses.
  - A label-free release gate:
    - every id renamed (19,760 tokens) with no row change;
    - a spec audit whose negative control catches 18/18 injected defects;
    - an independent re-derivation by a separate agent (0 OUR_BUG).
- **Weakness.** "Novel" claims invite "isn't that standard?". Answer with the measurement, not the adjective.

## Q14. How did you work with the AI tool? What did you direct, accept and push back on?

Use only what is in `log.txt`. These are the participant's own logged instructions, not something to embellish:
- **Directed the architecture.** The Phase 1 prompt fixed D-ATTR (labels; AMBIGUOUS gives no steps), D11 and D-CITE
  rule C before any build. Model choice was also the participant's: "Dont use groq instead we'll use … gemini … as
  fall back".
- **Pushed back on a claimed result.** At the STEP 0 checkpoint the exit-code-4 guard turned out to exist only in a
  deleted cloud session ("Correction accepted … Rebuild it now as change C0").
- **Pushed back on a measured result.** C10: "all 5 ids C9 dropped are visible … so the matcher is suspect". This led
  to 4-pass OCR and underscore-tolerant matching.
- **Set the safety rule for inference.** Round 5 CORE RULE: A1/A2 "are FILTERING signals, not proof of ownership". A
  timing-only resource is assigned only when exactly one owner remains.
- **Demanded evidence for eval changes.** Round 6: "any eval row change must be justified by raw evidence quoted …".
- **Rejected AI work by measurement.**
  - A2 broke case_02.
  - Ordering made 1 wrong owner.
  - E2 accepted 0/126.
  - Re-extracting screenshots broke case_61.
- **Weakness.** Never say "Claude built it". Say "I required X; the run showed Y; I kept or reverted it". Describe
  only decisions you actually made and can explain.

## rc8 additions to the facts (all [M] unless marked)

- The 3 NOT_DERIVABLE ids appear in none of the 4 PDF text layers, none of the 11 screenshots' OCR text, and no
  other dataset file except `sample_output.csv`.
- Capacity probe (`code/analysis/capacity_probe.py`): no sample case owns two resources of one system (111 pairs).
  The rule forces 0 of the 283 AMBIGUOUS rows that have two or more candidates. Its extra assumption ("every row is
  owned") fails on sample case_32. Rejected.
- D-SCOPE:
  - The samples cannot decide the single-request question: 30/32 sample cases are single-request, with 0 exclusions.
  - case_45's 5 exclusions are evidence-supported: keeping them would give 6 security_service resources, against 0/32
    sample cases with two in one system.
  - case_34 `audit_record_3753` stays JUDGEMENT.
- 37 of the 904 eval AMBIGUOUS pairs involve a resource another case owns, so 867 are truly unresolved. 0 resources
  are owned by two cases.
- Official October README: the code judge ignores `.json/.csv/.log` files. Every audit result used here is quoted in
  shipped Markdown.

---

## Last-minute revision sheet (one page)

**Numbers to say with their denominators (all [M]).**

| Area | Figure |
|---|---|
| Sample status | 31/32 |
| Sample steps | precision 178/178 · recall 178/193 · sequence 19/32 |
| Eval output | 518 rows: 153 COMPLETE / 355 INCOMPLETE / 10 NOT_APPLICABLE · 3,012 steps · 0 violations |
| Attribution | 96/111 labelled pairs · A1 252/252 on hard links · 7/19 timing pairs resolved, 0 wrong |
| Ambiguity | 131 eval cases · M1 904 pairs / 347 cases · 0 proven present · 0 status flips |
| Citations | rule C 74/75 (next best 71/75) · sort reproduces 32/32 sequences |
| Identity | 550/550 resolve to one user · 103 by historical email only |
| Model use | intent 42/42 · 25 calls · 80,243 tokens · 154.9 tokens/request · $0 · E2 0/126 |
| Robustness | custom cases 19/19 · probes 5/5 (19,760 ids renamed) · spec-audit control 18/18 · fail-closed 5/5 · regex 44/45 + 1 dynamic, worst 0.553 s |
| Reproducibility | 3 seeds, 553 files identical · cache 574 files, 0 misses · clean room byte-identical |
| Tests | 129 |

**Three sentences to open with.**

1. The model only perceives: request intent and 11 screenshots. Code decides every field of every row, and each
   decision is traced.
2. When evidence cannot decide an owner, the record gets no step. A wrong owner would plan deletion of another
   person's data.
3. Nothing after audit_at is used, and every citation is the latest admissible file that mentions the record.

**Judgements to own (say "judgement", then the reason).**
- Omit AMBIGUOUS owners.
- case_63 = INCOMPLETE [].
- 6 pre-request D-SCOPE exclusions.
- F1 screenshot absence proof (1 record, case_46).
- rc7-X1: a screenshot mention counts for citation.
- Reported status is used for linkage only.

**Unknowns to admit.**
- Eval correctness.
- The official score.
- Grader row collation.
- Whether the reference cites PNGs.
- Owners of the AMBIGUOUS pairs.
- The provider's privacy terms.

**rc7 delta (4 eval rows, 0 sample rows).**
- case_34: citation evidence_01 → evidence_04.
- case_526, case_528, case_531: +VERIFY_RECORD_ABSENT "" (required check for an attributed account whose state no
  evidence decides).

**Never say.**
- "Accuracy" for the eval set.
- "Rank", or an official or predicted score.
- That E2's ceiling was measured: it is an argument.
- That a live run reproduces the output.

**Commands for a live demo (exit 0).**
- `python3 code/main.py --case case_05 --explain --from-cache`: replica drift (account_005 in the failover replica,
  evidence_54).
- `python3 code/main.py --case case_46 --explain --from-cache`: F1.
