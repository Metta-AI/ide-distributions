"""How easy the research tools and skills are to find, read and use, for the model that uses them.

The measurement costs real money, so it runs only with ``-m model`` and a key. The offline tests
here and in ``test_usability_reads.py`` need neither and run in the default ``pytest``: they check
that the situations still match the shipped tools and skills, how a run is judged, and that what a
run answers has the real tools' shape. The measurement writes its raw runs to
``USABILITY_RUN_DIR``, ends by printing a comparison with ``usability_baseline.json``, and replaces
that file only when ``USABILITY_WRITE_BASELINE=1``.
A run that is cut short resumes from the same ``USABILITY_RUN_DIR``, and is refused there if the
tools, skills, situations or test changed. The report on a run belongs in
``docs/reports/``; the first is ``tool-usability-baseline-2026-11-04.md``.

Environment, all optional but the first:
  IDE_HERMES_SRC            a Hermes source tree, for its schema sanitizers (required)
  USABILITY_RUN_DIR         where raw results go (default: a dated folder in the temp folder)
  USABILITY_PARTS           comma-separated: findability, readability, comprehension, sizes
  USABILITY_REPEATS         runs per situation (default 5)
  USABILITY_OPINION_REPEATS self-report runs per item (default 3)
  USABILITY_SITUATIONS      comma-separated substrings of situation ids
  USABILITY_WORKERS         requests at once (default 8)
  USABILITY_WRITE_BASELINE  1: write usability_baseline.json from this run
  IDE_TOOL_CALL_MODELS      the first entry replaces the test model
  IDE_TOOL_CALL_HOSTS       the first entry replaces the shipped host pin
  IDE_TOOL_CALL_SCHEMAS     ``module:attribute`` naming another ``harness.SchemaSet``; only
                            findability runs with one
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import json
import os
import subprocess
import tempfile
import threading
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import research_core
from research_core.tools import catalog
from tests.model import (
    harness,
    usability_context,
    usability_probe,
    usability_readability,
    usability_reads,
    usability_results,
    write_actions,
)
from tests.model.usability_situations import SITUATIONS, Situation

BASELINE = Path(__file__).with_name("usability_baseline.json")
MODEL = "moonshotai/kimi-k3.1-pro"
ALL_PARTS = ("sizes", "comprehension", "readability", "findability")


def inputs_fingerprint(model: str, host: str, schemas: harness.SchemaSet) -> dict:
    """Everything a run's results depend on, so a resumed run cannot pass off old results: what
    the model is sent, what answers it, and what judges it."""
    judging = (usability_probe, usability_readability, write_actions)
    source = "".join(Path(module.__file__).read_text() for module in judging)
    package = Path(research_core.__file__).parent
    for path in sorted(package.rglob("*")):  # the checks, the tools, the prompts and the skills
        if path.suffix in (".py", ".md", ".json", ".yaml"):
            source += path.read_text()
    tools = {role: schemas.tools(role) for role in catalog.ROLES}
    return {
        "model": model,
        "host": host,
        "provider": harness.provider_for(model, host),
        "schemas": schemas.label,
        "hermes_source": os.environ.get("IDE_HERMES_SRC"),
        "reads": usability_reads.READS_HASH,
        "source": usability_results.short_hash(source),
        "tools": usability_results.short_hash(json.dumps([tools, usability_context.HERMES])),
        "situations": {s.id: usability_results.situation_hash(s) for s in SITUATIONS},
        "messages": {
            s.id: usability_results.short_hash(json.dumps(usability_probe.messages_for(s, schemas)))
            for s in SITUATIONS
        },
        "items": {
            item.id: usability_results.short_hash(item.text)
            for item in usability_readability.items()
        },
    }


def parts_for(schemas: harness.SchemaSet) -> list[str]:
    """The parts to run. Sizes and the reading parts read the shipped catalog, so with another
    schema set only findability measures what the label says."""
    parts = harness.env_list("USABILITY_PARTS") or list(ALL_PARTS)
    return parts if schemas.label == "shipped" else [p for p in parts if p == "findability"]


def require_same_inputs(folder: Path, inputs: dict) -> None:
    """A run folder holds results of one set of inputs: resume it only with those."""
    path = folder / "inputs.json"
    if not path.exists():
        path.write_text(json.dumps(inputs, indent=1))
    elif json.loads(path.read_text()) != inputs:
        pytest.fail(
            f"{folder} holds results of other inputs: set USABILITY_RUN_DIR to a new folder"
        )


def today() -> str:
    return datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()


def run_dir() -> Path:
    default = Path(tempfile.gettempdir()) / f"usability-{today()}"
    folder = Path(os.environ.get("USABILITY_RUN_DIR") or default)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


class RawFile:
    """A JSON-lines file of finished work, which a resumed run does not repeat."""

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.rows = (
            [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        )

    def add(self, row: dict) -> None:
        with self.lock:
            self.rows.append(row)
            with self.path.open("a") as file:
                file.write(json.dumps(row, ensure_ascii=False) + "\n")


def in_parallel(jobs: list, worker) -> None:
    """Run the jobs, at most USABILITY_WORKERS at once. A job's unexpected error fails the test."""
    workers = int(os.environ.get("USABILITY_WORKERS") or 8)
    with concurrent.futures.ThreadPoolExecutor(workers) as pool:
        for future in [pool.submit(worker, job) for job in jobs]:
            future.result()


def selected_situations() -> list[Situation]:
    wanted = harness.env_list("USABILITY_SITUATIONS")
    return [s for s in SITUATIONS if not wanted or any(w in s.id for w in wanted)]


def model_and_provider() -> tuple[str, str, dict | None]:
    model = next(iter(harness.env_list("IDE_TOOL_CALL_MODELS")), MODEL)
    host = next(iter(harness.env_list("IDE_TOOL_CALL_HOSTS")), harness.SHIPPED)
    return model, host, harness.provider_for(model, host)


# --- the parts --------------------------------------------------------------------------------


def run_findability(folder: Path, model: str, host: str, schemas: harness.SchemaSet) -> list[dict]:
    raw = RawFile(folder / "findability.jsonl")
    done = {(row["situation"], row["repeat"]) for row in raw.rows if row["outcome"] != "host_error"}
    repeats = int(os.environ.get("USABILITY_REPEATS") or 5)
    jobs = [
        (situation, repeat)
        for situation in selected_situations()
        for repeat in range(repeats)
        if (situation.id, repeat) not in done
    ]

    def work(job: tuple[Situation, int]) -> None:
        situation, repeat = job
        raw.add(usability_probe.run_situation(situation, repeat, model, host, schemas).to_dict())

    # One run per role first, alone: later requests then read the provider's prompt cache.
    warmers = {job[0].role: job for job in jobs}
    for job in warmers.values():
        work(job)
    warmed = {(job[0].id, job[1]) for job in warmers.values()}
    in_parallel([job for job in jobs if (job[0].id, job[1]) not in warmed], work)
    wanted = {
        (situation.id, repeat) for situation in selected_situations() for repeat in range(repeats)
    }
    latest: dict[tuple[str, int], dict] = {}
    for row in raw.rows:
        latest[(row["situation"], row["repeat"])] = row
    return [row for key, row in latest.items() if key in wanted]


def _item_jobs(raw: RawFile, repeats: int) -> list[tuple[usability_readability.Item, int]]:
    done = Counter(row["item"] for row in raw.rows)
    return [
        (item, repeat)
        for item in usability_readability.items()
        for repeat in range(done[item.id], repeats)
    ]


def run_items(folder: Path, name: str, repeats: int, ask, model: str, provider) -> list[dict]:
    raw = RawFile(folder / f"{name}.jsonl")

    def work(job: tuple[usability_readability.Item, int]) -> None:
        raw.add(ask(job[0], model, provider))

    in_parallel(_item_jobs(raw, repeats), work)
    return raw.rows


def run_sizes(folder: Path, model: str, provider) -> tuple[dict, float]:
    """Bytes, real tokens and a text hash per skill and tool, plus each role's fixed prompt."""
    path = folder / "sizes.json"
    if path.exists():
        saved = json.loads(path.read_text())
        return saved["sizes"], saved["cost"]
    sizes: dict[str, dict] = {}
    baselines: dict[str, int] = {}
    cost = 0.0
    for item in usability_readability.items():
        tokens, spent = usability_readability.tokens_of(item, model, provider, baselines)
        cost += spent
        sizes[item.id] = {
            "hash": usability_results.short_hash(item.text),
            "bytes": len(item.text.encode()),
            "tokens": tokens,
        }
    path.write_text(json.dumps({"sizes": sizes, "cost": cost}, indent=1))
    return sizes, cost


# --- the results ------------------------------------------------------------------------------


def score_items(rows: list[dict], key: str) -> dict[str, dict]:
    scored: dict[str, dict] = {}
    for row in rows:
        entry = scored.setdefault(row["item"], {"right": 0, "n": 0, "wrong": Counter()})
        entry["n"] += len(row[key])
        entry["right"] += sum(row[key])
        entry["wrong"].update(n + 1 for n, right in enumerate(row[key]) if not right)
    for entry in scored.values():
        entry["wrong"] = {str(k): v for k, v in entry["wrong"].items()}
    return scored


def opinion_scores(rows: list[dict]) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for row in rows:
        review = row["review"] or {}
        entry = found.setdefault(
            row["item"],
            {"n": 0, "clarity_sum": 0, "unclear": 0, "contradictory": 0, "would_cut": 0},
        )
        entry["n"] += 1
        entry["clarity_sum"] += review.get("clarity") or 0
        for field in ("unclear", "contradictory", "would_cut"):
            entry[field] += len(review.get(field) or [])
    return {
        item: {
            "n": entry["n"],
            "clarity": round(entry["clarity_sum"] / entry["n"], 2),
            "unclear": entry["unclear"],
            "contradictory": entry["contradictory"],
            "would_cut": entry["would_cut"],
        }
        for item, entry in found.items()
    }


def build_results(
    model: str,
    host: str,
    schemas_label: str,
    runs: list[dict],
    sizes: dict,
    comprehension: list[dict],
    opinions: list[dict],
    other_cost: float,
) -> dict:
    situations = selected_situations()
    grouped: dict[str, list[dict]] = {}
    for run in runs:
        grouped.setdefault(run["situation"], []).append(run)
    rows = {s.id: usability_results.situation_row(s, grouped.get(s.id, [])) for s in situations}
    acts = usability_results.by_act(situations, rows, runs)
    tools = usability_results.by_tool(
        acts,
        runs,
        {spec.name for role in catalog.ROLES for spec in catalog.tools_for(role)},
    )
    labels = {
        (f"{spec.name}.{action.name}" if spec.actions else spec.name)
        for role in catalog.ROLES
        for spec in catalog.tools_for(role)
        for action in (spec.actions_for(role) if spec.actions else [spec])
    }
    every_skill = {skill.name for skill in usability_context.all_skills()}
    opened = {name for run in runs for name in run["skills"]}
    index_bytes = {
        role: len(usability_context.skill_index(role).encode()) for role in catalog.ROLES
    }
    first_tokens: dict[str, list[int]] = {role: [] for role in catalog.ROLES}
    roles = {s.id: s.role for s in situations}
    for run in runs:
        if run["first_prompt_tokens"]:
            first_tokens[roles[run["situation"]]].append(run["first_prompt_tokens"])
    commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent,
    ).stdout.strip()
    return {
        "meta": {
            "date": today(),
            "commit": commit,
            "model": model,
            "host": host,
            "schemas": schemas_label,
            "served_by": sorted({run["provider"] for run in runs if run["provider"]}),
            "max_requests": usability_probe.MAX_REQUESTS,
            "reads": usability_reads.READS_HASH,
            "runs": len(runs),
            "requests": sum(run["requests"] for run in runs),
            "cost_findability": round(sum(run["cost"] for run in runs), 2),
            "cost_other": round(other_cost, 2),
            "index_bytes": index_bytes,
            "first_prompt_tokens": {
                role: round(sum(values) / len(values)) if values else 0
                for role, values in first_tokens.items()
            },
        },
        "totals": usability_results.totals(situations, rows),
        "situations": rows,
        "skills": usability_results.by_skill(situations, rows, runs),
        "acts": acts,
        "tools": tools,
        "never_opened": sorted(every_skill - opened),
        "never_called": usability_results.never_called(runs, labels),
        "sizes": sizes,
        "comprehension": score_items(comprehension, "right"),
        "opinions": opinion_scores(opinions),
    }


# --- the tests --------------------------------------------------------------------------------


def _catalog_labels(role: str) -> set[str]:
    labels = set()
    for spec in catalog.tools_for(role):
        if spec.actions:
            labels.update(f"{spec.name}.{action.name}" for action in spec.actions_for(role))
        else:
            labels.add(spec.name)
    return labels


def test_offline_every_skill_and_tool_has_a_situation():
    wanted_skills = {s for situation in SITUATIONS for s in situation.skills}
    assert {skill.name for skill in usability_context.all_skills()} <= wanted_skills
    wanted_tools = {act.split(".")[0] for situation in SITUATIONS for act in situation.acts}
    shipped = {spec.name for role in catalog.ROLES for spec in catalog.tools_for(role)}
    assert shipped <= wanted_tools


def test_offline_situations_name_what_the_role_has():
    for situation in SITUATIONS:
        skills = {skill.name for skill in usability_context.skills_for(situation.role)}
        assert set(situation.skills) <= skills, situation.id
        known = _catalog_labels(situation.role) | usability_context.builtin_names() | {"text"}
        assert set(situation.acts) <= known, situation.id
        assert set(situation.before) <= known, situation.id


def test_offline_every_item_has_questions():
    items = {item.id for item in usability_readability.items()}
    assert items == set(usability_readability.QUESTIONS)


def test_offline_the_prompt_holds_soul_and_every_skill():
    for role in catalog.ROLES:
        prompt = usability_context.system_prompt(role)
        assert "## Order of authority" in prompt
        assert "[briefing]" in prompt
        for skill in usability_context.skills_for(role):
            assert f"- {skill.name}: {skill.description}" in prompt


class FakeReplies:
    def __init__(self, *replies: list[tuple[str, dict]]):
        self.replies = list(replies)

    def __call__(self, *_, **__) -> harness.Reply:
        calls = [
            harness.Call(f"c{n}", name, args, json.dumps(args))
            for n, (name, args) in enumerate(self.replies.pop(0))
        ]
        message = {"role": "assistant", "content": ""}
        return harness.Reply(message, calls, "", "fake", 100, 0, 10, 0.0, 0.0)


@pytest.mark.parametrize(
    ("replies", "outcome", "valid", "skills"),
    [
        # Opens the skill, reads, then acts validly.
        (
            [
                [("skill_view", {"name": "writing-claims"})],
                [("claim", {"action": "search", "query": "x"})],
                [
                    (
                        "claim",
                        {
                            "action": "retire",
                            "claim": "C-2",
                            "status": "refuted",
                            "type": "experiment",
                            "ref": "X-3",
                        },
                    )
                ],
            ],
            "right",
            True,
            ["writing-claims"],
        ),
        # The category path works in Hermes too, and is recorded as the skill's name.
        (
            [
                [("skill_view", {"name": "records/writing-claims"})],
                [
                    (
                        "claim",
                        {
                            "action": "retire",
                            "claim": "C-2",
                            "status": "refuted",
                            "type": "experiment",
                            "ref": "X-3",
                        },
                    )
                ],
            ],
            "right",
            True,
            ["writing-claims"],
        ),
        # An invalid attempt is refused, and the next attempt can still be right.
        (
            [
                [("claim", {"action": "retire"})],
                [
                    (
                        "claim",
                        {
                            "action": "retire",
                            "claim": "C-2",
                            "status": "refuted",
                            "type": "experiment",
                            "ref": "X-3",
                        },
                    )
                ],
            ],
            "right",
            False,
            [],
        ),
        # Another write ends the run: production would have done it.
        (
            [[("task", {"action": "message", "task": "T-1", "text": "x", "operation_id": "o"})]],
            "wrong_act",
            None,
            [],
        ),
        # Without a required field it is refused: nothing happened, and the run goes on.
        (
            [
                [("task", {"action": "message", "task": "T-1"})],
                [],
            ],
            "text",
            None,
            [],
        ),
        # Words instead of a call.
        ([[]], "text", None, []),
    ],
)
def test_offline_how_a_run_is_judged(monkeypatch, replies, outcome, valid, skills):
    situation = next(s for s in SITUATIONS if s.id == "o-claim-retire")
    monkeypatch.setattr(harness, "complete", FakeReplies(*replies))
    monkeypatch.setattr(usability_context, "session_tools", lambda *_: [])
    run = usability_probe.run_situation(
        situation, 0, MODEL, harness.SHIPPED, harness.ShippedSchemas()
    )
    assert run.outcome == outcome
    assert run.act_valid is valid
    assert run.skills == skills


def test_offline_the_baseline_compares_clean_with_itself():
    baseline = json.loads(BASELINE.read_text())
    headline_lines = 6  # the date line and one line per scope: nothing else differs
    assert len(usability_results.compare(baseline, baseline)) == headline_lines


def test_offline_the_baseline_keeps_what_compare_needs():
    """Every count ``compare`` tests survives ``baseline_text``, so a later run compares clean."""
    results = _results_of_two_runs()
    saved = json.loads(usability_results.baseline_text(results))
    assert len(usability_results.compare(saved, saved)) == 6


def test_offline_compare_says_so_when_the_baseline_had_other_reads():
    saved = json.loads(usability_results.baseline_text(_results_of_two_runs()))
    older = json.loads(json.dumps(saved))
    older["meta"]["reads"] = "placeholder"
    lines = usability_results.compare(older, saved)
    assert any("not comparable as a change in the tools" in line for line in lines)


def test_offline_a_change_in_any_field_of_a_situation_changes_its_hash():
    situation = SITUATIONS[0]
    assert usability_results.situation_hash(situation) != usability_results.situation_hash(
        dataclasses.replace(situation, x5="confirmed")
    )


def test_offline_first_try_validity_of_a_single_situation_is_compared():
    """Finding 1 of the report: ``o-review`` going from 0/5 to 5/5 must be reported."""
    saved = json.loads(usability_results.baseline_text(_results_of_two_runs()))
    later = json.loads(json.dumps(saved))
    saved["situations"]["o-review"] |= {"attempted": 5, "act_valid_first_try": 0}
    later["situations"]["o-review"] |= {"attempted": 5, "act_valid_first_try": 5}
    lines = usability_results.compare(saved, later)
    assert any("o-review act_valid_first_try: 0/5 -> 5/5" in line for line in lines)


def _results_of_two_runs() -> dict:
    """``build_results`` on two made-up runs, one right and one wrong, with nothing else."""

    def run(repeat: int, outcome: str) -> dict:
        row = usability_probe.Run("o-review", repeat, outcome, ["task.review"]).to_dict()
        row |= {"act": "task.review", "act_valid": outcome == "right", "act_attempts": 1}
        return row

    sizes = {"skill:review": {"hash": "abc", "bytes": 10, "tokens": 3}}
    return build_results(
        MODEL,
        harness.SHIPPED,
        "shipped",
        [run(0, "right"), run(1, "wrong_act")],
        sizes,
        [],
        [],
        0.0,
    )


def test_offline_compare_leaves_out_totals_of_changed_situations():
    old = json.loads(usability_results.baseline_text(_results_of_two_runs()))
    new = json.loads(json.dumps(old))
    new["situations"]["o-review"]["hash"] = "changed"
    old["tools"]["task"] |= {"right": 0, "n": 50}
    new["tools"]["task"] |= {"right": 50, "n": 50}
    lines = usability_results.compare(old, new)
    assert "situation o-review changed: not comparable" in lines
    assert not any("tools task" in line for line in lines)
    new["situations"]["o-review"]["hash"] = old["situations"]["o-review"]["hash"]
    assert any("tools task" in line for line in usability_results.compare(old, new))


def test_offline_another_schema_set_runs_only_findability():
    class Other(harness.ShippedSchemas):
        label = "other"

    assert parts_for(Other()) == ["findability"]
    assert parts_for(harness.ShippedSchemas()) == list(ALL_PARTS)


def test_offline_a_run_folder_is_refused_for_other_inputs(tmp_path):
    inputs = inputs_fingerprint(MODEL, harness.SHIPPED, harness.ShippedSchemas())
    require_same_inputs(tmp_path, inputs)
    require_same_inputs(tmp_path, inputs)
    with pytest.raises(pytest.fail.Exception, match="new folder"):
        require_same_inputs(tmp_path, {**inputs, "reads": "other"})


def test_offline_fisher_separates_only_what_five_repeats_can():
    assert usability_results.fisher_p(0, 5, 5, 5) < 0.01
    assert usability_results.fisher_p(3, 5, 5, 5) > 0.4


@pytest.mark.model
@pytest.mark.skipif(not harness.KEY, reason="OPENROUTER_API_KEY is not set")
def test_measure_usability(capsys):
    harness.hermes_sanitizers()  # fails loudly without IDE_HERMES_SRC
    folder = run_dir()
    model, host, provider = model_and_provider()
    schemas = harness.schema_set()
    parts = parts_for(schemas)
    require_same_inputs(folder, inputs_fingerprint(model, host, schemas))
    sizes, cost = run_sizes(folder, model, provider) if "sizes" in parts else ({}, 0.0)
    comprehension: list[dict] = []
    if "comprehension" in parts:
        comprehension = run_items(
            folder,
            "comprehension",
            int(os.environ.get("USABILITY_REPEATS") or 5),
            usability_readability.comprehension,
            model,
            provider,
        )
    opinions: list[dict] = []
    if "readability" in parts:
        opinions = run_items(
            folder,
            "readability",
            int(os.environ.get("USABILITY_OPINION_REPEATS") or 3),
            usability_readability.self_report,
            model,
            provider,
        )
    runs: list[dict] = []
    if "findability" in parts:
        runs = run_findability(folder, model, host, schemas)
    other_cost = cost + sum(row["cost"] for row in [*comprehension, *opinions])
    results = build_results(
        model, host, schemas.label, runs, sizes, comprehension, opinions, other_cost
    )
    (folder / "results.json").write_text(json.dumps(results, indent=1))
    lines = [f"Raw results and results.json are in {folder}."]
    if BASELINE.exists() and "findability" in parts:
        old = json.loads(BASELINE.read_text())
        lines += usability_results.compare(old, results)
    if os.environ.get("USABILITY_WRITE_BASELINE") == "1":
        BASELINE.write_text(usability_results.baseline_text(results))
        lines.append(f"Wrote {BASELINE}.")
    with capsys.disabled():
        print("\n" + "\n".join(lines))
