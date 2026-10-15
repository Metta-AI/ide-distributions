"""Self-monitoring signals: stuck and thrash detection (System Design §8; defaults §10).

``compute_signals`` runs in queue-keeper each run. It reads the records and the ledger in
canonical, the queue, the tool event log and the preflight result, and returns the signals
that fire now. The same inputs give the same signals. The briefing shows them; the agent
changes course, explains, or sends a help request (System Design §5.9).

Iterations. An iteration is one card run that ended with ``card finish``, that is, one
handoff record in ``.supervisor/ledger/handoffs/``. Persistence and "N iterations" thresholds
count iterations, not queue-keeper runs.

Persistence (disposable): ``.supervisor/runtime/signals.json`` holds, per signal key
(``<code>`` or ``<code>:<entity>``), when it was first seen and the iteration count then, plus
samples of the Pending card count for queue growth. A signal that stops firing is dropped.
``Signal.count`` is 1 in the iteration it first fires and grows by one per iteration.

Escalation. A signal that still fires ``escalation_iterations`` (T) more iterations after it
first fired (``count > T``) adds an ``escalation`` signal for its key and publishes ONE
runtime ``help_request`` per episode of that key (an episode starts when the signal first
fires and ends when it stops). The committed marker
``.supervisor/ledger/escalations/<key>@<first_seen>.json`` dedupes it across runs; when
``signals.json`` is lost (host rebuilt), a still-firing signal continues its latest
escalated episode from that marker instead of escalating again. A signal that clears and
returns is a new episode and can escalate again. A crash between publication and the marker
commit is caught by looking for the key and episode in the outbox (``runtime_key``) first. Identity mismatch is not escalated: it is a hard stop
that preflight already answers with a pause and a help request (C6).

Signals and their data sources:

- ``spinning``: the last N handoffs created or changed no record besides their journal
  (handoff ``report.created`` / ``report.changed_existing``).
- ``thrash``: an experiment not yet reviewed whose ``lever`` equals the ``lever`` of a
  refuted mechanism claim. Whether new evidence meets the claim's ``revise_if`` is prose, so
  the signal fires and the agent answers it (explain, or change course).
- ``plateau``: M experiments reviewed since the last published promotion, or D days since
  it (since the canonical root commit when nothing was ever promoted).
- ``repeated_tool_error``: the same command and error signature (System Design §8) failed in
  3 card runs (iterations) within ``tool_error_window``, from ``runtime/tool_events.jsonl``
  (the plugin's post_tool_call log). The command is ``cmd_signature`` (setup such as
  ``cd ... &&`` skipped); the error signature is ``error_signature`` (terminal: ``exit <code>``
  plus ``timeout`` or the autoresearch CLI's ``reason``; other tools: the masked first line of
  the error), else the error type. Retries inside one run count once: Hermes's own loop guard
  answers those. Not errors: exit 1 of the program the exit code came from (the last command
  of the line) when its 1 means "no match" or "false" (``grep``, ``test``, ``diff``, ...),
  and an autoresearch CLI envelope with ``problems`` (validation feedback the card fixes).
- ``long_iterations``: the last 3 journals (by ``started``) each ran over 45 minutes.
- ``stale_plan``: ``plan.md`` unchanged for 6 iterations while no experiment was reviewed
  and no promotion published.
- ``queue_growth``: the Pending count never fell over the last D' days and ends higher than
  it started (samples kept in ``signals.json``). queue-keeper then requests planning
  (``planning_request(growth=True)``).
- ``stale_card``: a Pending card admitted more than X days ago (receipt history).
- ``merge_hotspot``: 2 reconciliations (handoff ``overlap``) on one entity among the last
  ``merge_hotspot_window`` handoffs.
- ``escalation``: see above.
- ``identity_mismatch``: preflight's identity problems (C6).
- ``credits_exhausted``: preflight read a balance of 0 or less (A10), or an experiment is
  ``awaiting-credits`` (a CreditDenied moves the experiment there).
- ``keeper_step_failed``: a queue-keeper step raised (``step_failures``, step name -> error);
  queue-keeper contains the failure and passes it here, so a step that keeps failing escalates
  like any other signal.

TODO (no data source yet; these return nothing):

- ``build_failure_loop`` (K consecutive build or gate failures on one component): needs
  per-component build and gate results, which the Plan 02 build path and gates will record
  (``version.gates`` has no failure format yet).
- ``plateau``'s "no leaderboard gain" half: no leaderboard history is recorded yet, so
  plateau uses promotions only.
"""

import datetime
import itertools
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from autoresearch.comms import runtime_messages
from autoresearch.comms.publisher import already_published, publish_message
from autoresearch.io import gitops
from autoresearch.io.docs import read_json, write_json
from autoresearch.paths import Layout
from autoresearch.queue.base import Queue
from autoresearch.records.store import RecordStore
from autoresearch.runctx import RunContext, runtime_dir
from autoresearch.runtime.ledger import canonical_lock, commit, committed_json, ledger_of, now_iso
from autoresearch.runtime.preflight import PreflightResult
from autoresearch.runtime.promotion import HISTORY_DIR_REL

SPINNING = "spinning"
BUILD_FAILURE_LOOP = "build_failure_loop"
THRASH = "thrash"
PLATEAU = "plateau"
REPEATED_TOOL_ERROR = "repeated_tool_error"
LONG_ITERATIONS = "long_iterations"
STALE_PLAN = "stale_plan"
QUEUE_GROWTH = "queue_growth"
STALE_CARD = "stale_card"
MERGE_HOTSPOT = "merge_hotspot"
ESCALATION = "escalation"
IDENTITY_MISMATCH = "identity_mismatch"
CREDITS_EXHAUSTED = "credits_exhausted"
KEEPER_STEP_FAILED = "keeper_step_failed"
NOT_ESCALATED = frozenset({ESCALATION, IDENTITY_MISMATCH})

STATE_FILE = "signals.json"
TOOL_EVENTS_FILE = "tool_events.jsonl"  # written by plugin.adapters.post_tool_call
OK_TOOL_STATUSES = frozenset({None, "", "ok", "success", "completed"})
# Exit status 1 of these programs is an answer (no match, false, files differ), not a failure.
EXIT_1_ANSWERS = frozenset({"grep", "egrep", "fgrep", "rg", "test", "[", "diff", "cmp"})
OPEN_EXPERIMENT_STATES = frozenset({"designed", "built", "uploaded", "evaluating"})
SAMPLE_EVERY = datetime.timedelta(hours=1)


@dataclass(frozen=True)
class SignalConfig:
    """System Design §10 defaults (they assume a 10-minute iteration cadence). The two
    ``*_window`` values are runtime choices the design leaves open."""

    spinning_iterations: int = 6
    build_failure_k: int = 3
    plateau_experiments: int = 8
    plateau_days: int = 2
    tool_error_repeats: int = 3
    tool_error_window: datetime.timedelta = datetime.timedelta(hours=2)
    long_iteration: datetime.timedelta = datetime.timedelta(minutes=45)
    long_iteration_run: int = 3
    stale_plan_iterations: int = 6
    queue_growth_days: int = 2
    stale_card_days: int = 3
    merge_hotspot_conflicts: int = 2
    merge_hotspot_window: int = 20
    escalation_iterations: int = 3


@dataclass(frozen=True)
class Signal:
    code: str
    entity: str | None
    detail: str
    first_seen: str  # ISO time it first fired (this persistence run)
    count: int  # iterations it has fired: 1 in the first

    @property
    def key(self) -> str:
        return self.code if self.entity is None else f"{self.code}:{self.entity}"

    def line(self) -> str:
        """One line for a briefing (``BriefingInputs.signals``)."""
        return f"{self.key} (iteration {self.count}): {self.detail}"


@dataclass(frozen=True)
class _Raw:
    code: str
    entity: str | None
    detail: str

    @property
    def key(self) -> str:
        return self.code if self.entity is None else f"{self.code}:{self.entity}"


@dataclass
class _Inputs:
    """Everything the detectors read, loaded once per call."""

    ctx: RunContext
    queue: Queue
    now: datetime.datetime
    cfg: SignalConfig
    preflight: PreflightResult | None
    step_failures: dict[str, str] = field(default_factory=dict)
    handoffs: list[dict] = field(default_factory=list)  # oldest first
    experiments: list[dict] = field(default_factory=list)
    state: dict = field(default_factory=dict)


# --- small readers ---


def _time(value) -> datetime.datetime | None:
    if isinstance(value, datetime.datetime):
        moment = value
    elif isinstance(value, str) and value:
        try:
            moment = datetime.datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=datetime.UTC)


def _handoffs(canonical: Path) -> list[dict]:
    records = [read_json(path) for path in (ledger_of(canonical) / "handoffs").glob("*/*.json")]
    records = [r for r in records if _time(r["history"][0]["at"]) is not None]
    return sorted(records, key=lambda r: (_time(r["history"][0]["at"]), r["card"]))


def _records(canonical: Path, entity: str) -> list[dict]:
    store = RecordStore(Layout(canonical))
    return [store.load(record_id)[0] for record_id in store.find(entity)]


def _published_promotion_times(canonical: Path) -> list[datetime.datetime]:
    times = []
    for path in (Path(canonical) / HISTORY_DIR_REL).glob("P-*.json"):
        record = read_json(path)
        if record.get("stage") == "published" and _time(record.get("updated_at")):
            times.append(_time(record["updated_at"]))
    return sorted(times)


def _git_time(canonical: Path, *args: str) -> datetime.datetime | None:
    try:
        output = gitops.git(canonical, "log", "--format=%cI", *args)
    except gitops.GitError:
        return None
    lines = output.splitlines()
    return _time(lines[0]) if lines else None


def _decided_at(experiment: dict) -> datetime.datetime | None:
    return _time((experiment.get("decision") or {}).get("decided_at"))


def _changed_records(handoff: dict) -> list[str]:
    report = handoff.get("report") or {}
    ids = [*report.get("created", []), *report.get("changed_existing", [])]
    return [record_id for record_id in ids if not str(record_id).startswith("J-")]


# --- detectors: each returns the raw signals that fire now ---


def _spinning(inputs: _Inputs) -> list[_Raw]:
    n = inputs.cfg.spinning_iterations
    recent = inputs.handoffs[-n:]
    if len(recent) < n or any(_changed_records(handoff) for handoff in recent):
        return []
    return [_Raw(SPINNING, None, f"the last {n} iterations changed no record")]


def _build_failure_loop(inputs: _Inputs) -> list[_Raw]:
    del inputs  # TODO: no per-component build/gate results yet (module docstring)
    return []


def _thrash(inputs: _Inputs) -> list[_Raw]:
    refuted = {}
    for claim in _records(inputs.ctx.canonical, "claim"):
        mechanism = claim.get("kind") == "mechanism" and claim.get("lever")
        if mechanism and claim.get("status") == "refuted":
            refuted.setdefault(claim["lever"], claim["id"])
    found = []
    for experiment in inputs.experiments:
        lever = experiment.get("lever")
        if experiment.get("status") in OPEN_EXPERIMENT_STATES and lever in refuted:
            # No ids in the text: the experiment is the signal's entity (a ref when it
            # escalates), and the refuted claim is the one on this lever.
            detail = (
                f"an open experiment acts on lever {lever}, which a refuted mechanism claim "
                f"covers; it needs new evidence that meets the claim's revise_if"
            )
            found.append(_Raw(THRASH, experiment["id"], detail))
    return found


def _plateau(inputs: _Inputs) -> list[_Raw]:
    canonical, cfg = inputs.ctx.canonical, inputs.cfg
    promoted = _published_promotion_times(canonical)
    since = promoted[-1] if promoted else None
    completed = [
        e
        for e in inputs.experiments
        if e.get("status") == "reviewed"
        and (since is None or _decided_at(e) is None or _decided_at(e) > since)
    ]
    what = "the last promotion" if since else "the start (nothing promoted yet)"
    if len(completed) >= cfg.plateau_experiments:
        return [_Raw(PLATEAU, None, f"{len(completed)} experiments reviewed since {what}")]
    base = since or _git_time(canonical, "--reverse", "--max-parents=0", "HEAD")
    if base is not None and inputs.now - base > datetime.timedelta(days=cfg.plateau_days):
        days = (inputs.now - base).days
        return [_Raw(PLATEAU, None, f"{days} days without a promotion since {what}")]
    return []


def _tool_events(inputs: _Inputs) -> list[dict]:
    path = runtime_dir(inputs.ctx) / TOOL_EVENTS_FILE
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _tool_error(event: dict) -> str | None:
    """The error signature of a failed tool event, or None when it is not a failure."""
    error = event.get("error_type") or (
        None if event.get("status") in OK_TOOL_STATUSES else event.get("status")
    )
    if not error:
        return None
    signature = event.get("error_signature")
    if isinstance(signature, str) and signature.endswith(" problems"):
        return None  # validation feedback: the card reads the problems and fixes them
    program = event.get("exit_program")
    if "exit_program" not in event:  # events written before exit_program was recorded
        program = str(event.get("cmd_signature") or "").removeprefix("terminal: ").split(" ")[0]
    if event.get("exit_code") == 1 and program in EXIT_1_ANSWERS:
        return None
    return signature or error


def _repeated_tool_error(inputs: _Inputs) -> list[_Raw]:
    cutoff = (inputs.now - inputs.cfg.tool_error_window).timestamp()
    runs: dict[str, set] = {}
    for event in _tool_events(inputs):
        if not isinstance(event.get("ts"), int | float) or event["ts"] < cutoff:
            continue
        error = _tool_error(event)
        if error:
            command = event.get("cmd_signature") or event.get("tool")
            key = f"{command} [{error}]"
            runs.setdefault(key, set()).add((event.get("task"), event.get("run")))
    return [
        _Raw(REPEATED_TOOL_ERROR, key, f"{key} failed in {len(found)} card runs recently")
        for key, found in sorted(runs.items())
        if len(found) >= inputs.cfg.tool_error_repeats
    ]


def _long_iterations(inputs: _Inputs) -> list[_Raw]:
    cfg = inputs.cfg
    timed = []
    for journal in _records(inputs.ctx.canonical, "journal"):
        started, ended = _time(journal.get("started")), _time(journal.get("ended"))
        if started and ended:
            timed.append((started, ended - started))
    recent = [duration for _, duration in sorted(timed)[-cfg.long_iteration_run :]]
    if len(recent) < cfg.long_iteration_run or any(d <= cfg.long_iteration for d in recent):
        return []
    minutes = ", ".join(str(int(d.total_seconds() // 60)) for d in recent)
    return [_Raw(LONG_ITERATIONS, None, f"the last iterations took {minutes} minutes")]


def _stale_plan(inputs: _Inputs) -> list[_Raw]:
    canonical, cfg = inputs.ctx.canonical, inputs.cfg
    changed = _git_time(canonical, "-1", "--", "plan.md")
    if changed is None:
        return []
    since = [h for h in inputs.handoffs if _time(h["history"][0]["at"]) > changed]
    if len(since) < cfg.stale_plan_iterations:
        return []
    reviewed = any(
        e.get("status") == "reviewed" and (_decided_at(e) or changed) > changed
        for e in inputs.experiments
    )
    promoted = any(moment > changed for moment in _published_promotion_times(canonical))
    if reviewed or promoted:
        return []
    detail = f"plan.md unchanged for {len(since)} iterations with no review or promotion"
    return [_Raw(STALE_PLAN, None, detail)]


def _queue_growth(inputs: _Inputs) -> list[_Raw]:
    """Sample the Pending count (kept in the state) and judge the last D' days."""
    now, window = inputs.now, datetime.timedelta(days=inputs.cfg.queue_growth_days)
    pending = len(inputs.queue.list("pending"))
    samples = [(_time(at), count) for at, count in inputs.state.get("pending_samples", [])]
    samples = [(at, count) for at, count in samples if at is not None and at >= now - 2 * window]
    if not samples or now - samples[-1][0] >= SAMPLE_EVERY or samples[-1][1] != pending:
        samples.append((now, pending))
    inputs.state["pending_samples"] = [[at.isoformat(), count] for at, count in samples]
    older = [index for index, (at, _) in enumerate(samples) if at <= now - window]
    if not older:
        return []
    series = [count for _, count in samples[older[-1] :]]
    rising = all(a <= b for a, b in itertools.pairwise(series))
    if not rising or series[-1] <= series[0]:
        return []
    detail = (
        f"Pending cards rose from {series[0]} to {series[-1]} over "
        f"{inputs.cfg.queue_growth_days} days"
    )
    return [_Raw(QUEUE_GROWTH, None, detail)]


def _admitted_times(canonical: Path) -> dict[str, datetime.datetime]:
    """task id -> when its request was (last) admitted."""
    times = {}
    for path in (ledger_of(canonical) / "requests").glob("Q-*.json"):
        receipt = read_json(path)
        admitted = [e["at"] for e in receipt.get("history", []) if e["state"] == "admitted"]
        if receipt.get("tasks") and admitted and _time(admitted[-1]):
            times[receipt["tasks"][-1]["task_id"]] = _time(admitted[-1])
    return times


def _stale_card(inputs: _Inputs) -> list[_Raw]:
    limit = datetime.timedelta(days=inputs.cfg.stale_card_days)
    admitted = _admitted_times(inputs.ctx.canonical)
    found = []
    for card in inputs.queue.list("pending"):
        at = admitted.get(card.task_id)
        if at is not None and inputs.now - at > limit:
            days = (inputs.now - at).days
            detail = f"the card “{card.spec.title}” has been pending {days} days"
            found.append(_Raw(STALE_CARD, card.task_id, detail))
    return found


def _merge_hotspot(inputs: _Inputs) -> list[_Raw]:
    counts: Counter = Counter()
    for handoff in inputs.handoffs[-inputs.cfg.merge_hotspot_window :]:
        counts.update(set(handoff.get("overlap") or []))
    return [
        _Raw(MERGE_HOTSPOT, entity, f"{count} reconciliations on {entity}")
        for entity, count in sorted(counts.items())
        if count >= inputs.cfg.merge_hotspot_conflicts
    ]


def _identity_mismatch(inputs: _Inputs) -> list[_Raw]:
    problems = inputs.preflight.identity_problems if inputs.preflight else []
    if not problems:
        return []
    return [_Raw(IDENTITY_MISMATCH, None, "; ".join(problems) + " (hard stop)")]


def _credits_exhausted(inputs: _Inputs) -> list[_Raw]:
    waiting = sorted(e["id"] for e in inputs.experiments if e.get("status") == "awaiting-credits")
    credits = inputs.preflight.credits if inputs.preflight is not None else None
    no_credits = credits is not None and credits <= 0
    if not waiting and not no_credits:
        return []
    parts = ["no XP credits remain"] if no_credits else []
    if waiting:
        parts.append("awaiting credits: " + ", ".join(waiting))
    return [_Raw(CREDITS_EXHAUSTED, None, "; ".join(parts) + ". Other work continues.")]


def _keeper_step_failed(inputs: _Inputs) -> list[_Raw]:
    return [
        _Raw(KEEPER_STEP_FAILED, step, f"queue-keeper step {step} failed: {error}")
        for step, error in sorted(inputs.step_failures.items())
    ]


DETECTORS = (
    _spinning,
    _build_failure_loop,
    _thrash,
    _plateau,
    _repeated_tool_error,
    _long_iterations,
    _stale_plan,
    _queue_growth,
    _stale_card,
    _merge_hotspot,
    _identity_mismatch,
    _credits_exhausted,
    _keeper_step_failed,
)


# --- persistence and escalation ---


def signals_state_path(ctx: RunContext) -> Path:
    return runtime_dir(ctx) / STATE_FILE


def _file_key(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", key.replace(":", "--"))


def escalation_marker_path(canonical: Path, key: str, first_seen: str) -> Path:
    """The marker of one episode of signal ``key`` (it first fired at ``first_seen``): a
    signal that clears and returns later is a new episode and can escalate again."""
    return ledger_of(canonical) / "escalations" / f"{_file_key(f'{key}@{first_seen}')}.json"


def _load_state(ctx: RunContext) -> dict:
    path = signals_state_path(ctx)
    try:
        state = read_json(path) if path.exists() else None
    except ValueError:
        state = None  # disposable: a damaged file just restarts persistence
    return {
        "signals": (state or {}).get("signals", {}),
        "pending_samples": (state or {}).get("pending_samples", []),
        "lost": state is None,
    }


def _escalated_episode(canonical: Path, key: str) -> dict | None:
    """The latest escalated episode of ``key`` (from its committed markers), if any."""
    episodes = [
        marker
        for marker in (
            committed_json(canonical, path)
            for path in (ledger_of(canonical) / "escalations").glob("*.json")
        )
        if marker is not None and marker.get("key") == key and "first_iteration" in marker
    ]
    return max(episodes, key=lambda marker: marker["first_seen"]) if episodes else None


def _escalate(ctx: RunContext, signal: Signal, first_iteration: int, allow_no_remote: bool):
    """Publish one runtime help_request for this episode of ``signal`` and commit its marker
    (once per episode). Takes the canonical lock."""
    canonical = ctx.canonical
    marker = escalation_marker_path(canonical, signal.key, signal.first_seen)
    message = runtime_messages.signal_persists(
        signal.code, signal.entity, signal.detail, signal.first_seen, signal.count - 1
    )
    with canonical_lock(canonical):
        if committed_json(canonical, marker) is not None:
            return
        if not already_published(canonical, message):  # its key names the episode
            publish_message(canonical, message, allow_no_remote)
        record = {
            "key": signal.key,
            "first_seen": signal.first_seen,
            "first_iteration": first_iteration,
            "detail": signal.detail,
            "at": now_iso(),
        }
        write_json(marker, record)
        commit(canonical, [marker], f"signals: escalated {signal.key}")


def compute_signals(
    ctx: RunContext,
    queue: Queue,
    now: datetime.datetime,
    cfg: SignalConfig | None = None,
    *,
    preflight: PreflightResult | None = None,
    allow_no_remote: bool = False,
    step_failures: dict[str, str] | None = None,
) -> list[Signal]:
    """The signals that fire now, with persistence and escalation (module docstring).

    ``preflight`` is this run's preflight result (identity, credits); ``allow_no_remote`` is
    for the escalation help_request (tests, local runs); ``step_failures`` are queue-keeper
    steps that raised (step -> error). The caller must not hold the canonical lock."""
    cfg = cfg or SignalConfig()
    canonical = ctx.canonical
    inputs = _Inputs(
        ctx=ctx,
        queue=queue,
        now=now,
        cfg=cfg,
        preflight=preflight,
        step_failures=dict(step_failures or {}),
        handoffs=_handoffs(canonical),
        experiments=_records(canonical, "experiment"),
        state=_load_state(ctx),
    )
    raw = [found for detector in DETECTORS for found in detector(inputs)]
    iteration = len(inputs.handoffs)
    seen = inputs.state["signals"]
    persisted = {}
    signals = []
    for item in raw:
        first = seen.get(item.key)
        if first is None and inputs.state["lost"]:
            # signals.json was lost (host rebuilt): continue the escalated episode, if any,
            # rather than start a new one that would escalate again.
            episode = _escalated_episode(canonical, item.key)
            if episode is not None:
                first = {k: episode[k] for k in ("first_seen", "first_iteration")}
        first = first or {"first_seen": now.isoformat(), "first_iteration": iteration}
        persisted[item.key] = first
        count = iteration - first["first_iteration"] + 1
        signals.append(Signal(item.code, item.entity, item.detail, first["first_seen"], count))
    escalations = []
    for signal in signals:
        if signal.code in NOT_ESCALATED or signal.count <= cfg.escalation_iterations:
            continue
        _escalate(ctx, signal, persisted[signal.key]["first_iteration"], allow_no_remote)
        detail = f"{signal.key} persisted {signal.count - 1} iterations; help requested"
        escalations.append(Signal(ESCALATION, signal.key, detail, signal.first_seen, signal.count))
    inputs.state["signals"] = persisted
    inputs.state.pop("lost")
    write_json(signals_state_path(ctx), inputs.state)
    return signals + escalations
