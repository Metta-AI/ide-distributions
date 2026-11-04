"""Executable bounded contract, independent of coordinator implementation.

Explore every ordering of acceptance, outcome, receipt, pause, owner crash and handoff.
Messages can be repeated or delivered after a successor starts. Crash boundaries stutter
unless a durable commit occurred. Refinement against SQLite lives in test_transition_contract.
Liveness is conditional on eventual observation/recovery and availability; pause is intentional.
"""

from collections import deque
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Attempt:
    phase: str = "queued"
    native: str = "absent"
    generation: int = 0
    excluded: bool = False
    receipt: bool = False
    outcome: str = ""
    disposition: str = ""


@dataclass(frozen=True)
class Work:
    attempts: tuple[Attempt, ...] = (Attempt(),)
    holder: int = 0
    outcome: str = ""


@dataclass(frozen=True)
class Model:
    tasks: tuple[Work, ...]
    paused: bool = False
    generation: int = 0


def actions(state, budget):
    yield "pause/resume gate", replace(state, paused=not state.paused)
    # Abstract two runtime labels; exclusion is a monotone fact, never label equality alone.
    retired = []
    for task in state.tasks:
        attempts = tuple(
            replace(a, excluded=True, native="done", outcome=a.outcome or "interrupted")
            if a.phase != "queued" and a.phase != "settled"
            else a
            for a in task.attempts
        )
        retired.append(replace(task, attempts=attempts))
    yield (
        "exclude old private namespace",
        replace(state, generation=1 - state.generation, tasks=tuple(retired)),
    )
    for task_id, task in enumerate(state.tasks):

        def update(attempt_id, attempt, task=task, task_id=task_id, **fields):
            attempts = list(task.attempts)
            attempts[attempt_id] = attempt
            work = replace(task, attempts=tuple(attempts), **fields)
            tasks = list(state.tasks)
            tasks[task_id] = work
            return replace(state, tasks=tuple(tasks))

        current = len(task.attempts) - 1
        for attempt_id, attempt in enumerate(task.attempts):
            prefix = f"task {task_id}, attempt {attempt_id}: "
            # Before commit/notification and duplicate wakeup: state unchanged.
            yield prefix + "crash or duplicate notification", state
            if attempt.phase == "queued" and not state.paused and attempt_id == task.holder:
                yield (
                    prefix + "commit admission",
                    update(
                        attempt_id, replace(attempt, phase="starting", generation=state.generation)
                    ),
                )
            if attempt.phase == "starting" and attempt.native == "absent" and not attempt.excluded:
                yield (
                    prefix + "native acceptance, reply lost",
                    update(attempt_id, replace(attempt, native="active")),
                )
            if attempt.native == "active":
                for result in ("completed", "failed"):
                    yield (
                        prefix + "native " + result,
                        update(attempt_id, replace(attempt, native="done", outcome=result)),
                    )
            if attempt.native != "absent":
                # Delayed receipt attaches evidence; terminal state remains absorbing.
                phase = "running" if attempt.phase == "starting" else attempt.phase
                yield (
                    prefix + "receipt (possibly delayed/duplicate)",
                    update(attempt_id, replace(attempt, receipt=True, phase=phase)),
                )
            if attempt.native == "done" and attempt.phase in {"starting", "running"}:
                yield (
                    prefix + "observe outcome before/after receipt",
                    update(attempt_id, replace(attempt, phase="settled")),
                )
            if attempt.phase in {"starting", "running"}:
                yield (
                    prefix + "native status missing",
                    update(attempt_id, replace(attempt, phase="uncertain")),
                )
            if attempt.phase == "uncertain" and attempt.excluded:
                yield (
                    prefix + "recover namespace exclusion",
                    update(
                        attempt_id,
                        replace(attempt, phase="settled", native="done", outcome="interrupted"),
                    ),
                )
            if attempt.phase == "running" and attempt_id == task.holder and attempt_id == current:
                yield (
                    prefix + "worker reports task success",
                    update(attempt_id, attempt, outcome="succeeded"),
                )
            if attempt_id != current or attempt.phase != "settled" or attempt.disposition:
                continue
            if task.outcome:
                yield (
                    prefix + "acknowledge terminal task",
                    update(attempt_id, replace(attempt, disposition=task.outcome)),
                )
            elif len(task.attempts) == budget:
                yield (
                    prefix + "exhaust bounded recovery",
                    update(attempt_id, replace(attempt, disposition="failed"), outcome="failed"),
                )
            elif not state.paused:
                attempts = (
                    *task.attempts[:-1],
                    replace(attempt, disposition="successor queued"),
                    Attempt(),
                )
                tasks = list(state.tasks)
                tasks[task_id] = replace(task, attempts=attempts)
                yield prefix + "atomic successor and handoff", replace(state, tasks=tuple(tasks))
        if task.holder != current:
            predecessor = task.attempts[task.holder]
            assert predecessor.phase == "settled"
            tasks = list(state.tasks)
            tasks[task_id] = replace(task, holder=current)
            yield f"task {task_id}: commit holder transfer", replace(state, tasks=tuple(tasks))


def invariant(previous, state, budget):
    for task_id, task in enumerate(state.tasks):
        assert len(task.attempts) <= budget
        authoritative = [
            i
            for i, a in enumerate(task.attempts)
            if a.phase in {"starting", "running", "uncertain"}
        ]
        assert len(authoritative) <= 1  # uncertainty reserves authority too
        for i, a in enumerate(task.attempts):
            if a.phase == "settled":
                assert a.outcome and a.native == "done"
            elif a.phase == "queued":
                assert (
                    i == len(task.attempts) - 1
                )  # coordinator owes admission, or pause awaits resume
            elif a.phase == "uncertain":
                assert a.generation in {0, 1}  # supervisor owes namespace exclusion
            else:
                assert a.phase in {"starting", "running"}  # exact attempt owns native execution
            if a.disposition == "successor queued":
                assert i + 1 < len(task.attempts)
            if i < len(previous.tasks[task_id].attempts):
                old = previous.tasks[task_id].attempts[i]
                if old.phase == "settled":
                    assert a.phase == "settled"
                if previous.paused and old.phase == "queued":
                    assert a.phase == "queued"  # pause closes the sole admission edge
        if task.outcome:
            assert task.outcome in {"succeeded", "failed"}
        if previous.tasks[task_id].outcome:
            assert task.outcome == previous.tasks[task_id].outcome


def explore(tasks, budget):
    initial = Model(tuple(Work() for _ in range(tasks)))
    pending = deque([initial])
    seen = {initial}
    reverse = {initial: set()}
    terminal = set()
    edges = 0
    while pending:
        state = pending.popleft()
        if all(
            task.outcome and all(a.phase == "settled" and a.disposition for a in task.attempts)
            for task in state.tasks
        ):
            terminal.add(state)
        for action, successor in actions(state, budget):
            invariant(state, successor, budget)
            edges += 1
            reverse.setdefault(successor, set()).add(state)
            if successor not in seen:
                assert len(seen) < 300_000, (action, "model bound unexpectedly exceeded")
                seen.add(successor)
                pending.append(successor)
    # Every state has a defined recovery route to an explicit disposition. No accidental sink.
    recoverable = set(terminal)
    pending = deque(terminal)
    while pending:
        for state in reverse[pending.popleft()]:
            if state not in recoverable:
                recoverable.add(state)
                pending.append(state)
    assert recoverable == seen
    return len(seen), edges


def test_all_orderings_one_task_two_attempts():
    states, edges = explore(tasks=1, budget=2)
    assert states > 100 and edges > states * 3


def test_two_tasks_do_not_share_execution_authority():
    states, edges = explore(tasks=2, budget=1)
    assert states > 1000 and edges > states * 3
