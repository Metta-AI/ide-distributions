# Review wake

Event `{event_kind}` on `{subject}` (Coworld `{coworld_id}`). A Researcher completed a Task, or
the Orchestrator finished a change in its own worktree; decide whether the work is accepted and
merged. This is a background Session: never write into a user conversation; `ui.notify` is the
only channel to the user.

Payload:

```json
{payload_json}
```

Do this, then end the run:

1. `task.read` the Task with its checklist. Read the Experiment (`experiment.read`), the new
   claims (`claim.read`) and the Researcher's answers recorded on the Task. Judge the evidence,
   not the summary: the results against the champion, the decision rule, the replays named as
   evidence, whether the deliverables the checklist names exist.
2. Call `task.review {accept, merge, reason}`. Accept and merge when the work is sound and
   `main` stays healthy; accept without merge when the result is a finding, not a change;
   reject with a specific, actionable reason when something must be redone, and the same
   Researcher Session resumes with your reason.
3. The watcher merges accepted work mechanically and re-runs the mixin checks; a merge that
   conflicts or fails its checks comes back to you as a management wake. When the accepted
   Version is a submission candidate, `submit` applies the `submissions.*` settings and the
   quality bar; a user's earlier request to submit is carried out, never gated.
4. `ui.notify` the outcome in one or two sentences with the record references.
