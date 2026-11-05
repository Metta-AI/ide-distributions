"""Turning the raw runs into the baseline file, and comparing two baselines.

``usability_baseline.json`` holds counts, not rates, so a later run can be tested against it:
:func:`compare` runs Fisher's exact test on each count. With about 180 situations some will differ
by chance, so a single situation is listed only below p = 0.01 and a whole skill or tool below
p = 0.05. Each situation and item carries a short hash of its text: a changed hash says the test
or the text changed, which is what a before/after comparison needs to show.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict

from tests.model.usability_situations import Situation

NOT_COUNTED = {"host_error"}


def short_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:10]


def situation_hash(situation: Situation) -> str:
    """Of every field, so a change to anything the model is shown or judged on says so."""
    return short_hash(json.dumps(asdict(situation), sort_keys=True))


def _top(counter: Counter, count: int = 5) -> dict[str, int]:
    return dict(counter.most_common(count))


def _mean(values: Iterable[float]) -> float:
    values = list(values)
    return round(sum(values) / len(values), 2) if values else 0.0


def _first_real_call(run: dict) -> str:
    return next((c for c in run["calls"] if not c.startswith("skill")), "")


def _steps_before_act(run: dict) -> int:
    """Calls before the one that ended the run; all of them when no call did."""
    return len(run["calls"]) - (1 if run["act"] else 0)


def _preparation_count(run: dict) -> int:
    """Calls that were neither opening a skill nor the call that ended the run."""
    others = [c for c in run["calls"] if not c.startswith("skill")]
    return max(len(others) - (1 if run["act"] else 0), 0)


def situation_row(situation: Situation, runs: list[dict]) -> dict:
    counted = [r for r in runs if r["outcome"] not in NOT_COUNTED]
    accepted = set(situation.skills)
    attempts = [r for r in counted if r["act_valid"] is not None]
    row = {
        "hash": situation_hash(situation),
        "role": situation.role,
        "acts": list(situation.acts),
        "skills": list(situation.skills),
        "n": len(counted),
        "host_errors": len(runs) - len(counted),
        "right": sum(r["outcome"] == "right" for r in counted),
        "first_call_right": sum((_first_real_call(r) or "text") in situation.acts for r in counted),
        "args_checked": sum(r.get("args_right") is not None for r in counted),
        "args_right": sum(r.get("args_right") is True for r in counted),
        "attempted": len(attempts),
        "act_valid_first_try": sum(bool(r["act_valid"]) for r in attempts),
        "mean_steps": _mean(_steps_before_act(r) for r in counted),
        "mean_preparation": _mean(_preparation_count(r) for r in counted),
        "mean_requests": _mean(r["requests"] for r in counted),
        "mean_skills_opened": _mean(len(r["skills"]) for r in counted),
        "outcomes": dict(Counter(r["outcome"] for r in counted)),
        "first_calls": _top(Counter(_first_real_call(r) or "(none)" for r in counted)),
        "ended_with": _top(Counter(r["act"] or r["outcome"] for r in counted)),
        # All of them: by_skill counts each skill from this, so a top few would undercount.
        "skills_opened": dict(Counter(s for r in counted for s in set(r["skills"]))),
    }
    if accepted:
        row["skill_opened"] = sum(bool(accepted & set(r["skills"])) for r in counted)
        row["skill_first"] = sum(
            bool(r["skills"]) and r["skills"][0] in accepted and r["calls"][0] == "skill_view"
            for r in counted
        )
    return row


def _labels_of(act: str) -> str:
    return act.split(".", 1)[0]


def by_skill(situations: list[Situation], rows: dict[str, dict], runs: list[dict]) -> dict:
    """Per skill: the situations that want it first, and how often it or an accepted one opened."""
    result: dict[str, dict] = {}
    names = {name for s in situations for name in s.skills}
    by_situation = _group(runs)
    opened_at_all: Counter = Counter()
    for run in runs:
        opened_at_all.update(set(run["skills"]))
    for name in sorted(names | set(opened_at_all)):
        primary = [s for s in situations if s.skills and s.skills[0] == name]
        any_listed = [s for s in situations if name in s.skills]
        confused: Counter = Counter()
        for situation in primary:
            for run in by_situation.get(situation.id, []):
                if run["outcome"] in NOT_COUNTED:
                    continue
                if not set(situation.skills) & set(run["skills"]):
                    confused[run["skills"][0] if run["skills"] else "(no skill opened)"] += 1
        unlisted = [s for s in situations if name not in s.skills]
        result[name] = {
            "situations": [s.id for s in primary],
            "n": sum(rows[s.id]["n"] for s in primary),
            "opened_accepted": sum(rows[s.id].get("skill_opened", 0) for s in primary),
            "opened_this": sum(rows[s.id]["skills_opened"].get(name, 0) for s in primary),
            "n_listed": sum(rows[s.id]["n"] for s in any_listed),
            "opened_listed": sum(rows[s.id]["skills_opened"].get(name, 0) for s in any_listed),
            "opened_unexpected": sum(rows[s.id]["skills_opened"].get(name, 0) for s in unlisted),
            "confused_with": _top(confused),
        }
    return result


def _group(runs: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for run in runs:
        grouped.setdefault(run["situation"], []).append(run)
    return grouped


def by_act(situations: list[Situation], rows: dict[str, dict], runs: list[dict]) -> dict:
    """Per ``tool.action``: the situations that want it, and what happened instead."""
    result: dict[str, dict] = {}
    by_situation = _group(runs)
    for situation in situations:
        for act in dict.fromkeys(situation.acts[:1]):
            entry = result.setdefault(
                act,
                {
                    "situations": [],
                    "n": 0,
                    "right": 0,
                    "first_call_right": 0,
                    "attempted": 0,
                    "valid_first_try": 0,
                    "instead": Counter(),
                },
            )
            row = rows[situation.id]
            entry["situations"].append(situation.id)
            entry["n"] += row["n"]
            entry["right"] += row["right"]
            entry["first_call_right"] += row["first_call_right"]
            entry["attempted"] += row["attempted"]
            entry["valid_first_try"] += row["act_valid_first_try"]
            for run in by_situation.get(situation.id, []):
                if run["outcome"] == "right" or run["outcome"] in NOT_COUNTED:
                    continue
                entry["instead"][run["act"] or f"({run['outcome']})"] += 1
    for entry in result.values():
        entry["instead"] = _top(entry["instead"])
    return result


def by_tool(acts: dict[str, dict], runs: list[dict], tool_names: Iterable[str]) -> dict:
    """Per tool: its actions' counts summed, and how often anything called it."""
    called: Counter = Counter()
    for run in runs:
        called.update(_labels_of(call) for call in run["calls"])
    result: dict[str, dict] = {}
    for name in sorted(tool_names):
        mine = {act: row for act, row in acts.items() if _labels_of(act) == name}
        result[name] = {
            "acts": sorted(mine),
            "n": sum(r["n"] for r in mine.values()),
            "right": sum(r["right"] for r in mine.values()),
            "first_call_right": sum(r["first_call_right"] for r in mine.values()),
            "attempted": sum(r["attempted"] for r in mine.values()),
            "valid_first_try": sum(r["valid_first_try"] for r in mine.values()),
            "calls_anywhere": called[name],
        }
    return result


def never_called(runs: list[dict], every_label: Iterable[str]) -> list[str]:
    seen = {call for run in runs for call in run["calls"]}
    return sorted(set(every_label) - seen)


def totals(situations: list[Situation], rows: dict[str, dict]) -> dict:
    """The headline counts, overall and per role."""

    def sums(selected: list[Situation]) -> dict:
        picked = [rows[s.id] for s in selected]
        with_skill = [r for r in picked if r["skills"]]
        return {
            "situations": len(picked),
            "n": sum(r["n"] for r in picked),
            "right": sum(r["right"] for r in picked),
            "first_call_right": sum(r["first_call_right"] for r in picked),
            "attempted": sum(r["attempted"] for r in picked),
            "act_valid_first_try": sum(r["act_valid_first_try"] for r in picked),
            "args_checked": sum(r["args_checked"] for r in picked),
            "args_right": sum(r["args_right"] for r in picked),
            "skill_n": sum(r["n"] for r in with_skill),
            "skill_opened": sum(r["skill_opened"] for r in with_skill),
            "skill_first": sum(r["skill_first"] for r in with_skill),
            "mean_steps": _mean(r["mean_steps"] for r in picked),
            "mean_requests": _mean(r["mean_requests"] for r in picked),
            "mean_skills_opened": _mean(r["mean_skills_opened"] for r in picked),
        }

    roles = {
        role: sums([s for s in situations if s.role == role])
        for role in ("orchestrator", "researcher")
    }
    tiers = {
        "plain": sums([s for s in situations if not s.is_hard]),
        "hard": sums([s for s in situations if s.is_hard]),
    }
    return {"all": sums(situations), **roles, **tiers}


# --- comparing two baselines ------------------------------------------------------------------


def fisher_p(a: int, n_a: int, b: int, n_b: int) -> float:
    """Two-sided Fisher's exact test of ``a`` of ``n_a`` against ``b`` of ``n_b``."""
    total = a + b
    size = n_a + n_b

    def probability(x: int) -> float:
        return math.comb(n_a, x) * math.comb(n_b, total - x) / math.comb(size, total)

    observed = probability(a)
    low = max(0, total - n_b)
    high = min(total, n_a)
    return min(
        1.0,
        sum(p for x in range(low, high + 1) if (p := probability(x)) <= observed * (1 + 1e-9)),
    )


def _difference(
    label: str, metric: str, old: tuple[int, int], new: tuple[int, int], limit: float
) -> str | None:
    if not old[1] or not new[1]:
        return None
    p = fisher_p(old[0], old[1], new[0], new[1])
    if p >= limit:
        return None
    return f"{label} {metric}: {old[0]}/{old[1]} -> {new[0]}/{new[1]} (p={p:.3f})"


def compare(old: dict, new: dict) -> list[str]:
    """What changed from baseline ``old`` to run ``new``, as lines for a person."""
    lines = [
        f"Baseline {old['meta']['date']} at {old['meta']['commit']}; "
        f"this run {new['meta']['date']} at {new['meta']['commit']}."
    ]
    if old["meta"].get("reads") != new["meta"].get("reads"):
        lines.append(
            "The two ran on different read results (meta.reads), so what differs below is not "
            "comparable as a change in the tools."
        )
    for scope in ("all", "orchestrator", "researcher", "plain", "hard"):
        before, after = old["totals"][scope], new["totals"][scope]
        lines.append(
            f"{scope}: right {before['right']}/{before['n']} -> {after['right']}/{after['n']}; "
            f"skill opened {before['skill_opened']}/{before['skill_n']} -> "
            f"{after['skill_opened']}/{after['skill_n']}; "
            f"first try valid {before['act_valid_first_try']}/{before['attempted']} -> "
            f"{after['act_valid_first_try']}/{after['attempted']}; "
            f"arguments as the rule says {before['args_right']}/{before['args_checked']} -> "
            f"{after['args_right']}/{after['args_checked']}"
        )
    found: list[str] = []
    for name, row in new["situations"].items():
        was = old["situations"].get(name)
        if was is None:
            found.append(f"new situation {name}")
            continue
        if was["hash"] != row["hash"]:
            found.append(f"situation {name} changed: not comparable")
            continue
        for metric, n_key in (
            ("right", "n"),
            ("first_call_right", "n"),
            ("skill_opened", "n"),
            ("args_right", "args_checked"),
            ("act_valid_first_try", "attempted"),
        ):
            if metric in row and metric in was:
                found.append(
                    _difference(
                        name, metric, (was[metric], was[n_key]), (row[metric], row[n_key]), 0.01
                    )
                )
    comparable = old["situations"].keys() == new["situations"].keys() and not any(
        old["situations"][name]["hash"] != row["hash"]
        for name, row in new["situations"].items()
        if name in old["situations"]
    )
    if not comparable:
        found.append(
            "Whole-skill and whole-tool totals not compared: they add up different situations."
        )
    for section, metrics in (
        ("skills", (("opened_accepted", "n"),)) if comparable else ("skills", ()),
        ("tools", (("right", "n"), ("valid_first_try", "attempted")) if comparable else ()),
    ):
        for name, row in new[section].items():
            was = old[section].get(name)
            if was is None:
                continue
            for metric, n_key in metrics:
                found.append(
                    _difference(
                        f"{section} {name}",
                        metric,
                        (was[metric], was[n_key]),
                        (row[metric], row[n_key]),
                        0.05,
                    )
                )
    for name, row in new["sizes"].items():
        was = old["sizes"].get(name)
        if was is not None and was["hash"] != row["hash"]:
            found.append(
                f"{name} text changed: {was['bytes']} -> {row['bytes']} bytes, "
                f"{was.get('tokens')} -> {row.get('tokens')} tokens"
            )
    return [*lines, *[line for line in found if line]]


# --- the baseline file ------------------------------------------------------------------------


def _without(row: dict, *keys: str) -> dict:
    return {key: value for key, value in row.items() if key not in keys}


def _endings_not_right(row: dict) -> dict:
    """What ended the runs that did not go right. Most situations have none, so most rows save
    the room of a line that would only repeat ``right``."""
    acts = set(row["acts"])
    return {
        ending: count
        for ending, count in row["ended_with"].items()
        if ending not in acts and ending != "right"
    }


def _baseline_situation(row: dict) -> dict:
    kept = {
        key: row[key]
        for key in (
            "hash",
            "n",
            "right",
            "first_call_right",
            "args_checked",
            "args_right",
            "attempted",
            "act_valid_first_try",
        )
    }
    if "skill_opened" in row:
        kept["skill_opened"] = row["skill_opened"]
    missed = _endings_not_right(row)
    if missed:
        kept["ended_with"] = missed
    return kept


def _baseline_view(results: dict) -> dict:
    """The counts :func:`compare` tests and the report's tables rest on; the raw runs and the
    situations file hold the rest."""
    return {
        **results,
        "situations": {
            name: _baseline_situation(row) for name, row in results["situations"].items()
        },
        "acts": {
            name: {"n": row["n"], "right": row["right"], "instead": row["instead"]}
            for name, row in results["acts"].items()
        },
        "skills": {
            name: _without(row, "situations", "n_listed", "opened_listed")
            for name, row in results["skills"].items()
        },
        "comprehension": {
            name: {"right": row["right"], "n": row["n"]}
            for name, row in results["comprehension"].items()
        },
        "opinions": {
            name: {"n": row["n"], "clarity": row["clarity"]}
            for name, row in results["opinions"].items()
        },
    }


def baseline_text(results: dict) -> str:
    """JSON with one line per entry of each section, so two baselines diff line by line."""
    sections = []
    for section, content in _baseline_view(results).items():
        if section == "meta" or not isinstance(content, dict):
            body = json.dumps(content)
        else:
            lines = [f"  {json.dumps(key)}: {json.dumps(row)}" for key, row in content.items()]
            body = "{\n" + ",\n".join(lines) + "\n }"
        sections.append(f" {json.dumps(section)}: {body}")
    return "{\n" + ",\n".join(sections) + "\n}\n"
