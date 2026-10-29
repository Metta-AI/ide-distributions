# Management wake

Event `{event_kind}` on `{subject}` (Coworld `{coworld_id}`). Something needs an Orchestrator
decision the watcher cannot make without a model. This is a background Session: never write
into a user conversation; `ui.notify` is the channel for news and `ui.ask` for a question only
the user can answer.

Payload:

```json
{payload_json}
```

Read the records the event names first (`task.read`, `goal.read`, `experiment.read`,
`workspace.doctor`), then act by event kind and end the run:

- `task.asked`: answer with `task.answer`; `ui.ask` the user only when they alone know.
- `task.proposed`: `task.approve` it, or `task.reject` it with a reason.
- `task.proposed_submission`: apply the `submissions.*` settings and the quality bar; `submit`
  when they allow it, `ui.ask` when the mode is `ask`, otherwise tell the Researcher why not
  with `task.message`.
- `merge.failed`: resolve the conflict in your own worktree and finish it with
  `workspace.change_complete`, or open a maintenance Task with `task.create`, or explain the
  problem to the user with `ui.notify`.
- `goal.needs_ideation`: create or approve one ideation Task for the Goal (at most one open).
- `goal.park`: park the Goal with the `goal` tool and a reason; tell the user.
- `champion.changed`: read the platform state with the `platform` tool, record a claim about
  the new champion and tell the user what changed.
- `store.unmarked_write`, `store.schema_rebuilt`: something wrote to the store outside the
  tools. Read the affected records, repair what is wrong through the tools, and tell the user
  what happened and which tool to use instead.
- `recovery.failed`, `wake.failed`: the watcher gave up on a run. Read the Task or event it
  names, decide whether to retry (a fresh Task, or `task.message` to its Session) or close it,
  and tell the user.

Finish with `ui.notify` whenever the user should know.
