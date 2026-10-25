# GAP_REGISTER.md — official criteria vs evidence, and the ranked gap register (rc8 round)

Not shipped in code.zip. Audited commit: rc7 `3fa3254` (candidate rc8 = rc7 + docs, output.csv byte-identical).
Labels: **MEASURED**, **EVIDENCE-SUPPORTED**, **JUDGMENT**, **UNKNOWN**. No official score exists; nothing below is
one.

## Sources loaded (2026-10-25)

| Source | Status |
|---|---|
| October `problem_statement.md` (raw.githubusercontent.com) | loaded; byte-identical to ours |
| October `README.md` | loaded |
| October `AGENTS.md` | loaded. It differs from ours in one place: the submission URL is `https://www.hackerrank.com/...`, ours says `wwww` |
| October `agent_trace_instructions.md` | loaded; identical to ours |
| github.com repository page | HTML blocked by the proxy (403); the raw files above cover its content |
| BTS article (hackerrank.com/blog) | loaded through Firecrawl (direct curl is blocked) |
| Help article 3728112689 | loaded through Firecrawl |

**Weights.**
- **Overall [EVIDENCE-SUPPORTED].** The help article (updated about 9 days before 2026-10-25) gives Code ZIP 30 /
  Output 30 / Interview 30 / Transcript 10, and says "weights and rules can vary by edition".
- **Sub-criteria (provisional).** Code ZIP 30/30/25/15, Interview 40/25/20/15 and Transcript 35/25/25/15 come from
  the BTS article about the May edition. No October-specific rubric was found.
- **October output scoring [MEASURED: README text].** The README lists deletion assessment accuracy, current and
  historical identity resolution, discovery of remaining data and recreation risks, policy application and evidence
  references, and completeness and ordering of steps. The help article adds safety on adversarial inputs.

**Timeline.** Help article: the October challenge starts 2026-10-24 12:30 UTC; live evaluation runs 2026-10-26–27.
A 24-hour window would end 2026-10-25 12:30 UTC [JUDGMENT: inferred, not stated].

## Criteria → evidence matrix

| Criterion (source) | Current behaviour | Evidence | Label |
|---|---|---|---|
| Deletion assessment accuracy (Oct README "Evaluation") | intent model → closed enum; NON_DELETION cross-checked against the request's own activity rows | samples status 31/32; 10 eval NOT_APPLICABLE texts are all non-deletion (rc7-X2) | MEASURED (samples) / UNKNOWN (eval) |
| Current + historical identity (Oct README) | ID1/ID2: request email matched against current and audit-log values | 550/550 resolve to one user; 103 by historical email only; fixture X2 | MEASURED |
| Remaining data and recreation risks (Oct README) | D-ATTR + state at audit_at; flows and restores checked | recall 178/193; residual 12 ATTRIBUTION + 3 NOT_DERIVABLE | MEASURED |
| Policy and evidence references (Oct README) | category → rule → verdict; citation rule C | key-aligned operation/state/evidence 178/178; rule C 74/75 | MEASURED |
| Completeness and ordering of steps (Oct README) | `steps.sort_key` | sequence 19/32; the sort reproduces 32/32 sequences when the step set is right | MEASURED |
| Format (Oct README "Before submitting") | validator + spec audit | 518 rows, case_id order, 0 violations, negative control 18/18 | MEASURED |
| Output safety on adversarial input (help article) | closed-enum intent, OCR grounding | fixtures X14–X19 6/6 | MEASURED |
| Code ZIP: agent architecture 30% (BTS) | deterministic core; model for intent and screenshots; E2 tool loop archived in `code/analysis/` | BTS May table: "Deterministic rule pipeline" best rank 17, avg 933; "single agent with tools" best 1 | EVIDENCE-SUPPORTED risk |
| Code ZIP: prompt and tool craft 30% (BTS) | prompts give role, label set, refusal (UNCLEAR / skip), injection rule, JSON shape; temperature 0, JSON mime type, local schema validation with retries; E2 tool schemas | `code/prompts/*.md`, `llm_client.py` | MEASURED (present) / JUDGMENT (quality) |
| Code ZIP: robustness 25% (BTS) | retries, backoff, key/model fallback, exit 4 on any model failure, fail-closed files, E2 `max_tool_calls` 8 | fail-closed 5/5; regex 44/45 + 1 dynamic; probes 5/5 | MEASURED |
| Code ZIP: rigor 15% (BTS) | modules, typed decision code, env secrets | 163/165 decision functions typed; 129 tests | MEASURED |
| README: judge ignores .json/.csv/.log (Oct README "Evaluation") | results quoted in Markdown | 15 audit results all quoted in shipped Markdown (rc8-P4) | MEASURED |
| Interview (BTS, help article) | `INTERVIEW_TRAINING_PACK.md` Q1–Q14 | covers the BTS probe order including monitoring, novelty, AI use (Q11–Q14 added in rc8) | JUDGMENT |
| Transcript (BTS) | `log.txt`, 20+ entries with tool= / model= | user prompts set architecture, constraints and rejections (Q14 quotes) | JUDGMENT |

## Ranked gap register

Ranking is by expected value = impact × confidence × P(safe fix) ÷ cost, judged qualitatively.

| ID | Criterion / source | Finding and evidence | Impact | Confidence | Experiment / action | Risk / cost | Acceptance or rollback | Status |
|---|---|---|---|---|---|---|---|---|
| G1 | Submission (Oct AGENTS.md §4.1) | Our AGENTS.md has `wwww.hackerrank.com`; upstream has `www.` | a broken link at submit time | MEASURED | use the upstream URL | none | — | REPORTED to user (AGENTS.md left unchanged) |
| G2 | Upstream `agent_trace_instructions.md` | asks for `agent-trace.zip` with the submission; the help page lists only 3 files | unknown; probably unscored ("improve our evaluation from the next time") | MEASURED (request) / UNKNOWN (scoring) | user decision; the local-machine sessions are on the user's computer | privacy: traces need redaction | — | USER DECISION |
| G3 | Help article timeline | about 5 h left at 07:20Z if the window is 24 h | prioritisation | JUDGMENT | finish rc8 or keep rc7, then submit | — | — | ACTED ON |
| G4 | Interview + Code ZIP credibility | shipped DESIGN.md line 3: "No production code yet"; §12 calls PNG content UNKNOWN | false statement in a shipped file | MEASURED | rc8-P5 docs fix | none to output (byte-identical) | gate passes; else keep rc7 | DONE in rc8 (gate) |
| G5 | Interview (BTS probe order; help article topics) | pack had no pitch, monitoring, novelty or AI-collaboration answers | interview 30% | EVIDENCE-SUPPORTED | Q11–Q14 from code and log facts | none (root file) | — | DONE |
| G6 | Code ZIP architecture 30% | deterministic pipeline; BTS: "Is this an agent, or a hardcoded workflow with LLM calls?" | possibly large | EVIDENCE-SUPPORTED | an agent loop in the decision path needs model calls; no key is configured; E2 measured 0/126 | high; blocked | — | BLOCKED; defend in interview (Q2) |
| G7 | Prompt craft 30% | no API-level `responseSchema`; any prompt or request change misses the content-addressed cache | small | JUDGMENT | — | needs model calls to regenerate | — | BLOCKED |
| G8 | Output: discovery (12 ATTRIBUTION misses) | timing-only rows in shared bursts | up to +12 sample steps | MEASURED | rc8-P1 capacity probe: 0 forced; extra assumption refuted by case_32 | wrong-owner risk | 0 wrong owners on samples | CLOSED (third failed line of attack) |
| G9 | Output: 3 NOT_DERIVABLE steps | ids only in sample_output.csv | none recoverable | MEASURED | rc8-P2 PDF + OCR search | — | — | CLOSED |
| G10 | Output: 6 pre-request D-SCOPE records | samples cannot separate the hypotheses (30/32 single-request, 0 exclusions) | ≤ 6 eval steps, either sign | case_45 EVIDENCE-SUPPORTED; case_34 JUDGMENT | rc8-P3 | change would be speculative | — | PRESERVED |
| G11 | Trace semantics | 26 resources owned by one case still labelled AMBIGUOUS for a rival case (37/904 eval pairs) | 0 output | MEASURED | none | rewrites 553 traces | — | DOCUMENTED |
| G12 | case_63 INCOMPLETE [] | empty COMPLETE proves nothing; NOT_STARTED → INCOMPLETE in 7/10 samples | 1 row | JUDGMENT | — | — | — | PRESERVED |

## Iteration rc8+1 (after rc8; no shipped change)

| ID | Hypothesis | Test | Result | Decision |
|---|---|---|---|---|
| H2 | The sample reference shows whether a screenshot should be cited (10 eval rows cite a PNG: cases 34, 35, 39, 45, 46, 50, 52, 53, 55, 61) | search every sample-reference resource id in the 4-pass OCR text of all 11 screenshots | 1 hit: case_15 `data_flow_9846`, whose reference step has evidence `""` (an INCOMPLETE future check). The screenshots otherwise concern eval cases or the earlier requests of multi-request users | UNTESTABLE on the samples (MEASURED). Keep rc7-X1 / rule C |
| H1 | The single rule-C miss (case_04 `account_004`: C cites `evidence_90`, the reference cites `evidence_54`) reveals a fallback rule | SQLite load of all 24 snapshots | `evidence_90` has an empty customer_profiles table and a snapshot_scope of `account_service` only. `evidence_54` holds primary and failover account tables (4 + 3 rows) and none of user_04's identifiers. case_04 is one of the hand-crafted sample cases (01–10). Eval exposure: 34 citations use the full-snapshot fallback; 7 of them cite an empty inventory (`evidence_66` case_452; `evidence_69` case_393; `evidence_81` 5 cases) | UNDECIDABLE: one sample with three confounded properties (empty, single-scope, replica coverage). No change (JUDGMENT) |

Stop rule: two consecutive attempts (H2, H1) produced no defensible improvement. The remaining gaps are blocked
(G6, G7: model calls needed, no key) or are user decisions (G1, G2, G3).

## Hardening pass rc9 (tag `rc9` → `318c159`; ledger round rc9 Q1–Q9)

| Item | Classification | Evidence |
|---|---|---|
| Row order | FIXED AND VERIFIED (no defect) | numeric = input order; validator, spec audit and test enforce it; grader collation UNKNOWN |
| case_63 `INCOMPLETE []` | VERIFIED BUT UNRESOLVED (participant decision) | the alternative COMPLETE `[]` changes 2 rows (case_04 sample → 32/32 status; case_63 eval); it overturns the approved M2(b) |
| 131 ambiguous accounts | VERIFIED BUT UNRESOLVED | 366 TIMING edges, 0 identity; AMBIGUOUS 131/131 |
| F1 (case_46) | VERIFIED BUT UNRESOLVED (JUDGMENT) | own detail page, no identifiers in the OCR text; 1 row |
| D-SCOPE 33/35/39/55 | FIXED AND VERIFIED (no defect; test added) | linked only by post-audit evidence_01 |
| D-SCOPE case_34 `audit_record_3753` | REQUIRES EXTERNAL OR HIDDEN INFORMATION | pre-request link; the samples cannot decide |
| Cache prompt versioning | FIXED AND VERIFIED (no defect; test added) | key includes the rendered-prompt hash; legacy reuse is restricted and recorded |
| `score_e1.py` crash | FIXED AND VERIFIED | runs again; E1 still rejected on the current code |
| E2 replay claim | FIXED AND VERIFIED | byte-identical at `63c7ecb`; a cache miss on the current code (documented) |
| Report/security timing lag | FIXED AND VERIFIED | 44/44 rows match |
| Architecture (agent loop) | REQUIRES EXTERNAL OR HIDDEN INFORMATION | no API key; rubric weight for October unpublished |

## Baseline vs best

| Metric | rc7 | rc8 candidate |
|---|---|---|
| output.csv sha256 | `ce35e3a5…` | identical |
| Sample status / precision / recall / sequence | 31/32 · 178/178 · 178/193 · 19/32 | identical |
| Eval rows changed | — | 0 |
| Tests | 129 | 129 |
| Shipped-doc defects known | 1 (DESIGN status and §12) | 0 known |
