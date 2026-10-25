# Kit heuristic estimate — NOT an official HackerRank score

Produced by `python -m orchestrate_kit score --no-history --python python --transcript log.txt` (orchestrate-kit
1.0.0, a fresh clone at its commit ccb175f) on 2026-10-25, against commit ad4d146 (rc6 candidate). The rc5 run (commit
4aff96c) gave Code ZIP 82.4; rc6 adds `attribution.page_scope` and `code/analysis/dscope_review.py`, and the heuristic
reads 82.3. Its sub-scores are architecture 100, craft 100, robustness 60, rigor 49 (weights 30/30/25/15, giving about 82.35), so the 0.1 change is below the precision of the sub-scores it prints. The kit itself says: "ESTIMATED, not official. No public ground-truth
graded Orchestrate submission exists to calibrate any of these scorers against."

Labels: **MEASURED** (a number this repository computed from its own outputs), **ESTIMATED** (a kit heuristic),
**NOT CHECKED** (the kit could not run the check, or nothing local can measure it).

## Kit sections

| Section (official weight) | Kit result | Label | Notes |
|---|---|---|---|
| Code ZIP (30%) | 82.3 / 100 | ESTIMATED | One finding, disputed below |
| Output CSV (30%) | 100.0 / 100 | ESTIMATED, mostly NOT CHECKED | The kit marks it "measured", but only 1 of its 4 audits ran; spec conformance, evidence quality and output sanity "could not run" (they expect a different challenge's files) |
| AI Judge Interview (30%) | UNKNOWN | NOT CHECKED | No interview result exists locally |
| AI Chat Transcript (10%) | 100.0 / 100 | ESTIMATED | The kit counted "1 turn(s)" and 7/7 evidence-chain nodes, "not in a plausible order"; `transcript analyze` PASS |
| Overall | UNKNOWN (64.71 points measured from 70% of the weight) | ESTIMATED | The interview is missing and is not counted as zero |

**Weakest known section: Code ZIP (82.3, ESTIMATED).** The interview (30%) is unmeasured and could be weaker.

## Our own measurements (not from the kit)

| Check | Result | Label |
|---|---|---|
| Sample status accuracy | 31/32 | MEASURED |
| Sample step precision / recall | 178/178 / 178/193 | MEASURED |
| Sample sequence accuracy | 19/32 | MEASURED |
| Custom, category and adversarial cases | 19/19 pass | MEASURED |
| Literal spec audit (`code/evaluation/spec_audit.py`) | 0 violations; negative control 18/18 caught (rc5 added the numeric case_id order check) | MEASURED |
| Coupling probes (`code/evaluation/coupling_probes.py`) | 5/5 pass | MEASURED |
| Security audit (`code/evaluation/security_audit.py`) | product regexes < 2 s on 1 MB lines (worst 0.50 s); hostile-file fixtures 5/5 | MEASURED |
| Determinism | 3 processes (PYTHONHASHSEED 1/2/3, the third keyless), 553 files byte-identical | MEASURED |
| Clean-folder reproduction (`code/evaluation/clean_room.py`) | output.csv byte-identical, same metrics | MEASURED |
| Eval-set correctness (518 rows) | UNKNOWN: no labels exist for the eval set | NOT CHECKED |

## Disputed kit findings

- **"no retry or iteration-cap pattern found" (Code ZIP robustness).** The regex `\bretry\w*\b` needs a word boundary
  before "retry", so it cannot see `schema_retries` or `max_429_retries_per_attempt`. Both exist and are enforced:
  `code/agent/llm_client.py:204` (`for schema_try in range(limits["schema_retries"] + 1)`, the validator's error fed
  back) and `:246` (429 retries with exponential backoff capped at 60 s), configured in `code/config/models.yaml`
  `limits`. Round 6 added the named constants `MAX_SCHEMA_RETRIES`, `MAX_HTTP_RETRIES` and `MAX_BACKOFF_SECONDS` (used at both call sites) for readability; the heuristic still reports the finding, because it matches only `max_retries` or words starting with "retry". Not renamed further: changing names to satisfy a regex would game the heuristic, not improve robustness.
- **README ↔ Code "claims agent loop, rate limit, no corresponding evidence".** The README says there is *no*
  free-form agent loop (E2 was measured and removed); the keyword match ignores the negation. The rate limit is the
  client-side throttle `LLMClient._throttle` (`requests_per_minute` per model in `models.yaml`).
- **Code ↔ Transcript "claims agent loop, no corresponding evidence found in code".** Same negation: the transcript
  describes the E2 loop as built, measured (0/126 accepted) and removed; its code is archived in `code/analysis/`.
- **Output CSV 100 "measured".** Three of the four underlying audits did not run; the 100 should not be read as a
  measured CSV score. The repository's own spec audit is the measured conformance check.
