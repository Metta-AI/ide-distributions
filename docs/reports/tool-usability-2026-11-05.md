# Tool and skill usability, November 5, 2026

How easy the research tools and skills are to find, read and use for Kimi K3.1 Pro, after the repairs
in #188, #190 and #198. This is the run behind `core/tests/model/usability_baseline.json`: 905 runs,
$33.98. The shipped text and test code are those of `c0bd889b`. The file records `34e3800f`, which
differs only in server and image code. How to read a run is in
[`tool-usability-baseline-2026-11-04.md`](tool-usability-baseline-2026-11-04.md).

## The short version

- **Calls are valid: 819 of 821 acting calls on the first try**, against 786 of 813 on November 4.
- **Finding the right tool is the weak part, for the Researcher above all**: 604 of 625 for the
  Orchestrator and 228 of 280 for the Researcher. Many Researcher misses end at the limit of 8 requests.
- **Do not read the two runs as a before and after.** Their read results and fixtures differ. The
  controlled test below is the fair comparison.

| | Nov 4 | Nov 5 |
|---|---:|---:|
| Right tool and action, all | 831/880 | 832/905 |
| Orchestrator | 588/600 | 604/625 |
| Researcher | 243/280 | 228/280 |
| First try valid | 786/813 | 819/821 |
| Arguments as the rule says | 124/127 | 137/144 |

## The controlled test

A first run looked like a large drop in some situations. To test that, the same test code ran on `main`
without the repairs and with them, 10 to 60 runs per side, $79.94. Intervals are 95% Wilson. Measured
at `4b1760d0` for the first three rows and `962fe369` for the others. Fixes are in `c0bd889b`.

| Situation | Without repairs | With repairs | With fix |
|---|---|---|---|
| `r-wait-credits` | 29/30 [.83,.99] | 41/60 [.56,.79] | 46/60 [.65,.86] |
| `o-hard-better-not-survey` | 13/30 [.27,.61] | 4/30 [.05,.30] | 13/20 [.43,.82] |
| `r-hard-fail-args` | 10/30 [.19,.51] | 2/30 [.02,.21] | 8/20 [.22,.61] |
| `r-complete-failed` | 11/30 [.22,.54] | 6/30 [.10,.37] | 6/20 [.15,.52] |

- **`r-wait-credits` is still worse, and is not resolved.** After a credit refusal the model now often
  calls `task.read` first, and sometimes ends the Task as failed. The prompt edits cause it together:
  main's prompts restore it, and no single group of them does. The fix is not shown to help. Every
  rate here predates `95377a74`, which corrected its fixture: it showed V-12 not uploaded and a balance
  of 4 against a need of 2, which cannot go with a credit refusal. The corrected fixture is measured in [`tool-usability-2026-11-06.md`](tool-usability-2026-11-06.md).
- **`r-hard-fail-args` is ambiguous.** The prompt says to release when a directive forbids the only
  path, and this situation describes that, so `task.release` is arguably right.
- **The large `experiment.create` drops were the test's fault.** The fixture returned the very
  Experiment the situation asks to create, so updating it was sensible. Fixed in the test.
- **The other situations tested showed no real change** at n=10. Request-limit failures are about as
  common on both sides, partly because the fake `terminal` tool returns empty output and the model
  keeps probing. That has not been fixed.

## Reading

Comprehension was 655 of 675. All 20 misses were 4 questions, each wrong in all 5 runs, and each asked
about something the repairs changed on purpose. Those questions are fixed in the test. They are
look-up questions with the text in front of the model, so a pass shows little.

Asked to review each text, the model named contradictions in 12 reviews across 10 texts. Those feed
the next repair.

## Limits

No live workspace was involved: the tools answer from fixtures. The controlled test covers about 20
situations of 181, chosen for looking worst, so other small shifts could exist. Five runs per
situation can't show small changes in any one situation.
