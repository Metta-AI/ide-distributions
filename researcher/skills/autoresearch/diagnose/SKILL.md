---
name: diagnose
description: Use when turning a named cause into hypotheses.
---

# Diagnose

This skill implements the hypothesis part of System Design §5.2 (investigation). It adapts
optimizer-seed `diagnose`. The agent chooses; no human picks. Run it after
`replay-diagnosis` has named a cause.

Doctrine upheld: D-05, D-07, D-08, D-10, D-22.

## Reads and writes

- Reads: Topics, evidence, claims (refuted mechanism claims first), hubs, the opponent and
  league hubs.
- Writes: hypotheses, links from Topics to hypotheses, and the journal.

## Procedure

1. Check the record first (D-10). Find refuted mechanism claims on the levers you will
   touch:
   `"$RESEARCH_CLI" records find claim --where status=refuted`
   `"$RESEARCH_CLI" records show <C-id>`
   A lever is closed unless your new evidence meets the claim's `revise_if`. Say which
   evidence does, or drop the idea.
2. Read the league meta hub and opponent hubs. A loss can come from the field, not from our
   policy. A "regression" with no change of ours is a scouting question.
3. Explain the signal in game terms: "the metric says X; in the episodes that is Y; here is
   the tick where it goes wrong" with `ar://evidence/…` refs.
4. Propose two to four varied, independent hypotheses. Cover stop (a bad behaviour fires),
   enable (a good behaviour is absent), and amplify (a working behaviour is unreliable). If
   refuting one would refute all, you proposed one hypothesis several times. Each hypothesis
   has:
   - a mechanism ("X happens because Y, which causes Z"), not a tweak;
   - a lever: the STRATEGY component that drives it;
   - a prediction that differs from what happens if it is false, with the metric that moves;
   - its Topics;
   - the evidence it rests on.
5. Create each one:
   `"$RESEARCH_CLI" records new hypothesis --meta-file .scratch/hypothesis.json --body-file .scratch/hypothesis.md --slug <slug>`
   with `{"statement": …, "prediction": …, "lever": …, "topics": ["T-…"], "source": ["ar://claim/C-…"], "state": "proposed"}`.
   Link it from its Topic when the card names the Topic:
   `"$RESEARCH_CLI" records link <T-id> hypotheses <H-id>`
6. Recommend one experimentation card for the best hypothesis (D-22) in `metadata.next`
   or a `suggestion:` comment, citing `ar://hypothesis/<H-id>` and `ar://topic/<T-id>`, the
   prediction and evidence. Do not design the experiment in this card.
7. Write the journal Decisions section: which hypotheses, why this order, and which levers
   you skipped as refuted. Finish with `card`.

## Failure handling

- The cause is not visible in the evidence: do not propose a behaviour change. Suggest an
  instrumentation card to the orchestrator (see `replay-diagnosis` step 7).
- Every idea matches a refuted lever: record that in the journal and suggest scouting or
  investigation on a different Topic to the orchestrator.
