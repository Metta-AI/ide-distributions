"""Run a game's bindings. Core code never names a game.

``binding_evaluate(game)`` is the ``evaluate`` binding in hosted-results mode:
``<evaluate> --results <results.json> --json``, run from the repo root. It returns the
binding's ``data.episodes``: one entry per (episode, seat), ``[{episode, seat, metrics,
status?, seed?}]`` (contract in ``games/_template/GAME.md``). xp-poll uses it on XP results and
reduces our seats by the mean; ``softmax league-episodes`` on league episode results, one row
per seat.
"""

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

from autoresearch.games.loader import Game, binding_argv

EVALUATE_TIMEOUT_S = 300
REPO_ROOT = Path(__file__).resolve().parents[3]  # games/ lives at the project root

# (results.json path) -> the binding's entries [{episode, seat, metrics, status?, seed?}]
Evaluate = Callable[[Path], list[dict]]


def binding_evaluate(game: Game, repo_root: Path = REPO_ROOT) -> Evaluate:
    def evaluate(results: Path) -> list[dict]:
        argv = [*binding_argv(game.bindings["evaluate"]), "--results", str(results), "--json"]
        completed = subprocess.run(
            argv,
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=EVALUATE_TIMEOUT_S,
            check=False,
        )
        try:
            payload = json.loads(completed.stdout)
        except ValueError:
            payload = None
        if completed.returncode != 0 or not isinstance(payload, dict) or not payload.get("ok"):
            detail = (payload or {}).get("error") or completed.stderr.strip()[-500:]
            raise RuntimeError(f"{game.name} evaluate binding failed on {results}: {detail}")
        return payload["data"]["episodes"]

    return evaluate
