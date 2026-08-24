# Ox Alpha eval bundle - reproducibility

Everything needed to reproduce the controlled eval in the 2026-08-25 deep post.

## Files
- `eval_harness.py` - task definitions (7 coding + 4 reasoning + 1 tool),
  provider routing (Nous portal + opencode-go gateway + ZAI), and verdict
  generation. Coding verdicts come from ACTUALLY executing generated code
  against hidden tests in a subprocess, not the model's claim.
- `self_check_tests.py` - validates every hidden-test template against a
  known-good reference implementation (7/7 pass). Proves the tests are fair.
- `regrade.py` - re-grades saved evidence with the corrected rubric without
  re-calling any API, and writes an audit trail of original->corrected verdicts.
- `evidence/*.regraded.json` - raw per-model API responses + final verdicts.
- `evidence/_v1_unfixed_harness/` - the pre-correction run (grader bugs are
  documented in the post; these show the original 8/12 before the rubric fix).

## How to reproduce
1. `python3 eval_harness.py --model stealth/ox-alpha --tasks all` (and the other
   models) with valid keys in `~/.hermes/.env` (NOUS_API_KEY / OPENCODE_GO_API_KEY).
2. `python3 self_check_tests.py` - should print 7/7 OK.
3. `python3 regrade.py` against each `evidence/*_all.json`.

Requires: Python 3.11+, network to the named gateways. Keys are not committed.

## Method note (honesty)
The first harness run scored Ox Alpha 8/12; three of those "failures" were bugs
in OUR grader (LaTeX numbers, a wrong expected value, case-sensitivity), not Ox
Alpha failures. After correcting the rubric to accept valid alternate phrasings,
Ox Alpha is 11/12 and the corrected rubric was applied identically to ALL ten
models. The audit trail is preserved under _v1_unfixed_harness/ so the
correction is transparent, not hidden. Run date: 2026-08-24.