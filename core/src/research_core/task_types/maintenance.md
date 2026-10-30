```json
{
  "required": [],
  "checklist": [
    {
      "key": "commit",
      "text": "the committed change on the Task branch (a git sha)",
      "pattern": "^[0-9a-f]{7,40}$"
    },
    {
      "key": "summary",
      "text": "a summary of the change",
      "pattern": "\\S"
    },
    {
      "key": "capability",
      "text": "the capability this repair restores (its check was rerun)",
      "pattern": "^\\S+$",
      "required_when": "capability"
    }
  ]
}
```

# Maintenance Task

Deliver a committed change on your Task branch and a summary; never a Version. A maintenance
Task fixes the workspace: a broken capability, a mixin command, a toolchain pin, a document.
Policy behaviour belongs to an improvement Task.

Start: read the Task, its Goal when it has one (a repair may have none), the directives, the
briefing and these instructions; load `repair` and `environment`. `workspace.capabilities`
shows what the last check found; the Task's `capability` field names the one to restore, and
the failure result carries the log path, output and exit code.

Work: reproduce the failure first, from the log and the mixin's command in `MIXIN.md`. Fix the
cause, not the symptom: a missing toolchain is pinned in the `MIXIN.md` toolchains; a changed
API is followed in the mixin's tool; a check that fails for a real defect is fixed in the code
it checks. Rerun the command yourself and, for a repair, `workspace.capabilities {check: true}`
so the watcher reruns the check next tick; the capability turns `ok` when it passes. Commit a
small change with a message saying what broke and why this fixes it. Game-specific build or
evaluate details: see the mixin's docs.

Ask (`task.ask`) when two fixes differ in what the user would want (upgrade or pin a runtime);
decide everything else.

End: `task.complete {task, commit, summary, capability, merge: "yes", result}`. `commit` is on
your branch; `capability` is required for a repair. The change merges after review once build
and check pass again.

When it fails, `task.complete {outcome: "failed", failure}` with `what_failed` and `next_step`,
plus `error`, `exit_code` and `tried` when they exist. Good: what_failed "the build failed on
nim 2.2: undefined symbol X", next_step "pin nim 2.0 in MIXIN.md toolchains or patch
tools/build.sh line 12". Bad: what_failed "it broke", next_step "try again".
