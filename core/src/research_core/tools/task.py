"""The ``task`` tool (design B.4, B.12, B.13, C.2): the unit of work both roles act on.

The store module holds every rule; this module maps the flat tool arguments onto it and adds
the two things only the tool can do: ``claim`` creates the Task's worktree and registers the
Session, and ``complete`` checks that a given commit is on the Task branch. Stdlib only: it
runs inside Hermes (A3.1), so the git work goes through ``gitops`` (subprocess).

Messages. ``read`` adds the undelivered messages addressed to the caller's role and ``claim``
adds those for the Researcher. Neither marks them delivered: that is the watcher's, which
steers or resumes the holder's run with them (entry 5).
"""

from __future__ import annotations

from research_core import gitops, task_types
from research_core.store import sessions, tasks
from research_core.store.registry import TASK_STATUSES, TASK_TYPES, WAIT_KINDS
from research_core.tools import _args as a
from research_core.tools.base import ActionSpec, ToolCall, ToolError, ToolSpec, tool

ORCHESTRATOR = frozenset({"orchestrator"})
RESEARCHER = frozenset({"researcher"})
BOTH = frozenset({"orchestrator", "researcher"})

TASK = a.string("The Task, such as T-3.")
YES_NO = ["yes", "no"]
# Flat convenience arguments of ``complete`` that stand for checklist entries of the same name.
CHECKLIST_ARGS = (
    "claims",
    "experiment",
    "versions",
    "proposed_tasks",
    "summary",
    "submission_reason",
    "capability",
)

CREATE_PARAMS = {
    "goal": a.string("The Goal, such as G-3; only a maintenance Task may have none."),
    "type": a.string("research, improvement, ideation or maintenance.", enum=list(TASK_TYPES)),
    "title": a.string("Short name, for the agents."),
    "description": a.string("What to do and deliver, in Markdown."),
    "reasoning": a.string("Why, with references to the Goal, claims and Experiments."),
    "for_human": a.string("One plain sentence that clients show."),
    "end_condition": a.string(
        "How the Task ends: deliverable, steps or effort.",
        enum=["deliverable", "steps", "effort"],
    ),
    "end_detail": a.string("The detail of the end condition."),
    "experiment": a.string("The Experiment an improvement Task runs, such as X-2."),
    "priority": a.string("normal (default) or high.", enum=["normal", "high"]),
    "depends_on": a.strings('Tasks that must complete first, such as ["T-1"].'),
    "ideation_round": a.integer("Round number of an ideation Task."),
}


# --- handlers -----------------------------------------------------------------------------


def handle_create(call: ToolCall) -> dict:
    """``create`` (Orchestrator; accepted unless the type is gated) and ``propose`` (both)."""
    store, actor = call.ctx.store, call.ctx.actor
    goal = call.get("goal")
    if goal is None and call.get("type") != "maintenance":
        raise ToolError("goal is required unless type is maintenance")
    fields = {
        "type": call.get("type"),
        "title": call.get("title"),
        "description": call.get("description"),
        "goal_id": None if goal is None else a.record_number(goal, "goal", "goal"),
        "reasoning": call.get("reasoning") or "",
        "for_human": call.get("for_human") or "",
        "end_condition": call.get("end_condition"),
        "end_detail": call.get("end_detail"),
        "priority": call.get("priority") or "normal",
        "depends_on": a.record_numbers(call.get("depends_on"), "task", "depends_on"),
        "ideation_round": call.get("ideation_round"),
    }
    if call.get("experiment") is not None:
        fields["experiment_id"] = a.record_number(
            call.get("experiment"), "experiment", "experiment"
        )
    if call.action == "propose":
        return {"record": tasks.propose(store, actor, **fields)}
    return {"record": tasks.create(store, actor, cites=call.get("cites"), **fields)}


def handle_list(call: ToolCall) -> dict:
    goal = call.get("goal")
    records = tasks.list(
        call.ctx.store,
        status=call.get("status"),
        goal_id=None if goal is None else a.record_number(goal, "goal", "goal"),
        type=call.get("type"),
        held_by=call.get("held_by"),
    )
    return {"records": records}


def handle_claim(call: ToolCall) -> dict:
    """Take the lease: the worktree on ``task/<n>`` from main, the Session row, the Task.

    The run id comes from the Session row the watcher wrote (A3.7), never from the environment.
    The result carries the type's instructions and the messages waiting for the Researcher.
    """
    ctx = call.ctx
    session_id = ctx.hermes_session_id()
    if not session_id:
        raise ToolError(
            "claim needs the Hermes session id (IDE_SESSION_ID); a Task is held by a Session"
        )
    n = a.record_number(call.get("task"), "task", "task")
    coworld = ctx.coworld()
    if not (coworld.code / ".git").exists():
        raise ToolError(
            "the Coworld's code repository is not set up (code/ is not a git repository);"
            " the setup wake creates it, so ask the Orchestrator to run the Coworld setup"
        )
    branch = f"task/{n}"
    try:
        worktree = gitops.worktree_add(
            coworld.code, coworld.worktree_for_task(n), branch=branch, base="main"
        )
    except gitops.GitError as error:
        raise ToolError(
            f"could not create the worktree for {branch}: {error}",
            command=error.command,
            exit_code=error.exit_code,
            stderr_tail=error.stderr[-2000:],
        ) from None
    session = sessions.read(ctx.wstore, session_id)
    run_id = (session or {}).get("run_id") or ""
    record = tasks.claim(
        ctx.store, ctx.actor, n, run_id=run_id, worktree=str(worktree), branch=branch
    )
    sessions.register(
        ctx.wstore,
        ctx.actor,
        session_id=session_id,
        profile=ctx.env.get("HERMES_PROFILE_NAME") or ctx.env.get("IDE_ROLE") or ctx.role,
        kind="task",
        coworld_id=coworld.coworld_id,
        task_id=n,
        goal_id=record["goal_id"],
    )
    return {
        "record": record,
        "worktree": str(worktree),
        "branch": branch,
        "instructions": task_types.load(record["type"]).body,
        "pending_messages": tasks.pending_messages(ctx.store, n, "to_researcher"),
    }


def handle_complete(call: ToolCall) -> dict:
    """Finish the Task. The checklist is the ``checklist`` object plus the flat convenience
    arguments; a ``commit`` must be reachable from the Task branch."""
    ctx, store = call.ctx, call.ctx.store
    n = a.record_number(call.get("task"), "task", "task")
    checklist = dict(call.get("checklist") or {})
    for key in CHECKLIST_ARGS:
        if call.get(key) is not None:
            checklist.setdefault(key, call.get(key))
    commit = call.get("commit")
    if commit is not None:
        branch = tasks.read(store, n)["branch"]
        if not branch or not gitops.commit_on_branch(ctx.coworld().code, commit, branch):
            raise ToolError(
                f"commit {commit} is not on the Task branch {branch or '(none: not claimed)'};"
                " commit in the Task's worktree and pass that sha"
            )
    record = tasks.complete(
        store,
        ctx.actor,
        n,
        checklist=checklist,
        commit=commit,
        merge_request=call.get("merge"),
        merge_reason=call.get("merge_reason") or "",
        submission_candidate=call.get("submission_candidate"),
        result=call.get("result"),
        outcome=call.get("outcome") or "succeeded",
        failure=call.get("failure"),
    )
    return {"record": record}


def handle(call: ToolCall) -> dict:
    """Every action that takes one Task and maps straight onto the store."""
    ctx, store, actor = call.ctx, call.ctx.store, call.ctx.actor
    n = a.record_number(call.get("task"), "task", "task")
    if call.action == "read":
        direction = "to_orchestrator" if ctx.role == "orchestrator" else "to_researcher"
        return {
            "record": tasks.read(store, n),
            "pending_messages": tasks.pending_messages(store, n, direction),
        }
    if call.action == "approve":
        return {"record": tasks.approve(store, actor, n, cites=call.get("cites"))}
    if call.action == "link":
        deps = a.record_numbers(call.get("depends_on"), "task", "depends_on")
        return {"record": tasks.link(store, actor, n, deps)}
    if call.action == "answer":
        record = tasks.answer(
            store, actor, n, text=call.get("text"), question_id=call.get("question_id")
        )
        return {"record": record}
    if call.action == "message":
        record = tasks.message(store, actor, n, text=call.get("text"), direction="to_researcher")
        return {"record": record}
    if call.action == "abandon":
        return {"record": tasks.abandon(store, actor, n, failure=call.get("failure"))}
    if call.action == "review":
        record = tasks.review(
            store,
            actor,
            n,
            accept=call.get("accept"),
            merge=call.get("merge"),
            reason=call.get("reason"),
        )
        return {"record": record}
    if call.action == "reject":
        return {"record": tasks.reject(store, actor, n, next_step=call.get("next_step"))}
    if call.action == "wait":
        record = tasks.wait(
            store,
            actor,
            n,
            kind=call.get("kind"),
            detail=call.get("detail"),
            ref=call.get("ref"),
        )
        return {"record": record}
    if call.action == "ask":
        return {"record": tasks.ask(store, actor, n, question=call.get("question"))}
    if call.action == "release":
        return {"record": tasks.release(store, actor, n, reason=call.get("reason"))}
    if call.action == "propose_submission":
        version = a.record_number(call.get("version"), "version", "version")
        record = tasks.propose_submission(
            store, actor, n, version_id=version, reason=call.get("reason")
        )
        return {"record": record}
    raise AssertionError(call.action)


def dispatch(call: ToolCall) -> dict:
    if call.action in ("create", "propose"):
        return handle_create(call)
    if call.action == "list":
        return handle_list(call)
    if call.action == "claim":
        return handle_claim(call)
    if call.action == "complete":
        return handle_complete(call)
    return handle(call)


# --- the spec ---------------------------------------------------------------------------------

SPEC = tool(
    ToolSpec(
        name="task",
        description=(
            "Tasks: units of work under a Goal with a type checklist, a lease held by one "
            "Researcher Session, waits and messages. Goals and Tasks are the plan. Returns the "
            "updated record."
        ),
        handler=dispatch,
        roles=BOTH,
        executor="inproc",
        actions=(
            ActionSpec(
                "create",
                "Create an accepted Task (stored as proposed when its type needs the user's "
                "approval and no cites is given).",
                {
                    **CREATE_PARAMS,
                    "cites": a.string(
                        "The user's message that asked for it; lets a gated type through."
                    ),
                },
                ("type", "title", "description"),
                ORCHESTRATOR,
            ),
            ActionSpec(
                "propose",
                "Suggest a Task for the Orchestrator to approve or reject.",
                CREATE_PARAMS,
                ("type", "title", "description"),
            ),
            ActionSpec(
                "approve",
                "Accept a proposed Task so the watcher can dispatch it.",
                {
                    "task": TASK,
                    "cites": a.string(
                        "The user's message that asked for it; required for a gated type."
                    ),
                },
                ("task",),
                ORCHESTRATOR,
            ),
            ActionSpec(
                "link",
                "Make a Task wait for others to complete.",
                {"task": TASK, "depends_on": a.strings('Tasks it depends on, such as ["T-1"].')},
                ("task", "depends_on"),
                ORCHESTRATOR,
            ),
            ActionSpec(
                "answer",
                "Answer a Researcher's question; the Task's question wait resolves.",
                {
                    "task": TASK,
                    "text": a.string("The answer."),
                    "question_id": a.integer("Which question, when more than one is open."),
                },
                ("task", "text"),
                ORCHESTRATOR,
            ),
            ActionSpec(
                "message",
                "Send the Researcher a note; the watcher delivers it.",
                {"task": TASK, "text": a.string("The note.")},
                ("task", "text"),
                ORCHESTRATOR,
            ),
            ActionSpec(
                "abandon",
                "Drop an open Task, held or not; a held run is stopped by the watcher.",
                {
                    "task": TASK,
                    "failure": a.obj(
                        "{what_failed, next_step, error?, exit_code?, tried?}: why it is dropped."
                    ),
                },
                ("task", "failure"),
                ORCHESTRATOR,
            ),
            ActionSpec(
                "review",
                "Judge a succeeded Task: accept (and merge yes|no) or send it back.",
                {
                    "task": TASK,
                    "accept": a.boolean("true to accept, false to reopen with the reason."),
                    "merge": a.string(
                        "yes to merge the recorded commit, no to keep it.", enum=YES_NO
                    ),
                    "reason": a.string("The verdict's reason."),
                },
                ("task", "accept", "merge", "reason"),
                ORCHESTRATOR,
            ),
            ActionSpec(
                "reject",
                "Turn down a proposed Task.",
                {"task": TASK, "next_step": a.string("What to do instead.")},
                ("task", "next_step"),
                ORCHESTRATOR,
            ),
            ActionSpec(
                "claim",
                "Take the Task: its worktree is created and the instructions returned.",
                {"task": TASK},
                ("task",),
                RESEARCHER,
            ),
            ActionSpec(
                "wait",
                "Record what the Task waits on, then end the run; the watcher resumes it.",
                {
                    "task": TASK,
                    "kind": a.string(", ".join(WAIT_KINDS) + ".", enum=list(WAIT_KINDS)),
                    "detail": a.string("What exactly it waits for."),
                    "ref": {
                        "description": "A pid, a request id or a list of request ids to watch."
                    },
                },
                ("task", "kind", "detail"),
                RESEARCHER,
            ),
            ActionSpec(
                "ask",
                "Ask the Orchestrator a question and wait for the answer.",
                {"task": TASK, "question": a.string("The question.")},
                ("task", "question"),
                RESEARCHER,
            ),
            ActionSpec(
                "complete",
                "Finish the Task with its type's checklist (succeeded) or a failure record.",
                {
                    "task": TASK,
                    "outcome": a.string(
                        "succeeded (default) or failed.", enum=["succeeded", "failed"]
                    ),
                    "checklist": a.obj(
                        "The type's deliverables by key; flat arguments also count."
                    ),
                    "claims": {
                        "anyOf": [a.STRINGS, a.STRING],
                        "description": 'Claims recorded, such as ["C-3"], or "none: <reason>".',
                    },
                    "experiment": a.string("The Experiment run, such as X-2."),
                    "versions": a.strings('Versions tested, such as ["V-4"].'),
                    "proposed_tasks": a.strings('An ideation Task\'s proposals, such as ["T-7"].'),
                    "summary": a.string("A maintenance Task's summary of the change."),
                    "submission_reason": a.string("Why a Version is or is not a candidate."),
                    "capability": a.string("The capability a repair Task restored."),
                    "commit": a.string("The commit to merge; it must be on the Task branch."),
                    "merge": a.string("yes to ask for a merge, no with merge_reason.", enum=YES_NO),
                    "merge_reason": a.string("Why nothing is merged."),
                    "submission_candidate": a.string(
                        "Whether a tested Version should be submitted.", enum=YES_NO
                    ),
                    "result": a.string("What came of the Task."),
                    "failure": a.obj(
                        "{what_failed, next_step, error?, exit_code?, tried?} for outcome failed."
                    ),
                },
                ("task",),
                RESEARCHER,
            ),
            ActionSpec(
                "release",
                "Give the Task back unfinished.",
                {"task": TASK, "reason": a.string("Why.")},
                ("task", "reason"),
                RESEARCHER,
            ),
            ActionSpec(
                "propose_submission",
                "Propose a Version for submission; the Orchestrator applies the rules.",
                {
                    "task": TASK,
                    "version": a.string("The Version, such as V-4."),
                    "reason": a.string("Why it should be submitted."),
                },
                ("task", "version", "reason"),
                RESEARCHER,
            ),
            ActionSpec(
                "read",
                "Read a Task with its messages; adds the messages waiting for you.",
                {"task": TASK},
                ("task",),
            ),
            ActionSpec(
                "list",
                "List Tasks, filtered by status, Goal, type or holder.",
                {
                    "status": a.string(", ".join(TASK_STATUSES) + ".", enum=list(TASK_STATUSES)),
                    "goal": a.string("The Goal, such as G-3."),
                    "type": a.string("The Task type.", enum=list(TASK_TYPES)),
                    "held_by": a.string("The holding session id."),
                },
            ),
        ),
    )
)
